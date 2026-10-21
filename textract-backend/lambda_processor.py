"""
Processor Lambda
----------------
GET /upload-url?filename=foo.pdf

Returns a presigned S3 PUT URL so the browser uploads directly to S3.
The S3 PutObject event then triggers lambda_starter which runs Textract.

Response:
  {
    "upload_url":   "https://s3...presigned...",
    "document_id":  "uuid",
    "s3_key":       "uploads/{document_id}/{filename}"
  }
"""

from __future__ import annotations

import json
import logging
import os
import uuid

import boto3
from botocore.config import Config

LOGGER    = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
S3_BUCKET = os.environ.get("S3_BUCKET")

s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    endpoint_url=f"https://s3.{AWS_REGION}.amazonaws.com",
    config=Config(signature_version="s3v4"),
)

CONTENT_TYPES = {
    ".pdf":  "application/pdf",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif":  "image/tiff",
    ".tiff": "image/tiff",
}

ALLOWED_EXTENSIONS = set(CONTENT_TYPES.keys())


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin":  os.environ.get("ALLOWED_ORIGIN", "*"),
        "Access-Control-Allow-Methods": "GET,OPTIONS",
        "Access-Control-Allow-Headers": "content-type",
    }


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {**_cors_headers(), "Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event: dict, context) -> dict:
    method = (event.get("requestContext") or {}).get("http", {}).get("method", "").upper()

    if method == "OPTIONS":
        return _response(200, {})

    if not S3_BUCKET:
        LOGGER.error("S3_BUCKET environment variable is required")
        return _response(500, {"detail": "Upload service is not configured"})

    qs       = event.get("queryStringParameters") or {}
    filename = (qs.get("filename") or "").strip()

    if not filename:
        return _response(400, {"detail": "filename query parameter is required"})
    if (
        len(filename) > 255
        or "/" in filename
        or "\\" in filename
        or any(ord(character) < 32 for character in filename)
    ):
        return _response(400, {"detail": "filename must be a plain file name up to 255 characters"})

    ext = os.path.splitext(filename.lower())[1]
    if ext not in ALLOWED_EXTENSIONS:
        return _response(400, {
            "detail": f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        })

    content_type = CONTENT_TYPES[ext]
    document_id  = str(uuid.uuid4())
    s3_key       = f"uploads/{document_id}/{filename}"

    try:
        upload_url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket":      S3_BUCKET,
                "Key":         s3_key,
                "ContentType": content_type,
            },
            ExpiresIn=900,
        )
    except Exception:
        LOGGER.exception("Failed to generate presigned upload URL")
        return _response(500, {"detail": "Failed to generate upload URL"})

    LOGGER.info("Presigned upload URL issued: doc=%s key=%s", document_id, s3_key)
    return _response(200, {
        "upload_url":  upload_url,
        "document_id": document_id,
        "s3_key":      s3_key,
        "filename":    filename,
        "content_type": content_type,
    })
