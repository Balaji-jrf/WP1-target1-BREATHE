"""Bounded OCR chunking, taxonomy-grounded extraction, and output validation."""

from __future__ import annotations

import json
import logging
import math
import os
import time

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

MIN_FACT_WORDS  = 5
MIN_FACT_CHARS  = 35
MIN_CONFIDENCE  = 0.55
PAGE_GROUP_SIZE = int(os.environ.get("PAGE_GROUP_SIZE", "10"))
DEFAULT_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "meta.llama3-1-8b-instruct-v1:0"
)
MODEL_CONFIGS = {
    "amazon.nova-lite-v1:0": {
        "region": "ap-southeast-2",
        "label": "Amazon Nova Lite",
    },
    "amazon.nova-2-lite-v1:0": {
        "region": "ap-southeast-2",
        "label": "Amazon Nova 2 Lite",
    },
    "mistral.ministral-3-8b-instruct": {
        "region": "ap-southeast-2",
        "label": "Mistral Ministral 3 8B Instruct",
    },
    "meta.llama3-1-8b-instruct-v1:0": {
        "region": "us-west-2",
        "label": "Meta Llama 3.1 8B Instruct",
    },
}

_LOG = logging.getLogger(__name__)


def active_taxonomy(items):
    """Return only named, explicitly active taxonomy rows."""
    return sorted(
        [
            item for item in items
            if isinstance(item, dict)
            and item.get("is_active") is True
            and isinstance(item.get("entity_key"), str)
            and item["entity_key"].strip()
        ],
        key=lambda item: item["entity_key"],
    )


def get_model_config(model_id):
    """Return a server-controlled model route; never accept arbitrary AWS regions."""
    try:
        return MODEL_CONFIGS[model_id]
    except (KeyError, TypeError) as error:
        raise ValueError(f"Unsupported extraction model: {model_id}") from error


def load_active_taxonomy(table):
    """Read the active entity definitions from the configured DynamoDB table."""
    items, kwargs = [], {}
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key

    active = active_taxonomy(items)
    if not active:
        raise ValueError("No active entity taxonomy found in DynamoDB.")
    _LOG.info(
        "Loaded %d active taxonomy themes from DynamoDB: %s",
        len(active),
        ", ".join(item["entity_key"] for item in active),
    )
    return active


def clear_generated_records(records_table, document_id):
    """Remove prior AI extractions and legacy table/figure assets before refresh."""
    items, kwargs = [], {
        "KeyConditionExpression": Key("id").eq(f"DOC#{document_id}"),
        "ProjectionExpression": "#pk, #sk, entity_type",
        "ExpressionAttributeNames": {"#pk": "id", "#sk": "theme_entity_id"},
    }
    while True:
        response = records_table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key

    obsolete = [
        item for item in items
        if item.get("entity_type") == "thematic_fact"
        or item.get("theme_entity_id", "").startswith(("TABLES#", "FIGURES#"))
    ]
    if obsolete:
        with records_table.batch_writer() as batch:
            for item in obsolete:
                batch.delete_item(Key={
                    "id": item["id"],
                    "theme_entity_id": item["theme_entity_id"],
                })
    _LOG.info(
        "Cleared %d previous AI/legacy table and figure records for %s",
        len(obsolete),
        document_id,
    )
    return len(obsolete)


# ── Chunking — page-group strategy (same as LLM indexer) ─────────────────────

def _valid_bbox(bbox):
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        vals = [float(v) for v in bbox]
    except (TypeError, ValueError):
        return None
    return vals if all(math.isfinite(v) for v in vals) else None


def _merge_bboxes(elements):
    boxes = [b for e in elements if (b := _valid_bbox(e.get("bbox")))]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def page_chunks(elements, max_chars=None, page_group_size=PAGE_GROUP_SIZE):
    """
    Group OCR text by page range and split each group into bounded model inputs.
    Yields dicts: {page, page_end, chunk_index, text, bbox}
    """
    max_chars = max(1, int(max_chars or 6000))
    # Group by page
    by_page: dict[int, list] = {}
    for el in (elements or []):
        text = " ".join(str(el.get("text") or "").split())
        if not text:
            continue
        try:
            page = max(1, int(el.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        by_page.setdefault(page, []).append({"text": text, "bbox": el.get("bbox")})

    if not by_page:
        return

    sorted_pages = sorted(by_page)
    total_pages  = max(sorted_pages)
    chunk_index  = 0

    def split_text(text):
        pieces, current = [], ""
        for word in text.split():
            if len(word) > max_chars:
                if current:
                    pieces.append(current)
                    current = ""
                pieces.extend(
                    word[index:index + max_chars]
                    for index in range(0, len(word), max_chars)
                )
                continue
            candidate = f"{current} {word}".strip()
            if len(candidate) > max_chars:
                pieces.append(current)
                current = word
            else:
                current = candidate
        if current:
            pieces.append(current)
        return pieces

    page_start = 1
    while page_start <= total_pages:
        page_end    = page_start + page_group_size - 1
        group_pages = [p for p in sorted_pages if page_start <= p <= page_end]
        if group_pages:
            group_els  = [el for p in group_pages for el in by_page[p]]
            current_parts = []
            current_length = 0
            current_elements = []

            def emit():
                nonlocal chunk_index, current_parts, current_length, current_elements
                if not current_parts:
                    return None
                chunk_index += 1
                result = {
                    "page":        page_start,
                    "page_end":    min(page_end, total_pages),
                    "chunk_index": chunk_index,
                    "text":        " ".join(current_parts),
                    "bbox":        _merge_bboxes(current_elements),
                }
                current_parts = []
                current_length = 0
                current_elements = []
                return result

            for element in group_els:
                for piece in split_text(element["text"]):
                    extra_length = len(piece) + (1 if current_parts else 0)
                    if current_length + extra_length > max_chars:
                        chunk = emit()
                        if chunk:
                            yield chunk
                        extra_length = len(piece)
                    current_parts.append(piece)
                    current_length += extra_length
                    current_elements.append(element)
            chunk = emit()
            if chunk:
                yield chunk
        page_start += page_group_size


# ── Prompt ────────────────────────────────────────────────────────────────────

def build_system_prompt(taxonomy):
    active = [
        {
            "entity_key":          t["entity_key"],
            "display_name":        t.get("display_name", t["entity_key"]),
            "description":         t.get("description", ""),
            "keywords":            t.get("keywords", []),
            "extraction_guidance": t.get("extraction_guidance", ""),
        }
        for t in active_taxonomy(taxonomy)
    ]
    schema = json.dumps(active, ensure_ascii=False, separators=(",", ":"))
    return f"""You are a careful historical-document research extractor for BREATHE.
Extract concise, useful facts from the OCR excerpt and assign each only to a matching active entity below.
Use each entity's description, keywords, and extraction_guidance to decide relevance. Use its exact entity_key.
Each fact must explain what the source says about that theme in one clear sentence (maximum 40 words).
Do not return figure captions, table dumps, headings, fragments, generic statements, or unsupported inferences.
For every fact, include a short verbatim source_excerpt copied exactly from the OCR excerpt (8-35 words).
Do not create a fact if you cannot provide an exact supporting excerpt.
Give confidence_score 0-1. Use spatial_units with optional district/taluk/village/town strings.
Use temporal_period only when the excerpt supports one; otherwise use an empty string.
Return ONLY a JSON array:
[{{"theme":"KEY","extracted_fact":"Concise theme-related statement.","source_excerpt":"Exact supporting words from the excerpt.","spatial_units":{{"district":"..."}}, "temporal_period":"...","confidence_score":0.91}}]
Return [] if no strong facts.

ACTIVE TAXONOMY:
{schema}"""


# ── Response parsing ──────────────────────────────────────────────────────────

def _response_text(response):
    body = response["output"]
    content = body.get("message", {}).get("content", [])
    text = next((part.get("text") for part in content if part.get("text")), "")
    if not text:
        raise ValueError("Bedrock model returned no text content")
    return text.strip()


def _parse_json_array(text):
    cleaned = text.strip()
    fence   = "```"
    if cleaned.startswith(fence):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith(fence):
            cleaned = cleaned.rstrip()[:-3].strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end < start:
        _LOG.warning("Bedrock model returned no JSON array — raw: %.300s", text)
        return []
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as e:
        _LOG.warning("Bedrock JSON parse error (%s) — raw: %.300s", e, text)
        return []
    return parsed if isinstance(parsed, list) else []


# ── Validation ────────────────────────────────────────────────────────────────

def validate_entities(items, taxonomy, source_text=""):
    active = {
        t["entity_key"].casefold(): t["entity_key"]
        for t in active_taxonomy(taxonomy)
    }
    accepted = []
    for item in items:
        if not isinstance(item, dict):
            continue
        theme = active.get(str(item.get("theme") or "").strip().casefold())
        fact  = " ".join(str(item.get("extracted_fact") or "").split())
        if not theme or len(fact) < MIN_FACT_CHARS or len(fact.split()) < MIN_FACT_WORDS:
            continue
        source_excerpt = " ".join(str(item.get("source_excerpt") or "").split())
        excerpt_words = source_excerpt.split()
        normalized_source = " ".join(source_text.casefold().split())
        if (
            not 8 <= len(excerpt_words) <= 35
            or len(source_excerpt) > 400
            or " ".join(source_excerpt.casefold().split()) not in normalized_source
        ):
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
            str(k).lower(): " ".join(v.split())
            for k, v in raw_spatial.items()
            if str(k).lower() in {"district", "taluk", "village", "town"}
            and isinstance(v, str) and v.strip()
        }
        accepted.append({
            "theme":           theme,
            "extracted_fact":  fact,
            "source_excerpt":  source_excerpt,
            "spatial_units":   spatial,
            "temporal_period": " ".join(str(item.get("temporal_period") or "").split()),
            "confidence_score": confidence,
        })
    return accepted


# ── Single chunk extraction with retry ───────────────────────────────────────

def extract_chunk(chunk, taxonomy, bedrock_client, model_id=DEFAULT_MODEL_ID):
    if not active_taxonomy(taxonomy):
        raise ValueError("No active entity taxonomy configured")
    if not chunk.get("text", "").strip():
        return []

    page_info = f"Pages {chunk['page']}-{chunk.get('page_end', chunk['page'])}"
    request_messages = [{
        "role": "user",
        "content": [{
            "text": (
                f"{page_info} OCR excerpt. Extract every useful fact explicitly supported "
                f"by this excerpt. Return [] when it contains no theme-relevant facts.\n\n"
                f"{chunk['text']}"
            )
        }],
    }]

    for attempt in range(6):
        try:
            response = bedrock_client.converse(
                modelId=model_id,
                system=[{"text": build_system_prompt(taxonomy)}],
                messages=request_messages,
                inferenceConfig={
                    "maxTokens": 1800,
                    "temperature": 0.1,
                    "topP": 0.9,
                },
            )
            break
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code not in {"ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException"} or attempt == 5:
                raise
            import random
            sleep = min(2 ** (attempt + 1), 30) + random.uniform(0, 2)
            _LOG.info("Throttled attempt %d — sleeping %.1fs", attempt + 1, sleep)
            time.sleep(sleep)

    raw = _response_text(response)
    return validate_entities(_parse_json_array(raw), taxonomy, chunk["text"])


# ── Sequential extraction over all chunks ────────────────────────────────────

def extract_chunks_sequential(chunks, taxonomy, bedrock_client, model_id=DEFAULT_MODEL_ID):
    """
    Submit each OCR chunk to Bedrock in order, without dropping failed chunks.
    """
    results = []
    for index, chunk in enumerate(chunks, start=1):
        _LOG.info(
            "Extracting chunk %d/%d (pages %s-%s)",
            index, len(chunks), chunk["page"], chunk.get("page_end", chunk["page"]),
        )
        facts = extract_chunk(chunk, taxonomy, bedrock_client, model_id)
        _LOG.info("Chunk %d/%d: %d facts", index, len(chunks), len(facts))
        results.append((chunk, facts))
    return results
