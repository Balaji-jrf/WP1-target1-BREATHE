# BREATHE Archive Lens · Frontend

The React app for uploading documents, reviewing OCR output, and browsing facts.
It is in `ocr-explorer/` and uses the Sydney API by default.

## Main views

- **Input Extract** uploads a PDF or image directly to private S3 and waits for OCR.
- **OCR History** filters documents by their saved tag; admins can request complete
  document deletion.
- **AI-Extraction & Database** reviews AI and user-saved facts, with document-level
  tag, theme, source, status, location, and PDF-page filters. The page number is the
  page within that one document (Doc PG). The document list and PDF viewer panes can
  be resized.
- **Active entity taxonomy** displays the themes used to guide extraction.
- **Atlas** embeds the independent `jrf-task-atlas/frontend` React/Vite app at
  `https://main.d1g1u8rtpgjn48.amplifyapp.com`. It lists approved facts, supports
  place-match review, and displays confirmed geotags using the deployed Atlas APIs.
- **Users** is visible to admins and assigns tab permissions.

The extraction source filter groups `user_saved` and manually added `manual_fact`
records under **User saved**. AI-generated records use `thematic_fact`.

## Run and build

```bash
cd NLP-Pipleline-frontEnd/ocr-explorer
npm install
npm start
npm run build
```

Set `REACT_APP_API_URL` in `.env` to override the production API. Deployment requires
the configured frontend origin to be allowed by API Gateway and S3 CORS.
For local Atlas integration, run its Vite app on port 5173 and set
`REACT_APP_ATLAS_APP_URL=http://localhost:5173` in this app's `.env`. The production
build defaults to the deployed Atlas Amplify URL. Atlas derives the Archive Lens
origin from the iframe referrer by default. The parent forwards the signed-in token
only after an exact-origin and iframe-source-checked readiness message.

See the [technical app notes](./ocr-explorer/NLP-frontend-App-Technical-doc-README.md)
and the repository [system guide](../SYSTEM-GUIDE.md) for request flows and data shapes.
See the [Atlas guide](../jrf-task-atlas/README.md) for the standalone app contract.
