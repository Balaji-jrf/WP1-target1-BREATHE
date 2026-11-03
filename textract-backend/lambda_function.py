"""
lambda_function.py  —  jrf-task-dynamo-ingestion
=================================================
Lambda handler: lambda_function.lambda_handler

EXTRACTION DESIGN
-----------------
OCR text is divided into bounded chunks and sent to the user-selected,
allow-listed Bedrock model sequentially. The active taxonomy is loaded from
DynamoDB for each run and included in every model prompt.

Two execution modes
-------------------
1. HTTP mode  (API Gateway POST /api/documents/ingest-dynamo)
   → Validates doc, marks processing, self-invokes async, returns 202 immediately.

2. Async worker mode  (self-invoked with {"async_worker": True, "document_id": "..."})
   → Loads OCR JSON from S3, runs sequential model extraction, batch-writes to DynamoDB.

DynamoDB tables
---------------
  jrf-task-ocr-DocumentOCR      — document metadata + ingestion status
  jrf-task-ocr-ExtractedRecords — one item per taxonomy-aligned extracted fact
  jrf-task-data-EntityTaxonomy  — active entity taxonomy (read-only)

Environment variables
---------------------
  DYNAMO_TABLE            — DocumentOCR table name
  EXTRACTED_RECORDS_TABLE — ExtractedRecords table name
  TAXONOMY_TABLE          — EntityTaxonomy table name
  BEDROCK_MODEL_ID        — default Bedrock model (default: Llama 3.1 8B)
  MAX_CHUNK_CHARS         — max OCR chars per model call (default: 6000)
  ALLOWED_ORIGIN          — CORS origin for HTTP responses
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.config import Config

from ai_extractor import (
    DEFAULT_MODEL_ID, clear_generated_records, extract_chunks_sequential,
    get_model_config, load_active_taxonomy, page_chunks,
)

LOGGER = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
AWS_REGION       = os.environ.get("AWS_REGION", "ap-southeast-2")
MODEL_ID         = DEFAULT_MODEL_ID
MAX_CHUNK_CHARS  = int(os.environ.get("MAX_CHUNK_CHARS", "6000"))

OCR_TABLE_NAME               = "jrf-task-ocr-DocumentOCR"
EXTRACTED_RECORDS_TABLE_NAME = "jrf-task-ocr-ExtractedRecords"
TAXONOMY_TABLE_NAME          = "jrf-task-data-EntityTaxonomy"

# ── AWS clients (created once at cold-start, reused across invocations) ───────
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

import threading
_bedrock_local = threading.local()

def _get_bedrock(region_name):
    """Return a Bedrock client for the selected, allow-listed model region."""
    if (
        not hasattr(_bedrock_local, "client")
        or getattr(_bedrock_local, "region_name", None) != region_name
    ):
        _bedrock_local.client = boto3.client(
            "bedrock-runtime",
            region_name=region_name,
            config=Config(
                retries={"mode": "adaptive", "total_max_attempts": 5},
                connect_timeout=5,
                read_timeout=60,
            ),
        )
        _bedrock_local.region_name = region_name
    return _bedrock_local.client


lambda_client = boto3.client("lambda", region_name=AWS_REGION)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ddb_safe(value):
    """Recursively convert floats → Decimal so DynamoDB accepts them."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [_ddb_safe(i) for i in value]
    if isinstance(value, dict):
        return {k: _ddb_safe(v) for k, v in value.items()}
    return value


def _bbox(bbox):
    """Convert [left, top, right, bottom] → [x, y, width, height]. Returns None if invalid."""
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    left, top, right, bottom = [float(v) for v in bbox]
    return [left, top, max(0.0, right - left), max(0.0, bottom - top)]


def _load_taxonomy():
    """Load explicitly active entity definitions from EntityTaxonomy."""
    return load_active_taxonomy(TAXONOMY_TABLE)


def _batch_write_records(items):
    """
    Write a list of DynamoDB items using batch_writer().
    batch_writer() automatically handles:
      - Splitting into 25-item batches (DynamoDB limit)
      - Retrying unprocessed items
    Much faster than individual put_item() calls.
    """
    with EXTRACTED_RECORDS_TABLE.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=_ddb_safe(item))


def _make_entity_item(document_id, theme, entity_id, attributes, filename):
    """
    Build the DynamoDB item dict for one extracted entity.
    Primary key: id = "DOC#{document_id}", theme_entity_id = "{THEME}#{entity_id}"
    District is denormalised to top-level for GSI queries.
    """
    item = {
        "id":               f"DOC#{document_id}",
        "theme_entity_id":  f"{theme.upper()}#{entity_id}",
        "document_id":      document_id,
        "theme":            theme,
        "filename":         filename,
        **attributes,
    }
    district = (attributes.get("spatial_units") or {}).get("district")
    if district:
        item["district"] = district
    else:
        item.pop("district", None)
    return item


def _extract_text_facts_sequential(elements, taxonomy, model_id):
    """Submit every bounded OCR chunk to the selected Bedrock model in order."""
    chunks = list(page_chunks(elements, MAX_CHUNK_CHARS))
    model_config = get_model_config(model_id)
    model_client = _get_bedrock(model_config["region"])
    LOGGER.info(
        "Starting sequential extraction: %d chunks, model=%s, region=%s",
        len(chunks), model_id, model_config["region"],
    )
    results = []
    for chunk, facts in extract_chunks_sequential(
        chunks, taxonomy, model_client, model_id
    ):
        for fact_index, fact in enumerate(facts, start=1):
            entity_id = f"P{chunk['page']:02d}_C{chunk['chunk_index']:03d}_F{fact_index:02d}"
            results.append((fact["theme"], entity_id, {
                "pdf_page_num":      chunk["page"],
                "original_page_num": chunk["page"],
                "entity_type":       "thematic_fact",
                "content_type":      "narrative_text",
                "extracted_fact":    fact["extracted_fact"],
                "source_text_chunk": fact["source_excerpt"],
                "spatial_units":     fact["spatial_units"],
                "temporal_period":   fact["temporal_period"],
                "bbox":              _bbox(chunk.get("bbox")),
                "confidence_score":  _ddb_safe(fact["confidence_score"]),
                "review_status":     "pending_review",
                "custom_attributes": {},
            }))
    LOGGER.info("Sequential extraction done: %d facts from %d chunks", len(results), len(chunks))
    return results


# ── Core ingestion pipeline ───────────────────────────────────────────────────

def ingest_document(document_id, s3_bucket, ocr_s3_key, s3_ocr_json_url=None, model_id=MODEL_ID):
    """
    Full ingestion pipeline:
      1. Load ocr.json from S3
      2. Mark DocumentOCR as processing
      3. Load taxonomy
      4. Extract taxonomy-aligned text facts sequentially via selected Bedrock model
      5. Batch-write the facts to DynamoDB
      7. Update DocumentOCR with final status + counts
    """
    LOGGER.info("ingest_document start: doc=%s bucket=%s key=%s", document_id, s3_bucket, ocr_s3_key)

    payload = s3.get_object(Bucket=s3_bucket, Key=ocr_s3_key)["Body"].read()
    ocr     = json.loads(payload)

    filename      = ocr.get("filename", "")
    document_name = filename or document_id
    existing      = OCR_TABLE.get_item(Key={"document_id": document_id}).get("Item", {})

    counts = {
        "text_blocks_count":    len(ocr.get("elements", [])),
        "layout_regions_count": len(ocr.get("layout", [])),
        "table_count":          len(ocr.get("tables", [])),
        "figure_count":         len(ocr.get("figures", [])),
    }
    LOGGER.info("OCR loaded: pages=%s blocks=%s tables=%s figures=%s",
                ocr.get("total_pages"), counts["text_blocks_count"],
                counts["table_count"], counts["figure_count"])

    # Mark as processing in DynamoDB
    OCR_TABLE.update_item(
        Key={"document_id": document_id},
        UpdateExpression=(
            "SET record_type = :rt, document_name = :name, total_pages = :pages, "
            "text_blocks_count = :tc, layout_regions_count = :lc, "
            "table_count = :tbl, figure_count = :fig, "
            "s3_ocr_json_url = :url, created_at = if_not_exists(created_at, :ca), "
            "ingestion_status = :status"
        ),
        ExpressionAttributeValues={
            ":rt":     "METADATA",
            ":name":   document_name,
            ":pages":  int(ocr.get("total_pages", 0)),
            ":tc":     counts["text_blocks_count"],
            ":lc":     counts["layout_regions_count"],
            ":tbl":    counts["table_count"],
            ":fig":    counts["figure_count"],
            ":url":    s3_ocr_json_url or f"s3://{s3_bucket}/{ocr_s3_key}",
            ":ca": (
                ocr.get("processed_timestamp")
                or ocr.get("uploaded_at")
                or existing.get("uploaded_at")
                or datetime.now(timezone.utc).isoformat()
            ),
            ":status": "processing",
        },
    )

    try:
        taxonomy = _load_taxonomy()
        elements = ocr.get("elements", [])
        # ── Extract taxonomy-aligned text facts sequentially ───────────────
        all_records = _extract_text_facts_sequential(elements, taxonomy, model_id)

        # ── Replace old AI results and remove legacy table/figure assets ─────
        items = [
            _make_entity_item(document_id, theme, entity_id, attrs, filename)
            for theme, entity_id, attrs in all_records
        ]
        clear_generated_records(EXTRACTED_RECORDS_TABLE, document_id)
        LOGGER.info("Writing %d records to DynamoDB via batch_writer", len(items))
        _batch_write_records(items)

    except Exception as error:
        OCR_TABLE.update_item(
            Key={"document_id": document_id},
            UpdateExpression="SET ingestion_status = :s, ingestion_error = :e",
            ExpressionAttributeValues={":s": "failed", ":e": str(error)[:500]},
        )
        raise

    written          = len(all_records)
    ingestion_status = "pending_review" if written else "completed"

    OCR_TABLE.update_item(
        Key={"document_id": document_id},
        UpdateExpression=(
            "SET ingestion_status = :s, extracted_entity_count = :c, "
            "pending_review_count = :p REMOVE ingestion_error"
        ),
        ExpressionAttributeValues={":s": ingestion_status, ":c": written, ":p": written},
    )

    LOGGER.info("ingest_document done: doc=%s written=%d status=%s",
                document_id, written, ingestion_status)
    return {
        "document_id": document_id,
        **counts,
        "extracted_entity_count": written,
        "ingestion_status":       ingestion_status,
    }


# ── HTTP response helper ──────────────────────────────────────────────────────

def _response(status_code, payload):
    """Build an API Gateway HTTP response with CORS headers."""
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
    Dual-mode entry point — handler: lambda_function.lambda_handler

    Mode A — Async worker (self-invoked):
      event = {"async_worker": True, "document_id": "..."}
      Runs ingest_document() with sequential extraction using the selected model.
      No HTTP response returned.

    Mode B — HTTP trigger (API Gateway POST /api/documents/ingest-dynamo):
      Validates doc/model → marks processing → self-invokes Mode A → returns 202.
      Frontend polls GET /document/{id} watching ingestion_status.
    """

    # ── Mode A: async worker ──────────────────────────────────────────────────
    if event.get("async_worker"):
        document_id = event.get("document_id", "")
        model_id = event.get("model_id", MODEL_ID)
        try:
            get_model_config(model_id)
            item = OCR_TABLE.get_item(Key={"document_id": document_id}).get("Item")
            if not item or item.get("status") != "completed" or not item.get("ocr_s3_key"):
                raise ValueError("OCR processing is not complete")
            ingest_document(
                document_id,
                item["s3_bucket"],
                item["ocr_s3_key"],
                item.get("s3_ocr_json_url"),
                model_id,
            )
        except Exception as error:
            LOGGER.exception("Async ingestion failed for %s", document_id)
            OCR_TABLE.update_item(
                Key={"document_id": document_id},
                UpdateExpression="SET ingestion_status = :s, ingestion_error = :e",
                ExpressionAttributeValues={":s": "failed", ":e": str(error)[:500]},
            )
        return  # no HTTP response for async invocations

    # ── Mode B: HTTP trigger ──────────────────────────────────────────────────
    method = (event.get("requestContext") or {}).get("http", {}).get("method", "").upper()
    if method == "OPTIONS":
        return _response(200, {})

    try:
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            import base64
            body = base64.b64decode(body).decode("utf-8")
        body = json.loads(body) if isinstance(body, str) else body

        document_id = (body.get("document_id") or "").strip()
        model_id = body.get("model_id") or MODEL_ID
        if not document_id:
            return _response(400, {"detail": "document_id is required"})
        try:
            get_model_config(model_id)
        except ValueError as error:
            return _response(400, {"detail": str(error)})

        item = OCR_TABLE.get_item(Key={"document_id": document_id}).get("Item")
        if not item:
            return _response(404, {"detail": "Document not found"})
        if item.get("status") != "completed" or not item.get("ocr_s3_key"):
            return _response(409, {"detail": "OCR processing is not complete"})
        if item.get("ingestion_status") == "processing":
            return _response(409, {"detail": "Entity extraction is already running"})

        # Atomic conditional write — prevents two simultaneous ingestion runs
        OCR_TABLE.update_item(
            Key={"document_id": document_id},
            UpdateExpression=(
                "SET ingestion_status = :s, ingestion_model_id = :model "
                "REMOVE ingestion_error"
            ),
            ConditionExpression="attribute_not_exists(ingestion_status) OR ingestion_status <> :s",
            ExpressionAttributeValues={":s": "processing", ":model": model_id},
        )

        # Self-invoke asynchronously — HTTP response returns immediately
        try:
            lambda_client.invoke(
                FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
                InvocationType="Event",  # fire-and-forget
                Payload=json.dumps({
                    "async_worker": True,
                    "document_id": document_id,
                    "model_id": model_id,
                }).encode(),
            )
        except Exception:
            OCR_TABLE.update_item(
                Key={"document_id": document_id},
                UpdateExpression="SET ingestion_status = :s",
                ExpressionAttributeValues={":s": "failed"},
            )
            raise

        return _response(202, {
            "document_id":      document_id,
            "ingestion_status": "processing",
            "detail":           "Entity extraction started",
        })

    except Exception:
        LOGGER.exception("DynamoDB ingestion failed")
        return _response(500, {"detail": "DynamoDB ingestion failed"})
