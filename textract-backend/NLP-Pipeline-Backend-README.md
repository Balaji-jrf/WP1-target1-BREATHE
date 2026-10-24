# BREATHE NLP Pipeline — Backend

AWS OCR ingestion and retrieval services for the BREATHE research project.

## Deployed resources

| Resource | Name | Region |
|---|---|---|
| S3 bucket | `jrf-task-ocr-931092012939-ap-southeast-2` | `ap-southeast-2` |
| DynamoDB table | `jrf-task-ocr-DocumentOCR` | `ap-southeast-2` |
| HTTP API | `jrf-task-ocr-api` (`o0dqnexpsg`) | `ap-southeast-2` |
| API stage/base URL | `prod` — `https://o0dqnexpsg.execute-api.ap-southeast-2.amazonaws.com/prod` | `ap-southeast-2` |

## Request and processing flow

```text
Browser
  ├─ GET /upload-url?filename=report.pdf
  │    └─ jrf-task-ocr-upload-url
  │         returns document_id + 15-minute presigned S3 PUT URL
  ├─ PUT file directly to S3: uploads/{document_id}/{filename}
  │    └─ S3 ObjectCreated notification invokes jrf-task-ocr-starter
  │         ├─ PNG/JPEG: AnalyzeDocument with TABLES + LAYOUT (synchronous)
  │         └─ PDF/TIFF: StartDocumentAnalysis and poll GetDocumentAnalysis
  │         ├─ writes processed/{document_id}/ocr.json and figure PNG crops
  │         └─ records processing status and metadata in DynamoDB
  ├─ GET /document/{document_id}
  │    └─ jrf-task-ocr-retrieval returns status and presigned document/OCR URLs
  └─ GET /documents
       └─ jrf-task-ocr-retrieval lists document metadata for History
```

The S3 notification is filtered to the `uploads/` prefix. OCR output is written under
`processed/`, so output writes do not invoke the starter again.

## S3 object layout

```text
jrf-task-ocr-931092012939-ap-southeast-2/
├── uploads/{document_id}/{filename}       original input
├── processed/{document_id}/ocr.json       OCR JSON
└── processed/{document_id}/figures/*.png  rendered figure crops
```

The bucket is private. The browser uses presigned URLs; do not make the bucket public.
Its CORS configuration must include each frontend origin that needs browser PUT, GET,
and HEAD access. The configured origins currently include the production site, test
site, and `http://localhost:3000`.

## OCR JSON

```json
{
  "document_id": "uuid",
  "filename": "report.pdf",
  "total_pages": 1,
  "elements": [
    {
      "page": 1,
      "text": "Detected text",
      "confidence": 0.9987,
      "bbox": [0.1, 0.2, 0.8, 0.3]
    }
  ],
  "layout": [
    {"type": "title", "page": 1, "text": "Report", "bbox": [0.1, 0.1, 0.8, 0.2]}
  ],
  "tables": [
    {"page": 1, "bbox": [0.1, 0.2, 0.8, 0.5], "rows": [["Item", "Count"]]}
  ],
  "figures": [
    {
      "figure_id": "figure-0001",
      "page": 1,
      "bbox": [0.1, 0.5, 0.8, 0.9],
      "crop_status": "saved",
      "image_s3_key": "processed/uuid/figures/page-0001-figure-0001.png"
    }
  ]
}
```

- `elements` preserves line text, confidence (0–1), page, and normalized bounding box.
- `layout` contains Textract layout types and associated text where available.
- `tables` contains table cell text arranged by row and column.
- `figures` contains the detected page and normalized bounding box, crop status, and
  S3 object key when the PNG crop was saved. Retrieval signs crop URLs for the browser;
  temporary URLs are not persisted in OCR JSON.

## DynamoDB table

Table: `jrf-task-ocr-DocumentOCR`

Partition key: `document_id` (String), no sort key.

The table stores document metadata and status (`processing`, `completed`, or `failed`).
OCR JSON remains in S3 to avoid DynamoDB's item-size limit. Common attributes include
`filename`, `s3_bucket`, `s3_key`, `ocr_s3_key`, `total_pages`, `element_count`,
`table_count`, `figure_count`, `uploaded_at`, `file_size_bytes`, and
`processing_time_seconds`.

## Lambda functions and roles

| Function | Handler source | Execution role | Purpose |
|---|---|---|---|
| `jrf-task-ocr-upload-url` | `lambda_processor.py` | `jrf-task-ocr-upload-url-role` | Signs browser upload URLs |
| `jrf-task-ocr-starter` | `lambda_starter.py` | `jrf-task-ocr-starter-role` | Runs Textract after S3 upload |
| `jrf-task-ocr-retrieval` | `lambda_retrieval.py` | `jrf-task-ocr-retrieval-role` | Reads metadata and signs result URLs |

Each role includes `AWSLambdaBasicExecutionRole` for CloudWatch Logs and a scoped inline
policy for its service access. The upload role can `s3:PutObject` only under `uploads/*`.
The starter accesses the OCR bucket, the OCR table, and Textract analysis APIs. The
retrieval role can read the OCR table and generate URLs for objects under `uploads/*`
and `processed/*`.

The starter Lambda needs PyMuPDF to render and crop detected figure regions. Its
dependency is listed in `requirements.txt`; include it in the deployment package for
the same Python runtime and architecture as the Lambda. Build the package in a
Linux-compatible environment, install the requirements into a staging directory, copy
`lambda_starter.py` there, and zip the staging contents before updating the function.
The package must remain within Lambda deployment size limits. The starter role needs
`s3:GetObject` on `uploads/*` and `s3:PutObject` on `processed/*`.

Example package commands (run from `textract-backend/` in a compatible Linux
environment):

```bash
mkdir -p build-starter
python3 -m pip install -r requirements.txt -t build-starter/
cp lambda_starter.py build-starter/
(cd build-starter && zip -r ../starter.zip .)
```

Only documents processed after deploying this version get figure crops automatically.
Older records continue to display detection metadata; re-upload the source file to make
a newly processed record with image crops.

Starter environment variables:

| Variable | Value |
|---|---|
| `S3_BUCKET` | `jrf-task-ocr-931092012939-ap-southeast-2` |
| `DYNAMO_TABLE` | `jrf-task-ocr-DocumentOCR` |

Upload URL environment variables:

| Variable | Value |
|---|---|
| `S3_BUCKET` | `jrf-task-ocr-931092012939-ap-southeast-2` |
| `ALLOWED_ORIGIN` | Frontend origin |

Retrieval environment variables:

| Variable | Value |
|---|---|
| `DYNAMO_TABLE` | `jrf-task-ocr-DocumentOCR` |
| `ALLOWED_ORIGIN` | Frontend origin |

Lambda provides `AWS_REGION` automatically. All functions and AWS clients use
`ap-southeast-2`.

## HTTP API routes

| Method | Path | Integration |
|---|---|---|
| `GET` | `/upload-url` | `jrf-task-ocr-upload-url` |
| `GET` | `/documents` | `jrf-task-ocr-retrieval` |
| `GET` | `/document/{document_id}` | `jrf-task-ocr-retrieval` |

The HTTP API must also be configured with the database routes below; adding handler
code alone does not create API Gateway routes.

| `POST` | `/api/documents/ingest-dynamo` | `jrf-task-dynamo-ingestion` — body: `{"document_id":"..."}` |
| `GET` | `/api/documents/{document_id}/records` | `jrf-task-ocr-retrieval` — filters: `theme`, `district`, `location`, `pdf_page_num`, `content_type`, `entity_type`, `status` |
| `POST` | `/api/documents/{document_id}/records` | `jrf-task-ocr-retrieval` — create a record |
| `PUT` | `/api/documents/{document_id}/records/{theme_entity_id}` | `jrf-task-ocr-retrieval` — edit a record |
| `DELETE` | `/api/documents/{document_id}/records/{theme_entity_id}` | `jrf-task-ocr-retrieval` — delete a record |
| `GET` | `/api/database/taxonomy` | `jrf-task-ocr-retrieval` — list the dynamic extraction schema |
| `POST` | `/api/database/taxonomy` | `jrf-task-ocr-retrieval` — add or update a taxonomy entry |
| `DELETE` | `/api/database/taxonomy/{entity_key}` | `jrf-task-ocr-retrieval` — remove a taxonomy entry |
| `POST` | `/api/documents/{document_id}/records/approve` | `jrf-task-ocr-retrieval` — approve selected staged records |

Extracted output is stored in `jrf-task-ocr-ExtractedRecords`, configured through
`EXTRACTED_RECORDS_TABLE`. It uses `(id, theme_entity_id)` keys and the ThemeIndex and
LocationIndex. The retrieval role needs DynamoDB read/write access to this table,
update access to DocumentOCR, and `s3:GetObject` for OCR JSON.

Add the routes above to the HTTP API with Lambda proxy integration to
`jrf-task-dynamo-ingestion` for the ingestion route and `jrf-task-ocr-retrieval`
for document-record and taxonomy routes. API Gateway CORS
must allow `GET, POST, PUT, DELETE, OPTIONS` and the `content-type` header for the
configured frontend origins. Package `dynamo_ingestion.py` as `lambda_function.py`
and include `ai_extractor.py` in the ingestion Lambda package because its configured
handler is `lambda_function.lambda_handler`.

### Nova Lite review workflow

The ingestion starter asynchronously invokes itself after validating OCR completion.
It groups OCR lines into page chunks of up to 1,500 characters, loads active entries
from `jrf-task-data-EntityTaxonomy`, and sends each chunk to Amazon Bedrock model
`amazon.nova-lite-v1:0` in `ap-southeast-2`. There is no local model fallback. Facts
with an unknown theme, fewer than five words / 35 characters, or confidence below
0.55 are discarded. Facts, tables, and figures are written with
`review_status=pending_review`; the document's `ingestion_status` is set accordingly.

`GET /api/documents/{document_id}/records` returns approved records by default.
Use `?status=pending_review` for the review queue or `?status=all` for all records.
The frontend can edit and delete staged records, add manual facts, and approve chosen
`theme_entity_id` values. Approval marks them `approved`; the document changes to
`completed` when no pending records remain. Taxonomy entries are prompt schema, so
new active themes are used on later ingestion runs without a backend code change.

The ingestion Lambda requires `BEDROCK_MODEL_ID=amazon.nova-lite-v1:0`,
`TAXONOMY_TABLE=jrf-task-data-EntityTaxonomy`, and permission for
`bedrock:InvokeModel` on the Nova Lite foundation model. It also needs permission to
invoke itself asynchronously and a timeout long enough for page-level model calls.
The retrieval role needs DynamoDB read/write access to the taxonomy table.

Provision or inspect the tables with:

```bash
python3 textract-backend/provision_tables.py --region ap-southeast-2
```

The current deployed DocumentOCR table has only the
`document_id` partition key. DynamoDB cannot add `record_type` as a sort key in place;
the ingestion writes `record_type="METADATA"` as a regular attribute while preserving
the existing table and its records. The script creates the extraction-entities table
and rejects incompatible existing key schemas rather than deleting or replacing data.
The legacy `jrf-task-data-Extraction-entities` table was migrated to
`jrf-task-ocr-ExtractedRecords`; 1,478 source records were key-verified before the
old table was removed. Backup `before-records-table-migration-20261024` is available
for recovery. `migrate_extracted_records.py` documents the one-time copy operation.
For a new environment, provision the OCR table with the same existing single-key
schema before deploying these functions.

Entity ingestion reads the completed `ocr_s3_key`, writes table, figure, and
Nova Lite extracted-fact records, and updates OCR metadata with counts and ingestion
status. Re-running ingestion upserts records with stable document/page identifiers.
Figure `s3_crop_url` values are stable `s3://` URIs; retrieval presigned URLs remain
short-lived and are returned only by the existing document endpoint.

The deployed `prod` stage has CORS configured for the production frontend and
`http://localhost:3000`. API routes currently use no authentication; CORS does not
protect the API from non-browser clients. Add authentication and request controls before
using it for sensitive documents or unrestricted public access.

## Local validation

From the repository root:

```bash
python3 -m venv textract-backend/.venv
textract-backend/.venv/bin/python -m pip install \
  -r textract-backend/requirements.txt
textract-backend/.venv/bin/python -m pip install boto3
textract-backend/.venv/bin/python -m unittest discover \
  -s textract-backend -p 'test_*.py' -v
```

Compile the Lambda source separately with:

```bash
python3 -m py_compile \
  textract-backend/lambda_processor.py \
  textract-backend/lambda_starter.py \
  textract-backend/lambda_retrieval.py
```

The unit tests cover upload URL validation, OCR block parsing, PDF/raster figure
cropping, and signing crop URLs. A synthetic PNG and a synthetic PDF containing a table
have also been processed successfully in the Sydney account.
