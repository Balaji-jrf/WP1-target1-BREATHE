## OCR backend

This service accepts PNG, JPEG, TIFF, WebP, and multi-page PDF uploads at
`POST /process-ocr`. PDF pages are rendered at 144 DPI and processed
sequentially by Surya OCR.

Surya 0.22 uses `llama-server` for CPU inference. Install the llama.cpp
runtime before starting the API on Linux:

```bash
sudo apt update
sudo apt install -y llama-server
```

If your Ubuntu release does not provide that package, download the Linux
`llama-server` binary from the llama.cpp releases page, make it executable,
place it on `PATH`, and verify it with `command -v llama-server`. You can also
set `LLAMA_CPP_BINARY=/absolute/path/to/llama-server` before starting Uvicorn.

### Run locally

```bash
cd NLP-Pipleline-backEnd
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn backend:app --host 0.0.0.0 --port 8000
```

Submit a document with:

```bash
curl -X POST http://localhost:8000/process-ocr \
	-F "file=@document.pdf"
```

The response contains `filename`, `total_pages`, and `elements`. Each element
has a 1-based `page`, `text`, numeric `confidence`, and `[left, top, right,
bottom]` `bbox`.

Configuration is available through `MAX_UPLOAD_MB` (default `50`),
`OCR_LANGUAGES` (comma-separated, default `en`), `CORS_ORIGINS` (comma-
separated, default `*`), and `PORT` (default `8000`).
