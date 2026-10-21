"""
Starter Lambda
--------------
Triggered by S3 PutObject on the OCR bucket (uploads/* prefix).

Flow:
  Images: synchronous Textract AnalyzeDocument (TABLES + LAYOUT)
  PDFs:   asynchronous Textract StartDocumentAnalysis (TABLES + LAYOUT)
  Both:   write structured ocr.json to S3 → mark completed

Retrieval Lambda never touches Textract — it only reads DynamoDB + S3.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal

import boto3

LOGGER             = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

AWS_REGION         = os.environ.get("AWS_REGION", "ap-southeast-2")
S3_BUCKET          = os.environ.get("S3_BUCKET")
DYNAMO_TABLE       = os.environ.get("DYNAMO_TABLE", "jrf-task-ocr-DocumentOCR")
ANALYSIS_FEATURES  = ["TABLES", "LAYOUT"]

# Poll interval and max wait for async Textract jobs (Lambda timeout is 900s)
POLL_INTERVAL_S = 10
POLL_MAX_S      = 850  # leave 50s buffer before Lambda timeout

s3          = boto3.client("s3", region_name=AWS_REGION)
textract    = boto3.client("textract", region_name=AWS_REGION)
dynamo      = boto3.resource("dynamodb", region_name=AWS_REGION)
table       = dynamo.Table(DYNAMO_TABLE)


def _bbox(block: dict) -> list[float]:
    box = block["Geometry"]["BoundingBox"]
    return [
        round(box["Left"], 4),
        round(box["Top"], 4),
        round(box["Left"] + box["Width"], 4),
        round(box["Top"] + box["Height"], 4),
    ]


def _child_text(block: dict, blocks_by_id: dict[str, dict]) -> str:
    text = []
    for relationship in block.get("Relationships", []):
        if relationship["Type"] != "CHILD":
            continue
        for child_id in relationship["Ids"]:
            child = blocks_by_id.get(child_id, {})
            if child.get("BlockType") == "WORD":
                text.append(child.get("Text", ""))
            elif child.get("BlockType") == "LINE":
                text.append(child.get("Text", ""))
            elif child.get("BlockType") == "SELECTION_ELEMENT":
                text.append("X" if child.get("SelectionStatus") == "SELECTED" else "")
    return " ".join(part for part in text if part)


def _blocks_to_analysis(blocks: list[dict]) -> dict:
    blocks_by_id = {block["Id"]: block for block in blocks if "Id" in block}
    elements = []
    layout = []
    tables = []
    figures = []

    for block in blocks:
        block_type = block["BlockType"]
        page = block.get("Page", 1)

        if block_type == "LINE":
            elements.append({
                "page":       page,
                "text":       block["Text"],
                "confidence": round(block["Confidence"] / 100, 4),
                "bbox":       _bbox(block),
            })
        elif block_type.startswith("LAYOUT_"):
            layout.append({
                "type":  block_type.removeprefix("LAYOUT_").lower(),
                "page":  page,
                "text":  _child_text(block, blocks_by_id),
                "bbox":  _bbox(block),
            })
            if block_type == "LAYOUT_FIGURE":
                figures.append({
                    "figure_id": f"figure-{len(figures) + 1:04d}",
                    "page": page,
                    "bbox": _bbox(block),
                    "crop_status": "pending",
                })
        elif block_type == "TABLE":
            cells = [
                blocks_by_id[cell_id]
                for relationship in block.get("Relationships", [])
                if relationship["Type"] == "CHILD"
                for cell_id in relationship["Ids"]
                if cell_id in blocks_by_id
                and blocks_by_id[cell_id].get("BlockType") == "CELL"
            ]
            row_count = max((cell["RowIndex"] for cell in cells), default=0)
            column_count = max((cell["ColumnIndex"] for cell in cells), default=0)
            rows = [["" for _ in range(column_count)] for _ in range(row_count)]
            for cell in cells:
                rows[cell["RowIndex"] - 1][cell["ColumnIndex"] - 1] = _child_text(
                    cell, blocks_by_id
                )
            tables.append({
                "page": page,
                "bbox": _bbox(block),
                "rows": rows,
            })

    page_blocks = [block for block in blocks if block["BlockType"] == "PAGE"]
    total_pages = len(page_blocks) or max(
        (block.get("Page", 1) for block in blocks),
        default=1,
    )
    return {
        "total_pages": total_pages,
        "elements": elements,
        "layout": layout,
        "tables": tables,
        "figures": figures,
    }


def _crop_figures(s3_key: str, document_id: str, filename: str,
                  figures: list[dict]) -> list[dict]:
    if not figures:
        return figures

    extension = os.path.splitext(filename.lower())[1].lstrip(".")
    supported_types = {"pdf", "png", "jpg", "jpeg", "tif", "tiff"}
    if extension not in supported_types:
        LOGGER.warning("Cannot crop figures for unsupported file type: %s", extension)
        for figure in figures:
            figure.update(crop_status="failed", crop_error="Unsupported source file type.")
        return figures

    try:
        import pymupdf

        source = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)["Body"].read()
        document = pymupdf.open(stream=source, filetype=extension)
    except Exception:
        LOGGER.exception("Could not open source document for figure cropping: %s", s3_key)
        for figure in figures:
            figure.update(crop_status="failed", crop_error="Could not open source document.")
        return figures

    try:
        for index, figure in enumerate(figures, start=1):
            try:
                page_number = int(figure["page"])
                bbox = [float(value) for value in figure["bbox"]]
                if (
                    page_number < 1
                    or page_number > document.page_count
                    or len(bbox) != 4
                    or not all(math.isfinite(value) for value in bbox)
                    or bbox[0] < 0
                    or bbox[1] < 0
                    or bbox[2] > 1
                    or bbox[3] > 1
                    or bbox[2] <= bbox[0]
                    or bbox[3] <= bbox[1]
                ):
                    raise ValueError("Invalid figure page or bounding box.")

                page = document.load_page(page_number - 1)
                page_rect = page.rect
                clip = pymupdf.Rect(
                    page_rect.x0 + bbox[0] * page_rect.width,
                    page_rect.y0 + bbox[1] * page_rect.height,
                    page_rect.x0 + bbox[2] * page_rect.width,
                    page_rect.y0 + bbox[3] * page_rect.height,
                ) & page_rect
                pixmap = page.get_pixmap(
                    matrix=pymupdf.Matrix(1.5, 1.5),
                    clip=clip,
                    alpha=False,
                )
                if pixmap.width < 1 or pixmap.height < 1:
                    raise ValueError("Figure bounding box produced an empty crop.")

                image_key = (
                    f"processed/{document_id}/figures/"
                    f"page-{page_number:04d}-figure-{index:04d}.png"
                )
                s3.put_object(
                    Bucket=S3_BUCKET,
                    Key=image_key,
                    Body=pixmap.tobytes("png"),
                    ContentType="image/png",
                )
                figure.update(
                    crop_status="saved",
                    image_s3_key=image_key,
                    image_width=pixmap.width,
                    image_height=pixmap.height,
                )
            except Exception:
                LOGGER.exception(
                    "Could not crop figure %s from %s",
                    figure.get("figure_id", index),
                    s3_key,
                )
                figure.update(
                    crop_status="failed",
                    crop_error="Could not render this figure crop.",
                )
    finally:
        document.close()

    return figures


def _save_ocr_to_s3(document_id: str, filename: str, analysis: dict) -> str:
    ocr_s3_key = f"processed/{document_id}/ocr.json"
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=ocr_s3_key,
        Body=json.dumps({
            "document_id": document_id,
            "filename":    filename,
            **analysis,
        }),
        ContentType="application/json",
    )
    LOGGER.info(
        "OCR JSON saved: s3://%s/%s elements=%d tables=%d figures=%d",
        S3_BUCKET, ocr_s3_key, len(analysis["elements"]),
        len(analysis["tables"]), len(analysis["figures"]),
    )
    return ocr_s3_key


def _write_processing(document_id, filename, s3_key, timestamp, file_size_bytes=0):
    if not S3_BUCKET:
        raise RuntimeError("S3_BUCKET environment variable is required")
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
                     ocr_s3_key, analysis, file_size_bytes=0, started_at=None):
    elements = analysis["elements"]
    item = {
        "document_id":     document_id,
        "filename":        filename,
        "s3_bucket":       S3_BUCKET,
        "s3_key":          s3_key,
        "uploaded_at":     timestamp,
        "file_size_bytes": Decimal(str(file_size_bytes)),
        "total_pages":     analysis["total_pages"],
        "element_count":   len(elements),
        "table_count":     len(analysis["tables"]),
        "figure_count":    len(analysis["figures"]),
        "status":          "completed",
        "ocr_s3_key":      ocr_s3_key,
    }
    if started_at is not None:
        item["processing_time_seconds"] = Decimal(str(round(time.time() - started_at, 1)))
    table.put_item(Item=item)
    LOGGER.info(
        "Completed: %s — %d elements, %d tables, %d figures, %d pages",
        document_id, len(elements), len(analysis["tables"]),
        len(analysis["figures"]), analysis["total_pages"],
    )


def _mark_failed(document_id):
    table.update_item(
        Key={"document_id": document_id},
        UpdateExpression="SET #st = :s",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={":s": "failed"},
    )


def _poll_async_job(job_id: str) -> dict:
    """
    Poll Textract async job until SUCCEEDED or FAILED.
    Waits up to POLL_MAX_S seconds inside the Lambda (no API Gateway in the way).
    Returns structured analysis on success, raises on failure/timeout.
    """
    deadline = time.time() + POLL_MAX_S
    attempt  = 0

    while time.time() < deadline:
        attempt += 1
        result     = textract.get_document_analysis(JobId=job_id)
        job_status = result["JobStatus"]
        LOGGER.info("Textract job %s — status=%s attempt=%d", job_id, job_status, attempt)

        if job_status == "SUCCEEDED":
            blocks = result["Blocks"]
            while "NextToken" in result:
                result = textract.get_document_analysis(
                    JobId=job_id, NextToken=result["NextToken"]
                )
                blocks.extend(result["Blocks"])
            LOGGER.info("Textract job %s — collected %d blocks", job_id, len(blocks))
            return _blocks_to_analysis(blocks)

        if job_status == "FAILED":
            raise RuntimeError(f"Textract job {job_id} FAILED")

        # IN_PROGRESS — wait and retry
        time.sleep(POLL_INTERVAL_S)

    raise TimeoutError(f"Textract job {job_id} did not complete within {POLL_MAX_S}s")


def handler(event, context):
    if not S3_BUCKET:
        raise RuntimeError("S3_BUCKET environment variable is required")

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
        extension       = os.path.splitext(filename.lower())[1]
        is_sync_image   = extension in {".png", ".jpg", ".jpeg"}

        LOGGER.info("Processing s3://%s/%s  doc=%s  size=%d",
                    S3_BUCKET, s3_key, document_id, file_size_bytes)
        _write_processing(document_id, filename, s3_key, timestamp, file_size_bytes)
        started_at = time.time()

        if is_sync_image:
            try:
                resp = textract.analyze_document(
                    Document={"S3Object": {"Bucket": S3_BUCKET, "Name": s3_key}},
                    FeatureTypes=ANALYSIS_FEATURES,
                )
                analysis   = _blocks_to_analysis(resp["Blocks"])
                _crop_figures(s3_key, document_id, filename, analysis["figures"])
                ocr_s3_key = _save_ocr_to_s3(document_id, filename, analysis)
                _write_completed(document_id, filename, s3_key, timestamp,
                                 ocr_s3_key, analysis, file_size_bytes, started_at)
            except Exception:
                LOGGER.exception("Sync Textract failed for %s", s3_key)
                _mark_failed(document_id)
            continue

        # PDFs and TIFF images use asynchronous analysis.
        try:
            job = textract.start_document_analysis(
                DocumentLocation={"S3Object": {"Bucket": S3_BUCKET, "Name": s3_key}},
                FeatureTypes=ANALYSIS_FEATURES,
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
            analysis   = _poll_async_job(job_id)
            _crop_figures(s3_key, document_id, filename, analysis["figures"])
            ocr_s3_key = _save_ocr_to_s3(document_id, filename, analysis)
            _write_completed(document_id, filename, s3_key, timestamp,
                             ocr_s3_key, analysis, file_size_bytes, started_at)

        except Exception:
            LOGGER.exception("PDF processing failed for %s", s3_key)
            _mark_failed(document_id)
