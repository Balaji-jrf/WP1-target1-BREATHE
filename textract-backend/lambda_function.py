"""
lambda_function.py  —  jrf-task-dynamo-ingestion
=================================================
Lambda handler: lambda_function.lambda_handler

PERFORMANCE DESIGN
------------------
Problem: 160-page doc = ~960 Nova Lite calls. Sequential at ~2s each = 32 min.
         Lambda timeout = 15 min → it dies before finishing.
         518-page doc = ~3000 calls = ~100 min → completely impossible sequentially.

Solution: ThreadPoolExecutor runs PARALLEL_WORKERS=20 Nova Lite calls at once.
          960 calls ÷ 20 workers = ~48 batches × 2s = ~96s for 160 pages.
          3000 calls ÷ 20 workers = ~150 batches × 2s = ~5 min for 518 pages.
          DynamoDB writes use batch_writer() for up to 25 items per request.

Two execution modes
-------------------
1. HTTP mode  (API Gateway POST /api/documents/ingest-dynamo)
   → Validates doc, marks processing, self-invokes async, returns 202 immediately.

2. Async worker mode  (self-invoked with {"async_worker": True, "document_id": "..."})
   → Loads OCR JSON from S3, runs parallel extraction, batch-writes to DynamoDB.

DynamoDB tables
---------------
  jrf-task-ocr-DocumentOCR      — document metadata + ingestion status
  jrf-task-ocr-ExtractedRecords — one item per extracted fact/table/figure
  jrf-task-data-EntityTaxonomy  — active entity taxonomy (read-only)

Environment variables
---------------------
  DYNAMO_TABLE            — DocumentOCR table name
  EXTRACTED_RECORDS_TABLE — ExtractedRecords table name
  TAXONOMY_TABLE          — EntityTaxonomy table name
  BEDROCK_MODEL_ID        — Bedrock model (default: amazon.nova-lite-v1:0)
  MAX_CHUNK_CHARS         — max OCR chars per Nova Lite call (default: 1500)
  PARALLEL_WORKERS        — concurrent Nova Lite threads (default: 20)
  ALLOWED_ORIGIN          — CORS origin for HTTP responses
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.config import Config

from ai_extractor import extract_chunk, page_chunks

LOGGER = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
AWS_REGION       = os.environ.get("AWS_REGION", "ap-southeast-2")
MODEL_ID         = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
MAX_CHUNK_CHARS  = int(os.environ.get("MAX_CHUNK_CHARS", "1500"))
# 20 parallel workers saturates Bedrock throughput without hitting throttle limits.
# Reduce if you see ThrottlingException in logs.
PARALLEL_WORKERS = int(os.environ.get("PARALLEL_WORKERS", "20"))

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

# Each thread needs its own Bedrock client — boto3 clients are NOT thread-safe.
# We create one per worker thread using a thread-local factory.
import threading
_bedrock_local = threading.local()

def _get_bedrock():
    """Return a thread-local Bedrock client (one per worker thread)."""
    if not hasattr(_bedrock_local, "client"):
        _bedrock_local.client = boto3.client(
            "bedrock-runtime",
            region_name=AWS_REGION,
            config=Config(
                retries={"mode": "adaptive", "total_max_attempts": 5},
                connect_timeout=5,
                read_timeout=60,
            ),
        )
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


def _caption_for(figure, elements):
    """
    Find the nearest OCR text line on the same page that horizontally
    overlaps the figure bbox — used as the figure caption.
    """
    box = figure.get("bbox") or []
    if len(box) != 4:
        return ""
    left, top, right, bottom = box
    candidates = []
    for el in elements:
        if el.get("page", 1) != figure.get("page", 1):
            continue
        if len(el.get("bbox") or []) != 4:
            continue
        el_left, el_top, el_right, el_bottom = el["bbox"]
        if el_right >= left and el_left <= right:
            if el_bottom <= top or el_top >= bottom:
                dist = min(abs(top - el_bottom), abs(el_top - bottom))
                candidates.append((dist, el.get("text", "")))
    return min(candidates, default=(0, ""), key=lambda c: c[0])[1]


def _load_taxonomy():
    """Scan EntityTaxonomy table with pagination. Returns list sorted by entity_key."""
    items, kwargs = [], {}
    while True:
        resp = TAXONOMY_TABLE.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return sorted(items, key=lambda i: i.get("entity_key", ""))


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


def _make_entity_item(document_id, theme, entity_id, attributes, filename, s3_bucket):
    """
    Build the DynamoDB item dict for one extracted entity.
    Primary key: id = "DOC#{document_id}", theme_entity_id = "{THEME}#{entity_id}"
    District is denormalised to top-level for GSI queries.
    """
    # Prefix S3 crop key with full bucket path
    crop_key = attributes.get("s3_crop_url")
    if crop_key and not crop_key.startswith("s3://"):
        attributes = {**attributes, "s3_crop_url": f"s3://{s3_bucket}/{crop_key}"}

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


# ── Parallel Nova Lite extraction ─────────────────────────────────────────────

def _process_chunk(chunk, taxonomy, model_id):
    """
    Worker function — runs in a thread pool.
    Calls Nova Lite for one OCR chunk and returns (chunk, facts) or (chunk, []).
    Errors are caught so one bad chunk never kills the whole document.
    """
    try:
        facts = extract_chunk(chunk, taxonomy, _get_bedrock(), model_id)
        return chunk, facts
    except Exception as exc:
        LOGGER.warning("Chunk p%s c%s failed: %s", chunk.get("page"), chunk.get("chunk_index"), exc)
        return chunk, []


def _extract_text_facts_parallel(elements, taxonomy, model_id):
    """
    Run all OCR chunks through Nova Lite in parallel using ThreadPoolExecutor.

    Why threads (not processes)?
      - Nova Lite calls are I/O-bound (network wait), not CPU-bound.
      - Threads share memory (taxonomy, config) with zero copy overhead.
      - Python's GIL doesn't matter here — threads release it during I/O.

    Returns a list of (theme, entity_id, attributes) tuples.
    """
    chunks = list(page_chunks(elements, MAX_CHUNK_CHARS))
    total  = len(chunks)
    LOGGER.info("Starting parallel extraction: %d chunks, %d workers", total, PARALLEL_WORKERS)

    results = []  # list of (theme, entity_id, attributes)

    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
        # Submit all chunks to the thread pool at once
        futures = {
            pool.submit(_process_chunk, chunk, taxonomy, model_id): chunk
            for chunk in chunks
        }
        completed = 0
        for future in as_completed(futures):
            chunk, facts = future.result()
            completed += 1
            if completed % 50 == 0:
                # Progress log every 50 chunks so CloudWatch shows activity
                LOGGER.info("Progress: %d/%d chunks done", completed, total)
            for fact_index, fact in enumerate(facts, start=1):
                entity_id = f"P{chunk['page']:02d}_C{chunk['chunk_index']:03d}_F{fact_index:02d}"
                results.append((fact["theme"], entity_id, {
                    "pdf_page_num":      chunk["page"],
                    "original_page_num": chunk["page"],
                    "entity_type":       "thematic_fact",
                    "content_type":      "narrative_text",
                    "extracted_fact":    fact["extracted_fact"],
                    "source_text_chunk": chunk["text"],
                    "spatial_units":     fact["spatial_units"],
                    "temporal_period":   fact["temporal_period"],
                    "bbox":              _bbox(chunk.get("bbox")),
                    "confidence_score":  _ddb_safe(fact["confidence_score"]),
                    "review_status":     "pending_review",
                    "custom_attributes": {},
                }))

    LOGGER.info("Parallel extraction done: %d facts from %d chunks", len(results), total)
    return results


# ── Core ingestion pipeline ───────────────────────────────────────────────────

def ingest_document(document_id, s3_bucket, ocr_s3_key, s3_ocr_json_url=None):
    """
    Full ingestion pipeline:
      1. Load ocr.json from S3
      2. Mark DocumentOCR as processing
      3. Load taxonomy
      4. Extract tables + figures (instant, no AI needed)
      5. Extract text facts in PARALLEL via Nova Lite
      6. Batch-write all records to DynamoDB
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
        all_records = []  # accumulate all (theme, entity_id, attributes) here

        # ── 1. Tables (no AI — instant) ───────────────────────────────────────
        for idx, table in enumerate(ocr.get("tables", []), start=1):
            page = int(table.get("page", 1))
            all_records.append(("TABLES", f"P{page:02d}_T{idx:03d}", {
                "pdf_page_num":      page,
                "original_page_num": page,
                "entity_type":       "table",
                "content_type":      "table",
                "table_data":        table.get("rows", []),
                "bbox":              _bbox(table.get("bbox")),
                "source_text_chunk": " | ".join(
                    " ".join(str(c) for c in row if c)
                    for row in table.get("rows", [])
                ),
                "spatial_units":    {},
                "temporal_period":  "",
                "confidence_score": Decimal("1"),
                "review_status":    "pending_review",
                "custom_attributes": {},
            }))

        # ── 2. Figures (no AI — instant) ──────────────────────────────────────
        for idx, figure in enumerate(ocr.get("figures", []), start=1):
            page    = int(figure.get("page", 1))
            caption = _caption_for(figure, elements)
            all_records.append(("FIGURES", f"P{page:02d}_F{idx:03d}", {
                "pdf_page_num":      page,
                "original_page_num": page,
                "entity_type":       "figure",
                "content_type":      "figure_crop",
                "extracted_fact":    caption,
                "source_text_chunk": caption,
                "s3_crop_url":       figure.get("image_s3_key", ""),
                "bbox":              _bbox(figure.get("bbox")),
                "crop_status":       figure.get("crop_status", "pending"),
                "spatial_units":    {},
                "temporal_period":  "",
                "confidence_score": Decimal("1"),
                "review_status":    "pending_review",
                "custom_attributes": {},
            }))

        # ── 3. Text facts — PARALLEL Nova Lite calls ──────────────────────────
        text_facts = _extract_text_facts_parallel(elements, taxonomy, MODEL_ID)
        all_records.extend(text_facts)

        # ── 4. Batch-write everything to DynamoDB ─────────────────────────────
        # Build item dicts first, then write in one batch_writer pass
        items = [
            _make_entity_item(document_id, theme, entity_id, attrs, filename, s3_bucket)
            for theme, entity_id, attrs in all_records
        ]
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
      Runs ingest_document() with parallel Nova Lite extraction.
      No HTTP response returned.

    Mode B — HTTP trigger (API Gateway POST /api/documents/ingest-dynamo):
      Validates doc → marks processing → self-invokes Mode A → returns 202.
      Frontend polls GET /document/{id} watching ingestion_status.
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
        if not document_id:
            return _response(400, {"detail": "document_id is required"})

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
            UpdateExpression="SET ingestion_status = :s REMOVE ingestion_error",
            ConditionExpression="attribute_not_exists(ingestion_status) OR ingestion_status <> :s",
            ExpressionAttributeValues={":s": "processing"},
        )

        # Self-invoke asynchronously — HTTP response returns immediately
        try:
            lambda_client.invoke(
                FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
                InvocationType="Event",  # fire-and-forget
                Payload=json.dumps({"async_worker": True, "document_id": document_id}).encode(),
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
