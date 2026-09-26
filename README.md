# BREATHE — WP1 Target 1: NLP Pipeline

This is the OCR and NLP pipeline for the BREATHE research project.
It digitises historical documents — policy papers, archival records, speeches, grey literature —
and extracts structured text ready for NLP analysis.

---

## What this project does

```
Scanned document (PDF / image)
         │
         ▼
   Upload via browser
         │
         ▼
  AWS Textract reads every
  line of text on every page
         │
         ▼
  Text stored in database
  with page number, confidence
  score, and bounding box
         │
         ▼
  Ready for NLP pipeline
  (entity extraction, topic
   modelling, classification)
```

---

## Full system architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        BROWSER (React)                          │
│                                                                 │
│   Extract tab          History tab                              │
│   ─ Drop file          ─ Browse all docs                        │
│   ─ Click Extract      ─ Click card → load doc + OCR            │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTPS
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              AWS API Gateway  (HTTP API)                        │
│              Region: ap-south-1                                 │
│                                                                 │
│   POST /process-ocr  ──────────────────► Processor Lambda      │
│   GET  /documents    ──────────────────► Retrieval Lambda       │
│   GET  /document/{id}──────────────────► Retrieval Lambda       │
└─────────────────────────────────────────────────────────────────┘
          │                                        │
          ▼                                        ▼
┌──────────────────────┐              ┌────────────────────────┐
│  Processor Lambda    │              │  Retrieval Lambda      │
│  lambda_processor.py │              │  lambda_retrieval.py   │
│                      │              │                        │
│  1. Parse upload     │              │  /documents:           │
│  2. Save to S3       │              │    Scan DynamoDB       │
│  3. Run Textract     │              │    Return metadata     │
│  4. Save to DynamoDB │              │                        │
│  5. Return result    │              │  /document/{id}:       │
└──────────┬───────────┘              │    Get DynamoDB item   │
           │                          │    Generate S3 URL     │
           │                          │    Return full result  │
           │                          └────────────┬───────────┘
           │                                       │
     ┌─────┴──────────────────────────────────────┘
     │
     ├──► S3 Bucket: jrf-task-ocr-docs-bucket  (ap-south-2)
     │    Original files stored permanently
     │    Path: uploads/{document_id}/{filename}
     │
     ├──► S3 Bucket: jrf-task-ocr-textract-bucket  (ap-south-1)
     │    PDF copies for Textract (can auto-delete after 7 days)
     │
     ├──► AWS Textract  (ap-south-1)
     │    Images: DetectDocumentText (sync, ~2s)
     │    PDFs:   StartDocumentTextDetection (async, poll up to 55s)
     │
     └──► DynamoDB Table: DocumentOCR  (ap-south-2)
          Full OCR results + metadata
          Partition key: document_id (UUID)
```

---

## Folder structure

```
WP1-target1-BREATHE/
├── NLP-Pipleline-frontEnd/
│   ├── ocr-explorer/              React application
│   │   ├── src/
│   │   │   ├── App.js             Tab shell
│   │   │   ├── api.js             All API calls
│   │   │   └── components/
│   │   │       ├── ExtractTab.js  Upload + extract
│   │   │       ├── HistoryTab.js  Browse archive
│   │   │       ├── ResultPanel.js Shared result display
│   │   │       └── Modal.js       Overlay modal
│   │   └── .env.example           API URL config template
│   └── NLP-Pipeline-Work-README.md   Frontend docs
│
├── textract-backend/
│   ├── lambda_processor.py        POST /process-ocr
│   ├── lambda_retrieval.py        GET /documents, GET /document/{id}
│   └── NLP-Pipeline-Backend-README.md  Backend docs
│
└── WP1-targets-BREATHE-README.md  This file
```

---

## How the two sides talk to each other

The frontend and backend communicate only through the API Gateway URL.

```
Frontend (React)
    │
    │  REACT_APP_API_URL=https://xxx.execute-api.ap-south-1.amazonaws.com/prod
    │
    ├── POST /process-ocr      → sends file, gets OCR result + document_id
    ├── GET  /documents        → gets list of all docs (for History tab)
    └── GET  /document/{id}   → gets full OCR + presigned S3 URL (for doc viewer)
```

The presigned S3 URL is a temporary link (valid 1 hour) that lets the browser
display the original document without any public S3 access.

---

## Quick start

**Backend** — deploy the two Lambda zip files and set up API Gateway routes.
See `textract-backend/NLP-Pipeline-Backend-README.md` for full instructions.

**Frontend:**

```bash
cd NLP-Pipleline-frontEnd/ocr-explorer
cp .env.example .env
# Edit .env — paste your API Gateway URL
npm install
npm start
```

---

## Current status

| Component | Status |
|---|---|
| Document upload | Done |
| AWS Textract OCR | Done |
| S3 storage | Done |
| DynamoDB persistence | Done |
| History / archive browser | Done |
| Document viewer (presigned URL) | Done |
| NLP pipeline (entity extraction, classification) | In progress |

---

## Cost summary

For a research-scale workload (a few hundred documents per month):

| Monthly volume | Estimated cost |
|---|---|
| Up to ~200 documents | Free (AWS free tier) |
| ~500 documents | ~$2–3 / month |
| ~5,000 documents | ~$35–40 / month |

The main cost driver is AWS Textract (~$0.0015 per page).
All other services (Lambda, S3, DynamoDB, API Gateway) are negligible at research scale.

Full cost breakdown is in `textract-backend/NLP-Pipeline-Backend-README.md`.
