# NLP Pipeline — Frontend

React app for the BREATHE WP1 OCR & NLP pipeline.
Upload historical documents, extract text via AWS Textract, browse and download the archive.

Live: `https://jrf-nlp-pipeline.skillrouteai.com`

---

## What it does

```
User drops a file (PDF / PNG / JPEG / TIFF / WebP)
        │
        ▼
GET /upload-url  →  presigned S3 PUT URL + document_id
        │
        ▼
PUT file directly to S3  (no size limit — bypasses API Gateway)
        │
        ▼  S3 triggers starter Lambda automatically
        │
        ▼
Poll GET /document/{id} every 5s
  status=processing  →  wait, show elapsed time
  status=completed   →  display OCR result
  503/502/504        →  Lambda finishing large doc in background, keep polling
        │
        ▼
OCR result displayed:
  - Text blocks with page number + confidence score
  - Original document viewer (PDF iframe / image)
  - Raw JSON toggle
  - Download OCR JSON button
```

---

## Folder structure

```
ocr-explorer/
├── public/
└── src/
    ├── App.js               Tab shell — switches between Extract and History
    ├── App.css              All styles (responsive, compare popup, modals)
    ├── index.js             React entry point
    ├── index.css            Global reset + Google Fonts
    ├── api.js               All API calls and download logic
    └── components/
        ├── ExtractTab.js    Upload form + live status + result display
        ├── HistoryTab.js    Archive grid + Doc/OCR/Compare/Download actions
        ├── ResultPanel.js   Shared result display (text blocks + doc viewer)
        └── Modal.js         Reusable overlay modal (Escape to close)
```

---

## Component responsibilities

### `api.js`
All network calls in one place. No component imports fetch directly.

| Export | What it does |
|---|---|
| `uploadDocument(file, onStatus)` | Full upload pipeline: GET presigned URL → PUT to S3 → poll until completed |
| `fetchDocuments()` | GET `/documents` — returns metadata list for History tab |
| `fetchDocument(id)` | GET `/document/{id}` — returns metadata + elements + presigned doc URL |
| `downloadOcrJson(id)` | Calls `fetchDocument`, builds Blob, triggers browser download as `{name}_ocr.json` |

**Polling logic in `uploadDocument`:**
- Polls every 5s, up to 240 attempts (20 minutes max)
- `503 / 502 / 504` → transient (Lambda finishing large doc in background) → keep polling silently
- Network error (fetch throws) → keep polling silently
- `status=failed` or other 4xx/5xx → throw, show error to user

### `ExtractTab.js`
- Drag-and-drop or click-to-browse file picker
- Calls `uploadDocument` with `onStatus` callback — shows live status messages during upload and OCR wait
- Passes completed result to `ResultPanel`

### `HistoryTab.js`
- Loads all documents on mount via `fetchDocuments`
- Renders a card grid — each card shows: filename, page count, text blocks, file size, processing time, upload date
- Four buttons per card:
  - `⊞ Doc` — opens Modal with PDF iframe or image
  - `≡ OCR` — opens Modal with scrollable text block list
  - `⧉ Compare` — opens draggable/resizable popup with doc on left, OCR text on right
  - `⬇ JSON` — downloads `{filename}_ocr.json` directly to browser

### `ResultPanel.js`
- Shared between ExtractTab (after upload) and used inside HistoryTab modals
- Shows: result heading, page/element counts, text block list, doc viewer, raw JSON toggle

### `Modal.js`
- Overlay modal with Escape-to-close and click-outside-to-close
- Used for Doc view and OCR view in HistoryTab

---

## API calls

Base URL from `REACT_APP_API_URL` env var (falls back to `http://localhost:8000`).

| Call | Method | Endpoint | Response |
|---|---|---|---|
| Get upload URL | GET | `/upload-url?filename=foo.pdf` | `{ upload_url, document_id, s3_key, content_type }` |
| Poll document | GET | `/document/{id}` | `{ status, filename, total_pages, element_count, elements[], s3_presigned_url, file_size_bytes, processing_time_seconds }` |
| List documents | GET | `/documents` | `{ documents[], count }` — metadata only, no elements |

The `elements` array is fetched from S3 (`processed/{id}/ocr.json`) by the retrieval Lambda and inlined into the response. Each element:

```json
{
  "page":       1,
  "text":       "SOME ECONOMIC FACTS AND FIGURES",
  "confidence": 0.9987,
  "bbox":       [0.1823, 0.0412, 0.8177, 0.0601]
}
```

---

## History tab — card metadata displayed

| Field | Source |
|---|---|
| Filename | DynamoDB |
| Page count | DynamoDB |
| Text block count | DynamoDB `element_count` |
| File size | DynamoDB `file_size_bytes` (from S3 event) |
| Processing time | DynamoDB `processing_time_seconds` |
| Upload date/time | DynamoDB `uploaded_at` |

---

## Environment setup

```bash
cd NLP-Pipleline-frontEnd/ocr-explorer

cp .env.example .env
# Edit .env:
# REACT_APP_API_URL=https://cblo1ukhb4.execute-api.ap-south-2.amazonaws.com/prod

npm install
npm start
```

---

## Deploy (Amplify)

Amplify is connected to the `main` branch of `https://github.com/Balaji-jrf/WP1-target1-BREATHE`.
Every `git push` to `main` triggers an automatic build and deploy.

Amplify env var `REACT_APP_API_URL` must be set in the Amplify console:
```
https://cblo1ukhb4.execute-api.ap-south-2.amazonaws.com/prod
```

Manual build:
```bash
npm run build
# Output in build/ — deploy to any static host
```

---

## Supported file types

| Type | Extensions | Processing |
|---|---|---|
| PDF | `.pdf` | Async Textract — starter Lambda polls internally up to 850s |
| PNG | `.png` | Sync Textract — completes in ~2s |
| JPEG | `.jpg` `.jpeg` | Sync Textract |
| TIFF | `.tif` `.tiff` | Sync Textract |
| WebP | `.webp` | Sync Textract |

No file size limit — files go directly to S3, bypassing API Gateway.

---

## Console logging

Every step of the upload pipeline is logged to the browser console with timestamps:

```
[OCR 08:01:27] [INIT]   file=document.pdf size=3.73MB type=application/pdf
[OCR 08:01:27] [STEP-1] GET /upload-url
[OCR 08:01:28] [STEP-1] response status=200 { document_id, content_type }
[OCR 08:01:28] [STEP-2] PUT to S3 — document_id=b827...
[OCR 08:01:28] [STEP-2] S3 PUT done — status=200 took=0.7s
[OCR 08:01:28] [STEP-3] starting poll — interval=5000ms max=240
[OCR 08:01:33] [POLL]   attempt 1/240 elapsed=5s
[OCR 08:01:33] [POLL]   attempt 1 response — status=processing
[OCR 08:02:08] [POLL]   attempt 8 response — status=completed
[OCR 08:02:08] [POLL]   COMPLETED after 40s — pages=53 elements=3158
```

Transient errors (503 during large doc processing) are logged as:
```
[OCR 08:07:36] [POLL]   attempt 49 transient 503 — Lambda still processing, retrying
```
