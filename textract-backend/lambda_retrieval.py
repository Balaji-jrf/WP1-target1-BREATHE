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

import boto3

LOGGER       = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

DYNAMO_TABLE = os.environ.get("DYNAMO_TABLE", "DocumentOCR")

CONTENT_TYPES = {
    ".pdf":  "application/pdf",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif":  "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}

dynamo = boto3.resource("dynamodb", region_name="ap-south-2")
table  = dynamo.Table(DYNAMO_TABLE)
s3     = boto3.client("s3", region_name="ap-south-2",
                      endpoint_url="https://s3.ap-south-2.amazonaws.com")


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin":  os.environ.get("ALLOWED_ORIGIN", "*"),
        "Access-Control-Allow-Methods": "GET,OPTIONS",
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
                                "uploaded_at, #st, file_size_bytes, processing_time_seconds",
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

    return item


def handler(event: dict, context) -> dict:
    rc          = event.get("requestContext") or {}
    method      = rc.get("http", {}).get("method", "").upper()
    raw_path    = event.get("rawPath", "")
    path_params = event.get("pathParameters") or {}

    if method == "OPTIONS":
        return _response(200, {})

    LOGGER.info("method=%s path=%s", method, raw_path)

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
