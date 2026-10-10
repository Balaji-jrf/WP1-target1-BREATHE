"""
Starter Lambda
--------------
Triggered by S3 PutObject on jrf-task-ocr-docs-bucket (uploads/* prefix).

Flow:
  1. Parse document_id and filename from S3 key
  2. Write "processing" record to DynamoDB
  3. Copy file to Textract bucket (ap-south-1) for PDFs
  4. Start Textract async job (PDFs) or sync job (images)
  5. Update DynamoDB with job_id / completed result
"""

from __future__ import annotations

import json
import logging
import os
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
            "confidence": Decimal(str(round(block["Confidence"] / 100, 4))),
            "bbox": [
                Decimal(str(round(bb["Left"], 4))),
                Decimal(str(round(bb["Top"], 4))),
                Decimal(str(round(bb["Left"] + bb["Width"], 4))),
                Decimal(str(round(bb["Top"] + bb["Height"], 4))),
            ],
        })
    return elements


def _write_processing(document_id, filename, s3_key, timestamp, job_id=None):
    item = {
        "document_id":  document_id,
        "filename":     filename,
        "s3_bucket":    S3_BUCKET,
        "s3_key":       s3_key,
        "uploaded_at":  timestamp,
        "total_pages":  0,
        "element_count": 0,
        "status":       "processing",
    }
    if job_id:
        item["textract_job_id"] = job_id
    table.put_item(Item=item)


def _write_completed(document_id, filename, s3_key, timestamp, elements):
    ocr_result = {
        "filename":    filename,
        "total_pages": max((e["page"] for e in elements), default=1),
        "elements":    elements,
    }
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
    LOGGER.info("Completed: %s — %d elements, %d pages",
                document_id, len(elements), ocr_result["total_pages"])


def handler(event, context):
    for record in event.get("Records", []):
        s3_key   = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        # Expected key format: uploads/{document_id}/{filename}
        parts    = s3_key.split("/", 2)
        if len(parts) != 3 or parts[0] != "uploads":
            LOGGER.warning("Unexpected S3 key format: %s", s3_key)
            continue

        document_id = parts[1]
        filename    = parts[2]
        timestamp   = datetime.now(timezone.utc).isoformat()
        ext         = os.path.splitext(filename.lower())[1]
        is_pdf      = ext == ".pdf"

        LOGGER.info("Processing s3://%s/%s  doc=%s", S3_BUCKET, s3_key, document_id)

        if not is_pdf:
            # Images: sync Textract using S3 object (no byte limit issue)
            _write_processing(document_id, filename, s3_key, timestamp)
            try:
                resp     = textract.detect_document_text(
                    Document={"S3Object": {"Bucket": S3_BUCKET, "Name": s3_key}}
                )
                elements = _blocks_to_elements(resp["Blocks"])
                _write_completed(document_id, filename, s3_key, timestamp, elements)
            except Exception:
                LOGGER.exception("Sync Textract failed for %s", s3_key)
                table.update_item(
                    Key={"document_id": document_id},
                    UpdateExpression="SET #st = :s",
                    ExpressionAttributeNames={"#st": "status"},
                    ExpressionAttributeValues={":s": "failed"},
                )
            continue

        # PDFs: copy to ap-south-1 Textract bucket, start async job
        _write_processing(document_id, filename, s3_key, timestamp)
        try:
            s3_textract.copy_object(
                CopySource={"Bucket": S3_BUCKET, "Key": s3_key},
                Bucket=S3_TEXTRACT_BUCKET,
                Key=s3_key,
            )
            job = textract.start_document_text_detection(
                DocumentLocation={"S3Object": {"Bucket": S3_TEXTRACT_BUCKET, "Name": s3_key}}
            )
            job_id = job["JobId"]
            LOGGER.info("Textract async job started: %s for doc %s", job_id, document_id)
            # Update record with job_id so retrieval Lambda can poll it
            table.update_item(
                Key={"document_id": document_id},
                UpdateExpression="SET textract_job_id = :j",
                ExpressionAttributeValues={":j": job_id},
            )
        except Exception:
            LOGGER.exception("Failed to start Textract job for %s", s3_key)
            table.update_item(
                Key={"document_id": document_id},
                UpdateExpression="SET #st = :s",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":s": "failed"},
            )
