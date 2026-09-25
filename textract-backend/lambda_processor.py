"""
Processor Lambda — Amazon Textract Edition
-------------------------------------------
POST /process-ocr  (multipart/form-data, field name: file)

Images (PNG/JPEG/TIFF/WEBP):
  → Textract DetectDocumentText with Bytes (sync, instant)

PDFs:
  → Save to S3 → Textract StartDocumentTextDetection (async)
  → Poll until complete → return results
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from email import message_from_bytes

import boto3

LOGGER           = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

S3_BUCKET        = os.environ.get("S3_BUCKET", "jrf-task-ocr-docs-bucket")       # ap-south-2
S3_TEXTRACT_BUCKET = os.environ.get("S3_TEXTRACT_BUCKET", "jrf-task-ocr-textract-bucket")  # ap-south-1
DYNAMO_TABLE     = os.environ.get("DYNAMO_TABLE", "DocumentOCR")
TEXTRACT_REGION  = os.environ.get("TEXTRACT_REGION", "ap-south-1")
MAX_BYTES        = int(os.environ.get("MAX_UPLOAD_MB", "10")) * 1024 * 1024

s3          = boto3.client("s3", region_name="ap-south-2")
s3_textract = boto3.client("s3", region_name="ap-south-1")
textract    = boto3.client("textract", region_name=TEXTRACT_REGION)
dynamo      = boto3.resource("dynamodb", region_name="ap-south-2")
table       = dynamo.Table(DYNAMO_TABLE)


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
        "body": json.dumps(body, default=str),
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


def _blocks_to_elements(blocks: list) -> list[dict]:
    elements = []
    for block in blocks:
        if block["BlockType"] != "LINE":
            continue
        bb = block["Geometry"]["BoundingBox"]
        elements.append({
            "page":       block.get("Page", 1),
            "text":       block["Text"],
            "confidence": Decimal(str(round(block["Confidence"] / 100, 4))),
            "bbox": [
                Decimal(str(round(bb["Left"], 4))),
                Decimal(str(round(bb["Top"], 4))),
                Decimal(str(round(bb["Left"] + bb["Width"], 4))),
                Decimal(str(round(bb["Top"] + bb["Height"], 4))),
            ],
        })
    return elements


def _textract_image(file_bytes: bytes) -> list[dict]:
    """Sync Textract for images — fast, no S3 needed."""
    response = textract.detect_document_text(Document={"Bytes": file_bytes})
    return _blocks_to_elements(response["Blocks"])


def _textract_pdf(s3_key: str) -> list[dict]:
    """Async Textract for PDFs — must use S3 bucket in same region as Textract (ap-south-1)."""
    # Copy file to ap-south-1 bucket for Textract
    copy_source = {"Bucket": S3_BUCKET, "Key": s3_key}
    s3_textract.copy_object(
        CopySource=copy_source,
        Bucket=S3_TEXTRACT_BUCKET,
        Key=s3_key
    )
    job = textract.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": S3_TEXTRACT_BUCKET, "Name": s3_key}}
    )
    job_id = job["JobId"]
    LOGGER.info("Textract job started: %s", job_id)

    # Poll until complete (max 55s — Lambda timeout is 60s)
    for _ in range(55):
        time.sleep(1)
        result = textract.get_document_text_detection(JobId=job_id)
        status = result["JobStatus"]
        if status == "SUCCEEDED":
            blocks = result["Blocks"]
            # Paginate if needed
            while "NextToken" in result:
                result = textract.get_document_text_detection(
                    JobId=job_id, NextToken=result["NextToken"]
                )
                blocks.extend(result["Blocks"])
            LOGGER.info("Textract job succeeded: %s blocks", len(blocks))
            return _blocks_to_elements(blocks)
        if status == "FAILED":
            raise RuntimeError(f"Textract job failed: {result.get('StatusMessage')}")

    raise TimeoutError("Textract job timed out after 55 seconds")


def handler(event: dict, context) -> dict:
    if event.get("httpMethod") == "OPTIONS":
        return _response(200, {})

    try:
        file_bytes, filename = _parse_multipart(event)
    except (KeyError, TypeError):
        return _response(400, {"detail": "Invalid multipart/form-data — 'file' field required"})

    if not filename:
        return _response(400, {"detail": "A filename is required"})

    if len(file_bytes) > MAX_BYTES:
        return _response(413, {"detail": f"File exceeds {MAX_BYTES // (1024*1024)} MB limit"})

    is_pdf      = filename.lower().endswith(".pdf") or file_bytes.startswith(b"%PDF-")
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
        if is_pdf:
            elements = _textract_pdf(s3_key)
        else:
            elements = _textract_image(file_bytes)
    except TimeoutError:
        return _response(504, {"detail": "OCR timed out — try a smaller PDF"})
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
