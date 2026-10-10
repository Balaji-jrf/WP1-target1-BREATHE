"""
Starter Lambda
--------------
Triggered by S3 PutObject on jrf-task-ocr-docs-bucket (uploads/* prefix).

Flow:
  Images: sync Textract → write ocr.json to S3 → mark completed
  PDFs:   copy to Textract bucket → start async job → poll until done
          → write ocr.json to S3 → mark completed

Retrieval Lambda never touches Textract — it only reads DynamoDB + S3.
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

# Poll interval and max wait for async Textract jobs (Lambda timeout is 900s)
POLL_INTERVAL_S = 10
POLL_MAX_S      = 850  # leave 50s buffer before Lambda timeout

s3          = boto3.client("s3", region_name="ap-south-2")
s3_textract = boto3.client("s3", region_name="ap-south-1")
textract    = boto3.client("textract", region_name=TEXTRACT_REGION)
dynamo      = boto3.resource("dynamodb", region_name="ap-south-2")
table       = dynamo.Table(DYNAMO_TABLE)


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
    ocr_s3_key = f"processed/{document_id}/ocr.json"
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=ocr_s3_key,
        Body=json.dumps({
            "document_id": document_id,
            "filename":    filename,
            "total_pages": max((e["page"] for e in elements), default=1),
            "elements":    elements,
        }),
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
        "document_id":     document_id,
        "filename":        filename,
        "s3_bucket":       S3_BUCKET,
        "s3_key":          s3_key,
        "uploaded_at":     timestamp,
        "file_size_bytes": Decimal(str(file_size_bytes)),
        "total_pages":     total_pages,
        "element_count":   len(elements),
        "status":          "completed",
        "ocr_s3_key":      ocr_s3_key,
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


def _poll_async_job(job_id: str) -> list[dict]:
    """
    Poll Textract async job until SUCCEEDED or FAILED.
    Waits up to POLL_MAX_S seconds inside the Lambda (no API Gateway in the way).
    Returns elements list on success, raises on failure/timeout.
    """
    deadline = time.time() + POLL_MAX_S
    attempt  = 0

    while time.time() < deadline:
        attempt += 1
        result     = textract.get_document_text_detection(JobId=job_id)
        job_status = result["JobStatus"]
        LOGGER.info("Textract job %s — status=%s attempt=%d", job_id, job_status, attempt)

        if job_status == "SUCCEEDED":
            blocks = result["Blocks"]
            while "NextToken" in result:
                result = textract.get_document_text_detection(
                    JobId=job_id, NextToken=result["NextToken"]
                )
                blocks.extend(result["Blocks"])
            LOGGER.info("Textract job %s — collected %d blocks", job_id, len(blocks))
            return _blocks_to_elements(blocks)

        if job_status == "FAILED":
            raise RuntimeError(f"Textract job {job_id} FAILED")

        # IN_PROGRESS — wait and retry
        time.sleep(POLL_INTERVAL_S)

    raise TimeoutError(f"Textract job {job_id} did not complete within {POLL_MAX_S}s")


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

        LOGGER.info("Processing s3://%s/%s  doc=%s  size=%d",
                    S3_BUCKET, s3_key, document_id, file_size_bytes)
        _write_processing(document_id, filename, s3_key, timestamp, file_size_bytes)
        started_at = time.time()

        if not is_pdf:
            try:
                resp       = textract.detect_document_text(
                    Document={"S3Object": {"Bucket": S3_BUCKET, "Name": s3_key}}
                )
                elements   = _blocks_to_elements(resp["Blocks"])
                ocr_s3_key = _save_ocr_to_s3(document_id, filename, elements)
                _write_completed(document_id, filename, s3_key, timestamp,
                                 ocr_s3_key, elements, file_size_bytes, started_at)
            except Exception:
                LOGGER.exception("Sync Textract failed for %s", s3_key)
                _mark_failed(document_id)
            continue

        # PDFs — copy to ap-south-1, start async job, poll to completion here
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

            # Store job_id so the frontend can see it during polling
            table.update_item(
                Key={"document_id": document_id},
                UpdateExpression="SET textract_job_id = :j",
                ExpressionAttributeValues={":j": job_id},
            )

            # Poll to completion inside this Lambda (900s timeout, no API Gateway)
            elements   = _poll_async_job(job_id)
            ocr_s3_key = _save_ocr_to_s3(document_id, filename, elements)
            _write_completed(document_id, filename, s3_key, timestamp,
                             ocr_s3_key, elements, file_size_bytes, started_at)

        except Exception:
            LOGGER.exception("PDF processing failed for %s", s3_key)
            _mark_failed(document_id)
