"""Parse completed OCR JSON and persist document and entity records."""

from __future__ import annotations

import json
import logging
import os
import re
from decimal import Decimal
from datetime import datetime, timezone

import boto3

LOGGER = logging.getLogger(__name__)

OCR_TABLE_NAME = "jrf-task-ocr-DocumentOCR"
ENTITIES_TABLE_NAME = "jrf-task-data-Extraction-entities"
AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
OCR_TABLE = boto3.resource("dynamodb", region_name=AWS_REGION).Table(
    os.environ.get("DYNAMO_TABLE", OCR_TABLE_NAME)
)
ENTITIES_TABLE = boto3.resource("dynamodb", region_name=AWS_REGION).Table(
    os.environ.get("ENTITIES_TABLE", ENTITIES_TABLE_NAME)
)

THEME_KEYWORDS = {
    "Climate_Heat": ("temperature", "heat", "rainfall", "drought", "monsoon", "famine"),
    "Public_Health_Sanitation": (
        "disease", "cholera", "plague", "wells contamination", "sewage",
        "excrement", "sanitation", "epidemic", "health",
    ),
    "Land_Use": ("land use", "forest", "tenure", "land", "boundary"),
    "Agriculture": ("crop", "agriculture", "cultivation", "harvest", "agricultural"),
    "Irrigation": ("irrigation", "canal", "tank", "water supply"),
    "Structural_Inequality": ("caste", "inequality", "labour", "wages", "poverty"),
    "Policies": ("policy", "regulation", "government", "administration", "district", "taluk", "village"),
}
LOCATION_RE = re.compile(r"\b([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)?)\s+(district|taluk|village)\b", re.I)


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


def build_entities(document_id, ocr):
    """Yield stable table, figure and keyword-matched passage records."""
    elements = ocr.get("elements", [])
    for index, table in enumerate(ocr.get("tables", []), start=1):
        page = int(table.get("page", 1))
        yield "Tables", f"P{page:02d}_T{index:03d}", {
            "pdf_page_num": page,
            "original_page_num": page,
            "entity_type": "table",
            "content_type": "table",
            "table_data": table.get("rows", []),
            "bbox": _bbox(table.get("bbox")),
            "custom_attributes": {},
        }

    for index, figure in enumerate(ocr.get("figures", []), start=1):
        page = int(figure.get("page", 1))
        yield "Figures", f"P{page:02d}_F{index:03d}", {
            "pdf_page_num": page,
            "original_page_num": page,
            "entity_type": "figure",
            "content_type": "figure_crop",
            "extracted_text": _caption_for(figure, elements),
            "s3_crop_url": figure.get("image_s3_key", ""),
            "bbox": _bbox(figure.get("bbox")),
            "crop_status": figure.get("crop_status", "pending"),
            "custom_attributes": {},
        }

    for index, element in enumerate(elements, start=1):
        text = element.get("text", "")
        lowered = text.casefold()
        for theme, keywords in THEME_KEYWORDS.items():
            hits = [keyword for keyword in keywords if keyword in lowered]
            if not hits:
                continue
            page = int(element.get("page", 1))
            locations = LOCATION_RE.findall(text)
            spatial_units = [f"{name} {kind.title()}" for name, kind in locations]
            districts = [name for name, kind in locations if kind.lower() == "district"]
            attributes = {
                "pdf_page_num": page,
                "original_page_num": page,
                "entity_type": "thematic_passage",
                "content_type": "narrative_text",
                "extracted_text": text,
                "matched_keywords": hits,
                "spatial_units": spatial_units,
                "temporal_period": "",
                "bbox": _bbox(element.get("bbox")),
                "confidence": _ddb_safe(element.get("confidence")),
                "custom_attributes": {},
            }
            if districts:
                attributes["district"] = districts[0]
            yield theme, f"P{page:02d}_{index:04d}", attributes


def ingest_document(document_id, s3_bucket, ocr_s3_key, s3_ocr_json_url=None):
    """Load OCR JSON from S3, upsert metadata, then write extracted entities."""
    s3 = boto3.client("s3", region_name=AWS_REGION)
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
            ":status": "processing_entities",
        },
    )
    written = 0
    try:
        for theme, entity_id, attributes in build_entities(document_id, ocr):
            crop_key = attributes.get("s3_crop_url")
            if crop_key:
                attributes["s3_crop_url"] = f"s3://{s3_bucket}/{crop_key}"
            entity_district = attributes.pop("district", None)
            record = {"filename": filename, **attributes}
            if entity_district:
                record["district"] = entity_district
            _put_entity(document_id, theme, entity_id, record)
            written += 1
    except Exception:
        OCR_TABLE.update_item(
            Key={"document_id": document_id},
            UpdateExpression="SET ingestion_status = :status",
            ExpressionAttributeValues={":status": "failed"},
        )
        raise
    OCR_TABLE.update_item(
        Key={"document_id": document_id},
        UpdateExpression="SET ingestion_status = :status, extracted_entity_count = :count",
        ExpressionAttributeValues={":status": "completed", ":count": written},
    )
    return {"document_id": document_id, **counts, "extracted_entity_count": written}


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
    """API Gateway entry point for POST /api/documents/ingest-dynamo."""
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
        result = ingest_document(
            document_id,
            item["s3_bucket"],
            item["ocr_s3_key"],
            item.get("s3_ocr_json_url"),
        )
        return _response(200, result)
    except Exception:
        LOGGER.exception("DynamoDB ingestion failed")
        return _response(500, {"detail": "DynamoDB ingestion failed"})
