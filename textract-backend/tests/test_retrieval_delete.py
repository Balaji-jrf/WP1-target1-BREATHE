import base64
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import patch

import lambda_retrieval as retrieval


class FakeTable:
    def __init__(self, item=None):
        self.item = item
        self.deleted = []
        self.updates = []

    def get_item(self, **kwargs):
        return {"Item": self.item} if self.item else {}

    def delete_item(self, **kwargs):
        self.deleted.append(kwargs["Key"])

    def update_item(self, **kwargs):
        self.updates.append(kwargs)


class FakeRecordsTable:
    def __init__(self):
        self.delete_keys = []
        self.update_keys = []
        self.query_count = 0

    def query(self, **kwargs):
        self.query_count += 1
        if kwargs.get("ProjectionExpression") == "id, theme_entity_id":
            return {"Items": [
                {"id": "DOC#doc-1", "theme_entity_id": "CLIMATE#one"},
                {"id": "DOC#doc-1", "theme_entity_id": "CLIMATE#two"},
            ]}
        if self.query_count == 1:
            return {
                "Items": [{"theme_entity_id": "CLIMATE#one"}],
                "LastEvaluatedKey": {"id": "DOC#doc-1", "theme_entity_id": "next"},
            }
        return {"Items": [{"theme_entity_id": "CLIMATE#two"}]}

    def update_item(self, **kwargs):
        self.update_keys.append(kwargs["Key"])
        return {"Attributes": {"review_status": "approved"}}

    class BatchWriter:
        def __init__(self, parent):
            self.parent = parent

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def delete_item(self, **kwargs):
            self.parent.delete_keys.append(kwargs["Key"])

    def batch_writer(self):
        return self.BatchWriter(self)


class FakeS3:
    def __init__(self):
        self.deleted = []
        self.fail_delete = False

    def get_paginator(self, _operation):
        class Paginator:
            def paginate(_self, **_kwargs):
                return [{"Contents": [{"Key": "processed/doc-1/ocr.json"}]}]
        return Paginator()

    def delete_objects(self, **kwargs):
        self.deleted.extend(
            item["Key"] for item in kwargs["Delete"]["Objects"]
        )
        return {"Errors": [{"Key": "source.pdf", "Code": "AccessDenied"}]} if self.fail_delete else {}


def signed_token(username="admin"):
    encode = lambda value: base64.urlsafe_b64encode(
        json.dumps(value).encode()
    ).rstrip(b"=").decode()
    header = encode({"alg": "HS256", "typ": "JWT"})
    body = encode({"username": username, "exp": int(time.time()) + 600})
    signature = base64.urlsafe_b64encode(
        hmac.new(
            retrieval.JWT_SECRET.encode(),
            f"{header}.{body}".encode(),
            hashlib.sha256,
        ).digest()
    ).rstrip(b"=").decode()
    return f"{header}.{body}.{signature}"


class RetrievalDeleteTests(unittest.TestCase):
    def setUp(self):
        self.document_table = FakeTable({
            "document_id": "doc-1",
            "status": "completed",
            "ingestion_status": "pending_review",
            "s3_bucket": "private-bucket",
            "s3_key": "uploads/doc-1/source.pdf",
            "ocr_s3_key": "processed/doc-1/ocr.json",
        })
        self.records_table = FakeRecordsTable()
        self.s3 = FakeS3()

    def test_document_delete_removes_document_records_and_only_its_files(self):
        with (
            patch.object(retrieval, "table", self.document_table),
            patch.object(retrieval, "records_table", self.records_table),
            patch.object(retrieval, "s3", self.s3),
        ):
            status, body = retrieval._delete_document("doc-1")

        self.assertEqual(status, 200)
        self.assertEqual(body["deleted_record_count"], 2)
        self.assertEqual(
            set(self.s3.deleted),
            {"uploads/doc-1/source.pdf", "processed/doc-1/ocr.json"},
        )
        self.assertEqual(len(self.records_table.delete_keys), 2)
        self.assertEqual(self.document_table.deleted, [{"document_id": "doc-1"}])

    def test_document_delete_rejects_processing_document_without_changes(self):
        self.document_table.item["status"] = "processing"
        with (
            patch.object(retrieval, "table", self.document_table),
            patch.object(retrieval, "records_table", self.records_table),
            patch.object(retrieval, "s3", self.s3),
        ):
            status, _body = retrieval._delete_document("doc-1")

        self.assertEqual(status, 409)
        self.assertEqual(self.s3.deleted, [])
        self.assertEqual(self.records_table.delete_keys, [])
        self.assertEqual(self.document_table.deleted, [])

    def test_delete_route_requires_authentication(self):
        event = {
            "rawPath": "/prod/document/doc-1",
            "requestContext": {"http": {"method": "DELETE"}},
            "headers": {},
        }
        with patch.object(retrieval, "users_table") as users:
            response = retrieval.handler(event, None)

        self.assertEqual(response["statusCode"], 401)
        users.get_item.assert_not_called()

    def test_delete_route_requires_current_admin_role(self):
        event = {
            "rawPath": "/prod/document/doc-1",
            "requestContext": {"http": {"method": "DELETE"}},
            "headers": {"authorization": f"Bearer {signed_token('researcher')}"},
        }
        with patch.object(
            retrieval, "users_table", FakeTable({"username": "researcher", "role": "user"})
        ):
            response = retrieval.handler(event, None)

        self.assertEqual(response["statusCode"], 403)

    def test_admin_delete_route_deletes_the_document(self):
        event = {
            "rawPath": "/prod/document/doc-1",
            "requestContext": {"http": {"method": "DELETE"}},
            "headers": {"authorization": f"Bearer {signed_token()}"},
        }
        with (
            patch.object(retrieval, "users_table", FakeTable({"username": "admin", "role": "admin"})),
            patch.object(retrieval, "table", self.document_table),
            patch.object(retrieval, "records_table", self.records_table),
            patch.object(retrieval, "s3", self.s3),
        ):
            response = retrieval.handler(event, None)

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(self.document_table.deleted, [{"document_id": "doc-1"}])

    def test_s3_error_does_not_remove_database_rows(self):
        self.s3.fail_delete = True
        with (
            patch.object(retrieval, "table", self.document_table),
            patch.object(retrieval, "records_table", self.records_table),
            patch.object(retrieval, "s3", self.s3),
            self.assertRaises(RuntimeError),
        ):
            retrieval._delete_document("doc-1")

        self.assertEqual(self.records_table.delete_keys, [])
        self.assertEqual(self.document_table.deleted, [])

    def test_approve_all_updates_every_pending_record(self):
        with (
            patch.object(retrieval, "records_table", self.records_table),
            patch.object(retrieval, "table", self.document_table),
        ):
            status, body = retrieval._approve_document_records(
                "doc-1", {"approve_all": True}
            )

        self.assertEqual(status, 200)
        self.assertEqual(body["approved_count"], 2)
        self.assertEqual(len(self.records_table.update_keys), 2)


if __name__ == "__main__":
    unittest.main()
