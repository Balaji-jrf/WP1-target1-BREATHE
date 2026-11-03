# OCR Explorer · Technical Notes

This Create React App frontend is the user interface for the BREATHE document archive.
The `src/api.js` module handles upload, polling, OCR retrieval, metadata, facts, and
document operations. `App.js` controls the tabs; `AuthContext.js` provides login state
and tab access; each tab lives in `src/components/`.

## User and data flow

1. The browser asks `GET /upload-url?filename=...` for a document ID and short-lived
   S3 upload URL.
2. The browser uploads the file directly to S3. The S3 upload event starts OCR.
3. The app polls `GET /document/{document_id}`. When OCR completes, it fetches OCR JSON
   from the returned presigned S3 URL.
4. The History view reads `GET /documents`. Document tags are loaded from each
   document's `doc_tag` and filtered in the browser.
5. The Database view uses `/api/documents/{document_id}/records` to view and edit
   facts. The page number is a PDF page within the selected document.
6. AI facts use the active DynamoDB taxonomy; users can edit, approve, or remove facts.
7. Admins may delete a document through `DELETE /document/{document_id}`. The API
   verifies the signed token and current admin role before removing stored data.

## Interface details

Tag dropdowns share `src/documentTags.js`. `AtlasTab.js` embeds the independent
`jrf-task-atlas/frontend` app in a full-width iframe. It accepts only the configured
Atlas origin and the iframe's own `contentWindow` when forwarding the signed-in token.
The token is sent in a `postMessage`, never in the app URL. Set
`REACT_APP_ATLAS_APP_URL` to the Vite development server (`http://localhost:5173`) or
the deployed Atlas origin (`https://main.d1g1u8rtpgjn48.amplifyapp.com`). The Database list and PDF panes use accessible resize
handles. OCR Compare can resize both the dialog and its side-by-side panes.

Tab permissions control navigation in the UI. API permissions must also be enforced on
the server; currently, the document delete route has a server-side admin check, while
other retrieval routes retain their existing access behavior.

For table schemas, API routes, and the Atlas architecture, see the repository
[system guide](../../SYSTEM-GUIDE.md) and [Atlas guide](../../jrf-task-atlas/README.md). For local setup, see the
[frontend README](../NLP-Pipeline-Work-README.md).
