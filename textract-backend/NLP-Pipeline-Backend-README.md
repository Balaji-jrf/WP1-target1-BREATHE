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
