"""Parse completed OCR JSON and persist document and entity records."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.config import Config
from ai_extractor import extract_chunk, page_chunks

LOGGER = logging.getLogger(__name__)

OCR_TABLE_NAME = "jrf-task-ocr-DocumentOCR"
ENTITIES_TABLE_NAME = "jrf-task-data-Extraction-entities"
TAXONOMY_TABLE_NAME = "jrf-task-data-EntityTaxonomy"
AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
MAX_CHUNK_CHARS = int(os.environ.get("MAX_CHUNK_CHARS", "1500"))
OCR_TABLE = boto3.resource("dynamodb", region_name=AWS_REGION).Table(
    os.environ.get("DYNAMO_TABLE", OCR_TABLE_NAME)
)
ENTITIES_TABLE = boto3.resource("dynamodb", region_name=AWS_REGION).Table(
    os.environ.get("ENTITIES_TABLE", ENTITIES_TABLE_NAME)
)
TAXONOMY_TABLE = boto3.resource("dynamodb", region_name=AWS_REGION).Table(
    os.environ.get("TAXONOMY_TABLE", TAXONOMY_TABLE_NAME)
)
s3 = boto3.client("s3", region_name=AWS_REGION)
bedrock = boto3.client(
    "bedrock-runtime",
    region_name=AWS_REGION,
    config=Config(
        retries={"mode": "adaptive", "total_max_attempts": 5},
        connect_timeout=5,
        read_timeout=60,
    ),
)
lambda_client = boto3.client("lambda", region_name=AWS_REGION)


def _ddb_safe(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [_ddb_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _ddb_safe(item) for key, item in value.items()}
    return value


def _bbox(bbox):
    """Convert normalized [left, top, right, bottom] to [x, y, width, height]."""
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    left, top, right, bottom = [float(value) for value in bbox]
    return [left, top, max(0.0, right - left), max(0.0, bottom - top)]


def _put_entity(document_id, theme, entity_id, attributes):
    item = {
        "id": f"DOC#{document_id}",
        "theme_entity_id": f"{theme.upper()}#{entity_id}",
        "document_id": document_id,
        "theme": theme,
        **attributes,
    }
    district = (attributes.get("spatial_units") or {}).get("district")
    if district:
        item["district"] = district
    else:
        item.pop("district", None)
    ENTITIES_TABLE.put_item(Item=_ddb_safe(item))


def _caption_for(figure, elements):
    """Choose the nearest same-page short text line touching the figure's x-range."""
    box = figure.get("bbox") or []
    if len(box) != 4:
        return ""
    left, top, right, bottom = box
    candidates = []
    for element in elements:
        if element.get("page", 1) != figure.get("page", 1) or len(element.get("bbox") or []) != 4:
            continue
        el_left, el_top, el_right, el_bottom = element["bbox"]
        overlaps_x = el_right >= left and el_left <= right
        if overlaps_x and (el_bottom <= top or el_top >= bottom):
            distance = min(abs(top - el_bottom), abs(el_top - bottom))
            candidates.append((distance, element.get("text", "")))
    return min(candidates, default=(0, ""), key=lambda candidate: candidate[0])[1]


def _load_taxonomy():
    items, kwargs = [], {}
    while True:
        response = TAXONOMY_TABLE.scan(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return sorted(items, key=lambda item: item.get("entity_key", ""))


def build_entities(document_id, ocr, taxonomy, bedrock_client=None):
    """Yield structured assets and validated Nova Lite facts for review."""
    elements = ocr.get("elements", [])
    bedrock_client = bedrock_client or bedrock
    for index, table in enumerate(ocr.get("tables", []), start=1):
        page = int(table.get("page", 1))
        yield "TABLES", f"P{page:02d}_T{index:03d}", {
            "pdf_page_num": page,
            "original_page_num": page,
            "entity_type": "table",
            "content_type": "table",
            "table_data": table.get("rows", []),
            "bbox": _bbox(table.get("bbox")),
            "source_text_chunk": " | ".join(
                " ".join(str(cell) for cell in row if cell)
                for row in table.get("rows", [])
            ),
            "spatial_units": {},
            "temporal_period": "",
            "confidence_score": Decimal("1"),
            "review_status": "pending_review",
            "custom_attributes": {},
        }

    for index, figure in enumerate(ocr.get("figures", []), start=1):
        page = int(figure.get("page", 1))
        yield "FIGURES", f"P{page:02d}_F{index:03d}", {
            "pdf_page_num": page,
            "original_page_num": page,
            "entity_type": "figure",
            "content_type": "figure_crop",
            "extracted_fact": _caption_for(figure, elements),
            "source_text_chunk": _caption_for(figure, elements),
            "s3_crop_url": figure.get("image_s3_key", ""),
            "bbox": _bbox(figure.get("bbox")),
            "crop_status": figure.get("crop_status", "pending"),
            "spatial_units": {},
            "temporal_period": "",
            "confidence_score": Decimal("1"),
            "review_status": "pending_review",
            "custom_attributes": {},
        }

    for chunk in page_chunks(elements, MAX_CHUNK_CHARS):
        extracted = extract_chunk(chunk, taxonomy, bedrock_client, MODEL_ID)
        for fact_index, fact in enumerate(extracted, start=1):
            entity_id = (
                f"P{chunk['page']:02d}_C{chunk['chunk_index']:03d}_F{fact_index:02d}"
            )
            yield fact["theme"], entity_id, {
                "pdf_page_num": chunk["page"],
                "original_page_num": chunk["page"],
                "entity_type": "thematic_fact",
                "content_type": "narrative_text",
                "extracted_fact": fact["extracted_fact"],
                "source_text_chunk": chunk["text"],
                "spatial_units": fact["spatial_units"],
                "temporal_period": fact["temporal_period"],
                "bbox": _bbox(chunk.get("bbox")),
                "confidence_score": _ddb_safe(fact["confidence_score"]),
                "review_status": "pending_review",
                "custom_attributes": {},
            }


def ingest_document(document_id, s3_bucket, ocr_s3_key, s3_ocr_json_url=None):
    """Load OCR JSON, run Nova Lite over page chunks, and stage records for review."""
    payload = s3.get_object(Bucket=s3_bucket, Key=ocr_s3_key)["Body"].read()
    ocr = json.loads(payload)
    filename = ocr.get("filename", "")
    document_name = filename or document_id
    existing_metadata = OCR_TABLE.get_item(Key={"document_id": document_id}).get("Item", {})
    counts = {
        "text_blocks_count": len(ocr.get("elements", [])),
        "layout_regions_count": len(ocr.get("layout", [])),
        "table_count": len(ocr.get("tables", [])),
        "figure_count": len(ocr.get("figures", [])),
    }
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
            ":record_type": "METADATA",
            ":name": document_name,
            ":pages": int(ocr.get("total_pages", 0)),
            ":text_count": counts["text_blocks_count"],
            ":layout_count": counts["layout_regions_count"],
            ":table_count": counts["table_count"],
            ":figure_count": counts["figure_count"],
            ":ocr_url": s3_ocr_json_url or f"s3://{s3_bucket}/{ocr_s3_key}",
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
            crop_key = attributes.get("s3_crop_url")
            if crop_key:
                attributes["s3_crop_url"] = f"s3://{s3_bucket}/{crop_key}"
            _put_entity(document_id, theme, entity_id, {"filename": filename, **attributes})
    except Exception as error:
        OCR_TABLE.update_item(
            Key={"document_id": document_id},
            UpdateExpression="SET ingestion_status = :status, ingestion_error = :error",
            ExpressionAttributeValues={":status": "failed", ":error": str(error)[:500]},
        )
        raise
    written = len(staged_records)
    ingestion_status = "pending_review" if written else "completed"
    OCR_TABLE.update_item(
        Key={"document_id": document_id},
        UpdateExpression=(
            "SET ingestion_status = :status, extracted_entity_count = :count, "
            "pending_review_count = :pending_count REMOVE ingestion_error"
        ),
        ExpressionAttributeValues={
            ":status": ingestion_status,
            ":count": written,
            ":pending_count": written,
        },
    )
    return {
        "document_id": document_id,
        **counts,
        "extracted_entity_count": written,
        "ingestion_status": ingestion_status,
    }


def _response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
            "Access-Control-Allow-Methods": "POST,OPTIONS",
            "Access-Control-Allow-Headers": "content-type,authorization",
        },
        "body": json.dumps(payload, default=str),
    }


def lambda_handler(event, context):
    """API Gateway starter and asynchronous worker for OCR entity extraction."""
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
            LOGGER.exception("Asynchronous ingestion failed for %s", document_id)
            OCR_TABLE.update_item(
                Key={"document_id": document_id},
                UpdateExpression="SET ingestion_status = :status, ingestion_error = :error",
                ExpressionAttributeValues={":status": "failed", ":error": str(error)[:500]},
            )
        return

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
        OCR_TABLE.update_item(
            Key={"document_id": document_id},
            UpdateExpression="SET ingestion_status = :status REMOVE ingestion_error",
            ConditionExpression="attribute_not_exists(ingestion_status) OR ingestion_status <> :status",
            ExpressionAttributeValues={":status": "processing"},
        )
        try:
            lambda_client.invoke(
                FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
                InvocationType="Event",
                Payload=json.dumps({"async_worker": True, "document_id": document_id}).encode("utf-8"),
            )
        except Exception:
            OCR_TABLE.update_item(
                Key={"document_id": document_id},
                UpdateExpression="SET ingestion_status = :status",
                ExpressionAttributeValues={":status": "failed"},
            )
            raise
        return _response(202, {
            "document_id": document_id,
            "ingestion_status": "processing",
            "detail": "Entity extraction started",
        })
    except Exception:
        LOGGER.exception("DynamoDB ingestion failed")
        return _response(500, {"detail": "DynamoDB ingestion failed"})
