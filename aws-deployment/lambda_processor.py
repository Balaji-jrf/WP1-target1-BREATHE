"""
Processor Lambda
----------------
Triggered by API Gateway POST /process-ocr (multipart/form-data).
1. Parses the uploaded file from the multipart body.
2. Saves the raw file to S3 (jrf-task-ocr-docs-bucket).
3. Runs Surya OCR via backend_core.
4. Writes the result + metadata to DynamoDB (DocumentOCR table).
5. Returns the OCR JSON to the caller.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from email import message_from_bytes
from email.policy import HTTP as HTTP_POLICY

import boto3

from backend_core import run_ocr

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

S3_BUCKET = os.environ.get("S3_BUCKET", "jrf-task-ocr-docs-bucket")
DYNAMO_TABLE = os.environ.get("DYNAMO_TABLE", "DocumentOCR")
MAX_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "50")) * 1024 * 1024

s3 = boto3.client("s3")
dynamo = boto3.resource("dynamodb")
table = dynamo.Table(DYNAMO_TABLE)


def _cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {**_cors_headers(), "Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _parse_multipart(event: dict) -> tuple[bytes, str]:
    """Extract file bytes and filename from a multipart/form-data API Gateway event."""
    headers = event.get("headers") or {}
    content_type = headers.get("content-type") or headers.get("Content-Type", "")
    body_raw = event.get("body") or ""
    is_b64 = event.get("isBase64Encoded", False)
    body_bytes = base64.b64decode(body_raw) if is_b64 else body_raw.encode()

    # Use email parser (cgi module removed in Python 3.13)
    raw = f"Content-Type: {content_type}\r\n\r\n".encode() + body_bytes
    msg = message_from_bytes(raw)
    for part in msg.walk():
        disposition = part.get("Content-Disposition", "")
        if 'name="file"' in disposition or "name=file" in disposition:
            filename = part.get_filename() or ""
            return part.get_payload(decode=True), filename
    raise KeyError("file")


def handler(event: dict, context) -> dict:
    # Handle CORS preflight
    if event.get("httpMethod") == "OPTIONS":
        return _response(200, {})

    try:
        file_bytes, filename = _parse_multipart(event)
    except (KeyError, TypeError) as exc:
        LOGGER.warning("Multipart parse error: %s", exc)
        return _response(400, {"detail": "Invalid multipart/form-data — 'file' field required"})

    if not filename:
        return _response(400, {"detail": "A filename is required"})

    if len(file_bytes) > MAX_BYTES:
        return _response(413, {"detail": f"File exceeds {MAX_BYTES // (1024*1024)} MB limit"})

    document_id = str(uuid.uuid4())
    s3_key = f"uploads/{document_id}/{filename}"
    timestamp = datetime.now(timezone.utc).isoformat()

    # 1. Save raw file to S3
    try:
        s3.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=file_bytes)
        LOGGER.info("Saved to S3: %s", s3_key)
    except Exception as exc:
        LOGGER.exception("S3 upload failed")
        return _response(500, {"detail": "Failed to store document in S3"})

    # 2. Run OCR
    try:
        ocr_result = run_ocr(file_bytes, filename)
    except ValueError as exc:
        return _response(415, {"detail": str(exc)})
    except Exception as exc:
        LOGGER.exception("OCR failed for %s", filename)
        return _response(500, {"detail": "OCR processing failed"})

    # 3. Write to DynamoDB
    item = {
        "document_id": document_id,
        "filename": filename,
        "s3_bucket": S3_BUCKET,
        "s3_key": s3_key,
        "uploaded_at": timestamp,
        "total_pages": ocr_result["total_pages"],
        "element_count": len(ocr_result["elements"]),
        "status": "completed",
        "ocr_result": ocr_result,
    }
    try:
        table.put_item(Item=item)
        LOGGER.info("DynamoDB record written: %s", document_id)
    except Exception as exc:
        LOGGER.exception("DynamoDB write failed")
        return _response(500, {"detail": "Failed to persist OCR result"})

    return _response(200, {**ocr_result, "document_id": document_id, "s3_key": s3_key})
