# BREATHE NLP Pipeline · Backend

The backend uses AWS Lambda, API Gateway, S3, Textract, DynamoDB, and Amazon Bedrock
to store document uploads, OCR output, and reviewed facts. The AWS region is
`ap-southeast-2` unless the selected Bedrock model is hosted elsewhere.

## Processing flow

```text
Browser ── GET /upload-url ──> upload URL Lambda
   │                              │
   └── direct S3 PUT ─────────────┘
                                  │ uploads/{document_id}/...
                                  ▼
                         S3 upload notification
                                  │
                                  ▼
                         OCR starter Lambda
                                  │ Textract
                    processed/{document_id}/ocr.json
                                  │
                 DocumentOCR metadata/status row

Browser ── GET /documents, /document/{id} ──> retrieval Lambda
Browser ── records / taxonomy / delete ─────> retrieval Lambda
Browser ── POST /api/documents/ingest-dynamo > extraction handler
```

The browser sends files directly to S3 using a short-lived URL; API Gateway does not
proxy the file bytes. The OCR JSON and figure crops remain in private S3 and are
presented through temporary presigned URLs.

## Data tables

| DynamoDB table | Key | Purpose |
|---|---|---|
| `jrf-task-ocr-DocumentOCR` | `document_id` (partition) | One document's filename, tag, status, S3 keys, counts, and extraction review totals. |
| `jrf-task-ocr-ExtractedRecords` | `id` (partition), `theme_entity_id` (sort) | One fact per item. `id` is `DOC#{document_id}`. `ThemeIndex` uses `theme` + `document_id`; `LocationIndex` uses `district` + `document_id`. |
| `jrf-task-data-EntityTaxonomy` | `entity_key` (partition) | Active extraction themes, descriptions, keywords, and guidance. |
| `jrf-task-BreatheUsers` | `username` (partition) | User role, password hash, email, and tab permissions. |

An extracted record stores its theme, concise fact, source excerpt, `pdf_page_num`,
location and time fields, confidence, extraction type, and review status. AI facts use
`entity_type=thematic_fact`; manual facts use `manual_fact`; OCR items saved by a user
may use `user_saved`.

## AI fact extraction

`ai_extractor.py` loads active themes from `jrf-task-data-EntityTaxonomy`. It groups
OCR text by page, limits each chunk to a bounded size, and sends chunks to Bedrock
sequentially. The model must select an active theme and return a concise fact with
supporting source text. Invalid themes and unsupported excerpts are rejected. Records
are saved as `pending_review`; a reviewer can edit, approve, or delete them.

Allowed model routes are configured in `ai_extractor.py`: Nova Lite, Nova 2 Lite, and
Ministral in Sydney, plus Llama 3.1 8B in `us-west-2`. AWS account/model access and
cross-region permissions must be enabled for the selected model.

`lambda_starter.py` runs OCR after an upload. `ai_extractor.py` contains chunking,
prompt, model-routing, and validation logic. `dynamo_ingestion.py` and
`lambda_function.py` provide AI extraction/write flows; `lambda_retrieval.py` also
contains the existing extraction API route and implements document, record, taxonomy,
and deletion APIs. Confirm the deployed API Gateway integration matches the chosen
handler and package; source code alone does not create an API Gateway route.

## Document deletion

`DELETE /document/{document_id}` is configured on the `prod` API Gateway stage and
connects to the retrieval Lambda. The Lambda has access to the user table and receives
the same `JWT_SECRET` as the auth Lambda. For a new environment, add this route and
grant the API Gateway stage permission to invoke the Lambda. The endpoint:

1. Verifies the token signature and expiry, then checks the user's current DynamoDB
   role is `admin`.
2. Rejects documents whose OCR or AI extraction is still running.
3. Deletes the document's S3 upload and `processed/{document_id}/` objects.
4. Deletes all extracted-record rows with partition key `DOC#{document_id}`.
5. Deletes the DocumentOCR metadata row last.

Configure API CORS to permit the `authorization` header. S3 deletion errors are
reported and leave the metadata available for a retry. The frontend confirmation is
not a substitute for this server-side admin check.

## API routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/upload-url` | Create upload ID and presigned URL. |
| GET | `/documents` | List document metadata. |
| GET, DELETE | `/document/{document_id}` | Read document / admin-delete document data. |
| PUT | `/document/{document_id}/meta` | Save document name and tag. |
| POST | `/api/documents/ingest-dynamo` | Start AI extraction for completed OCR. |
| GET, POST | `/api/documents/{document_id}/records` | List or add facts. |
| PUT, DELETE | `/api/documents/{document_id}/records/{theme_entity_id}` | Edit or delete a fact. |
| POST | `/api/documents/{document_id}/records/approve` | Approve selected records or all pending records. |
| GET, POST | `/api/database/taxonomy` | Read or save extraction themes. |
| DELETE | `/api/database/taxonomy/{entity_key}` | Remove a theme. |

## Local checks and deployment

Run backend tests from the backend directory:

```bash
cd textract-backend
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 -m py_compile lambda_retrieval.py lambda_auth.py
```

Before deploying, package all modules imported by the configured Lambda handler, set
table/bucket names and permissions, configure API routes/CORS, and ensure the retrieval
Lambda receives `JWT_SECRET` and `USERS_TABLE` through protected environment settings.
Never put secret values in README files, source control, or terminal logs.

The [system guide](../SYSTEM-GUIDE.md) gives the full architecture and schema map.
