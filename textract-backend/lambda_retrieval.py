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
import base64
import hashlib
import hmac
import time
from decimal import Decimal
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from botocore.config import Config
from boto3.dynamodb.conditions import Attr, Key

LOGGER       = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

DYNAMO_TABLE = os.environ.get("DYNAMO_TABLE", "jrf-task-ocr-DocumentOCR")
EXTRACTED_RECORDS_TABLE_NAME = os.environ.get(
    "EXTRACTED_RECORDS_TABLE", "jrf-task-ocr-ExtractedRecords"
)
TAXONOMY_TABLE_NAME = os.environ.get(
    "TAXONOMY_TABLE", "jrf-task-data-EntityTaxonomy"
)
AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
JWT_SECRET = os.environ.get("JWT_SECRET", "breathe-secret-change-me")
USERS_TABLE_NAME = os.environ.get("USERS_TABLE", "jrf-task-BreatheUsers")

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
records_table = dynamo.Table(EXTRACTED_RECORDS_TABLE_NAME)
taxonomy_table = dynamo.Table(TAXONOMY_TABLE_NAME)
users_table = dynamo.Table(USERS_TABLE_NAME)
s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    endpoint_url=f"https://s3.{AWS_REGION}.amazonaws.com",
    config=Config(signature_version="s3v4"),
)


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin":  os.environ.get("ALLOWED_ORIGIN", "*"),
        "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
        "Access-Control-Allow-Headers": "content-type,authorization",
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
                                "extracted_entity_count, pending_review_count, ingestion_error, record_type, "
                                "doc_name, doc_tag",
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


def _get_document_records(document_id: str, params: dict) -> dict:
    try:
        limit = max(1, min(int(params.get("limit", 50)), 100))
    except (TypeError, ValueError):
        limit = 50

    filter_expression = None
    for field in ("theme", "content_type", "entity_type"):
        wanted = (params.get(field) or "").strip()
        if wanted:
            if field == "entity_type" and wanted == "user_saved":
                condition = (
                    Attr(field).eq("user_saved")
                    | Attr(field).eq("manual_fact")
                )
            else:
                condition = Attr(field).eq(wanted)
            filter_expression = condition if filter_expression is None else filter_expression & condition

    page = (params.get("pdf_page_num") or "").strip()
    if page:
        try:
            condition = Attr("pdf_page_num").eq(int(page))
        except ValueError:
            condition = Attr("pdf_page_num").eq(page)
        filter_expression = condition if filter_expression is None else filter_expression & condition

    district = (params.get("district") or "").strip()
    if district:
        condition = Attr("spatial_units.district").eq(district) | Attr("district").eq(district)
        filter_expression = condition if filter_expression is None else filter_expression & condition

    location = (params.get("location") or "").strip()
    if location:
        # Case-insensitive: store lowercase variants and search lowercase
        loc_lower = location.lower()
        loc_title = location.title()
        loc_upper = location.upper()
        condition = (
            Attr("spatial_units.district").contains(location)
            | Attr("spatial_units.district").contains(loc_lower)
            | Attr("spatial_units.district").contains(loc_title)
            | Attr("spatial_units.taluk").contains(location)
            | Attr("spatial_units.taluk").contains(loc_lower)
            | Attr("spatial_units.taluk").contains(loc_title)
            | Attr("spatial_units.village").contains(location)
            | Attr("spatial_units.village").contains(loc_lower)
            | Attr("spatial_units.village").contains(loc_title)
            | Attr("spatial_units.town").contains(location)
            | Attr("spatial_units.town").contains(loc_lower)
            | Attr("spatial_units.town").contains(loc_title)
            | Attr("district").contains(location)
            | Attr("district").contains(loc_lower)
            | Attr("district").contains(loc_title)
            | Attr("source_text_chunk").contains(location)
            | Attr("source_text_chunk").contains(loc_title)
            | Attr("extracted_fact").contains(location)
            | Attr("extracted_fact").contains(loc_title)
        )
        filter_expression = condition if filter_expression is None else filter_expression & condition

    status = (params.get("status") or "all").strip().casefold()
    if status != "all":
        condition = Attr("review_status").eq(status)
        filter_expression = condition if filter_expression is None else filter_expression & condition

    start_key = None
    token = (params.get("cursor") or "").strip()
    if token:
        try:
            start_key = json.loads(base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Invalid records pagination cursor") from error

    items = []
    last_key = start_key
    while len(items) < limit:
        kwargs = {
            "KeyConditionExpression": Key("id").eq(f"DOC#{document_id}"),
            "Limit": limit - len(items),
        }
        if filter_expression is not None:
            kwargs["FilterExpression"] = filter_expression
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        response = records_table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break

    next_token = base64.urlsafe_b64encode(
        json.dumps(last_key).encode("utf-8")
    ).decode("ascii") if last_key else None
    return {
        "document_id": document_id,
        "count": len(items),
        "records": items,
        "next_token": next_token,
    }


def _count_document_records(document_id: str, review_status: str) -> int:
    count = 0
    kwargs = {
        "KeyConditionExpression": Key("id").eq(f"DOC#{document_id}"),
        "FilterExpression": Attr("review_status").eq(review_status),
        "Select": "COUNT",
    }
    while True:
        response = records_table.query(**kwargs)
        count += response.get("Count", 0)
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return count
        kwargs["ExclusiveStartKey"] = last_key


def _list_taxonomy():
    items, kwargs = [], {}
    while True:
        response = taxonomy_table.scan(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    items.sort(key=lambda item: item.get("entity_key", ""))
    return {"count": len(items), "taxonomy": items}


def _save_taxonomy(body):
    entity_key = (body.get("entity_key") or "").strip().upper()
    display_name = (body.get("display_name") or "").strip()
    description = (body.get("description") or "").strip()
    keywords = body.get("keywords") or []
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", entity_key):
        return 400, {"detail": "entity_key must use 2-64 uppercase letters, numbers, or underscores"}
    if not display_name or not description:
        return 400, {"detail": "display_name and description are required"}
    if not isinstance(keywords, list) or any(not isinstance(word, str) for word in keywords):
        return 400, {"detail": "keywords must be a list of strings"}
    item = {
        "entity_key": entity_key,
        "display_name": display_name,
        "description": description,
        "keywords": [word.strip() for word in keywords if word.strip()],
        "extraction_guidance": (body.get("extraction_guidance") or "").strip(),
        "is_active": bool(body.get("is_active", True)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    taxonomy_table.put_item(Item=item)
    return 200, {"saved": True, "taxonomy": item}


def _delete_taxonomy(entity_key):
    entity_key = entity_key.strip().upper()
    if not entity_key:
        return 400, {"detail": "entity_key is required"}
    taxonomy_table.delete_item(Key={"entity_key": entity_key})
    return 200, {"deleted": True, "entity_key": entity_key}


def _update_record(document_id, theme_entity_id, body):
    key = {"id": f"DOC#{document_id}", "theme_entity_id": theme_entity_id}
    current = records_table.get_item(Key=key).get("Item")
    if not current:
        return 404, {"detail": "Record not found"}
    attributes = body.get("attributes", body)
    if not isinstance(attributes, dict):
        return 400, {"detail": "attributes must be a JSON object"}
    allowed = {
        "theme", "entity_type", "content_type", "extracted_fact", "spatial_units",
        "temporal_period", "confidence_score", "source_text_chunk", "bbox",
        "custom_attributes", "pdf_page_num", "original_page_num", "geotag",
    }
    updates = {name: value for name, value in attributes.items() if name in allowed}
    if not updates:
        return 400, {"detail": "No editable record fields were provided"}
    if "geotag" in updates:
        validation_error = _validate_geotags(updates["geotag"])
        if validation_error:
            return 400, {"detail": validation_error}
    updated = {**current, **_ddb_safe(updates)}
    if "spatial_units" in updates:
        district = (updates.get("spatial_units") or {}).get("district")
        if district:
            updated["district"] = district
        else:
            updated.pop("district", None)
    records_table.put_item(Item=updated)
    return 200, {"saved": True, "record": updated}


def _validate_geotags(geotags):
    if not isinstance(geotags, list) or len(geotags) > 50:
        return "geotag must be a list with no more than 50 places"
    allowed_fields = {
        "place_name", "place_type", "label", "latitude", "longitude",
        "provider", "place_id", "relevance", "status", "updated_at",
    }
    for geotag in geotags:
        if not isinstance(geotag, dict):
            return "Each geotag must be an object"
        if set(geotag) - allowed_fields:
            return "Geotag contains unsupported fields"
        place_name = geotag.get("place_name")
        place_type = geotag.get("place_type")
        if not isinstance(place_name, str) or not 1 <= len(place_name.strip()) <= 160:
            return "Each geotag requires a place_name of at most 160 characters"
        if not isinstance(place_type, str) or not 1 <= len(place_type.strip()) <= 60:
            return "Each geotag requires a place_type of at most 60 characters"
        if not isinstance(geotag.get("label"), str) or not 1 <= len(geotag["label"]) <= 300:
            return "Each geotag requires a label of at most 300 characters"
        if geotag.get("provider") != "Amazon Location Service":
            return "Each geotag must identify Amazon Location Service as its provider"
        if geotag.get("status") not in {"needs_review", "confirmed"}:
            return "Geotag status must be needs_review or confirmed"
        if not isinstance(geotag.get("updated_at"), str) or len(geotag["updated_at"]) > 80:
            return "Each geotag requires an updated_at timestamp"
        for field, minimum, maximum in (
            ("latitude", -90, 90),
            ("longitude", -180, 180),
        ):
            value = geotag.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
                return f"Geotag {field} must be a number"
            if not minimum <= value <= maximum:
                return f"Geotag {field} is out of range"
        relevance = geotag.get("relevance")
        if (
            isinstance(relevance, bool)
            or not isinstance(relevance, (int, float, Decimal))
            or not 0 <= relevance <= 1
        ):
            return "Geotag relevance must be between 0 and 1"
        if (
            not isinstance(geotag.get("place_id"), str)
            or not 1 <= len(geotag["place_id"].strip()) <= 200
        ):
            return "Each geotag requires an Amazon Location place_id"
    return None


def _delete_record(document_id, theme_entity_id):
    key = {"id": f"DOC#{document_id}", "theme_entity_id": theme_entity_id}
    result = records_table.delete_item(Key=key, ReturnValues="ALL_OLD")
    if not result.get("Attributes"):
        return 404, {"detail": "Record not found"}
    remaining = _count_document_records(document_id, "pending_review")
    table.update_item(
        Key={"document_id": document_id},
        UpdateExpression="SET ingestion_status = :status, pending_review_count = :remaining",
        ExpressionAttributeValues={
            ":status": "pending_review" if remaining else "completed",
            ":remaining": remaining,
        },
    )
    return 200, {"deleted": True, "theme_entity_id": theme_entity_id}


def _approve_document_records(document_id, body):
    document_id = (document_id or "").strip()
    if not document_id or not isinstance(body, dict):
        return 400, {"detail": "A document ID and approval request are required"}
    if body.get("approve_all") is True:
        entity_ids, last_key = [], None
        while True:
            kwargs = {
                "KeyConditionExpression": Key("id").eq(f"DOC#{document_id}"),
                "FilterExpression": Attr("review_status").eq("pending_review"),
                "ProjectionExpression": "theme_entity_id",
            }
            if last_key:
                kwargs["ExclusiveStartKey"] = last_key
            response = records_table.query(**kwargs)
            entity_ids.extend(
                item["theme_entity_id"]
                for item in response.get("Items", [])
                if item.get("theme_entity_id")
            )
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
    else:
        entity_ids = body.get("approved_entity_ids")
        if not isinstance(entity_ids, list) or not entity_ids:
            return 400, {"detail": "A non-empty approved_entity_ids list is required"}
    approved_at = datetime.now(timezone.utc).isoformat()
    approved = 0
    for entity_id in dict.fromkeys(str(value).strip() for value in entity_ids if str(value).strip()):
        try:
            result = records_table.update_item(
                Key={"id": f"DOC#{document_id}", "theme_entity_id": entity_id},
                UpdateExpression="SET review_status = :status, approved_at = :approved_at",
                ConditionExpression="review_status = :pending",
                ExpressionAttributeValues={
                    ":status": "approved",
                    ":pending": "pending_review",
                    ":approved_at": approved_at,
                },
                ReturnValues="UPDATED_NEW",
            )
            approved += 1 if result.get("Attributes") else 0
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
    remaining = _count_document_records(document_id, "pending_review")
    document_status = "pending_review" if remaining else "completed"
    table.update_item(
        Key={"document_id": document_id},
        UpdateExpression=(
            "SET ingestion_status = :status, pending_review_count = :remaining"
        ),
        ExpressionAttributeValues={
            ":status": document_status,
            ":remaining": remaining,
        },
    )
    return 200, {
        "document_id": document_id,
        "approved_count": approved,
        "pending_review_count": remaining,
        "ingestion_status": document_status,
    }


def _authenticated_user(event):
    def reject(status, detail):
        return None, (status, {"detail": detail})

    headers = event.get("headers") or {}
    authorization = next(
        (
            value for key, value in headers.items()
            if isinstance(key, str) and key.lower() == "authorization"
        ),
        "",
    )
    if not isinstance(authorization, str) or len(authorization) > 8192:
        return reject(401, "Invalid authentication token")
    if not authorization.lower().startswith("bearer "):
        return reject(401, "Authentication required")
    parts = authorization[7:].strip().split(".")
    if len(parts) != 3:
        return reject(401, "Invalid authentication token")
    header, body, signature = parts
    if not re.fullmatch(r"[A-Za-z0-9_-]+", signature):
        return reject(401, "Invalid authentication token")
    expected = base64.urlsafe_b64encode(
        hmac.new(
            JWT_SECRET.encode(),
            f"{header}.{body}".encode(),
            hashlib.sha256,
        ).digest()
    ).rstrip(b"=").decode()
    if not hmac.compare_digest(signature, expected):
        return reject(401, "Invalid authentication token")
    try:
        jwt_header = json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return reject(401, "Invalid authentication token")
    expiration = payload.get("exp") if isinstance(payload, dict) else None
    if (
        not isinstance(jwt_header, dict)
        or jwt_header.get("alg") != "HS256"
        or not isinstance(expiration, (int, float))
        or expiration < int(time.time())
    ):
        return reject(401, "Invalid or expired authentication token")
    username = payload.get("username")
    if not isinstance(username, str):
        return reject(401, "Invalid authentication token")
    username = username.strip().lower()
    if not username:
        return reject(401, "Invalid authentication token")
    current_user = users_table.get_item(Key={"username": username}).get("Item")
    if not current_user:
        return None, (401, {"detail": "User account is unavailable"})
    return current_user, None


def _admin_auth_error(event):
    current_user, auth_error = _authenticated_user(event)
    if auth_error:
        return auth_error
    if current_user.get("role") != "admin":
        return 403, {"detail": "Admin access required"}
    return None


def _atlas_auth_error(event):
    current_user, auth_error = _authenticated_user(event)
    if auth_error:
        return auth_error
    permissions = current_user.get("permissions") or []
    if not isinstance(permissions, list):
        permissions = []
    if current_user.get("role") != "admin" and "atlas" not in permissions:
        return 403, {"detail": "Atlas access required"}
    return None


def _delete_document(document_id):
    document_id = (document_id or "").strip()
    if not document_id:
        return 400, {"detail": "document_id is required"}
    document = table.get_item(Key={"document_id": document_id}).get("Item")
    if not document:
        return 404, {"detail": "Document not found"}
    if document.get("status") == "processing" or document.get("ingestion_status") == "processing":
        return 409, {"detail": "Cannot delete a document while OCR or AI extraction is running"}
    bucket = document.get("s3_bucket")
    if not bucket:
        return 409, {"detail": "Document storage details are missing; no data was deleted"}

    record_keys, last_key = [], None
    while True:
        kwargs = {
            "KeyConditionExpression": Key("id").eq(f"DOC#{document_id}"),
            "ProjectionExpression": "id, theme_entity_id",
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        response = records_table.query(**kwargs)
        record_keys.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break

    object_keys = {
        key for key in (document.get("s3_key"), document.get("ocr_s3_key"))
        if isinstance(key, str) and key
    }
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"processed/{document_id}/"):
        object_keys.update(
            item["Key"] for item in page.get("Contents", []) if item.get("Key")
        )
    object_key_list = sorted(object_keys)
    for start in range(0, len(object_key_list), 1000):
        response = s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in object_key_list[start:start + 1000]]},
        )
        if response.get("Errors"):
            LOGGER.error(
                "S3 document cleanup failed for %s (%d object errors)",
                document_id,
                len(response["Errors"]),
            )
            raise RuntimeError("One or more document files could not be deleted")

    with records_table.batch_writer() as batch:
        for key in record_keys:
            batch.delete_item(Key={
                "id": key["id"],
                "theme_entity_id": key["theme_entity_id"],
            })
    table.delete_item(Key={"document_id": document_id})
    return 200, {
        "deleted": True,
        "document_id": document_id,
        "deleted_record_count": len(record_keys),
        "deleted_file_count": len(object_keys),
    }


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


def _create_record(document_id, body):
    theme_entity_id = (body.get("theme_entity_id") or "").strip()
    if not theme_entity_id:
        return 400, {"detail": "theme_entity_id is required"}
    key = {"id": f"DOC#{document_id}", "theme_entity_id": theme_entity_id}
    attributes = body.get("attributes") or {
        name: value for name, value in body.items()
        if name not in {"theme_entity_id", "action"}
    }
    if not isinstance(attributes, dict):
        return 400, {"detail": "attributes must be a JSON object"}
    item = {
        **key,
        "document_id": document_id,
        "theme": str(body.get("theme") or attributes.get("theme") or "Custom"),
        **attributes,
    }
    item.setdefault("review_status", "pending_review")
    item.setdefault("content_type", "narrative_text")
    district = (item.get("spatial_units") or {}).get("district")
    if district:
        item["district"] = district
    else:
        item.pop("district", None)
    item["id"] = key["id"]
    item["theme_entity_id"] = theme_entity_id
    item["document_id"] = document_id
    try:
        records_table.put_item(
            Item=_ddb_safe(item),
            ConditionExpression="attribute_not_exists(id) AND attribute_not_exists(theme_entity_id)",
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return 409, {"detail": "A record with this theme_entity_id already exists"}
        raise
    if item.get("review_status") == "pending_review":
        pending = _count_document_records(document_id, "pending_review")
        table.update_item(
            Key={"document_id": document_id},
            UpdateExpression="SET ingestion_status = :status, pending_review_count = :count",
            ExpressionAttributeValues={":status": "pending_review", ":count": pending},
        )
    return 201, {"saved": True, "record": item}


def handler(event: dict, context) -> dict:
    rc          = event.get("requestContext") or {}
    method      = rc.get("http", {}).get("method", "").upper()
    raw_path    = event.get("rawPath", "")
    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}

    if method == "OPTIONS":
        return _response(200, {})

    LOGGER.info("method=%s path=%s", method, raw_path)

    if raw_path.rstrip("/").endswith("/api/database/taxonomy"):
        if method == "GET":
            try:
                return _response(200, _list_taxonomy())
            except Exception:
                LOGGER.exception("Failed to list entity taxonomy")
                return _response(500, {"detail": "Failed to list entity taxonomy"})
        if method == "POST":
            try:
                status, body = _save_taxonomy(_parse_body(event))
                return _response(status, body)
            except Exception:
                LOGGER.exception("Failed to save entity taxonomy")
                return _response(500, {"detail": "Failed to save entity taxonomy"})

    taxonomy_match = re.search(r"/api/database/taxonomy/([^/]+)/?$", raw_path)
    if taxonomy_match and method == "DELETE":
        try:
            entity_key = path_params.get("entity_key") or taxonomy_match.group(1)
            status, body = _delete_taxonomy(entity_key)
            return _response(status, body)
        except Exception:
            LOGGER.exception("Failed to delete taxonomy entry")
            return _response(500, {"detail": "Failed to delete taxonomy entry"})

    approve_match = re.search(r"/api/documents/([^/]+)/records/approve/?$", raw_path)
    if approve_match and method == "POST":
        try:
            document_id = path_params.get("document_id") or approve_match.group(1)
            status, body = _approve_document_records(document_id, _parse_body(event))
            return _response(status, body)
        except Exception:
            LOGGER.exception("Failed to approve records for %s", path_params.get("document_id"))
            return _response(500, {"detail": "Failed to approve records"})

    record_match = re.search(r"/api/documents/([^/]+)/records/([^/]+)/?$", raw_path)
    if record_match:
        document_id = path_params.get("document_id") or record_match.group(1)
        theme_entity_id = path_params.get("theme_entity_id") or record_match.group(2)
        try:
            if method == "PUT":
                body = _parse_body(event)
                attributes = body.get("attributes", body)
                if isinstance(attributes, dict) and "geotag" in attributes:
                    auth_error = _atlas_auth_error(event)
                    if auth_error:
                        return _response(*auth_error)
                status, body = _update_record(
                    document_id, theme_entity_id, body
                )
                return _response(status, body)
            if method == "DELETE":
                status, body = _delete_record(document_id, theme_entity_id)
                return _response(status, body)
        except Exception:
            LOGGER.exception("Failed to mutate record %s", theme_entity_id)
            return _response(500, {"detail": "Failed to update record"})

    records_match = re.search(r"/api/documents/([^/]+)/records/?$", raw_path)
    if records_match:
        document_id = path_params.get("document_id") or records_match.group(1)
        if method == "GET":
            try:
                return _response(200, _get_document_records(document_id, query_params))
            except ValueError as error:
                return _response(400, {"detail": str(error)})
            except Exception:
                LOGGER.exception("Failed to query records for %s", document_id)
                return _response(500, {"detail": "Failed to query records"})
        if method == "POST":
            try:
                status, body = _create_record(document_id, _parse_body(event))
                return _response(status, body)
            except Exception:
                LOGGER.exception("Failed to create record for %s", document_id)
                return _response(500, {"detail": "Failed to create record"})

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
            from ai_extractor import DEFAULT_MODEL_ID, get_model_config
            from dynamo_ingestion import ingest_document
            model_id = body.get("model_id") or DEFAULT_MODEL_ID
            try:
                get_model_config(model_id)
            except ValueError as error:
                return _response(400, {"detail": str(error)})
            result = ingest_document(
                document_id,
                item["s3_bucket"],
                item["ocr_s3_key"],
                item.get("s3_ocr_json_url"),
                model_id,
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

    # PUT /document/{id}/meta — save doc_name and doc_tag
    meta_match = re.search(r"/document/([^/]+)/meta/?$", raw_path)
    if meta_match and method == "PUT":
        try:
            doc_id = path_params.get("document_id") or meta_match.group(1)
            body   = _parse_body(event)
            table.update_item(
                Key={"document_id": doc_id},
                UpdateExpression="SET doc_name = :n, doc_tag = :t",
                ExpressionAttributeValues={
                    ":n": (body.get("doc_name") or "").strip(),
                    ":t": (body.get("doc_tag") or "").strip(),
                },
            )
            return _response(200, {"saved": True})
        except Exception:
            LOGGER.exception("Failed to save document meta")
            return _response(500, {"detail": "Failed to save document metadata"})

    delete_document_match = re.search(r"/document/([^/]+)/?$", raw_path)
    if delete_document_match and method == "DELETE":
        auth_error = _admin_auth_error(event)
        if auth_error:
            return _response(*auth_error)
        try:
            document_id = path_params.get("document_id") or delete_document_match.group(1)
            status, body = _delete_document(document_id)
            return _response(status, body)
        except Exception:
            LOGGER.exception("Failed to delete document %s", delete_document_match.group(1))
            return _response(500, {"detail": "Failed to delete document and its stored data"})

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
