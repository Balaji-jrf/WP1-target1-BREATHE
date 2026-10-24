const API_URL = process.env.REACT_APP_API_URL
  || 'https://o0dqnexpsg.execute-api.ap-southeast-2.amazonaws.com/prod';

const log = (step, msg, data) => {
  const ts = new Date().toISOString().split('T')[1].slice(0, 12);
  if (data !== undefined) console.log(`[OCR ${ts}] [${step}]`, msg, data);
  else console.log(`[OCR ${ts}] [${step}]`, msg);
};

// Fetch OCR elements directly from S3 presigned URL — bypasses API Gateway 6MB limit
async function fetchOcrFromS3(ocr_presigned_url) {
  const res = await fetch(ocr_presigned_url);
  if (!res.ok) throw new Error(`Failed to fetch OCR JSON from S3 (${res.status})`);
  return await res.json();
}

function attachFigureImageUrls(ocr, figurePresignedUrls = {}) {
  return {
    ...ocr,
    figures: (ocr.figures || []).map((figure) => ({
      ...figure,
      image_url: figure.image_s3_key
        ? figurePresignedUrls[figure.image_s3_key] || null
        : null,
    })),
  };
}

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

    let res, data;
    try {
      res  = await fetch(`${API_URL}/document/${document_id}`);
      data = await res.json();
    } catch (err) {
      log('POLL', `attempt ${i + 1} network error — ${err.message} — retrying`);
      onStatus?.(`Processing large document… ${elapsed}s elapsed`);
      continue;
    }

    log('POLL', `attempt ${i + 1} response — status=${data.status} http=${res.status}`, {
      textract_job_id: data.textract_job_id,
      total_pages:     data.total_pages,
      element_count:   data.element_count,
    });

    // Transient gateway errors — Lambda still running in background
    if (res.status === 502 || res.status === 503 || res.status === 504) {
      log('POLL', `attempt ${i + 1} transient ${res.status} — Lambda still processing, retrying`);
      onStatus?.(`Processing large document… ${elapsed}s elapsed`);
      continue;
    }

    if (!res.ok) throw new Error(data.detail || 'Polling failed.');

    if (data.status === 'completed') {
      log('POLL', `COMPLETED after ${elapsed}s — pages=${data.total_pages} elements=${data.element_count}`);
      // Fetch OCR JSON directly from S3 — never through API Gateway (no 6MB limit)
      log('POLL', `fetching OCR JSON from S3…`);
      const ocr = attachFigureImageUrls(
        await fetchOcrFromS3(data.ocr_presigned_url),
        data.figure_presigned_urls,
      );
      log('POLL', `OCR JSON loaded — elements=${ocr.elements?.length}`);
      return {
        document_id,
        filename:                data.filename,
        total_pages:             ocr.total_pages,
        element_count:           data.element_count,
        elements:                ocr.elements || [],
        layout:                  ocr.layout || [],
        tables:                  ocr.tables || [],
        figures:                 ocr.figures || [],
        figure_presigned_url_error: data.figure_presigned_url_error,
        uploaded_at:             data.uploaded_at,
        file_size_bytes:         data.file_size_bytes,
        processing_time_seconds: data.processing_time_seconds,
        s3_presigned_url:        data.s3_presigned_url,
      };
    }

    if (data.status === 'failed') {
      log('POLL', `FAILED after ${elapsed}s`);
      throw new Error('OCR processing failed on the server.');
    }

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

  // Fetch OCR JSON directly from S3 if available
  if (data.ocr_presigned_url) {
    try {
      const ocr      = attachFigureImageUrls(
        await fetchOcrFromS3(data.ocr_presigned_url),
        data.figure_presigned_urls,
      );
      data.elements  = ocr.elements || [];
      data.total_pages = ocr.total_pages ?? data.total_pages;
      data.layout    = ocr.layout || [];
      data.tables    = ocr.tables || [];
      data.figures   = ocr.figures || [];
    } catch (err) {
      console.warn('[fetchDocument] Could not load OCR JSON from S3:', err.message);
    }
  }

  return data;
}

export async function downloadOcrJson(document_id) {
  const res  = await fetch(`${API_URL}/document/${document_id}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Failed to fetch document.');

  // Fetch OCR JSON directly from S3
  const ocr = await fetchOcrFromS3(data.ocr_presigned_url);

  const payload = {
    document_id:             data.document_id,
    filename:                data.filename,
    total_pages:             ocr.total_pages,
    element_count:           data.element_count,
    uploaded_at:             data.uploaded_at,
    file_size_bytes:         data.file_size_bytes,
    processing_time_seconds: data.processing_time_seconds,
    elements:                ocr.elements || [],
    layout:                  ocr.layout || [],
    tables:                  ocr.tables || [],
    figures:                 ocr.figures || [],
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = `${data.filename.replace(/\.[^.]+$/, '')}_ocr.json`;
  a.click();
  URL.revokeObjectURL(url);
}

export async function fetchExtractions(document_id, filters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  const suffix = params.size ? `?${params.toString()}` : '';
  const res = await fetch(`${API_URL}/api/database/extractions/${encodeURIComponent(document_id)}${suffix}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Failed to load database extractions.');
  return data.extractions || [];
}

export async function ingestDocumentToDatabase(document_id) {
  const res = await fetch(`${API_URL}/api/documents/ingest-dynamo`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ document_id }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Database ingestion failed.');
  return data;
}

export async function saveExtraction(document_id, extraction, action = 'upsert') {
  const res = await fetch(`${API_URL}/api/database/extractions/${encodeURIComponent(document_id)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, ...extraction }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Could not save extraction.');
  return data;
}

export async function deleteExtraction(document_id, theme_entity_id) {
  const res = await fetch(`${API_URL}/api/database/extractions/${encodeURIComponent(document_id)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'delete', theme_entity_id }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Could not delete extraction.');
  return data;
}
