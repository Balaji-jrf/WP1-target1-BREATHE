"""
Retrieval Lambda
----------------
GET /documents              → list all documents (metadata only)
GET /document/{document_id} → metadata + presigned doc URL + OCR JSON from S3

Pure reader — never calls Textract. All processing is done by the starter Lambda.
Every response is fast (~100ms): DynamoDB get + S3 get + presigned URL.
"""

from __future__ import annotations

import json
import logging
import os
import re
from decimal import Decimal

import boto3
from botocore.config import Config
from boto3.dynamodb.conditions import Key

LOGGER       = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

DYNAMO_TABLE = os.environ.get("DYNAMO_TABLE", "jrf-task-ocr-DocumentOCR")
ENTITIES_TABLE_NAME = os.environ.get(
    "ENTITIES_TABLE", "jrf-task-data-Extraction-entities"
)
AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")

CONTENT_TYPES = {
    ".pdf":  "application/pdf",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif":  "image/tiff",
    ".tiff": "image/tiff",
}

dynamo = boto3.resource("dynamodb", region_name=AWS_REGION)
table  = dynamo.Table(DYNAMO_TABLE)
entities_table = dynamo.Table(ENTITIES_TABLE_NAME)
s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    endpoint_url=f"https://s3.{AWS_REGION}.amazonaws.com",
    config=Config(signature_version="s3v4"),
)


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin":  os.environ.get("ALLOWED_ORIGIN", "*"),
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "content-type",
    }


def _response(status: int, body) -> dict:
    return {
        "statusCode": status,
        "headers": {**_cors_headers(), "Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _list_documents() -> dict:
    items, kwargs = [], {
        "ProjectionExpression": "document_id, filename, total_pages, element_count, "
                                "text_blocks_count, layout_regions_count, table_count, "
                                "figure_count, uploaded_at, created_at, document_name, #st, "
                                "file_size_bytes, processing_time_seconds, ingestion_status, "
                                "extracted_entity_count, record_type",
        "ExpressionAttributeNames": {"#st": "status"},
    }
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    items.sort(key=lambda x: x.get("uploaded_at", ""), reverse=True)
    return {"documents": items, "count": len(items)}


def _get_document(document_id: str) -> dict | None:
    item = table.get_item(Key={"document_id": document_id}).get("Item")
    if not item:
        return None

    # Generate presigned URL for the original document file
    ext          = os.path.splitext(item.get("filename", "").lower())[1]
    content_type = CONTENT_TYPES.get(ext, "application/octet-stream")
    try:
        item["s3_presigned_url"] = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket":                     item["s3_bucket"],
                "Key":                        item["s3_key"],
                "ResponseContentType":        content_type,
                "ResponseContentDisposition": "inline",
            },
            ExpiresIn=3600,
        )
    except Exception:
        LOGGER.warning("Could not generate presigned URL for %s", document_id)

    # If completed, return a presigned URL for the ocr.json — never inline elements
    # (avoids API Gateway 6MB response limit for large documents)
    if item.get("status") == "completed" and item.get("ocr_s3_key"):
        try:
            item["ocr_presigned_url"] = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": item["s3_bucket"], "Key": item["ocr_s3_key"]},
                ExpiresIn=3600,
            )
        except Exception:
            LOGGER.warning("Could not generate OCR presigned URL for %s", document_id)

        try:
            ocr_object = s3.get_object(
                Bucket=item["s3_bucket"],
                Key=item["ocr_s3_key"],
            )
            ocr_data = json.loads(ocr_object["Body"].read())
            item["figure_presigned_urls"] = {
                figure["image_s3_key"]: s3.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": item["s3_bucket"],
                        "Key": figure["image_s3_key"],
                    },
                    ExpiresIn=3600,
                )
                for figure in ocr_data.get("figures", [])
                if figure.get("crop_status") == "saved" and figure.get("image_s3_key")
            }
        except Exception:
            LOGGER.exception("Could not generate figure URLs for %s", document_id)
            item["figure_presigned_url_error"] = (
                "Figure previews are temporarily unavailable."
            )

    return item


def _get_extractions(document_id: str, params: dict) -> dict:
    kwargs = {"KeyConditionExpression": Key("id").eq(f"DOC#{document_id}")}
    response = entities_table.query(**kwargs)
    items = response.get("Items", [])
    while response.get("LastEvaluatedKey"):
        response = entities_table.query(
            **kwargs, ExclusiveStartKey=response["LastEvaluatedKey"]
        )
        items.extend(response.get("Items", []))
    for field in ("theme", "district", "content_type"):
        wanted = (params.get(field) or "").strip()
        if wanted:
            items = [item for item in items if str(item.get(field, "")).casefold() == wanted.casefold()]
    items.sort(key=lambda item: item.get("theme_entity_id", ""))
    return {"document_id": document_id, "count": len(items), "extractions": items}


def _parse_body(event):
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode("utf-8")
    return json.loads(body) if isinstance(body, str) else body


def _ddb_safe(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [_ddb_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _ddb_safe(item) for key, item in value.items()}
    return value


def _mutate_extraction(document_id, body):
    action = (body.get("action") or "upsert").lower()
    theme_entity_id = (body.get("theme_entity_id") or "").strip()
    if not theme_entity_id:
        return 400, {"detail": "theme_entity_id is required"}
    key = {"id": f"DOC#{document_id}", "theme_entity_id": theme_entity_id}
    if action == "delete":
        entities_table.delete_item(Key=key)
        return 200, {"deleted": True, "theme_entity_id": theme_entity_id}
    if action not in {"create", "update", "upsert"}:
        return 400, {"detail": "action must be create, update, upsert, or delete"}
    attributes = body.get("attributes") or {}
    if not isinstance(attributes, dict):
        return 400, {"detail": "attributes must be a JSON object"}
    item = {
        **key,
        "document_id": document_id,
        "theme": str(body.get("theme") or attributes.get("theme") or "Custom"),
        **attributes,
    }
    item["id"] = key["id"]
    item["theme_entity_id"] = theme_entity_id
    item["document_id"] = document_id
    if action == "create":
        existing = entities_table.get_item(Key=key).get("Item")
        if existing:
            return 409, {"detail": "An extraction with this theme_entity_id already exists"}
    if action == "update":
        existing = entities_table.get_item(Key=key).get("Item")
        if not existing:
            return 404, {"detail": "Extraction not found"}
        item = {**existing, **item}
    entities_table.put_item(Item=_ddb_safe(item))
    return 200, {"saved": True, "extraction": item}


def _add_custom_attribute(document_id, body):
    theme_entity_id = (body.get("theme_entity_id") or "").strip()
    name = (body.get("key") or "").strip()
    if not theme_entity_id or not name:
        return 400, {"detail": "theme_entity_id and key are required"}
    key = {"id": f"DOC#{document_id}", "theme_entity_id": theme_entity_id}
    result = entities_table.get_item(Key=key).get("Item")
    if not result:
        return 404, {"detail": "Extraction not found"}
    custom = result.get("custom_attributes") or {}
    custom[name] = body.get("value")
    result["custom_attributes"] = custom
    entities_table.put_item(Item=_ddb_safe(result))
    return 200, {"saved": True, "custom_attributes": custom}


def handler(event: dict, context) -> dict:
    rc          = event.get("requestContext") or {}
    method      = rc.get("http", {}).get("method", "").upper()
    raw_path    = event.get("rawPath", "")
    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}

    if method == "OPTIONS":
        return _response(200, {})

    LOGGER.info("method=%s path=%s", method, raw_path)

    extraction_match = re.search(r"/api/database/extractions/([^/]+)/?$", raw_path)
    if extraction_match:
        document_id = path_params.get("document_id") or extraction_match.group(1)
        if method == "GET":
            try:
                return _response(200, _get_extractions(document_id, query_params))
            except Exception:
                LOGGER.exception("Failed to query extractions for %s", document_id)
                return _response(500, {"detail": "Failed to query extractions"})
        if method == "POST":
            try:
                status, body = _mutate_extraction(document_id, _parse_body(event))
                return _response(status, body)
            except Exception:
                LOGGER.exception("Failed to update extractions for %s", document_id)
                return _response(500, {"detail": "Failed to update extraction"})

    if raw_path.rstrip("/").endswith("/api/database/custom-attribute") and method == "POST":
        document_id = ""
        try:
            body_payload = _parse_body(event)
            document_id = (body_payload.get("document_id") or "").strip()
            if not document_id:
                return _response(400, {"detail": "document_id is required"})
            status, body = _add_custom_attribute(document_id, body_payload)
            return _response(status, body)
        except Exception:
            LOGGER.exception("Failed to add custom attribute for %s", document_id)
            return _response(500, {"detail": "Failed to save custom attribute"})

    if raw_path.rstrip("/").endswith("/api/documents/ingest-dynamo") and method == "POST":
        try:
            body = _parse_body(event)
            document_id = (body.get("document_id") or "").strip()
            if not document_id:
                return _response(400, {"detail": "document_id is required"})
            item = table.get_item(Key={"document_id": document_id}).get("Item")
            if not item:
                return _response(404, {"detail": "Document not found"})
            if item.get("status") != "completed" or not item.get("ocr_s3_key"):
                return _response(409, {"detail": "OCR processing is not complete"})
            from dynamo_ingestion import ingest_document
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

    if raw_path.rstrip("/").endswith("/documents"):
        try:
            return _response(200, _list_documents())
        except Exception:
            LOGGER.exception("DynamoDB scan failed")
            return _response(500, {"detail": "Failed to list documents"})

    document_id = path_params.get("document_id", "").strip()
    if not document_id:
        return _response(400, {"detail": "document_id path parameter is required"})

    try:
        item = _get_document(document_id)
    except Exception:
        LOGGER.exception("Failed to retrieve document %s", document_id)
        return _response(500, {"detail": "Failed to retrieve document"})

    if not item:
        return _response(404, {"detail": f"No document found with id: {document_id}"})

    return _response(200, item)
