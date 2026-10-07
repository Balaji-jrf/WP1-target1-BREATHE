const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

export async function uploadDocument(file, onStatus) {
  const formData = new FormData();
  formData.append('file', file);
  const response = await fetch(`${API_URL}/process-ocr`, { method: 'POST', body: formData });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || 'OCR processing failed.');

  if (response.status === 202) {
    onStatus?.('Processing large PDF…');
    return await pollUntilDone(payload.document_id, onStatus);
  }
  return payload;
}

async function pollUntilDone(document_id, onStatus, intervalMs = 5000, maxAttempts = 240) {
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise(r => setTimeout(r, intervalMs));
    const res = await fetch(`${API_URL}/document/${document_id}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Polling failed.');
    if (data.status === 'completed') return data.ocr_result
      ? { ...data.ocr_result, document_id, uploaded_at: data.uploaded_at, s3_presigned_url: data.s3_presigned_url }
      : data;
    if (data.status === 'failed') throw new Error('OCR processing failed on the server.');
    const elapsed = Math.round(((i + 1) * intervalMs) / 1000);
    onStatus?.(`Processing… ${elapsed}s elapsed`);
  }
  throw new Error('OCR timed out after 20 minutes. Check the History tab — it may still complete.');
}

export async function fetchDocuments() {
  const res = await fetch(`${API_URL}/documents`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Failed to load history');
  return data.documents || [];
}

export async function fetchDocument(document_id) {
  const response = await fetch(`${API_URL}/document/${document_id}`);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || 'Failed to fetch document.');
  return payload;
}
