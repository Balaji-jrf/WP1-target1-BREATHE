# NLP Pipeline — Frontend

React app for the BREATHE WP1 OCR & NLP pipeline.
Upload historical documents, extract text via AWS Textract, browse the archive.

---

## What it does

1. User drops or selects a document (PDF, PNG, JPEG, TIFF, WebP)
2. App sends it to the backend API (AWS API Gateway → Lambda)
3. AWS Textract reads every line of text with confidence scores and bounding boxes
4. Results are shown on screen and saved to the database
5. History tab lets you browse every document ever processed

---

## Folder structure

```
ocr-explorer/
├── public/                  Static HTML shell
└── src/
    ├── api.js               All API calls in one place
    ├── App.js               Top-level shell — just handles tab switching
    ├── App.css              All styles
    ├── index.js             React entry point
    ├── index.css            Global reset + Google Fonts
    └── components/
        ├── ExtractTab.js    Upload form + shows result after extraction
        ├── HistoryTab.js    Grid of past documents + loads selected doc
        ├── ResultPanel.js   Shared result display (text blocks, doc viewer)
        └── Modal.js         Reusable overlay modal (Escape to close)
```

---

## Component map

```
App.js
 ├── Header (topbar, tab nav, status indicators)
 ├── ExtractTab
 │    ├── Dropzone (drag-and-drop or click to browse)
 │    ├── Upload button
 │    └── ResultPanel
 │         ├── Doc viewer (iframe for PDF, img for images)
 │         ├── Text block list (page, confidence, bbox)
 │         ├── Raw JSON toggle
 │         └── Action buttons
 │              ├── View Doc   → opens Modal with iframe/image
 │              ├── View OCR   → opens Modal with JSON
 │              └── Download   → downloads original file
 └── HistoryTab
      ├── Document grid (cards from DynamoDB)
      └── ResultPanel (same component, loaded on card click)
```

---

## How each file talks to the others

```
api.js  ←──────────────────────────────────────────────────────────────┐
  uploadDocument(file)      POST /process-ocr                          │
  fetchDocuments()          GET  /documents                            │
  fetchDocument(id)         GET  /document/{id}                        │
                                                                       │
ExtractTab.js  calls uploadDocument()  →  passes result to ResultPanel │
HistoryTab.js  calls fetchDocuments()  →  renders cards                │
               calls fetchDocument()   →  passes result to ResultPanel │
ResultPanel.js  renders text blocks, doc viewer, action buttons        │
Modal.js        used inside ResultPanel for View Doc / View OCR        │
App.js          switches between ExtractTab and HistoryTab             ┘
```

---

## API calls

| Function | Method | Endpoint | What it does |
|---|---|---|---|
| `uploadDocument` | POST | `/process-ocr` | Sends file, gets back OCR result + document_id |
| `fetchDocuments` | GET | `/documents` | Gets list of all processed docs (metadata only) |
| `fetchDocument` | GET | `/document/{id}` | Gets full OCR result + presigned S3 URL for one doc |

The base URL comes from the environment variable `REACT_APP_API_URL`.
Falls back to `http://localhost:8000` for local development.

---

## Environment setup

```bash
# 1. Copy the example env file
cp .env.example .env

# 2. Edit .env and paste your API Gateway URL
REACT_APP_API_URL=https://xxxxxxxxxx.execute-api.ap-south-1.amazonaws.com/prod

# 3. Install and run
npm install
npm start
```

---

## Build for production

```bash
npm run build
```

Output goes to `build/`. Deploy the contents of that folder to S3 + CloudFront
(or any static hosting). Make sure `REACT_APP_API_URL` is set at build time.

---

## Key design decisions

- No routing library — the app only has two views (Extract / History), so a simple `useState` tab switch is enough
- No state management library — all state is local to each tab component; they don't share state
- `api.js` is the only file that knows the API URL — easy to change in one place
- `ResultPanel` is shared between both tabs so the display is always consistent
- History cards toggle — click the same card again to collapse the result

---

## Supported file types

| Type | Extension | Notes |
|---|---|---|
| PDF | .pdf | Multi-page supported, processed async via Textract |
| PNG | .png | Sync processing, instant result |
| JPEG | .jpg .jpeg | Sync processing |
| TIFF | .tif .tiff | Sync processing |
| WebP | .webp | Sync processing |

Max file size: 10 MB (enforced by the backend Lambda)
