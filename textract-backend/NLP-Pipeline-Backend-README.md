# NLP Pipeline — Backend

AWS serverless backend for the BREATHE WP1 OCR pipeline.
Two Lambda functions handle document processing and retrieval.
No servers to manage — everything scales automatically.

---

## Architecture overview

```
User (browser)
     │
     │  POST /process-ocr  (multipart file upload)
     │  GET  /documents
     │  GET  /document/{id}
     ▼
┌─────────────────────────────┐
│   AWS API Gateway (HTTP)    │  ap-south-1
│   Invoke URL: your-api.     │
│   execute-api.amazonaws.com │
└────────────┬────────────────┘
             │ routes by path
     ┌───────┴────────┐
     ▼                ▼
┌──────────────┐  ┌──────────────────┐
│  Processor   │  │   Retrieval      │
│  Lambda      │  │   Lambda         │
│  (Python)    │  │   (Python)       │
└──────┬───────┘  └────────┬─────────┘
       │                   │
       │ put_object        │ get_item / scan
       ▼                   ▼
┌─────────────┐      ┌─────────────────┐
│  S3 Bucket  │      │    DynamoDB     │
│  (docs)     │      │  DocumentOCR    │
│  ap-south-2 │      │  ap-south-2     │
└──────┬──────┘      └─────────────────┘
       │ copy_object
       ▼
┌─────────────────┐
│  S3 Bucket      │
│  (textract)     │
│  ap-south-1     │
└──────┬──────────┘
       │ StartDocumentTextDetection (PDF)
       │ DetectDocumentText (images)
       ▼
┌─────────────────┐
│  AWS Textract   │
│  ap-south-1     │
└─────────────────┘
```

---

## Why two S3 buckets?

AWS Textract is only available in certain regions.
The project uses `ap-south-1` (Mumbai) for Textract.
The main document store uses `ap-south-2` (Hyderabad) for data residency.

When a PDF is uploaded, the Processor Lambda:
1. Saves the file to the main S3 bucket (ap-south-2)
2. Copies it to the Textract S3 bucket (ap-south-1)
3. Calls Textract from ap-south-1

Images are sent directly to Textract as bytes — no S3 copy needed.

---

## AWS services used

| Service | Region | Purpose |
|---|---|---|
| API Gateway (HTTP API) | ap-south-1 | Single entry point for all frontend requests |
| Lambda — Processor | ap-south-1 | Handles file upload, runs Textract, saves to DB |
| Lambda — Retrieval | ap-south-1 | Lists documents, fetches single document + presigned URL |
| S3 — docs bucket | ap-south-2 | Permanent storage of original uploaded files |
| S3 — textract bucket | ap-south-1 | Temporary copy of PDFs for Textract to read |
| AWS Textract | ap-south-1 | Extracts text from documents |
| DynamoDB | ap-south-2 | Stores OCR results and document metadata |

---

## Lambda: Processor (`lambda_processor.py`)

**Triggered by:** POST /process-ocr

**What it does, step by step:**

```
1. Parse the multipart form data to get the file bytes and filename
2. Check file size (max 10 MB)
3. Detect if it's a PDF or an image
4. Generate a unique document_id (UUID)
5. Save the file to S3:  uploads/{document_id}/{filename}
6. Run Textract:
     Image → DetectDocumentText (sync, ~1-3 seconds)
     PDF   → StartDocumentTextDetection (async, poll every 1s, max 55s)
7. Convert Textract blocks to clean elements:
     { page, text, confidence, bbox: [left, top, right, bottom] }
8. Write everything to DynamoDB
9. Return the OCR result as JSON
```

**DynamoDB record written:**

```json
{
  "document_id":   "uuid-v4",
  "filename":      "scan.pdf",
  "s3_bucket":     "jrf-task-ocr-docs-bucket",
  "s3_key":        "uploads/{id}/scan.pdf",
  "uploaded_at":   "2024-01-15T10:30:00Z",
  "total_pages":   3,
  "element_count": 142,
  "status":        "completed",
  "ocr_result": {
    "filename":    "scan.pdf",
    "total_pages": 3,
    "elements":    [ ... ]
  }
}
```

**Environment variables:**

| Variable | Default | Description |
|---|---|---|
| `S3_BUCKET` | `jrf-task-ocr-docs-bucket` | Main document storage bucket (ap-south-2) |
| `S3_TEXTRACT_BUCKET` | `jrf-task-ocr-textract-bucket` | Textract input bucket (ap-south-1) |
| `DYNAMO_TABLE` | `DocumentOCR` | DynamoDB table name |
| `TEXTRACT_REGION` | `ap-south-1` | Region where Textract runs |
| `MAX_UPLOAD_MB` | `10` | Max file size in MB |

---

## Lambda: Retrieval (`lambda_retrieval.py`)

**Triggered by:** GET /documents and GET /document/{id}

**GET /documents — list all:**
- Scans DynamoDB with a projection (only metadata fields, not the full OCR payload)
- Returns documents sorted newest first
- Paginates automatically if there are more than 1 MB of results

**GET /document/{id} — single document:**
- Fetches the full DynamoDB record including the complete OCR result
- Generates a presigned S3 URL (valid for 1 hour) so the browser can display the original file
- Returns both the OCR result and the presigned URL

**Environment variables:**

| Variable | Default | Description |
|---|---|---|
| `DYNAMO_TABLE` | `DocumentOCR` | DynamoDB table name |

---

## DynamoDB table: DocumentOCR

| Attribute | Type | Notes |
|---|---|---|
| `document_id` | String (PK) | UUID, partition key |
| `filename` | String | Original filename |
| `s3_bucket` | String | Bucket where file is stored |
| `s3_key` | String | Full S3 path |
| `uploaded_at` | String | ISO 8601 timestamp |
| `total_pages` | Number | Page count |
| `element_count` | Number | Number of text lines extracted |
| `status` | String | Always "completed" for now |
| `ocr_result` | Map | Full OCR payload with all elements |

---

## S3 buckets

**jrf-task-ocr-docs-bucket** (ap-south-2)
- Stores all original uploaded files
- Path pattern: `uploads/{document_id}/{filename}`
- No public access — files are accessed via presigned URLs only

**jrf-task-ocr-textract-bucket** (ap-south-1)
- Receives copies of PDFs before Textract processing
- Can be set with a lifecycle rule to auto-delete after 7 days (saves cost)

---

## API routes

| Method | Path | Lambda | Description |
|---|---|---|---|
| POST | `/process-ocr` | Processor | Upload and extract text from a document |
| GET | `/documents` | Retrieval | List all processed documents |
| GET | `/document/{document_id}` | Retrieval | Get full OCR result for one document |
| OPTIONS | any | both | CORS preflight (handled in each Lambda) |

---

## CORS

Both Lambdas return these headers on every response:

```
Access-Control-Allow-Origin:  *
Access-Control-Allow-Methods: POST,OPTIONS  (Processor) / GET,OPTIONS (Retrieval)
Access-Control-Allow-Headers: Content-Type
```

Before going to production, change `*` to your actual frontend domain.

---

## Deploying the Lambdas

Each Lambda is packaged as a `.zip` file containing the Python file and its dependencies.

```bash
# Package Processor Lambda
cd textract-backend
zip lambda_processor.zip lambda_processor.py

# Package Retrieval Lambda
zip lambda_retrieval.zip lambda_retrieval.py
```

Then upload each zip to its Lambda function in the AWS Console,
or use the AWS CLI:

```bash
aws lambda update-function-code \
  --function-name ocr-processor \
  --zip-file fileb://lambda_processor.zip \
  --region ap-south-1

aws lambda update-function-code \
  --function-name ocr-retrieval \
  --zip-file fileb://lambda_retrieval.zip \
  --region ap-south-1
```

---

## IAM permissions needed

The Processor Lambda's execution role needs:

```
s3:PutObject       on jrf-task-ocr-docs-bucket/*
s3:CopyObject      on jrf-task-ocr-textract-bucket/*
textract:DetectDocumentText
textract:StartDocumentTextDetection
textract:GetDocumentTextDetection
dynamodb:PutItem   on DocumentOCR table
```

The Retrieval Lambda's execution role needs:

```
dynamodb:Scan      on DocumentOCR table
dynamodb:GetItem   on DocumentOCR table
s3:GetObject       on jrf-task-ocr-docs-bucket/*  (for presigned URL generation)
```

---

## Cost estimation

All costs below are for the AWS ap-south-1 / ap-south-2 regions.
Prices are approximate as of 2024 — check https://calculator.aws for exact figures.

### AWS Textract

Textract charges per page processed.

| Volume | Price per page | Monthly cost |
|---|---|---|
| First 1,000 pages/month | Free (free tier) | £0 |
| 1,001 – 1,000,000 pages | ~$0.0015 per page | ~$1.50 per 1,000 pages |
| Over 1,000,000 pages | ~$0.0006 per page | ~$0.60 per 1,000 pages |

**Example:**
- 500 documents × 5 pages each = 2,500 pages/month → ~$2.25/month
- 5,000 documents × 5 pages each = 25,000 pages/month → ~$36/month

### AWS Lambda

Lambda charges for number of requests and duration.

| Metric | Free tier | Price after free tier |
|---|---|---|
| Requests | 1 million/month free | $0.20 per million |
| Duration (128 MB) | 400,000 GB-seconds free | $0.0000166667 per GB-second |

**Example (Processor Lambda, 512 MB, avg 10s per PDF):**
- 1,000 uploads/month = 1,000 requests × 10s × 0.5 GB = 5,000 GB-seconds
- Cost: ~$0.08/month (well within free tier)

### S3 Storage

| Item | Price |
|---|---|
| Storage | $0.025 per GB/month (ap-south-2) |
| PUT requests | $0.005 per 1,000 |
| GET requests | $0.0004 per 1,000 |

**Example (1,000 documents, avg 2 MB each = 2 GB):**
- Storage: ~$0.05/month
- Requests: negligible

### DynamoDB

| Item | Free tier | Price after |
|---|---|---|
| Storage | 25 GB free | $0.285 per GB/month |
| Read units | 25 RCU free | $0.285 per million RCUs |
| Write units | 25 WCU free | $1.425 per million WCUs |

For a research project processing hundreds of documents, DynamoDB will stay
within the free tier for a long time.

### API Gateway (HTTP API)

| Volume | Price |
|---|---|
| First 300 million requests/month | $1.00 per million |

For a research project: effectively free.

---

### Total estimated monthly cost

| Documents processed | Approx. monthly cost |
|---|---|
| 0 – 200 docs (≤1,000 pages) | **Free** (all within free tiers) |
| ~500 docs (~2,500 pages) | **~$2–3 / month** |
| ~5,000 docs (~25,000 pages) | **~$35–40 / month** |
| ~50,000 docs (~250,000 pages) | **~$350–380 / month** |

The dominant cost at scale is Textract page processing.
S3, Lambda, DynamoDB, and API Gateway are negligible for a research workload.
