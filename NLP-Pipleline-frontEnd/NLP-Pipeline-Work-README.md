# BREATHE NLP Pipeline — Frontend

React application for uploading historical documents, viewing Textract OCR results,
and browsing the OCR archive.

- **Production app:** https://jrf-nlp-pipeline.skillrouteai.com
- **API base URL:** https://o0dqnexpsg.execute-api.ap-southeast-2.amazonaws.com/prod
- **AWS region:** Asia Pacific (Sydney), `ap-southeast-2`

## Upload and retrieval flow

1. The browser requests `GET /upload-url?filename=...`.
2. `jrf-task-ocr-upload-url` returns a document ID and a short-lived S3 PUT URL.
3. The browser uploads directly to the private OCR bucket using that URL.
4. The S3 `uploads/` object-created event invokes `jrf-task-ocr-starter`.
5. The UI polls `GET /document/{document_id}` for processing status.
6. When complete, the browser fetches OCR JSON from its presigned S3 URL.
7. History uses `GET /documents`; document details use `GET /document/{document_id}`.

The browser sends the file to S3 directly, not through API Gateway. Upload capacity is
still subject to S3, Textract, Lambda, and account service quotas; it is not unlimited.

## Supported files

| File type | Extensions | OCR processing |
|---|---|---|
| PDF | `.pdf` | Asynchronous Textract analysis |
| PNG | `.png` | Synchronous Textract analysis |
| JPEG | `.jpg`, `.jpeg` | Synchronous Textract analysis |
| TIFF | `.tif`, `.tiff` | Asynchronous Textract analysis |

## OCR result

The result panel shows the original document, detected text, page numbers, confidence,
and page-relative bounding boxes. OCR JSON also carries Textract layout blocks, table
rows, and figure bounding-box metadata. Detected figure regions are also saved as image
crops for the Figures view.

Results are presented in separate **Text**, **Layout**, **Tables**, and **Figures** views.
Text blocks show a confidence indicator and bounding box; layout regions show their
Textract block type; tables render as readable grids; and figures show page/location
metadata. Search text/layout/table results, filter to a page, and page through large
result sets (50 items at a time). The raw JSON remains available for inspection/download.

History's **Compare** view synchronizes the selected PDF page with the shared structured
OCR explorer on the right. Text, layout, tables, and figures are filtered to that page.
The browser's built-in PDF viewer does not expose text bounding boxes to the app, so
precise highlights drawn over text inside the PDF itself require a PDF.js-based viewer.

```json
{
  "page": 1,
  "text": "SOME ECONOMIC FACTS AND FIGURES",
  "confidence": 0.9987,
  "bbox": [0.1823, 0.0412, 0.8177, 0.0601]
}
```

`bbox` is `[left, top, right, bottom]`, normalized to page dimensions from 0 to 1.
Textract does not return cropped image bytes. The backend renders detected figure
regions from the source PDF/image, saves PNG crops under `processed/{document_id}/figures/`,
and returns fresh presigned URLs for the Figures view. Existing documents that were
processed before this feature have metadata only; reprocess them to generate crops.
If a crop cannot be rendered or its URL cannot be signed, the Figures view reports that
preview state instead of pretending an image is available.

## Run locally

Requirements: Node.js and npm.

```bash
cd NLP-Pipleline-frontEnd/ocr-explorer
npm install
```

The app defaults to the Sydney API. To override it locally, create an ignored `.env`:

```dotenv
REACT_APP_API_URL=https://o0dqnexpsg.execute-api.ap-southeast-2.amazonaws.com/prod
```

Then start the development server:

```bash
npm start
```

Open http://localhost:3000. The OCR S3 bucket CORS configuration must allow this origin
for local browser uploads and reads.

Run a production build with:

```bash
npm run build
```

## Amplify Hosting deployment

The repository's [`amplify.yml`](../../amplify.yml) configures the app root as
`NLP-Pipleline-frontEnd/ocr-explorer`. In Amplify Hosting, set the build-time environment
variable below for the connected branch, then trigger a build/deploy:

```text
REACT_APP_API_URL=https://o0dqnexpsg.execute-api.ap-southeast-2.amazonaws.com/prod
```

If the Amplify app deploys a custom domain, that exact origin must be allowed by both
the API's CORS configuration and the OCR bucket's CORS rules.

## Current API routes

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/upload-url?filename=...` | Generate a presigned S3 upload URL |
| `GET` | `/document/{document_id}` | Read document status and presigned URLs |
| `GET` | `/documents` | List OCR document metadata |

These routes currently have no authentication. CORS is a browser policy, not an access
control mechanism. Add authentication and abuse protection before using the public API
for sensitive documents or unrestricted access.
