"""
lambda_function.py  —  jrf-task-dynamo-ingestion
=================================================
Lambda handler: lambda_function.lambda_handler

This is the AI-powered ingestion Lambda for the BREATHE NLP pipeline.
It is the ONLY Lambda that calls Amazon Bedrock (Nova Lite).

Two execution modes
-------------------
1. HTTP mode  (triggered by API Gateway POST /api/documents/ingest-dynamo)
   - Validates the document exists and OCR is complete
   - Marks ingestion_status = "processing" in DynamoDB
   - Invokes itself asynchronously (InvocationType="Event") so the HTTP
     response returns immediately (202 Accepted) without waiting for Nova Lite
   - Frontend polls /document/{id} to watch ingestion_status change

2. Async worker mode  (self-invoked with {"async_worker": True, "document_id": "..."})
   - Loads the OCR JSON from S3
   - Loads the active entity taxonomy from DynamoDB
   - Splits OCR text into page chunks and sends each to Nova Lite
   - Writes every extracted fact / table / figure as a record in
     jrf-task-ocr-ExtractedRecords with review_status = "pending_review"
   - Updates ingestion_status in jrf-task-ocr-DocumentOCR to
     "pending_review" (records exist) or "completed" (nothing extracted)

DynamoDB tables used
--------------------
  jrf-task-ocr-DocumentOCR      — document metadata + ingestion status
  jrf-task-ocr-ExtractedRecords — one item per extracted fact/table/figure
  jrf-task-data-EntityTaxonomy  — active entity taxonomy (read-only here)

Environment variables
---------------------
  DYNAMO_TABLE            — DocumentOCR table name
  EXTRACTED_RECORDS_TABLE — ExtractedRecords table name
  TAXONOMY_TABLE          — EntityTaxonomy table name
  BEDROCK_MODEL_ID        — Bedrock model (default: amazon.nova-lite-v1:0)
  MAX_CHUNK_CHARS         — max OCR chars per Nova Lite call (default: 1500)
  ALLOWED_ORIGIN          — CORS origin for HTTP responses
  AWS_LAMBDA_FUNCTION_NAME — injected automatically by Lambda runtime;
                             used to self-invoke in async mode
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.config import Config

# ai_extractor.py lives alongside this file in the same Lambda zip.
# It handles: page chunking, Nova Lite prompt building, response parsing,
# entity validation.
from ai_extractor import extract_chunk, page_chunks

LOGGER = logging.getLogger(__name__)

# ── DynamoDB table names (overridable via env vars) ──────────────────────────
OCR_TABLE_NAME              = "jrf-task-ocr-DocumentOCR"
EXTRACTED_RECORDS_TABLE_NAME = "jrf-task-ocr-ExtractedRecords"
TAXONOMY_TABLE_NAME         = "jrf-task-data-EntityTaxonomy"

# ── Runtime config ────────────────────────────────────────────────────────────
AWS_REGION     = os.environ.get("AWS_REGION", "ap-southeast-2")
MODEL_ID       = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
MAX_CHUNK_CHARS = int(os.environ.get("MAX_CHUNK_CHARS", "1500"))

# ── AWS clients (initialised once at cold-start) ──────────────────────────────
OCR_TABLE = boto3.resource("dynamodb", region_name=AWS_REGION).Table(
    os.environ.get("DYNAMO_TABLE", OCR_TABLE_NAME)
)
EXTRACTED_RECORDS_TABLE = boto3.resource("dynamodb", region_name=AWS_REGION).Table(
    os.environ.get("EXTRACTED_RECORDS_TABLE", EXTRACTED_RECORDS_TABLE_NAME)
)
TAXONOMY_TABLE = boto3.resource("dynamodb", region_name=AWS_REGION).Table(
    os.environ.get("TAXONOMY_TABLE", TAXONOMY_TABLE_NAME)
)
s3 = boto3.client("s3", region_name=AWS_REGION)

# Bedrock client with adaptive retry — Nova Lite can throttle on large docs
bedrock = boto3.client(
    "bedrock-runtime",
    region_name=AWS_REGION,
    config=Config(
        retries={"mode": "adaptive", "total_max_attempts": 5},
        connect_timeout=5,
        read_timeout=60,
    ),
)

# Lambda client used only to self-invoke in async worker mode
lambda_client = boto3.client("lambda", region_name=AWS_REGION)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ddb_safe(value):
    """Recursively convert floats to Decimal so DynamoDB accepts them."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [_ddb_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _ddb_safe(item) for key, item in value.items()}
    return value


def _bbox(bbox):
    """
    Convert Textract normalised bbox [left, top, right, bottom]
    to [x, y, width, height] for storage.
    Returns None if the input is invalid.
    """
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    left, top, right, bottom = [float(v) for v in bbox]
    return [left, top, max(0.0, right - left), max(0.0, bottom - top)]


def _put_entity(document_id, theme, entity_id, attributes):
    """
    Write one extracted entity record to jrf-task-ocr-ExtractedRecords.

    Primary key:
      id              = "DOC#{document_id}"   (partition key)
      theme_entity_id = "{THEME}#{entity_id}" (sort key)

    The top-level 'district' field is denormalised from spatial_units so
    the GSI on 'district' can be queried directly.
    """
    item = {
        "id": f"DOC#{document_id}",
        "theme_entity_id": f"{theme.upper()}#{entity_id}",
        "document_id": document_id,
        "theme": theme,
        **attributes,
    }
    # Denormalise district to top-level for GSI queries
    district = (attributes.get("spatial_units") or {}).get("district")
    if district:
        item["district"] = district
    else:
        item.pop("district", None)

    EXTRACTED_RECORDS_TABLE.put_item(Item=_ddb_safe(item))


def _caption_for(figure, elements):
    """
    Find the nearest OCR text line on the same page that horizontally
    overlaps the figure bounding box — used as the figure caption.
    Returns an empty string if no candidate is found.
    """
    box = figure.get("bbox") or []
    if len(box) != 4:
        return ""
    left, top, right, bottom = box
    candidates = []
    for element in elements:
        if element.get("page", 1) != figure.get("page", 1):
            continue
        if len(element.get("bbox") or []) != 4:
            continue
        el_left, el_top, el_right, el_bottom = element["bbox"]
        overlaps_x = el_right >= left and el_left <= right
        # Only consider lines above or below the figure, not inside it
        if overlaps_x and (el_bottom <= top or el_top >= bottom):
            distance = min(abs(top - el_bottom), abs(el_top - bottom))
            candidates.append((distance, element.get("text", "")))
    return min(candidates, default=(0, ""), key=lambda c: c[0])[1]


def _load_taxonomy():
    """
    Scan the entire EntityTaxonomy table and return all items sorted by
    entity_key. Uses pagination to handle tables larger than 1 MB.
    """
    items, kwargs = [], {}
    while True:
        response = TAXONOMY_TABLE.scan(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return sorted(items, key=lambda item: item.get("entity_key", ""))


# ── Core extraction logic ─────────────────────────────────────────────────────

def build_entities(document_id, ocr, taxonomy, bedrock_client=None):
    """
    Generator — yields (theme, entity_id, attributes) tuples for every
    structured asset found in the OCR output:

      1. Tables   — one record per Textract TABLE block
      2. Figures  — one record per LAYOUT_FIGURE block (with caption)
      3. Text facts — OCR lines are chunked by page and sent to Nova Lite;
                      each validated fact becomes one record

    All records start with review_status = "pending_review" so a human
    must approve them before they are used downstream.
    """
    elements = ocr.get("elements", [])
    bedrock_client = bedrock_client or bedrock

    # ── 1. Tables ─────────────────────────────────────────────────────────────
    for index, table in enumerate(ocr.get("tables", []), start=1):
        page = int(table.get("page", 1))
        yield "TABLES", f"P{page:02d}_T{index:03d}", {
            "pdf_page_num":      page,
            "original_page_num": page,
            "entity_type":       "table",
            "content_type":      "table",
            "table_data":        table.get("rows", []),
            "bbox":              _bbox(table.get("bbox")),
            # Flatten rows to a single string for keyword search
            "source_text_chunk": " | ".join(
                " ".join(str(cell) for cell in row if cell)
                for row in table.get("rows", [])
            ),
            "spatial_units":    {},
            "temporal_period":  "",
            "confidence_score": Decimal("1"),
            "review_status":    "pending_review",
            "custom_attributes": {},
        }

    # ── 2. Figures ────────────────────────────────────────────────────────────
    for index, figure in enumerate(ocr.get("figures", []), start=1):
        page = int(figure.get("page", 1))
        caption = _caption_for(figure, elements)
        yield "FIGURES", f"P{page:02d}_F{index:03d}", {
            "pdf_page_num":      page,
            "original_page_num": page,
            "entity_type":       "figure",
            "content_type":      "figure_crop",
            "extracted_fact":    caption,
            "source_text_chunk": caption,
            # S3 key of the cropped PNG saved by lambda_starter
            "s3_crop_url":       figure.get("image_s3_key", ""),
            "bbox":              _bbox(figure.get("bbox")),
            "crop_status":       figure.get("crop_status", "pending"),
            "spatial_units":    {},
            "temporal_period":  "",
            "confidence_score": Decimal("1"),
            "review_status":    "pending_review",
            "custom_attributes": {},
        }

    # ── 3. Nova Lite text facts ───────────────────────────────────────────────
    # page_chunks() groups OCR lines into ≤MAX_CHUNK_CHARS batches per page.
    # extract_chunk() sends each batch to Nova Lite and returns validated facts.
    for chunk in page_chunks(elements, MAX_CHUNK_CHARS):
        extracted = extract_chunk(chunk, taxonomy, bedrock_client, MODEL_ID)
        for fact_index, fact in enumerate(extracted, start=1):
            # Entity ID encodes page, chunk, and fact position for traceability
            entity_id = f"P{chunk['page']:02d}_C{chunk['chunk_index']:03d}_F{fact_index:02d}"
            yield fact["theme"], entity_id, {
                "pdf_page_num":      chunk["page"],
                "original_page_num": chunk["page"],
                "entity_type":       "thematic_fact",
                "content_type":      "narrative_text",
                "extracted_fact":    fact["extracted_fact"],
                "source_text_chunk": chunk["text"],   # raw OCR text that produced this fact
                "spatial_units":     fact["spatial_units"],
                "temporal_period":   fact["temporal_period"],
                "bbox":              _bbox(chunk.get("bbox")),
                "confidence_score":  _ddb_safe(fact["confidence_score"]),
                "review_status":     "pending_review",
                "custom_attributes": {},
            }


def ingest_document(document_id, s3_bucket, ocr_s3_key, s3_ocr_json_url=None):
    """
    Full ingestion pipeline for one document:

      1. Load ocr.json from S3
      2. Mark DocumentOCR record as ingestion_status = "processing"
      3. Load active taxonomy from DynamoDB
      4. Run build_entities() — tables, figures, Nova Lite facts
      5. Write every entity to ExtractedRecords
      6. Update DocumentOCR with final counts and status

    Returns a summary dict with counts and final ingestion_status.
    Raises on any unrecoverable error (caller marks status = "failed").
    """
    # Load the OCR JSON that lambda_starter wrote to S3
    payload = s3.get_object(Bucket=s3_bucket, Key=ocr_s3_key)["Body"].read()
    ocr = json.loads(payload)

    filename      = ocr.get("filename", "")
    document_name = filename or document_id

    # Read existing metadata so we can preserve created_at if already set
    existing_metadata = OCR_TABLE.get_item(Key={"document_id": document_id}).get("Item", {})

    counts = {
        "text_blocks_count":    len(ocr.get("elements", [])),
        "layout_regions_count": len(ocr.get("layout", [])),
        "table_count":          len(ocr.get("tables", [])),
        "figure_count":         len(ocr.get("figures", [])),
    }

    # Mark as processing so the frontend shows a spinner
    OCR_TABLE.update_item(
        Key={"document_id": document_id},
        UpdateExpression=(
            "SET record_type = :record_type, document_name = :name, total_pages = :pages, "
            "text_blocks_count = :text_count, layout_regions_count = :layout_count, "
            "table_count = :table_count, figure_count = :figure_count, "
            "s3_ocr_json_url = :ocr_url, created_at = if_not_exists(created_at, :created_at), "
            "ingestion_status = :status"
        ),
        ExpressionAttributeValues={
            ":record_type":  "METADATA",
            ":name":         document_name,
            ":pages":        int(ocr.get("total_pages", 0)),
            ":text_count":   counts["text_blocks_count"],
            ":layout_count": counts["layout_regions_count"],
            ":table_count":  counts["table_count"],
            ":figure_count": counts["figure_count"],
            ":ocr_url":      s3_ocr_json_url or f"s3://{s3_bucket}/{ocr_s3_key}",
            ":created_at": (
                ocr.get("processed_timestamp")
                or ocr.get("uploaded_at")
                or existing_metadata.get("uploaded_at")
                or datetime.now(timezone.utc).isoformat()
            ),
            ":status": "processing",
        },
    )

    try:
        taxonomy = _load_taxonomy()
        staged_records = list(build_entities(document_id, ocr, taxonomy))

        for theme, entity_id, attributes in staged_records:
            # Prefix the S3 crop key with the full bucket path before storing
            crop_key = attributes.get("s3_crop_url")
            if crop_key:
                attributes["s3_crop_url"] = f"s3://{s3_bucket}/{crop_key}"
            _put_entity(document_id, theme, entity_id, {"filename": filename, **attributes})

    except Exception as error:
        # Write the error message to DynamoDB so the frontend can surface it
        OCR_TABLE.update_item(
            Key={"document_id": document_id},
            UpdateExpression="SET ingestion_status = :status, ingestion_error = :error",
            ExpressionAttributeValues={":status": "failed", ":error": str(error)[:500]},
        )
        raise

    written = len(staged_records)
    # If records were staged, status = "pending_review" (human must approve).
    # If nothing was extracted (e.g. blank doc), status = "completed".
    ingestion_status = "pending_review" if written else "completed"

    OCR_TABLE.update_item(
        Key={"document_id": document_id},
        UpdateExpression=(
            "SET ingestion_status = :status, extracted_entity_count = :count, "
            "pending_review_count = :pending_count REMOVE ingestion_error"
        ),
        ExpressionAttributeValues={
            ":status":        ingestion_status,
            ":count":         written,
            ":pending_count": written,
        },
    )

    return {
        "document_id": document_id,
        **counts,
        "extracted_entity_count": written,
        "ingestion_status":       ingestion_status,
    }


# ── HTTP response helper ──────────────────────────────────────────────────────

def _response(status_code, payload):
    """Build an API Gateway-compatible HTTP response with CORS headers."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type":                "application/json",
            "Access-Control-Allow-Origin":  os.environ.get("ALLOWED_ORIGIN", "*"),
            "Access-Control-Allow-Methods": "POST,OPTIONS",
            "Access-Control-Allow-Headers": "content-type,authorization",
        },
        "body": json.dumps(payload, default=str),
    }


# ── Lambda entry point ────────────────────────────────────────────────────────

def lambda_handler(event, context):
    """
    Entry point — handler: lambda_function.lambda_handler

    Dual-mode execution:

    Mode A — Async worker (self-invoked by Mode B):
      event = {"async_worker": True, "document_id": "..."}
      Runs the full ingest_document() pipeline synchronously.
      No HTTP response is returned (fire-and-forget invocation).

    Mode B — HTTP trigger (API Gateway POST /api/documents/ingest-dynamo):
      event = standard API Gateway v2 proxy event
      Validates the document, sets status = "processing", then
      self-invokes this same function asynchronously (Mode A) and
      immediately returns HTTP 202 so the browser doesn't time out.
    """

    # ── Mode A: async worker ──────────────────────────────────────────────────
    if event.get("async_worker"):
        document_id = event.get("document_id", "")
        try:
            item = OCR_TABLE.get_item(Key={"document_id": document_id}).get("Item")
            if not item or item.get("status") != "completed" or not item.get("ocr_s3_key"):
                raise ValueError("OCR processing is not complete")
            ingest_document(
                document_id,
                item["s3_bucket"],
                item["ocr_s3_key"],
                item.get("s3_ocr_json_url"),
            )
        except Exception as error:
            LOGGER.exception("Async ingestion failed for %s", document_id)
            OCR_TABLE.update_item(
                Key={"document_id": document_id},
                UpdateExpression="SET ingestion_status = :status, ingestion_error = :error",
                ExpressionAttributeValues={":status": "failed", ":error": str(error)[:500]},
            )
        return  # No HTTP response for async invocations

    # ── Mode B: HTTP trigger ──────────────────────────────────────────────────
    method = (event.get("requestContext") or {}).get("http", {}).get("method", "").upper()
    if method == "OPTIONS":
        return _response(200, {})

    try:
        # Parse request body (may be base64-encoded by API Gateway)
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            import base64
            body = base64.b64decode(body).decode("utf-8")
        body = json.loads(body) if isinstance(body, str) else body

        document_id = (body.get("document_id") or "").strip()
        if not document_id:
            return _response(400, {"detail": "document_id is required"})

        # Verify the document exists in DynamoDB
        item = OCR_TABLE.get_item(Key={"document_id": document_id}).get("Item")
        if not item:
            return _response(404, {"detail": "Document not found"})

        # OCR must be complete before we can ingest
        if item.get("status") != "completed" or not item.get("ocr_s3_key"):
            return _response(409, {"detail": "OCR processing is not complete"})

        # Prevent duplicate concurrent ingestion runs
        if item.get("ingestion_status") == "processing":
            return _response(409, {"detail": "Entity extraction is already running"})

        # Atomically mark as processing (conditional write prevents race conditions)
        OCR_TABLE.update_item(
            Key={"document_id": document_id},
            UpdateExpression="SET ingestion_status = :status REMOVE ingestion_error",
            ConditionExpression="attribute_not_exists(ingestion_status) OR ingestion_status <> :status",
            ExpressionAttributeValues={":status": "processing"},
        )

        # Self-invoke asynchronously so this HTTP call returns immediately.
        # AWS_LAMBDA_FUNCTION_NAME is injected automatically by the Lambda runtime.
        try:
            lambda_client.invoke(
                FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
                InvocationType="Event",   # fire-and-forget
                Payload=json.dumps({"async_worker": True, "document_id": document_id}).encode("utf-8"),
            )
        except Exception:
            # Roll back status if we couldn't launch the async worker
            OCR_TABLE.update_item(
                Key={"document_id": document_id},
                UpdateExpression="SET ingestion_status = :status",
                ExpressionAttributeValues={":status": "failed"},
            )
            raise

        # Return 202 immediately — frontend polls /document/{id} for status
        return _response(202, {
            "document_id":      document_id,
            "ingestion_status": "processing",
            "detail":           "Entity extraction started",
        })

    except Exception:
        LOGGER.exception("DynamoDB ingestion failed")
        return _response(500, {"detail": "DynamoDB ingestion failed"})
