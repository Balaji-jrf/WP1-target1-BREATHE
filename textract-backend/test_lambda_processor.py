import json
import unittest
from unittest.mock import patch

import lambda_processor


def upload_event(filename):
    return {
        "requestContext": {"http": {"method": "GET"}},
        "queryStringParameters": {"filename": filename},
    }


class UploadUrlHandlerTests(unittest.TestCase):
    def setUp(self):
        self.bucket_patch = patch.object(
            lambda_processor, "S3_BUCKET", "jrf-task-ocr-test-bucket"
        )
        self.bucket_patch.start()
        self.addCleanup(self.bucket_patch.stop)
        self.url_patch = patch.object(
            lambda_processor.s3,
            "generate_presigned_url",
            return_value="https://upload.example.test",
        )
        self.generate_url = self.url_patch.start()
        self.addCleanup(self.url_patch.stop)

    def test_returns_presigned_url_for_supported_file(self):
        response = lambda_processor.handler(upload_event("research notes.pdf"), None)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["upload_url"], "https://upload.example.test")
        self.assertEqual(body["filename"], "research notes.pdf")
        self.assertEqual(body["content_type"], "application/pdf")
        self.assertTrue(body["s3_key"].startswith(f"uploads/{body['document_id']}/"))
        self.generate_url.assert_called_once()
        self.assertEqual(self.generate_url.call_args.kwargs["ExpiresIn"], 900)

    def test_rejects_missing_filename(self):
        event = upload_event("")
        response = lambda_processor.handler(event, None)

        self.assertEqual(response["statusCode"], 400)
        self.generate_url.assert_not_called()

    def test_rejects_unsupported_extension(self):
        response = lambda_processor.handler(upload_event("notes.docx"), None)

        self.assertEqual(response["statusCode"], 400)
        self.generate_url.assert_not_called()

    def test_rejects_path_components(self):
        for filename in ("../notes.pdf", "folder/notes.pdf", r"folder\notes.pdf"):
            with self.subTest(filename=filename):
                response = lambda_processor.handler(upload_event(filename), None)
                self.assertEqual(response["statusCode"], 400)
        self.generate_url.assert_not_called()

    def test_handles_options_without_s3_configuration(self):
        with patch.object(lambda_processor, "S3_BUCKET", None):
            response = lambda_processor.handler(
                {"requestContext": {"http": {"method": "OPTIONS"}}}, None
            )

        self.assertEqual(response["statusCode"], 200)
        self.generate_url.assert_not_called()


if __name__ == "__main__":
    unittest.main()
