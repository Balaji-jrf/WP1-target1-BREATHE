"""
Processor Lambda — Amazon Textract Edition
-------------------------------------------
POST /process-ocr  (multipart/form-data, field name: file)

Images (PNG/JPEG/TIFF/WEBP):
  → Textract DetectDocumentText sync → write completed record → return result

PDFs:
  → Save to S3 → Start async Textract job → write "processing" record → return immediately
  → A second Lambda (breathe-textract-finisher) is triggered by S3 event or
    the frontend polls GET /document/{id} until status == "completed"
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

CONTENT_TYPES = {
    ".pdf":  "application/pdf",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif":  "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}

LOGGER             = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

S3_BUCKET          = os.environ.get("S3_BUCKET", "jrf-task-ocr-docs-bucket")
S3_TEXTRACT_BUCKET = os.environ.get("S3_TEXTRACT_BUCKET", "jrf-task-ocr-textract-bucket")
DYNAMO_TABLE       = os.environ.get("DYNAMO_TABLE", "DocumentOCR")
TEXTRACT_REGION    = os.environ.get("TEXTRACT_REGION", "ap-south-1")
MAX_BYTES          = int(os.environ.get("MAX_UPLOAD_MB", "10")) * 1024 * 1024

s3          = boto3.client("s3", region_name="ap-south-2")
s3_textract = boto3.client("s3", region_name="ap-south-1")
textract    = boto3.client("textract", region_name=TEXTRACT_REGION)
dynamo      = boto3.resource("dynamodb", region_name="ap-south-2")
table       = dynamo.Table(DYNAMO_TABLE)


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
        "Access-Control-Allow-Methods": "POST,OPTIONS",
        "Access-Control-Allow-Headers": "content-type",
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
    response = textract.detect_document_text(Document={"Bytes": file_bytes})
    return _blocks_to_elements(response["Blocks"])


def _start_textract_pdf(s3_key: str) -> str:
    """Copy to ap-south-1 bucket and start async job. Returns job_id immediately."""
    s3_textract.copy_object(
        CopySource={"Bucket": S3_BUCKET, "Key": s3_key},
        Bucket=S3_TEXTRACT_BUCKET,
        Key=s3_key,
    )
    job = textract.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": S3_TEXTRACT_BUCKET, "Name": s3_key}}
    )
    return job["JobId"]


def _poll_textract_pdf(job_id: str, max_seconds: int = 20) -> list[dict] | None:
    """Poll for up to max_seconds. Returns elements if done, None if still running."""
    for _ in range(max_seconds):
        time.sleep(1)
        result = textract.get_document_text_detection(JobId=job_id)
        status = result["JobStatus"]
        if status == "SUCCEEDED":
            blocks = result["Blocks"]
            while "NextToken" in result:
                result = textract.get_document_text_detection(JobId=job_id, NextToken=result["NextToken"])
                blocks.extend(result["Blocks"])
            return _blocks_to_elements(blocks)
        if status == "FAILED":
            raise RuntimeError(f"Textract job failed: {result.get('StatusMessage')}")
    return None  # still in progress


def handler(event: dict, context) -> dict:
    method = (event.get("requestContext") or {}).get("http", {}).get("method", "").upper()
    if method == "OPTIONS":
        return _response(200, {})

    try:
        file_bytes, filename = _parse_multipart(event)
    except (KeyError, TypeError):
        return _response(400, {"detail": "Invalid multipart/form-data — 'file' field required"})

    if not file_bytes:
        return _response(400, {"detail": "Uploaded file is empty"})
    if not filename:
        return _response(400, {"detail": "A filename is required"})
    if len(file_bytes) > MAX_BYTES:
        return _response(413, {"detail": f"File exceeds {MAX_BYTES // (1024*1024)} MB limit"})

    is_pdf       = filename.lower().endswith(".pdf") or file_bytes.startswith(b"%PDF-")
    document_id  = str(uuid.uuid4())
    s3_key       = f"uploads/{document_id}/{filename}"
    timestamp    = datetime.now(timezone.utc).isoformat()
    ext          = os.path.splitext(filename.lower())[1]
    content_type = CONTENT_TYPES.get(ext, "application/octet-stream")

    # 1. Save to S3
    try:
        s3.put_object(
            Bucket=S3_BUCKET, Key=s3_key, Body=file_bytes,
            ContentType=content_type, ContentDisposition="inline",
        )
        LOGGER.info("Saved to S3: %s", s3_key)
    except Exception:
        LOGGER.exception("S3 upload failed")
        return _response(500, {"detail": "Failed to store document in S3"})

    # 2. Run Textract
    if not is_pdf:
        # Images: sync, fast
        try:
            elements = _textract_image(file_bytes)
        except Exception:
            LOGGER.exception("Textract image failed for %s", filename)
            return _response(500, {"detail": "OCR processing failed"})

        ocr_result = {
            "filename":    filename,
            "total_pages": max((e["page"] for e in elements), default=1),
            "elements":    elements,
        }
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
        except Exception:
            LOGGER.exception("DynamoDB write failed")
            return _response(500, {"detail": "Failed to persist OCR result"})

        presigned_url = _make_presigned(s3_key, content_type)
        return _response(200, {
            **ocr_result,
            "document_id":      document_id,
            "s3_key":           s3_key,
            "uploaded_at":      timestamp,
            "status":           "completed",
            "s3_presigned_url": presigned_url,
        })

    # PDFs: start async job, write "processing" record, poll up to 20s
    try:
        job_id = _start_textract_pdf(s3_key)
        LOGGER.info("Textract job started: %s", job_id)
    except Exception:
        LOGGER.exception("Textract start failed for %s", filename)
        return _response(500, {"detail": "Failed to start OCR job"})

    # Write processing record immediately so frontend can poll
    try:
        table.put_item(Item={
            "document_id":    document_id,
            "filename":       filename,
            "s3_bucket":      S3_BUCKET,
            "s3_key":         s3_key,
            "uploaded_at":    timestamp,
            "total_pages":    0,
            "element_count":  0,
            "status":         "processing",
            "textract_job_id": job_id,
        })
    except Exception:
        LOGGER.exception("DynamoDB processing record write failed")

    # Poll up to 20s — small PDFs finish in ~3-5s
    try:
        elements = _poll_textract_pdf(job_id, max_seconds=20)
    except RuntimeError as e:
        table.update_item(
            Key={"document_id": document_id},
            UpdateExpression="SET #st = :s",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":s": "failed"},
        )
        return _response(500, {"detail": str(e)})

    if elements is None:
        # Still processing — return 202 so frontend polls
        LOGGER.info("PDF still processing after 20s, returning 202: %s", document_id)
        return _response(202, {
            "document_id": document_id,
            "filename":    filename,
            "status":      "processing",
            "message":     "OCR in progress — poll GET /document/{id} for result",
        })

    # Completed within 20s
    ocr_result = {
        "filename":    filename,
        "total_pages": max((e["page"] for e in elements), default=1),
        "elements":    elements,
    }
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

    presigned_url = _make_presigned(s3_key, content_type)
    return _response(200, {
        **ocr_result,
        "document_id":      document_id,
        "s3_key":           s3_key,
        "uploaded_at":      timestamp,
        "status":           "completed",
        "s3_presigned_url": presigned_url,
    })


def _make_presigned(s3_key: str, content_type: str) -> str | None:
    try:
        s3_presign = boto3.client(
            "s3",
            region_name="ap-south-2",
            endpoint_url="https://s3.ap-south-2.amazonaws.com",
        )
        return s3_presign.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": S3_BUCKET,
                "Key": s3_key,
                "ResponseContentType": content_type,
                "ResponseContentDisposition": "inline",
            },
            ExpiresIn=3600,
        )
    except Exception:
        LOGGER.warning("Could not generate presigned URL for %s", s3_key)
        return None
