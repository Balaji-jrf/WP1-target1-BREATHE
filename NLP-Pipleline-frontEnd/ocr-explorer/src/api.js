const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

export async function uploadDocument(file) {
  const formData = new FormData();
  formData.append('file', file);
  const response = await fetch(`${API_URL}/process-ocr`, { method: 'POST', body: formData });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || 'OCR processing failed.');
  return payload;
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
