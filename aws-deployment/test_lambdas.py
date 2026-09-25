"""
Tests for lambda_processor.py and lambda_retrieval.py
Run: pytest test_lambdas.py -v
"""

import base64
import io
import json
import sys
import types
import uuid
from unittest.mock import MagicMock, patch

import pytest

# ─── Stub heavy OCR deps so tests run without installing surya/pymupdf ────────
for _mod in ("pymupdf", "surya", "surya.inference", "surya.recognition",
             "surya.inference.backends", "surya.inference.backends.spawn"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

# Stub the specific classes used in backend_core
_surya_inf = sys.modules["surya.inference"]
if not hasattr(_surya_inf, "SuryaInferenceManager"):
    _surya_inf.SuryaInferenceManager = MagicMock()
_surya_rec = sys.modules["surya.recognition"]
if not hasattr(_surya_rec, "RecognitionPredictor"):
    _surya_rec.RecognitionPredictor = MagicMock()

# ─── Fixtures ────────────────────────────────────────────────────────────────

MOCK_OCR_RESULT = {
    "filename": "test_doc.png",
    "total_pages": 1,
    "elements": [
        {"page": 1, "text": "Hello World", "confidence": 0.97, "bbox": [10.0, 20.0, 200.0, 50.0]},
        {"page": 1, "text": "Sample OCR text", "confidence": 0.91, "bbox": [10.0, 60.0, 300.0, 90.0]},
    ],
}

MOCK_DOCUMENT_ID = "abc123-test-uuid"

# Minimal 1x1 white PNG bytes
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_multipart(file_bytes: bytes, filename: str, content_type: str = "image/png") -> dict:
    """Build a minimal API Gateway multipart event."""
    boundary = "----TestBoundary123"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()

    return {
        "httpMethod": "POST",
        "headers": {"content-type": f"multipart/form-data; boundary={boundary}"},
        "body": base64.b64encode(body).decode(),
        "isBase64Encoded": True,
        "pathParameters": None,
    }


def _make_get_event(document_id: str) -> dict:
    return {
        "httpMethod": "GET",
        "headers": {},
        "body": None,
        "pathParameters": {"document_id": document_id},
    }


# ─── Processor Lambda Tests ───────────────────────────────────────────────────

class TestProcessorLambda:

    @patch("lambda_processor.table")
    @patch("lambda_processor.s3")
    @patch("lambda_processor.run_ocr", return_value=MOCK_OCR_RESULT)
    @patch("lambda_processor.uuid.uuid4", return_value=MOCK_DOCUMENT_ID)
    def test_successful_image_upload(self, mock_uuid, mock_ocr, mock_s3, mock_table):
        from lambda_processor import handler

        event = _make_multipart(TINY_PNG, "test_doc.png")
        response = handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["document_id"] == MOCK_DOCUMENT_ID
        assert body["filename"] == "test_doc.png"
        assert body["total_pages"] == 1
        assert len(body["elements"]) == 2
        assert body["elements"][0]["text"] == "Hello World"
        mock_s3.put_object.assert_called_once()
        mock_table.put_item.assert_called_once()

    @patch("lambda_processor.table")
    @patch("lambda_processor.s3")
    @patch("lambda_processor.run_ocr", return_value={**MOCK_OCR_RESULT, "filename": "report.pdf", "total_pages": 3})
    @patch("lambda_processor.uuid.uuid4", return_value=MOCK_DOCUMENT_ID)
    def test_successful_pdf_upload(self, mock_uuid, mock_ocr, mock_s3, mock_table):
        from lambda_processor import handler

        pdf_bytes = b"%PDF-1.4 fake pdf content"
        event = _make_multipart(pdf_bytes, "report.pdf", "application/pdf")
        response = handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["total_pages"] == 3
        assert body["s3_key"].startswith("uploads/")
        assert body["s3_key"].endswith("report.pdf")

    def test_options_preflight_returns_200(self):
        from lambda_processor import handler

        event = {"httpMethod": "OPTIONS", "headers": {}, "body": None}
        response = handler(event, None)
        assert response["statusCode"] == 200

    def test_missing_file_field_returns_400(self):
        from lambda_processor import handler

        boundary = "----Boundary"
        body = f"--{boundary}\r\nContent-Disposition: form-data; name=\"other\"\r\n\r\nvalue\r\n--{boundary}--\r\n"
        event = {
            "httpMethod": "POST",
            "headers": {"content-type": f"multipart/form-data; boundary={boundary}"},
            "body": base64.b64encode(body.encode()).decode(),
            "isBase64Encoded": True,
        }
        response = handler(event, None)
        assert response["statusCode"] == 400

    @patch("lambda_processor.table")
    @patch("lambda_processor.s3")
    @patch("lambda_processor.run_ocr", side_effect=ValueError("Not a readable image"))
    @patch("lambda_processor.uuid.uuid4", return_value=MOCK_DOCUMENT_ID)
    def test_invalid_file_returns_415(self, mock_uuid, mock_ocr, mock_s3, mock_table):
        from lambda_processor import handler

        event = _make_multipart(b"not-an-image", "bad.xyz")
        response = handler(event, None)
        assert response["statusCode"] == 415

    @patch("lambda_processor.table")
    @patch("lambda_processor.s3")
    @patch("lambda_processor.uuid.uuid4", return_value=MOCK_DOCUMENT_ID)
    def test_s3_failure_returns_500(self, mock_uuid, mock_s3, mock_table):
        from lambda_processor import handler

        mock_s3.put_object.side_effect = Exception("S3 connection error")
        event = _make_multipart(TINY_PNG, "test.png")
        response = handler(event, None)
        assert response["statusCode"] == 500
        assert "S3" in json.loads(response["body"])["detail"]

    def test_file_exceeds_size_limit_returns_413(self):
        from lambda_processor import handler

        # Patch MAX_BYTES to 10 bytes for this test
        with patch("lambda_processor.MAX_BYTES", 10):
            event = _make_multipart(b"x" * 100, "big.png")
            response = handler(event, None)
        assert response["statusCode"] == 413

    @patch("lambda_processor.table")
    @patch("lambda_processor.s3")
    @patch("lambda_processor.run_ocr", return_value=MOCK_OCR_RESULT)
    @patch("lambda_processor.uuid.uuid4", return_value=MOCK_DOCUMENT_ID)
    def test_response_has_cors_headers(self, mock_uuid, mock_ocr, mock_s3, mock_table):
        from lambda_processor import handler

        event = _make_multipart(TINY_PNG, "test.png")
        response = handler(event, None)
        assert response["headers"]["Access-Control-Allow-Origin"] == "*"

    @patch("lambda_processor.table")
    @patch("lambda_processor.s3")
    @patch("lambda_processor.run_ocr", return_value=MOCK_OCR_RESULT)
    @patch("lambda_processor.uuid.uuid4", return_value=MOCK_DOCUMENT_ID)
    def test_dynamo_item_has_required_fields(self, mock_uuid, mock_ocr, mock_s3, mock_table):
        from lambda_processor import handler

        event = _make_multipart(TINY_PNG, "test.png")
        handler(event, None)

        call_args = mock_table.put_item.call_args[1]["Item"]
        assert call_args["document_id"] == MOCK_DOCUMENT_ID
        assert call_args["status"] == "completed"
        assert call_args["s3_bucket"] == "jrf-task-ocr-docs-bucket"
        assert "uploaded_at" in call_args
        assert "ocr_result" in call_args


# ─── Retrieval Lambda Tests ───────────────────────────────────────────────────

MOCK_DYNAMO_ITEM = {
    "document_id": MOCK_DOCUMENT_ID,
    "filename": "test_doc.png",
    "s3_bucket": "jrf-task-ocr-docs-bucket",
    "s3_key": f"uploads/{MOCK_DOCUMENT_ID}/test_doc.png",
    "uploaded_at": "2024-01-15T10:30:00+00:00",
    "total_pages": 1,
    "element_count": 2,
    "status": "completed",
    "ocr_result": MOCK_OCR_RESULT,
}


class TestRetrievalLambda:

    @patch("lambda_retrieval.table")
    def test_successful_retrieval(self, mock_table):
        from lambda_retrieval import handler

        mock_table.get_item.return_value = {"Item": MOCK_DYNAMO_ITEM}
        event = _make_get_event(MOCK_DOCUMENT_ID)
        response = handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["document_id"] == MOCK_DOCUMENT_ID
        assert body["filename"] == "test_doc.png"
        assert body["status"] == "completed"
        assert body["total_pages"] == 1

    @patch("lambda_retrieval.table")
    def test_document_not_found_returns_404(self, mock_table):
        from lambda_retrieval import handler

        mock_table.get_item.return_value = {}  # No "Item" key
        event = _make_get_event("nonexistent-id")
        response = handler(event, None)

        assert response["statusCode"] == 404
        assert "nonexistent-id" in json.loads(response["body"])["detail"]

    def test_missing_document_id_returns_400(self):
        from lambda_retrieval import handler

        event = {
            "httpMethod": "GET",
            "headers": {},
            "body": None,
            "pathParameters": {},
        }
        response = handler(event, None)
        assert response["statusCode"] == 400

    def test_null_path_parameters_returns_400(self):
        from lambda_retrieval import handler

        event = {
            "httpMethod": "GET",
            "headers": {},
            "body": None,
            "pathParameters": None,
        }
        response = handler(event, None)
        assert response["statusCode"] == 400

    def test_options_preflight_returns_200(self):
        from lambda_retrieval import handler

        event = {"httpMethod": "OPTIONS", "headers": {}, "body": None}
        response = handler(event, None)
        assert response["statusCode"] == 200

    @patch("lambda_retrieval.table")
    def test_dynamo_exception_returns_500(self, mock_table):
        from lambda_retrieval import handler

        mock_table.get_item.side_effect = Exception("DynamoDB unavailable")
        event = _make_get_event(MOCK_DOCUMENT_ID)
        response = handler(event, None)
        assert response["statusCode"] == 500

    @patch("lambda_retrieval.table")
    def test_response_has_cors_headers(self, mock_table):
        from lambda_retrieval import handler

        mock_table.get_item.return_value = {"Item": MOCK_DYNAMO_ITEM}
        event = _make_get_event(MOCK_DOCUMENT_ID)
        response = handler(event, None)
        assert response["headers"]["Access-Control-Allow-Origin"] == "*"

    @patch("lambda_retrieval.table")
    def test_ocr_elements_returned_in_result(self, mock_table):
        from lambda_retrieval import handler

        mock_table.get_item.return_value = {"Item": MOCK_DYNAMO_ITEM}
        event = _make_get_event(MOCK_DOCUMENT_ID)
        response = handler(event, None)

        body = json.loads(response["body"])
        elements = body["ocr_result"]["elements"]
        assert len(elements) == 2
        assert elements[0]["text"] == "Hello World"
        assert elements[0]["confidence"] == 0.97
