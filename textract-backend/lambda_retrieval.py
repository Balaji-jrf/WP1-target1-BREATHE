"""
Retrieval Lambda
----------------
GET /documents              → list all documents (scan DynamoDB, metadata only)
GET /document/{document_id} → fetch single document with full OCR result
"""

from __future__ import annotations

import json
import logging
import os

import boto3
from boto3.dynamodb.conditions import Attr

LOGGER       = logging.getLogger(__name__)
DYNAMO_TABLE = os.environ.get("DYNAMO_TABLE", "DocumentOCR")

dynamo = boto3.resource("dynamodb", region_name="ap-south-2")
table  = dynamo.Table(DYNAMO_TABLE)


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


def _response(status: int, body) -> dict:
    return {
        "statusCode": status,
        "headers": {**_cors_headers(), "Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _list_documents() -> dict:
    """Scan table returning only metadata fields — no full OCR payload."""
    items = []
    kwargs = {
        "ProjectionExpression": "document_id, filename, total_pages, element_count, uploaded_at, #st",
        "ExpressionAttributeNames": {"#st": "status"},
    }
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    # Sort newest first by uploaded_at
    items.sort(key=lambda x: x.get("uploaded_at", ""), reverse=True)
    return {"documents": items, "count": len(items)}


def _get_document(document_id: str) -> dict:
    item = table.get_item(Key={"document_id": document_id}).get("Item")
    if not item:
        return None
    # Generate a presigned S3 URL valid for 1 hour so frontend can display the file
    try:
        s3 = boto3.client("s3", region_name="ap-south-2")
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": item["s3_bucket"], "Key": item["s3_key"]},
            ExpiresIn=3600,
        )
        item["s3_presigned_url"] = url
    except Exception:
        LOGGER.warning("Could not generate presigned URL for %s", document_id)
    return item


def handler(event: dict, context) -> dict:
    if event.get("httpMethod") == "OPTIONS":
        return _response(200, {})

    path_params = event.get("pathParameters") or {}
    route_key   = event.get("routeKey", "")
    raw_path    = event.get("rawPath", "")

    LOGGER.info("route=%s path=%s params=%s", route_key, raw_path, path_params)

    # GET /documents — list all
    if raw_path.rstrip("/").endswith("/documents") or route_key == "GET /documents":
        try:
            return _response(200, _list_documents())
        except Exception:
            LOGGER.exception("DynamoDB scan failed")
            return _response(500, {"detail": "Failed to list documents"})

    # GET /document/{document_id} — single
    document_id = path_params.get("document_id", "").strip()
    if not document_id:
        return _response(400, {"detail": "document_id path parameter is required"})

    try:
        item = _get_document(document_id)
    except Exception:
        LOGGER.exception("DynamoDB get_item failed for %s", document_id)
        return _response(500, {"detail": "Failed to retrieve document"})

    if not item:
        return _response(404, {"detail": f"No document found with id: {document_id}"})

    return _response(200, item)
