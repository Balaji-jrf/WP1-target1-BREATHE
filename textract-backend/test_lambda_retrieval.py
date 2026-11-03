import io
import json
import unittest
import base64
import hashlib
import hmac
import time
from decimal import Decimal
from unittest.mock import patch

import lambda_retrieval


class FigureUrlTests(unittest.TestCase):
    def test_signs_only_saved_figure_crops(self):
        document = {
            "document_id": "doc-1",
            "filename": "input.pdf",
            "s3_bucket": "test-bucket",
            "s3_key": "uploads/doc-1/input.pdf",
            "ocr_s3_key": "processed/doc-1/ocr.json",
            "status": "completed",
        }
        ocr = {
            "figures": [
                {
                    "crop_status": "saved",
                    "image_s3_key": "processed/doc-1/figures/figure.png",
                },
                {"crop_status": "failed"},
            ],
        }
        signed_url = lambda params: f"signed:{params['Key']}"

        with (
            patch.object(lambda_retrieval.table, "get_item", return_value={"Item": document}),
            patch.object(
                lambda_retrieval.s3,
                "generate_presigned_url",
                side_effect=lambda _operation, Params, ExpiresIn: signed_url(Params),
            ),
            patch.object(
                lambda_retrieval.s3,
                "get_object",
                return_value={"Body": io.BytesIO(json.dumps(ocr).encode())},
            ),
        ):
            result = lambda_retrieval._get_document("doc-1")

        self.assertEqual(
            result["figure_presigned_urls"],
            {"processed/doc-1/figures/figure.png": "signed:processed/doc-1/figures/figure.png"},
        )
        self.assertEqual(
            result["ocr_presigned_url"],
            "signed:processed/doc-1/ocr.json",
        )


class RecordGeotagTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "id": "DOC#doc-1",
            "theme_entity_id": "CLIMATE#P01",
            "review_status": "approved",
            "extracted_fact": "The district received rainfall.",
        }
        self.geotag = {
            "place_name": "Bidar",
            "place_type": "district",
            "label": "Bidar, Karnataka, India",
            "latitude": 17.9,
            "longitude": 77.5,
            "provider": "Amazon Location Service",
            "place_id": "place-1",
            "relevance": 0.94,
            "status": "confirmed",
            "updated_at": "2025-01-01T00:00:00Z",
        }

    @staticmethod
    def _token():
        secret = "test-secret"
        header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(json.dumps({
            "username": "researcher",
            "permissions": ["atlas"],
            "exp": int(time.time()) + 600,
        }).encode()).rstrip(b"=").decode()
        signature = base64.urlsafe_b64encode(
            hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest(),
        ).rstrip(b"=").decode()
        return f"{header}.{payload}.{signature}"

    def test_saves_valid_geotag_without_replacing_existing_record_fields(self):
        with (
            patch.object(lambda_retrieval.records_table, "get_item", return_value={"Item": self.record}),
            patch.object(lambda_retrieval.records_table, "put_item") as put_item,
        ):
            status, body = lambda_retrieval._update_record(
                "doc-1", "CLIMATE#P01", {"attributes": {"geotag": [self.geotag]}},
            )

        self.assertEqual(status, 200)
        self.assertEqual(body["record"]["review_status"], "approved")
        self.assertEqual(body["record"]["extracted_fact"], self.record["extracted_fact"])
        self.assertEqual(body["record"]["geotag"][0]["latitude"], Decimal("17.9"))
        put_item.assert_called_once()

    def test_rejects_out_of_range_coordinates_without_writing(self):
        invalid_geotag = {**self.geotag, "latitude": 91}
        with (
            patch.object(lambda_retrieval.records_table, "get_item", return_value={"Item": self.record}),
            patch.object(lambda_retrieval.records_table, "put_item") as put_item,
        ):
            status, body = lambda_retrieval._update_record(
                "doc-1", "CLIMATE#P01", {"attributes": {"geotag": [invalid_geotag]}},
            )

        self.assertEqual(status, 400)
        self.assertIn("out of range", body["detail"])
        put_item.assert_not_called()

    def test_geotag_update_route_requires_atlas_permission(self):
        with (
            patch.object(lambda_retrieval, "_update_record") as update_record,
            patch.object(lambda_retrieval.users_table, "get_item", return_value={"Item": {
                "username": "researcher",
                "role": "user",
                "permissions": ["history"],
            }}),
        ):
            response = lambda_retrieval.handler({
                "requestContext": {"http": {"method": "PUT"}},
                "rawPath": "/api/documents/doc-1/records/CLIMATE%23P01",
                "pathParameters": {"document_id": "doc-1", "theme_entity_id": "CLIMATE#P01"},
                "headers": {"Authorization": "Bearer invalid"},
                "body": json.dumps({"attributes": {"geotag": [self.geotag]}}),
            }, None)

        self.assertEqual(response["statusCode"], 401)
        update_record.assert_not_called()

    def test_geotag_update_route_accepts_current_atlas_member(self):
        with (
            patch.object(lambda_retrieval, "JWT_SECRET", "test-secret"),
            patch.object(lambda_retrieval.users_table, "get_item", return_value={"Item": {
                "username": "researcher",
                "role": "user",
                "permissions": ["atlas"],
            }}),
            patch.object(lambda_retrieval, "_update_record", return_value=(200, {"saved": True})) as update_record,
        ):
            response = lambda_retrieval.handler({
                "requestContext": {"http": {"method": "PUT"}},
                "rawPath": "/api/documents/doc-1/records/CLIMATE%23P01",
                "pathParameters": {"document_id": "doc-1", "theme_entity_id": "CLIMATE#P01"},
                "headers": {"Authorization": f"Bearer {self._token()}"},
                "body": json.dumps({"attributes": {"geotag": [self.geotag]}}),
            }, None)

        self.assertEqual(response["statusCode"], 200)
        update_record.assert_called_once()


if __name__ == "__main__":
    unittest.main()
