"""
ai_extractor.py  —  jrf-task-dynamo-ingestion
=============================================
Imported by lambda_function.py (same zip).

Responsibilities
----------------
1. page_chunks()      — split OCR line elements into bounded text chunks
2. build_system_prompt() — build the Nova Lite system prompt from the taxonomy
3. extract_chunk()    — send one chunk to Nova Lite, return validated facts
4. validate_entities() — filter out weak / malformed Nova Lite responses

No AWS clients are created here — the Bedrock client is passed in from
lambda_function.py so this module stays independently testable.
"""

from __future__ import annotations

import json
import logging
import math
import time

from botocore.exceptions import ClientError

# ── Quality thresholds for accepted facts ─────────────────────────────────────
MIN_FACT_WORDS  = 5     # reject facts shorter than 5 words
MIN_FACT_CHARS  = 35    # reject facts shorter than 35 characters
MIN_CONFIDENCE  = 0.55  # reject facts where Nova Lite confidence < 0.55

# Minimum chunk size worth sending to Nova Lite — very short chunks
# (page numbers, headers, single lines) produce unreliable responses
MIN_CHUNK_CHARS = 80


# ── Bounding-box helpers ──────────────────────────────────────────────────────

def _line_bbox(bbox):
    """
    Validate and normalise a single OCR line bounding box.
    Expects [left, top, right, bottom] as numbers.
    Returns the list of floats, or None if invalid / non-finite.
    """
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        values = [float(v) for v in bbox]
    except (TypeError, ValueError):
        return None
    return values if all(math.isfinite(v) for v in values) else None


def _make_chunk(page, chunk_index, lines):
    """
    Build a chunk dict from a list of (text, bbox) pairs.
    The chunk bbox is the union of all line bboxes on this chunk.
    """
    boxes = [box for _, box in lines if box]
    bbox = None
    if boxes:
        bbox = [
            min(box[0] for box in boxes),  # leftmost left
            min(box[1] for box in boxes),  # topmost top
            max(box[2] for box in boxes),  # rightmost right
            max(box[3] for box in boxes),  # bottommost bottom
        ]
    return {
        "page":        page,
        "chunk_index": chunk_index,
        "text":        " ".join(text for text, _ in lines),
        "bbox":        bbox,
    }


# ── Page chunking ─────────────────────────────────────────────────────────────

def page_chunks(elements, max_chars=1500):
    """
    Group OCR line elements by page, then split each page into text chunks
    of at most max_chars characters.

    Why chunking?
    Nova Lite has a token limit. Sending an entire page at once can exceed
    it for dense documents. Chunking keeps each call within budget while
    preserving spatial locality (lines on the same page stay together).

    Yields dicts: {page, chunk_index, text, bbox}
    """
    # Group lines by page number
    by_page: dict[int, list] = {}
    for element in elements or []:
        text = " ".join(str(element.get("text") or "").split())
        if text:
            page = int(element.get("page") or 1)
            by_page.setdefault(page, []).append((text, _line_bbox(element.get("bbox"))))

    for page in sorted(by_page):
        chunk_index, current, current_size = 0, [], 0

        for text, bbox in by_page[page]:
            # If a single line exceeds max_chars, split it at word boundaries
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
                # Flush current chunk if adding this part would exceed the limit
                if current and current_size + len(part) + 1 > max_chars:
                    chunk_index += 1
                    yield _make_chunk(page, chunk_index, current)
                    current, current_size = [], 0
                current.append((part, bbox))
                current_size += len(part) + (1 if len(current) > 1 else 0)

        # Flush the last chunk for this page
        if current:
            chunk_index += 1
            yield _make_chunk(page, chunk_index, current)


# ── Nova Lite prompt ──────────────────────────────────────────────────────────

def build_system_prompt(taxonomy):
    """
    Build the system prompt that instructs Nova Lite on what to extract.

    The active taxonomy is serialised as JSON and injected directly into
    the prompt so Nova Lite knows exactly which entity_keys are valid and
    what each one means. Only active entries (is_active=True) are included.
    """
    active = [{
        "entity_key":          item["entity_key"],
        "display_name":        item.get("display_name", item["entity_key"]),
        "description":         item.get("description", ""),
        "keywords":            item.get("keywords", []),
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


# ── Nova Lite response parsing ────────────────────────────────────────────────

def _response_text(response):
    """
    Extract the plain text string from a Bedrock invoke_model response.
    Nova Lite returns: response.body → JSON → output.message.content[].text
    Raises ValueError if no text content is found.
    """
    body    = json.loads(response["body"].read())
    content = body.get("output", {}).get("message", {}).get("content", [])
    text    = next((item.get("text") for item in content if item.get("text")), "")
    if not text:
        raise ValueError("Nova Lite returned no text content")
    return text.strip()


# Logger for this module — visible in CloudWatch under jrf-task-dynamo-ingestion
_LOG = logging.getLogger(__name__)


def _parse_json_array(text):
    """
    Parse a JSON array from Nova Lite's response text.

    Nova Lite sometimes:
      - Wraps the array in a markdown code fence (```json ... ```)
      - Returns a plain-text refusal ("I cannot extract...") with no array
      - Returns an empty array [] directly
      - Returns extra commentary before or after the array

    Strategy: strip any code fence, then find the outermost [...] bracket
    pair. If none exists, log the raw response and return [] so the
    ingestion continues rather than crashing the entire document.
    """
    cleaned = text.strip()
    fence   = chr(96) * 3  # ```

    # Strip markdown code fence if present (```json\n[...]\n```)
    if cleaned.startswith(fence):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith(fence):
            cleaned = cleaned.rstrip()[:-3].strip()

    # Find the outermost JSON array brackets
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end < start:
        # Nova Lite returned prose instead of JSON — log it and skip this chunk
        _LOG.warning("Nova Lite returned no JSON array — raw response: %.400s", text)
        return []

    try:
        parsed = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        # Malformed JSON — log and skip rather than crash
        _LOG.warning("Nova Lite JSON parse error (%s) — raw: %.400s", exc, text)
        return []

    if not isinstance(parsed, list):
        _LOG.warning("Nova Lite response was not a list — raw: %.400s", text)
        return []

    return parsed


# ── Entity validation ─────────────────────────────────────────────────────────

def validate_entities(items, taxonomy):
    """
    Filter Nova Lite's raw output and return only high-quality facts.

    Rejects an item if any of the following are true:
      - theme is not in the active taxonomy (unknown or inactive key)
      - extracted_fact is shorter than MIN_FACT_CHARS or MIN_FACT_WORDS
      - confidence_score is missing, non-numeric, non-finite, or < MIN_CONFIDENCE
      - spatial_units is not a dict

    Accepted items are normalised:
      - theme is mapped to the canonical casing from the taxonomy
      - spatial_units keys are lowercased; only district/taluk/village/town kept
      - extracted_fact whitespace is collapsed
    """
    # Build a casefold lookup: "water_access" → "WATER_ACCESS"
    active = {
        item["entity_key"].casefold(): item["entity_key"]
        for item in taxonomy
        if item.get("is_active", True) and item.get("entity_key")
    }

    accepted = []
    for item in items:
        if not isinstance(item, dict):
            continue

        # Validate theme against active taxonomy
        theme = active.get(str(item.get("theme") or "").strip().casefold())
        fact  = " ".join(str(item.get("extracted_fact") or "").split())
        if not theme or len(fact) < MIN_FACT_CHARS or len(fact.split()) < MIN_FACT_WORDS:
            continue

        # Validate confidence score
        try:
            confidence = float(item.get("confidence_score"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(confidence) or not MIN_CONFIDENCE <= confidence <= 1:
            continue

        # Validate and normalise spatial_units
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
            "theme":            theme,
            "extracted_fact":   fact,
            "spatial_units":    spatial,
            "temporal_period":  " ".join(str(item.get("temporal_period") or "").split()),
            "confidence_score": confidence,
        })

    return accepted


# ── Main extraction call ──────────────────────────────────────────────────────

def extract_chunk(chunk, taxonomy, bedrock_client, model_id="amazon.nova-lite-v1:0"):
    """
    Send one OCR text chunk to Nova Lite and return validated facts.

    Retry logic:
      Up to 4 attempts with exponential back-off (2s, 4s, 8s, 12s cap)
      for ThrottlingException / TooManyRequestsException / ServiceUnavailableException.
      Any other ClientError is re-raised immediately.

    Returns a list of validated fact dicts (may be empty).
    Raises ValueError if no active taxonomy entries exist.
    """
    if not any(item.get("is_active", True) for item in taxonomy):
        raise ValueError("No active entity taxonomy is configured")

    # Skip chunks that are too short — they produce unreliable Nova Lite responses
    # (e.g. a page containing only a page number or a single heading)
    if len(chunk.get("text", "")) < MIN_CHUNK_CHARS:
        _LOG.info("Skipping short chunk page=%s chunk=%s (%d chars)",
                  chunk.get("page"), chunk.get("chunk_index"), len(chunk.get("text", "")))
        return []

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
        # Low temperature = more deterministic, less hallucination
        "inferenceConfig": {"maxTokens": 1400, "temperature": 0.1},
    }

    for attempt in range(6):
        try:
            response = bedrock_client.invoke_model(
                modelId=model_id,
                body=json.dumps(request_body, ensure_ascii=False),
                contentType="application/json",
                accept="application/json",
            )
            break  # success — exit retry loop
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            retryable = {"ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException"}
            if code not in retryable or attempt == 5:
                raise  # non-retryable or exhausted retries
            # Exponential back-off with jitter: 2s, 4s, 8s, 16s, 30s
            # Jitter spreads retries from parallel threads so they don't all
            # hit Bedrock at the same moment after a throttle.
            import random
            sleep_time = min(2 ** (attempt + 1), 30) + random.uniform(0, 2)
            _LOG.info("Throttled on attempt %d — sleeping %.1fs", attempt + 1, sleep_time)
            time.sleep(sleep_time)

    raw_text = _response_text(response)
    _LOG.debug("Nova Lite raw response (page %s chunk %s): %.300s",
               chunk.get("page"), chunk.get("chunk_index"), raw_text)
    return validate_entities(_parse_json_array(raw_text), taxonomy)
