import unittest
from unittest.mock import patch

import lambda_starter


class DocumentMetadataPersistenceTests(unittest.TestCase):
    def test_processing_write_preserves_name_and_tag_saved_before_upload(self):
        with (
            patch.object(lambda_starter, "S3_BUCKET", "test-bucket"),
            patch.object(
                lambda_starter.table,
                "get_item",
                return_value={"Item": {"doc_name": "Bidar report", "doc_tag": "Policy Document"}},
            ),
            patch.object(lambda_starter.table, "put_item") as put_item,
        ):
            lambda_starter._write_processing(
                "doc-1", "report.pdf", "uploads/doc-1/report.pdf", "2026-11-02T00:00:00Z"
            )

        item = put_item.call_args.kwargs["Item"]
        self.assertEqual(item["doc_name"], "Bidar report")
        self.assertEqual(item["doc_tag"], "Policy Document")
        self.assertEqual(item["status"], "processing")

    def test_completed_write_preserves_name_and_tag(self):
        analysis = {
            "elements": [{"text": "A line"}],
            "total_pages": 1,
            "tables": [],
            "figures": [],
        }
        with (
            patch.object(
                lambda_starter.table,
                "get_item",
                return_value={"Item": {"doc_name": "Bidar report", "doc_tag": "Policy Document"}},
            ),
            patch.object(lambda_starter.table, "put_item") as put_item,
        ):
            lambda_starter._write_completed(
                "doc-1",
                "report.pdf",
                "uploads/doc-1/report.pdf",
                "2026-11-02T00:00:00Z",
                "processed/doc-1/ocr.json",
                analysis,
                file_size_bytes=100,
            )

        item = put_item.call_args.kwargs["Item"]
        self.assertEqual(item["doc_name"], "Bidar report")
        self.assertEqual(item["doc_tag"], "Policy Document")
        self.assertEqual(item["status"], "completed")


if __name__ == "__main__":
    unittest.main()
