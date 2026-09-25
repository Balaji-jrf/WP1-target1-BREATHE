"""FastAPI service for extracting structured text from images and PDFs with Surya OCR."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

import pymupdf
from fastapi import File, HTTPException, UploadFile
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, UnidentifiedImageError
from surya.inference import SuryaInferenceManager
from surya.inference.backends.spawn import SpawnError
from surya.recognition import RecognitionPredictor

LOGGER = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024
OCR_LANGUAGES = [language.strip() for language in os.getenv("OCR_LANGUAGES", "en").split(",") if language.strip()]


@dataclass(frozen=True)
class OCRModels:
    recognition_predictor: RecognitionPredictor


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    LOGGER.info("Loading Surya OCR models")
    application.state.ocr_models = OCRModels(
        recognition_predictor=RecognitionPredictor(SuryaInferenceManager()),
    )
    application.state.ocr_lock = asyncio.Lock()
    LOGGER.info("Surya OCR models loaded")
    yield
    application.state.ocr_models = None


app = FastAPI(title="Historical Document OCR API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _image_from_bytes(image_bytes: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.verify()
        with Image.open(io.BytesIO(image_bytes)) as image:
            return image.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=415, detail="The uploaded file is not a readable image") from exc


def _pdf_pages_from_bytes(pdf_bytes: bytes) -> list[Image.Image]:
    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=415, detail="The uploaded file is not a readable PDF") from exc

    pages: list[Image.Image] = []
    try:
        for page in document:
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            pages.append(Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples))
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=415, detail="The PDF could not be rendered") from exc
    finally:
        document.close()
    return pages


def _html_to_text(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_lines(prediction: Any, page_number: int) -> list[dict[str, Any]]:
    elements = []
    for block in prediction.blocks:
        text = _html_to_text(block.html)
        if not text:
            continue
        elements.append(
            {
                "page": page_number,
                "text": text,
                "confidence": float(block.confidence),
                "bbox": [float(value) for value in block.bbox],
            }
        )
    return elements


def _run_ocr(images: list[Image.Image], models: OCRModels) -> list[dict[str, Any]]:
    predictions = models.recognition_predictor(images, full_page=True)
    return [
        element
        for page_number, prediction in enumerate(predictions, start=1)
        for element in _extract_lines(prediction, page_number)
    ]


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/process-ocr")
async def process_ocr(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="A filename is required")

    image_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")

    content_type = (file.content_type or "").lower()
    is_pdf = content_type == "application/pdf" or image_bytes.startswith(b"%PDF-")
    if is_pdf:
        images = _pdf_pages_from_bytes(image_bytes)
    else:
        images = [_image_from_bytes(image_bytes)]

    if not images:
        raise HTTPException(status_code=422, detail="The document contains no pages")

    try:
        async with app.state.ocr_lock:
            elements = await asyncio.to_thread(_run_ocr, images, app.state.ocr_models)
    except HTTPException:
        raise
    except SpawnError as exc:
        LOGGER.exception("Surya inference runtime is unavailable")
        raise HTTPException(
            status_code=503,
            detail=(
                "OCR runtime is unavailable. Install llama.cpp and ensure "
                "the llama-server executable is on PATH, then restart the backend."
            ),
        ) from exc
    except Exception as exc:
        LOGGER.exception("OCR processing failed for %s", file.filename)
        raise HTTPException(status_code=500, detail="OCR processing failed") from exc
    finally:
        for image in images:
            image.close()

    return {
        "filename": file.filename,
        "total_pages": len(images),
        "elements": elements,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
    