"""
Retrieval Lambda
----------------
GET /document/{document_id}
Fetches OCR result and metadata from DynamoDB.
"""

from __future__ import annotations

import json
import logging
import os

import boto3

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


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {**_cors_headers(), "Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def handler(event: dict, context) -> dict:
    if event.get("httpMethod") == "OPTIONS":
        return _response(200, {})

    path_params = event.get("pathParameters") or {}
    document_id = path_params.get("document_id", "").strip()

    if not document_id:
        return _response(400, {"detail": "document_id path parameter is required"})

    try:
        item = table.get_item(Key={"document_id": document_id}).get("Item")
    except Exception:
        LOGGER.exception("DynamoDB get_item failed for %s", document_id)
        return _response(500, {"detail": "Failed to retrieve document"})

    if not item:
        return _response(404, {"detail": f"No document found with id: {document_id}"})

    return _response(200, item)
