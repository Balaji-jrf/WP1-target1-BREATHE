const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

export async function uploadDocument(file, onStatus) {
  // 1. Get presigned S3 upload URL from backend
  onStatus?.('Preparing upload…');
  const urlRes  = await fetch(`${API_URL}/upload-url?filename=${encodeURIComponent(file.name)}`);
  const urlData = await urlRes.json();
  if (!urlRes.ok) throw new Error(urlData.detail || 'Failed to get upload URL.');

  const { upload_url, document_id, content_type } = urlData;

  // 2. PUT file directly to S3 — no API Gateway, no size limit
  onStatus?.(`Uploading ${(file.size / 1024 / 1024).toFixed(1)} MB…`);
  const putRes = await fetch(upload_url, {
    method:  'PUT',
    body:    file,
    headers: { 'Content-Type': content_type },
  });
  if (!putRes.ok) throw new Error(`S3 upload failed (${putRes.status})`);

  // 3. Poll GET /document/{id} until Textract finishes
  onStatus?.('Upload complete — starting OCR…');
  return await pollUntilDone(document_id, onStatus);
}

async function pollUntilDone(document_id, onStatus, intervalMs = 5000, maxAttempts = 240) {
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise(r => setTimeout(r, intervalMs));
    const res  = await fetch(`${API_URL}/document/${document_id}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Polling failed.');
    if (data.status === 'completed') {
      const ocr = data.ocr_result || data;
      return { ...ocr, document_id, uploaded_at: data.uploaded_at, s3_presigned_url: data.s3_presigned_url };
    }
    if (data.status === 'failed') throw new Error('OCR processing failed on the server.');
    const elapsed = Math.round(((i + 1) * intervalMs) / 1000);
    onStatus?.(`Processing… ${elapsed}s elapsed`);
  }
  throw new Error('OCR timed out after 20 minutes. Check the History tab — it may still complete.');
}

export async function fetchDocuments() {
  const res  = await fetch(`${API_URL}/documents`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Failed to load history');
  return data.documents || [];
}

export async function fetchDocument(document_id) {
  const res  = await fetch(`${API_URL}/document/${document_id}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Failed to fetch document.');
  return data;
}
