"""
Auth Lambda
-----------
POST /auth/login          → { token, username, role, permissions }
POST /auth/signup         → { token, username, role }
GET  /auth/admin/users    → list all users (admin only)
PUT  /auth/admin/users/{username} → update user permissions (admin only)

DynamoDB table: BreatheUsers
  PK: username (string)
  Fields: password_hash, role ('admin'|'user'), email, permissions (list), created_at
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import base64
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

AWS_REGION   = os.environ.get("AWS_REGION", "ap-southeast-2")
USERS_TABLE  = os.environ.get("USERS_TABLE", "jrf-task-BreatheUsers")
JWT_SECRET   = os.environ.get("JWT_SECRET", "breathe-secret-change-me")

dynamo = boto3.resource("dynamodb", region_name=AWS_REGION)
table  = dynamo.Table(USERS_TABLE)

ALL_TABS = ["extract", "history", "database", "taxonomy", "llm", "atlas"]

# ── Helpers ──────────────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def _make_jwt(payload: dict) -> str:
    header  = base64.urlsafe_b64encode(json.dumps({"alg":"HS256","typ":"JWT"}).encode()).rstrip(b"=").decode()
    body    = base64.urlsafe_b64encode(json.dumps({**payload, "iat": int(time.time()), "exp": int(time.time()) + 86400 * 7}).encode()).rstrip(b"=").decode()
    sig_raw = hmac.new(JWT_SECRET.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()  # noqa: S324
    sig     = base64.urlsafe_b64encode(sig_raw).rstrip(b"=").decode()
    return f"{header}.{body}.{sig}"


def _verify_jwt(token: str) -> dict | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, body, sig = parts
        expected_sig_raw = hmac.new(JWT_SECRET.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()  # noqa: S324
        expected_sig     = base64.urlsafe_b64encode(expected_sig_raw).rstrip(b"=").decode()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=="))
        if payload.get("exp", 0) < int(time.time()):
            return None
        return payload
    except Exception:
        return None

def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin":  os.environ.get("ALLOWED_ORIGIN", "*"),
        "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
        "Access-Control-Allow-Headers": "content-type,authorization",
    }

def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {**_cors_headers(), "Content-Type": "application/json"},
        "body": json.dumps(body),
    }

def _parse_body(event: dict) -> dict:
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    return json.loads(body) if isinstance(body, str) else body

def _get_caller(event: dict) -> dict | None:
    auth = (event.get("headers") or {}).get("authorization", "")
    if auth.lower().startswith("bearer "):
        return _verify_jwt(auth[7:])
    return None

def _ensure_admin_exists():
    """Seed the default admin user if the table is empty."""
    try:
        table.put_item(
            Item={
                "username":      "admin",
                "password_hash": _hash_password("Admin@123"),
                "role":          "admin",
                "email":         "admin@breathe.local",
                "permissions":   ALL_TABS,
                "created_at":    datetime.now(timezone.utc).isoformat(),
            },
            ConditionExpression="attribute_not_exists(username)",
        )
        LOGGER.info("Default admin user seeded.")
    except ClientError as err:
        if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
            LOGGER.warning("Could not seed admin: %s", err)

# ── Route handlers ────────────────────────────────────────────────────────────

def _login(body: dict) -> tuple[int, dict]:
    username = (body.get("username") or "").strip().lower()
    password = (body.get("password") or "").strip()
    if not username or not password:
        return 400, {"detail": "username and password are required"}
    item = table.get_item(Key={"username": username}).get("Item")
    if not item or item["password_hash"] != _hash_password(password):
        return 401, {"detail": "Invalid username or password"}
    token = _make_jwt({"username": username, "role": item["role"], "permissions": item.get("permissions", [])})
    return 200, {"token": token, "username": username, "role": item["role"], "permissions": item.get("permissions", [])}


def _signup(body: dict) -> tuple[int, dict]:
    username = (body.get("username") or "").strip().lower()
    password = (body.get("password") or "").strip()
    email    = (body.get("email") or "").strip()
    if not username or not password or not email:
        return 400, {"detail": "username, password, and email are required"}
    if len(username) < 3 or not username.replace("_","").replace("-","").isalnum():
        return 400, {"detail": "username must be 3+ alphanumeric characters (hyphens/underscores allowed)"}
    if len(password) < 6:
        return 400, {"detail": "password must be at least 6 characters"}
    try:
        table.put_item(
            Item={
                "username":      username,
                "password_hash": _hash_password(password),
                "role":          "user",
                "email":         email,
                "permissions":   ["extract", "history", "database", "llm", "atlas"],  # default access for new users
                "created_at":    datetime.now(timezone.utc).isoformat(),
            },
            ConditionExpression="attribute_not_exists(username)",
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return 409, {"detail": "Username already taken. Please choose another."}
        raise
    permissions = ["extract", "history", "database", "llm", "atlas"]
    token = _make_jwt({"username": username, "role": "user", "permissions": permissions})
    return 201, {"token": token, "username": username, "role": "user", "permissions": permissions}


def _list_users(caller: dict) -> tuple[int, dict]:
    if caller.get("role") != "admin":
        return 403, {"detail": "Admin access required"}
    items, last_key = [], None
    while True:
        kwargs = {
            "ProjectionExpression": "username, #r, email, #p, created_at",
            "ExpressionAttributeNames": {"#r": "role", "#p": "permissions"},
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    items.sort(key=lambda x: x.get("created_at", ""))
    return 200, {"users": items}


def _update_user(username: str, body: dict, caller: dict) -> tuple[int, dict]:
    if caller.get("role") != "admin":
        return 403, {"detail": "Admin access required"}
    username = username.strip().lower()
    if username == "admin":
        return 400, {"detail": "Cannot modify the admin account via this endpoint"}
    item = table.get_item(Key={"username": username}).get("Item")
    if not item:
        return 404, {"detail": "User not found"}
    permissions = body.get("permissions")
    if permissions is not None:
        if not isinstance(permissions, list):
            return 400, {"detail": "permissions must be a list of tab names"}
        table.update_item(
            Key={"username": username},
            UpdateExpression="SET #p = :p",
            ExpressionAttributeNames={"#p": "permissions"},
            ExpressionAttributeValues={":p": permissions},
        )
    return 200, {"updated": True, "username": username, "permissions": permissions}


# ── Main handler ──────────────────────────────────────────────────────────────

_seeded = False

def handler(event: dict, context) -> dict:
    global _seeded
    if not _seeded:
        _ensure_admin_exists()
        _seeded = True

    method   = (event.get("requestContext") or {}).get("http", {}).get("method", "").upper()
    raw_path = event.get("rawPath", "")

    if method == "OPTIONS":
        return _response(200, {})

    LOGGER.info("auth method=%s path=%s", method, raw_path)

    # POST /auth/login
    if raw_path.rstrip("/").endswith("/auth/login") and method == "POST":
        try:
            status, body = _login(_parse_body(event))
            return _response(status, body)
        except Exception:
            LOGGER.exception("Login failed")
            return _response(500, {"detail": "Login failed"})

    # POST /auth/signup
    if raw_path.rstrip("/").endswith("/auth/signup") and method == "POST":
        try:
            status, body = _signup(_parse_body(event))
            return _response(status, body)
        except Exception:
            LOGGER.exception("Signup failed")
            return _response(500, {"detail": "Signup failed"})

    # GET /auth/admin/users
    if raw_path.rstrip("/").endswith("/auth/admin/users") and method == "GET":
        caller = _get_caller(event)
        if not caller:
            return _response(401, {"detail": "Authentication required"})
        try:
            status, body = _list_users(caller)
            return _response(status, body)
        except Exception:
            LOGGER.exception("List users failed")
            return _response(500, {"detail": "Failed to list users"})

    # PUT /auth/admin/users/{username}
    import re
    user_match = re.search(r"/auth/admin/users/([^/]+)/?$", raw_path)
    if user_match and method == "PUT":
        caller = _get_caller(event)
        if not caller:
            return _response(401, {"detail": "Authentication required"})
        try:
            username = (event.get("pathParameters") or {}).get("username") or user_match.group(1)
            status, body = _update_user(username, _parse_body(event), caller)
            return _response(status, body)
        except Exception:
            LOGGER.exception("Update user failed")
            return _response(500, {"detail": "Failed to update user"})

    return _response(404, {"detail": "Not found"})
