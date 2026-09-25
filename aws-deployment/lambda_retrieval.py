"""
Retrieval Lambda
----------------
Triggered by API Gateway GET /document/{document_id}.
Fetches the OCR result and metadata from DynamoDB (DocumentOCR table).
"""

from __future__ import annotations

import json
import logging
import os

import boto3
from boto3.dynamodb.conditions import Key

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

DYNAMO_TABLE = os.environ.get("DYNAMO_TABLE", "DocumentOCR")

dynamo = boto3.resource("dynamodb")
table = dynamo.Table(DYNAMO_TABLE)


def _cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {**_cors_headers(), "Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event: dict, context) -> dict:
    if event.get("httpMethod") == "OPTIONS":
        return _response(200, {})

    path_params = event.get("pathParameters") or {}
    document_id = path_params.get("document_id", "").strip()

    if not document_id:
        return _response(400, {"detail": "document_id path parameter is required"})

    try:
        response = table.get_item(Key={"document_id": document_id})
    except Exception as exc:
        LOGGER.exception("DynamoDB get_item failed for %s", document_id)
        return _response(500, {"detail": "Failed to retrieve document"})

    item = response.get("Item")
    if not item:
        return _response(404, {"detail": f"No document found with id: {document_id}"})

    LOGGER.info("Retrieved document: %s", document_id)
    return _response(200, item)
