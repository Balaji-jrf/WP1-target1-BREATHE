# NLP Pipeline — Backend

## Architecture

```
Browser
  │
  ├─ 1. GET /upload-url?filename=foo.pdf
  │       └─ breathe-textract-processor  (lambda_processor.py)
  │          Generates presigned S3 PUT URL + document_id (UUID)
  │          Returns: { upload_url, document_id, s3_key, content_type }
  │
  ├─ 2. PUT file → S3 directly (bypasses API Gateway — no size limit)
  │       Bucket: jrf-task-ocr-docs-bucket (ap-south-2)
  │       Key:    uploads/{document_id}/{filename}
  │
  │       S3 PutObject event fires automatically →
  │       └─ breathe-textract-starter  (lambda_starter.py)  [900s timeout]
  │
  │          Images (PNG/JPEG/TIFF/WebP):
  │            1. Write status=processing to DynamoDB
  │            2. Call Textract DetectDocumentText (sync, ~2s)
  │            3. Write ocr.json → S3: processed/{document_id}/ocr.json
  │            4. Write status=completed to DynamoDB
  │
  │          PDFs:
  │            1. Write status=processing to DynamoDB
  │            2. Copy PDF to jrf-task-ocr-textract-bucket (ap-south-1)
  │            3. Start Textract StartDocumentTextDetection (async)
  │            4. Store textract_job_id in DynamoDB
  │            5. Poll GetDocumentTextDetection every 10s (up to 850s)
  │               — all inside this Lambda, no API Gateway involved —
  │            6. Write ocr.json → S3: processed/{document_id}/ocr.json
  │            7. Write status=completed to DynamoDB
  │
  └─ 3. GET /document/{id}  (frontend polls every 5s)
          └─ breathe-textract-retrieval  (lambda_retrieval.py)  [pure reader]
             - DynamoDB get_item  (~10ms)
             - If completed: S3 get_object on ocr.json, inline elements
             - Generate presigned GET URL for original document (1hr)
             - Returns full response in ~100ms regardless of doc size
             - Never calls Textract
```

---

## S3 storage layout

```
jrf-task-ocr-docs-bucket  (ap-south-2 — permanent)
├── uploads/
│   └── {document_id}/
│       └── {filename}            ← original uploaded file
└── processed/
    └── {document_id}/
        └── ocr.json              ← full OCR result (any size, no DynamoDB limit)

jrf-task-ocr-textract-bucket  (ap-south-1 — Textract input only)
└── uploads/
    └── {document_id}/
        └── {filename}            ← PDF copy for Textract (can auto-delete after 7 days)
```

---

## ocr.json structure

```json
{
  "document_id": "b8279669-2293-44d4-87d6-649875fa78b6",
  "filename":    "1937 Some-Economic facts and figures.pdf",
  "total_pages": 53,
  "elements": [
    {
      "page":       1,
      "text":       "SOME ECONOMIC FACTS AND FIGURES",
      "confidence": 0.9987,
      "bbox":       [0.1823, 0.0412, 0.8177, 0.0601]
    }
  ]
}
```

- `confidence` — 0.0 to 1.0 (Textract's raw value divided by 100)
- `bbox` — `[left, top, right, bottom]` as fractions of page size (0.0–1.0)
- Only `LINE` blocks are stored — `WORD` and `PAGE` blocks are discarded

---

## DynamoDB — DocumentOCR (ap-south-2)

Partition key: `document_id` (String). No sort key. One item = one document.

| Field | Type | Notes |
|---|---|---|
| `document_id` | String (PK) | UUID |
| `filename` | String | Original filename |
| `status` | String | `processing` / `completed` / `failed` |
| `s3_bucket` | String | `jrf-task-ocr-docs-bucket` |
| `s3_key` | String | `uploads/{document_id}/{filename}` |
| `ocr_s3_key` | String | `processed/{document_id}/ocr.json` — set when completed |
| `textract_job_id` | String | PDF async job ID — present only during processing |
| `total_pages` | Number | Set when completed |
| `element_count` | Number | Set when completed |
| `file_size_bytes` | Number | From S3 event |
| `processing_time_seconds` | Number | Wall-clock seconds from upload to completion |
| `uploaded_at` | String | ISO 8601 UTC |

DynamoDB items are metadata only — the OCR text lives in `ocr.json` in S3.
No DynamoDB 400 KB item size limit risk regardless of document length.

---

## Lambda functions

| Function | File | Trigger | Timeout |
|---|---|---|---|
| `breathe-textract-processor` | `lambda_processor.py` | `GET /upload-url` | 30s |
| `breathe-textract-starter` | `lambda_starter.py` | S3 PutObject `uploads/*` | 900s |
| `breathe-textract-retrieval` | `lambda_retrieval.py` | `GET /documents`, `GET /document/{id}` | 120s |

---

## API Gateway routes

HTTP API id: `cblo1ukhb4`, region: `ap-south-2`

| Method | Path | Lambda |
|---|---|---|
| GET | `/upload-url` | breathe-textract-processor |
| GET | `/documents` | breathe-textract-retrieval |
| GET | `/document/{document_id}` | breathe-textract-retrieval |

---

## Environment variables

### breathe-textract-processor
| Key | Value |
|---|---|
| `S3_BUCKET` | `jrf-task-ocr-docs-bucket` |
| `ALLOWED_ORIGIN` | `https://jrf-nlp-pipeline.skillrouteai.com` |

### breathe-textract-starter
| Key | Value |
|---|---|
| `S3_BUCKET` | `jrf-task-ocr-docs-bucket` |
| `S3_TEXTRACT_BUCKET` | `jrf-task-ocr-textract-bucket` |
| `DYNAMO_TABLE` | `DocumentOCR` |
| `TEXTRACT_REGION` | `ap-south-1` |

### breathe-textract-retrieval
| Key | Value |
|---|---|
| `DYNAMO_TABLE` | `DocumentOCR` |
| `ALLOWED_ORIGIN` | `https://jrf-nlp-pipeline.skillrouteai.com` |

---

## IAM role — breathe-textract-lambda-role

| Service | Actions |
|---|---|
| S3 `jrf-task-ocr-docs-bucket/*` | `GetObject`, `PutObject`, `CopyObject` |
| S3 `jrf-task-ocr-textract-bucket/*` | `GetObject`, `PutObject`, `CopyObject` |
| DynamoDB `DocumentOCR` | `PutItem`, `GetItem`, `Scan`, `UpdateItem` |
| Textract | `DetectDocumentText`, `StartDocumentTextDetection`, `GetDocumentTextDetection` |

---

## Limits

| Item | Limit | Notes |
|---|---|---|
| Upload file size | None | Direct S3 PUT, bypasses API Gateway |
| PDF pages | 500+ | Textract async supports up to 3000 pages |
| Processing time | 850s max | Starter Lambda polls internally, 900s timeout |
| Poll response time | ~100ms | Retrieval Lambda is a pure reader — no Textract calls |
| Presigned URL validity | 1 hour | For both document view and OCR JSON |

---

## Deploy

```bash
cd textract-backend

zip -j processor.zip lambda_processor.py
aws lambda update-function-code --function-name breathe-textract-processor \
  --zip-file fileb://processor.zip --region ap-south-2

zip -j starter.zip lambda_starter.py
aws lambda update-function-code --function-name breathe-textract-starter \
  --zip-file fileb://starter.zip --region ap-south-2

zip -j retrieval.zip lambda_retrieval.py
aws lambda update-function-code --function-name breathe-textract-retrieval \
  --zip-file fileb://retrieval.zip --region ap-south-2
```

---

## AWS account details

| Item | Value |
|---|---|
| Account ID | `433448338709` |
| Primary region | `ap-south-2` (Hyderabad) |
| Textract region | `ap-south-1` (Mumbai) |
| Amplify app ID | `d2lfia267k9492` (ap-south-1) |
| GitHub repo | `https://github.com/Balaji-jrf/WP1-target1-BREATHE` |
