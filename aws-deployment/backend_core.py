"""Pure OCR logic extracted from backend.py — no HTTP framework dependency."""

from __future__ import annotations

import io
import re
from typing import Any

import pymupdf
from PIL import Image, UnidentifiedImageError
from surya.inference import SuryaInferenceManager
from surya.recognition import RecognitionPredictor

_predictor: RecognitionPredictor | None = None


def get_predictor() -> RecognitionPredictor:
    """Lazy-load and cache the Surya OCR predictor (warm Lambda reuse)."""
    global _predictor
    if _predictor is None:
        _predictor = RecognitionPredictor(SuryaInferenceManager())
    return _predictor


def image_from_bytes(image_bytes: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img.verify()
        with Image.open(io.BytesIO(image_bytes)) as img:
            return img.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Not a readable image") from exc


def pdf_pages_from_bytes(pdf_bytes: bytes) -> list[Image.Image]:
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except (RuntimeError, ValueError) as exc:
        raise ValueError("Not a readable PDF") from exc
    pages: list[Image.Image] = []
    try:
        for page in doc:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            pages.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    finally:
        doc.close()
    return pages


def _html_to_text(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def run_ocr(image_bytes: bytes, filename: str) -> dict[str, Any]:
    """Run OCR on raw bytes. Returns the same shape as the FastAPI response."""
    is_pdf = image_bytes.startswith(b"%PDF-")
    images = pdf_pages_from_bytes(image_bytes) if is_pdf else [image_from_bytes(image_bytes)]

    predictor = get_predictor()
    predictions = predictor(images, full_page=True)

    elements: list[dict[str, Any]] = []
    for page_num, pred in enumerate(predictions, start=1):
        for block in pred.blocks:
            text = _html_to_text(block.html)
            if text:
                elements.append({
                    "page": page_num,
                    "text": text,
                    "confidence": float(block.confidence),
                    "bbox": [float(v) for v in block.bbox],
                })

    for img in images:
        img.close()

    return {
        "filename": filename,
        "total_pages": len(images),
        "elements": elements,
    }
