"""Schema-driven page chunking and Amazon Nova Lite extraction."""

from __future__ import annotations

import json
import math
import time

from botocore.exceptions import ClientError

MIN_FACT_WORDS = 5
MIN_FACT_CHARS = 35
MIN_CONFIDENCE = 0.55


def _line_bbox(bbox):
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        values = [float(value) for value in bbox]
    except (TypeError, ValueError):
        return None
    return values if all(math.isfinite(value) for value in values) else None


def page_chunks(elements, max_chars=1500):
    """Group OCR lines by page into bounded chunks with page-relative bounds."""
    by_page = {}
    for element in elements or []:
        text = " ".join(str(element.get("text") or "").split())
        if text:
            by_page.setdefault(int(element.get("page") or 1), []).append((text, _line_bbox(element.get("bbox"))))

    for page in sorted(by_page):
        chunk_index, current, current_size = 0, [], 0
        for text, bbox in by_page[page]:
            parts = []
            while len(text) > max_chars:
                split_at = text.rfind(" ", 0, max_chars)
                if split_at <= 0:
                    split_at = max_chars
                parts.append(text[:split_at].strip())
                text = text[split_at:].strip()
            if text:
                parts.append(text)
            for part in parts:
                if current and current_size + len(part) + 1 > max_chars:
                    chunk_index += 1
                    yield _make_chunk(page, chunk_index, current)
                    current, current_size = [], 0
                current.append((part, bbox))
                current_size += len(part) + (1 if len(current) > 1 else 0)
        if current:
            chunk_index += 1
            yield _make_chunk(page, chunk_index, current)


def _make_chunk(page, chunk_index, lines):
    boxes = [box for _, box in lines if box]
    bbox = None
    if boxes:
        bbox = [
            min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes),
        ]
    return {
        "page": page,
        "chunk_index": chunk_index,
        "text": " ".join(text for text, _ in lines),
        "bbox": bbox,
    }


def build_system_prompt(taxonomy):
    active = [{
        "entity_key": item["entity_key"],
        "display_name": item.get("display_name", item["entity_key"]),
        "description": item.get("description", ""),
        "keywords": item.get("keywords", []),
        "extraction_guidance": item.get("extraction_guidance", ""),
    } for item in taxonomy if item.get("is_active", True)]
    schema = json.dumps(active, ensure_ascii=False, separators=(",", ":"))
    return f"""You are a careful historical-document research extractor for BREATHE.
Extract only meaningful, source-supported facts matching an active taxonomy entry.
The active taxonomy is authoritative and may change. Do not invent or rename keys.
Do not return headings, isolated words, generic statements, or unsupported facts.
Write concise, standalone facts useful for historical analysis. Give a confidence_score from
0 to 1. Use spatial_units as an object with optional district, taluk, village, and town strings.
Use temporal_period only when the excerpt supports one. Return ONLY a JSON array:
[{{"theme":"ACTIVE_ENTITY_KEY","extracted_fact":"...","spatial_units":{{"district":"..."}}, "temporal_period":"...", "confidence_score":0.91}}]
Return [] if there are no strong facts.

ACTIVE TAXONOMY:
{schema}"""


def _response_text(response):
    body = json.loads(response["body"].read())
    content = body.get("output", {}).get("message", {}).get("content", [])
    text = next((item.get("text") for item in content if item.get("text")), "")
    if not text:
        raise ValueError("Nova Lite returned no text content")
    return text.strip()


def _parse_json_array(text):
    cleaned = text.strip()
    fence = chr(96) * 3
    if cleaned.startswith(fence):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith(fence):
            cleaned = cleaned.rstrip()[:-3].strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end < start:
        raise ValueError("Nova Lite response did not contain a JSON array")
    parsed = json.loads(cleaned[start:end + 1])
    if not isinstance(parsed, list):
        raise ValueError("Nova Lite response must be a JSON array")
    return parsed


def validate_entities(items, taxonomy):
    """Reject unknown themes, trivial text, weak confidence, and malformed fields."""
    active = {
        item["entity_key"].casefold(): item["entity_key"]
        for item in taxonomy
        if item.get("is_active", True) and item.get("entity_key")
    }
    accepted = []
    for item in items:
        if not isinstance(item, dict):
            continue
        theme = active.get(str(item.get("theme") or "").strip().casefold())
        fact = " ".join(str(item.get("extracted_fact") or "").split())
        if not theme or len(fact) < MIN_FACT_CHARS or len(fact.split()) < MIN_FACT_WORDS:
            continue
        try:
            confidence = float(item.get("confidence_score"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(confidence) or not MIN_CONFIDENCE <= confidence <= 1:
            continue
        raw_spatial = item.get("spatial_units") or {}
        if not isinstance(raw_spatial, dict):
            continue
        spatial = {
            str(key).lower(): " ".join(value.split())
            for key, value in raw_spatial.items()
            if str(key).lower() in {"district", "taluk", "village", "town"}
            and isinstance(value, str) and value.strip()
        }
        accepted.append({
            "theme": theme,
            "extracted_fact": fact,
            "spatial_units": spatial,
            "temporal_period": " ".join(str(item.get("temporal_period") or "").split()),
            "confidence_score": confidence,
        })
    return accepted


def extract_chunk(chunk, taxonomy, bedrock_client, model_id="amazon.nova-lite-v1:0"):
    """Invoke Nova Lite with bounded retry for account-level throttling."""
    if not any(item.get("is_active", True) for item in taxonomy):
        raise ValueError("No active entity taxonomy is configured")
    request_body = {
        "schemaVersion": "messages-v1",
        "system": [{"text": build_system_prompt(taxonomy)}],
        "messages": [{
            "role": "user",
            "content": [{
                "text": (
                    f"Page {chunk['page']} OCR excerpt follows. Extract only facts explicitly "
                    f"supported by it.\n\n{chunk['text']}"
                )
            }],
        }],
        "inferenceConfig": {"maxTokens": 1400, "temperature": 0.1},
    }
    for attempt in range(4):
        try:
            response = bedrock_client.invoke_model(
                modelId=model_id,
                body=json.dumps(request_body, ensure_ascii=False),
                contentType="application/json",
                accept="application/json",
            )
            break
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            if code not in {"ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException"} or attempt == 3:
                raise
            time.sleep(min(2 ** (attempt + 1), 12))
    return validate_entities(_parse_json_array(_response_text(response)), taxonomy)
