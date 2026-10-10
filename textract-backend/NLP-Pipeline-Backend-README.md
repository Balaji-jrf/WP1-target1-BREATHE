# NLP Pipeline — Backend

## Architecture (current)

```
Browser
  │
  ├─ 1. GET /upload-url?filename=foo.pdf
  │       └─ lambda_processor.py
  │          Returns presigned S3 PUT URL + document_id
  │
  ├─ 2. PUT file → S3 directly (no API Gateway, no size limit)
  │       Bucket: jrf-task-ocr-docs-bucket (ap-south-2)
  │       Key:    uploads/{document_id}/{filename}
  │
  │       S3 PutObject event fires automatically
  │       └─ lambda_starter.py (S3 trigger)
  │          - Writes "processing" record to DynamoDB
  │          - Images: sync Textract → writes "completed"
  │          - PDFs:   copies to jrf-task-ocr-textract-bucket (ap-south-1)
  │                    starts async Textract job → stores job_id
  │
  └─ 3. GET /document/{id}  (poll every 5s)
          └─ lambda_retrieval.py
             - If status=processing: checks Textract job
             - If SUCCEEDED: writes completed record, returns result
             - If IN_PROGRESS: returns status=processing (frontend keeps polling)
             - Always returns presigned GET URL for document display
```

## Lambda functions

| Function | Trigger | Purpose |
|---|---|---|
| `breathe-textract-processor` | `GET /upload-url` | Issues presigned S3 PUT URL |
| `breathe-textract-starter` | S3 PutObject on `uploads/*` | Starts Textract job |
| `breathe-textract-retrieval` | `GET /documents`, `GET /document/{id}` | Lists docs, returns OCR result |

## API Gateway routes (HTTP API — cblo1ukhb4, ap-south-2)

| Method | Path | Lambda |
|---|---|---|
| GET | `/upload-url` | breathe-textract-processor |
| GET | `/documents` | breathe-textract-retrieval |
| GET | `/document/{document_id}` | breathe-textract-retrieval |

`POST /process-ocr` has been removed.

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
| `TEXTRACT_REGION` | `ap-south-1` |
| `ALLOWED_ORIGIN` | `https://jrf-nlp-pipeline.skillrouteai.com` |

## Limits — all removed

| Old limit | Old value | Now |
|---|---|---|
| Upload size | 10 MB (API Gateway) | Unlimited (direct S3) |
| Processing timeout | 30s (API Gateway) | Unlimited (async S3 trigger) |
| PDF pages | ~50 pages | 500+ pages supported |

## S3 buckets

| Bucket | Region | Purpose |
|---|---|---|
| `jrf-task-ocr-docs-bucket` | ap-south-2 | Permanent document storage |
| `jrf-task-ocr-textract-bucket` | ap-south-1 | Textract input (same region as Textract) |

S3 CORS is configured on `jrf-task-ocr-docs-bucket` to allow PUT from the frontend origins.

## DynamoDB — DocumentOCR (ap-south-2)

| Field | Type | Notes |
|---|---|---|
| `document_id` | String (PK) | UUID |
| `filename` | String | Original filename |
| `status` | String | `processing` / `completed` / `failed` |
| `textract_job_id` | String | Present while status=processing (PDFs only) |
| `ocr_result` | Map | Present when status=completed |
| `s3_key` | String | `uploads/{document_id}/{filename}` |
| `total_pages` | Number | |
| `element_count` | Number | |
| `uploaded_at` | String | ISO 8601 UTC |

## Deploy

```bash
cd textract-backend
zip -j lambda_processor.zip lambda_processor.py
zip -j lambda_starter.zip lambda_starter.py
zip -j lambda_retrieval.zip lambda_retrieval.py

aws lambda update-function-code --function-name breathe-textract-processor \
  --zip-file fileb://lambda_processor.zip --region ap-south-2

aws lambda update-function-code --function-name breathe-textract-starter \
  --zip-file fileb://lambda_starter.zip --region ap-south-2

aws lambda update-function-code --function-name breathe-textract-retrieval \
  --zip-file fileb://lambda_retrieval.zip --region ap-south-2
```
