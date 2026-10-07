"""
Retrieval Lambda
----------------
GET /documents              → list all documents (scan DynamoDB, metadata only)
GET /document/{document_id} → fetch single document; if still processing, checks
                              Textract job and finalises the record on the fly
"""

from __future__ import annotations

import json
import logging
import os
from decimal import Decimal

import boto3

LOGGER       = logging.getLogger(__name__)
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

dynamo   = boto3.resource("dynamodb", region_name="ap-south-2")
table    = dynamo.Table(DYNAMO_TABLE)
textract = boto3.client("textract", region_name=os.environ.get("TEXTRACT_REGION", "ap-south-1"))
s3       = boto3.client("s3", region_name="ap-south-2",
                        endpoint_url="https://s3.ap-south-2.amazonaws.com")


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
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
        "ProjectionExpression": "document_id, filename, total_pages, element_count, uploaded_at, #st",
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


def _blocks_to_elements(blocks: list) -> list[dict]:
    elements = []
    for block in blocks:
        if block["BlockType"] != "LINE":
            continue
        bb = block["Geometry"]["BoundingBox"]
        elements.append({
            "page":       block.get("Page", 1),
            "text":       block["Text"],
            "confidence": Decimal(str(round(block["Confidence"] / 100, 4))),
            "bbox": [
                Decimal(str(round(bb["Left"], 4))),
                Decimal(str(round(bb["Top"], 4))),
                Decimal(str(round(bb["Left"] + bb["Width"], 4))),
                Decimal(str(round(bb["Top"] + bb["Height"], 4))),
            ],
        })
    return elements


def _check_and_finish_job(item: dict) -> dict:
    """Poll Textract once; if done, write completed record to DynamoDB and return updated item."""
    job_id = item.get("textract_job_id")
    if not job_id:
        return item

    result = textract.get_document_text_detection(JobId=job_id)
    job_status = result["JobStatus"]

    if job_status == "SUCCEEDED":
        blocks = result["Blocks"]
        while "NextToken" in result:
            result = textract.get_document_text_detection(JobId=job_id, NextToken=result["NextToken"])
            blocks.extend(result["Blocks"])
        elements  = _blocks_to_elements(blocks)
        ocr_result = {
            "filename":    item["filename"],
            "total_pages": max((e["page"] for e in elements), default=1),
            "elements":    elements,
        }
        table.put_item(Item={
            **{k: v for k, v in item.items() if k != "textract_job_id"},
            "total_pages":   ocr_result["total_pages"],
            "element_count": len(elements),
            "status":        "completed",
            "ocr_result":    ocr_result,
        })
        return {**item, "status": "completed", "ocr_result": ocr_result,
                "total_pages": ocr_result["total_pages"], "element_count": len(elements)}

    if job_status == "FAILED":
        table.update_item(
            Key={"document_id": item["document_id"]},
            UpdateExpression="SET #st = :s",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":s": "failed"},
        )
        return {**item, "status": "failed"}

    return item  # still IN_PROGRESS


def _get_document(document_id: str) -> dict | None:
    item = table.get_item(Key={"document_id": document_id}).get("Item")
    if not item:
        return None

    # If still processing, check Textract and finalise if done
    if item.get("status") == "processing":
        try:
            item = _check_and_finish_job(item)
        except Exception:
            LOGGER.warning("Could not check Textract job for %s", document_id)

    # Generate presigned URL for display
    ext          = os.path.splitext(item.get("filename", "").lower())[1]
    content_type = CONTENT_TYPES.get(ext, "application/octet-stream")
    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": item["s3_bucket"],
                "Key":    item["s3_key"],
                "ResponseContentType":        content_type,
                "ResponseContentDisposition": "inline",
            },
            ExpiresIn=3600,
        )
        item["s3_presigned_url"] = url
    except Exception:
        LOGGER.warning("Could not generate presigned URL for %s", document_id)

    return item


def handler(event: dict, context) -> dict:
    rc          = event.get("requestContext") or {}
    method      = rc.get("http", {}).get("method", "").upper()
    raw_path    = event.get("rawPath", "")
    path_params = event.get("pathParameters") or {}

    if method == "OPTIONS":
        return _response(200, {})

    LOGGER.info("method=%s path=%s params=%s", method, raw_path, path_params)

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
        LOGGER.exception("DynamoDB get_item failed for %s", document_id)
        return _response(500, {"detail": "Failed to retrieve document"})

    if not item:
        return _response(404, {"detail": f"No document found with id: {document_id}"})

    return _response(200, item)
