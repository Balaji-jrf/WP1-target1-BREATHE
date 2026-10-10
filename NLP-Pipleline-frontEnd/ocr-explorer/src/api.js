const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

const log = (step, msg, data) => {
  const ts = new Date().toISOString().split('T')[1].slice(0, 12);
  if (data !== undefined) console.log(`[OCR ${ts}] [${step}]`, msg, data);
  else console.log(`[OCR ${ts}] [${step}]`, msg);
};

export async function uploadDocument(file, onStatus) {
  log('INIT', `file=${file.name} size=${(file.size/1024/1024).toFixed(2)}MB type=${file.type}`);

  // 1. Get presigned S3 upload URL
  onStatus?.('Preparing upload…');
  log('STEP-1', `GET ${API_URL}/upload-url?filename=${file.name}`);
  const urlRes  = await fetch(`${API_URL}/upload-url?filename=${encodeURIComponent(file.name)}`);
  const urlData = await urlRes.json();
  log('STEP-1', `response status=${urlRes.status}`, { document_id: urlData.document_id, content_type: urlData.content_type });
  if (!urlRes.ok) throw new Error(urlData.detail || 'Failed to get upload URL.');

  const { upload_url, document_id, content_type } = urlData;

  // 2. PUT file directly to S3
  onStatus?.(`Uploading ${(file.size / 1024 / 1024).toFixed(1)} MB…`);
  log('STEP-2', `PUT to S3 — document_id=${document_id}`);
  const t0 = Date.now();
  const putRes = await fetch(upload_url, {
    method:  'PUT',
    body:    file,
    headers: { 'Content-Type': content_type },
  });
  log('STEP-2', `S3 PUT done — status=${putRes.status} took=${((Date.now()-t0)/1000).toFixed(1)}s`);
  if (!putRes.ok) throw new Error(`S3 upload failed (${putRes.status})`);

  // 3. Poll until Textract finishes
  log('STEP-3', `S3 upload succeeded — starting poll for document_id=${document_id}`);
  onStatus?.('Upload complete — starting OCR…');
  return await pollUntilDone(document_id, onStatus);
}

async function pollUntilDone(document_id, onStatus, intervalMs = 5000, maxAttempts = 240) {
  log('POLL', `starting poll — document_id=${document_id} interval=${intervalMs}ms max=${maxAttempts}`);

  for (let i = 0; i < maxAttempts; i++) {
    await new Promise(r => setTimeout(r, intervalMs));
    const elapsed = Math.round(((i + 1) * intervalMs) / 1000);

    log('POLL', `attempt ${i + 1}/${maxAttempts} elapsed=${elapsed}s — GET /document/${document_id}`);
    const res  = await fetch(`${API_URL}/document/${document_id}`);
    const data = await res.json();
    log('POLL', `attempt ${i + 1} response — status=${data.status} http=${res.status}`, {
      textract_job_id: data.textract_job_id,
      total_pages:     data.total_pages,
      element_count:   data.element_count,
    });

    if (!res.ok) throw new Error(data.detail || 'Polling failed.');

    if (data.status === 'completed') {
      log('POLL', `COMPLETED after ${elapsed}s — pages=${data.total_pages} elements=${data.element_count}`);
      return {
        document_id,
        filename:        data.filename,
        total_pages:     data.total_pages,
        element_count:   data.element_count,
        elements:        data.elements || [],
        uploaded_at:     data.uploaded_at,
        file_size_bytes: data.file_size_bytes,
        processing_time_seconds: data.processing_time_seconds,
        s3_presigned_url: data.s3_presigned_url,
      };
    }

    if (data.status === 'failed') {
      log('POLL', `FAILED after ${elapsed}s`);
      throw new Error('OCR processing failed on the server.');
    }

    // still processing
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
