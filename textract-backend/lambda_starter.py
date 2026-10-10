"""
Starter Lambda
--------------
Triggered by S3 PutObject on jrf-task-ocr-docs-bucket (uploads/* prefix).

Flow:
  1. Parse document_id and filename from S3 key
  2. Write "processing" record to DynamoDB
  3. Copy file to Textract bucket (ap-south-1) for PDFs
  4. Run Textract (sync for images, async for PDFs)
  5. Write OCR JSON to S3: processed/{document_id}/ocr.json
  6. Update DynamoDB with ocr_s3_key + metadata (no ocr_result blob)
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal

import boto3

LOGGER             = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

S3_BUCKET          = os.environ.get("S3_BUCKET", "jrf-task-ocr-docs-bucket")
S3_TEXTRACT_BUCKET = os.environ.get("S3_TEXTRACT_BUCKET", "jrf-task-ocr-textract-bucket")
DYNAMO_TABLE       = os.environ.get("DYNAMO_TABLE", "DocumentOCR")
TEXTRACT_REGION    = os.environ.get("TEXTRACT_REGION", "ap-south-1")

s3          = boto3.client("s3", region_name="ap-south-2")
s3_textract = boto3.client("s3", region_name="ap-south-1")
textract    = boto3.client("textract", region_name=TEXTRACT_REGION)
dynamo      = boto3.resource("dynamodb", region_name="ap-south-2")
table       = dynamo.Table(DYNAMO_TABLE)

CONTENT_TYPES = {
    ".pdf":  "application/pdf",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif":  "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}


def _blocks_to_elements(blocks: list) -> list[dict]:
    elements = []
    for block in blocks:
        if block["BlockType"] != "LINE":
            continue
        bb = block["Geometry"]["BoundingBox"]
        elements.append({
            "page":       block.get("Page", 1),
            "text":       block["Text"],
            "confidence": round(block["Confidence"] / 100, 4),
            "bbox": [
                round(bb["Left"], 4),
                round(bb["Top"], 4),
                round(bb["Left"] + bb["Width"], 4),
                round(bb["Top"] + bb["Height"], 4),
            ],
        })
    return elements


def _save_ocr_to_s3(document_id: str, filename: str, elements: list) -> str:
    """Write OCR JSON to processed/{document_id}/ocr.json, return the S3 key."""
    ocr_s3_key = f"processed/{document_id}/ocr.json"
    payload = {
        "document_id": document_id,
        "filename":    filename,
        "total_pages": max((e["page"] for e in elements), default=1),
        "elements":    elements,
    }
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=ocr_s3_key,
        Body=json.dumps(payload),
        ContentType="application/json",
    )
    LOGGER.info("OCR JSON saved: s3://%s/%s  elements=%d", S3_BUCKET, ocr_s3_key, len(elements))
    return ocr_s3_key


def _write_processing(document_id, filename, s3_key, timestamp, file_size_bytes=0):
    table.put_item(Item={
        "document_id":     document_id,
        "filename":        filename,
        "s3_bucket":       S3_BUCKET,
        "s3_key":          s3_key,
        "uploaded_at":     timestamp,
        "file_size_bytes": Decimal(str(file_size_bytes)),
        "total_pages":     0,
        "element_count":   0,
        "status":          "processing",
    })


def _write_completed(document_id, filename, s3_key, timestamp,
                     ocr_s3_key, elements, file_size_bytes=0, started_at=None):
    total_pages = max((e["page"] for e in elements), default=1)
    item = {
        "document_id":            document_id,
        "filename":               filename,
        "s3_bucket":              S3_BUCKET,
        "s3_key":                 s3_key,
        "uploaded_at":            timestamp,
        "file_size_bytes":        Decimal(str(file_size_bytes)),
        "total_pages":            total_pages,
        "element_count":          len(elements),
        "status":                 "completed",
        "ocr_s3_key":             ocr_s3_key,
    }
    if started_at is not None:
        item["processing_time_seconds"] = Decimal(str(round(time.time() - started_at, 1)))
    table.put_item(Item=item)
    LOGGER.info("Completed: %s — %d elements, %d pages", document_id, len(elements), total_pages)


def _mark_failed(document_id):
    table.update_item(
        Key={"document_id": document_id},
        UpdateExpression="SET #st = :s",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={":s": "failed"},
    )


def handler(event, context):
    for record in event.get("Records", []):
        s3_key          = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        parts           = s3_key.split("/", 2)
        if len(parts) != 3 or parts[0] != "uploads":
            LOGGER.warning("Unexpected S3 key format: %s", s3_key)
            continue

        document_id     = parts[1]
        filename        = parts[2]
        timestamp       = datetime.now(timezone.utc).isoformat()
        file_size_bytes = record["s3"]["object"].get("size", 0)
        is_pdf          = os.path.splitext(filename.lower())[1] == ".pdf"

        LOGGER.info("Processing s3://%s/%s  doc=%s", S3_BUCKET, s3_key, document_id)
        _write_processing(document_id, filename, s3_key, timestamp, file_size_bytes)

        if not is_pdf:
            started_at = time.time()
            try:
                resp     = textract.detect_document_text(
                    Document={"S3Object": {"Bucket": S3_BUCKET, "Name": s3_key}}
                )
                elements    = _blocks_to_elements(resp["Blocks"])
                ocr_s3_key  = _save_ocr_to_s3(document_id, filename, elements)
                _write_completed(document_id, filename, s3_key, timestamp,
                                 ocr_s3_key, elements, file_size_bytes, started_at)
            except Exception:
                LOGGER.exception("Sync Textract failed for %s", s3_key)
                _mark_failed(document_id)
            continue

        # PDFs — copy to ap-south-1, start async job
        try:
            s3_textract.copy_object(
                CopySource={"Bucket": S3_BUCKET, "Key": s3_key},
                Bucket=S3_TEXTRACT_BUCKET,
                Key=s3_key,
            )
            job    = textract.start_document_text_detection(
                DocumentLocation={"S3Object": {"Bucket": S3_TEXTRACT_BUCKET, "Name": s3_key}}
            )
            job_id = job["JobId"]
            LOGGER.info("Textract async job started: %s for doc %s", job_id, document_id)
            table.update_item(
                Key={"document_id": document_id},
                UpdateExpression="SET textract_job_id = :j",
                ExpressionAttributeValues={":j": job_id},
            )
        except Exception:
            LOGGER.exception("Failed to start Textract job for %s", s3_key)
            _mark_failed(document_id)
