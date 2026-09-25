"""
Processor Lambda — Amazon Textract Edition
-------------------------------------------
POST /process-ocr  (multipart/form-data, field name: file)
1. Parse uploaded file from API Gateway event
2. Save raw file to S3
3. Call Amazon Textract DetectDocumentText
4. Write result + metadata to DynamoDB
5. Return OCR JSON to caller
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

import boto3

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

REGION         = os.environ.get("AWS_REGION", "ap-south-1")
S3_BUCKET      = os.environ.get("S3_BUCKET", "jrf-task-ocr-docs-bucket")
DYNAMO_TABLE   = os.environ.get("DYNAMO_TABLE", "DocumentOCR")
MAX_BYTES      = int(os.environ.get("MAX_UPLOAD_MB", "50")) * 1024 * 1024

s3       = boto3.client("s3")
textract = boto3.client("textract", region_name="ap-south-1")
dynamo   = boto3.resource("dynamodb")
table    = dynamo.Table(DYNAMO_TABLE)


def _cors_headers() -> dict:
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
    headers      = event.get("headers") or {}
    content_type = headers.get("content-type") or headers.get("Content-Type", "")
    body_raw     = event.get("body") or ""
    is_b64       = event.get("isBase64Encoded", False)
    body_bytes   = base64.b64decode(body_raw) if is_b64 else body_raw.encode()

    raw = f"Content-Type: {content_type}\r\n\r\n".encode() + body_bytes
    msg = message_from_bytes(raw)
    for part in msg.walk():
        disposition = part.get("Content-Disposition", "")
        if 'name="file"' in disposition or "name=file" in disposition:
            return part.get_payload(decode=True), (part.get_filename() or "upload")
    raise KeyError("file")


def _run_textract(file_bytes: bytes) -> list[dict]:
    response = textract.detect_document_text(Document={"Bytes": file_bytes})
    elements = []
    for block in response["Blocks"]:
        if block["BlockType"] != "LINE":
            continue
        bb = block["Geometry"]["BoundingBox"]
        elements.append({
            "page":       block.get("Page", 1),
            "text":       block["Text"],
            "confidence": round(block["Confidence"] / 100, 4),
            "bbox":       [
                round(bb["Left"], 4),
                round(bb["Top"], 4),
                round(bb["Left"] + bb["Width"], 4),
                round(bb["Top"]  + bb["Height"], 4),
            ],
        })
    return elements


def handler(event: dict, context) -> dict:
    if event.get("httpMethod") == "OPTIONS":
        return _response(200, {})

    try:
        file_bytes, filename = _parse_multipart(event)
    except (KeyError, TypeError) as exc:
        return _response(400, {"detail": "Invalid multipart/form-data — 'file' field required"})

    if not filename:
        return _response(400, {"detail": "A filename is required"})

    if len(file_bytes) > MAX_BYTES:
        return _response(413, {"detail": f"File exceeds {MAX_BYTES // (1024*1024)} MB limit"})

    document_id = str(uuid.uuid4())
    s3_key      = f"uploads/{document_id}/{filename}"
    timestamp   = datetime.now(timezone.utc).isoformat()

    # 1. Save to S3
    try:
        s3.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=file_bytes)
        LOGGER.info("Saved to S3: %s", s3_key)
    except Exception:
        LOGGER.exception("S3 upload failed")
        return _response(500, {"detail": "Failed to store document in S3"})

    # 2. Run Textract
    try:
        elements = _run_textract(file_bytes)
    except textract.exceptions.UnsupportedDocumentException:
        return _response(415, {"detail": "Unsupported file format. Use PDF, PNG, JPEG, or TIFF."})
    except textract.exceptions.DocumentTooLargeException:
        return _response(413, {"detail": "Document too large for Textract (max 10MB for sync API)"})
    except Exception:
        LOGGER.exception("Textract failed for %s", filename)
        return _response(500, {"detail": "OCR processing failed"})

    ocr_result = {
        "filename":    filename,
        "total_pages": max((e["page"] for e in elements), default=1),
        "elements":    elements,
    }

    # 3. Write to DynamoDB
    try:
        table.put_item(Item={
            "document_id":   document_id,
            "filename":      filename,
            "s3_bucket":     S3_BUCKET,
            "s3_key":        s3_key,
            "uploaded_at":   timestamp,
            "total_pages":   ocr_result["total_pages"],
            "element_count": len(elements),
            "status":        "completed",
            "ocr_result":    ocr_result,
        })
        LOGGER.info("DynamoDB record written: %s", document_id)
    except Exception:
        LOGGER.exception("DynamoDB write failed")
        return _response(500, {"detail": "Failed to persist OCR result"})

    return _response(200, {**ocr_result, "document_id": document_id, "s3_key": s3_key})
