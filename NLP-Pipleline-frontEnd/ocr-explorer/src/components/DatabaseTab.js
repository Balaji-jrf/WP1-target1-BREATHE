import { useCallback, useEffect, useState } from 'react';
import { fetchDocuments, fetchExtractions, fetchIngestionStatus, fetchTaxonomy, ingestDocumentToDatabase } from '../api';
import ExtractionReviewModal from './ExtractionReviewModal';

const display = (value) => value == null || value === '' ? '—' : String(value);

export default function DatabaseTab({ initialDocumentId = '' }) {
  const [documents, setDocuments] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [review, setReview] = useState(null);
  const [taxonomy, setTaxonomy] = useState([]);

  const selected = documents.find((document) => document.document_id === selectedId);

  const loadDocuments = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await fetchDocuments();
      setDocuments(result);
      setSelectedId((current) => initialDocumentId || current || result[0]?.document_id || '');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [initialDocumentId]);

  useEffect(() => { loadDocuments(); fetchTaxonomy().then(setTaxonomy).catch((err) => setError(err.message)); }, [loadDocuments]);

  useEffect(() => {
    let active = true;
    if (!selectedId) { setRecords([]); return undefined; }
    setError('');
    fetchExtractions(selectedId)
      .then((result) => { if (active) setRecords(result); })
      .catch((err) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [selectedId]);

  const ingest = async () => {
    if (!selectedId) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await ingestDocumentToDatabase(selectedId);
      setNotice('Nova Lite extraction started. Waiting for review records…');
      let ingestion = { status: 'processing' };
      for (let attempt = 0; attempt < 180 && ingestion.status === 'processing'; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
        ingestion = await fetchIngestionStatus(selectedId);
      }
      if (ingestion.status === 'failed') throw new Error(ingestion.error || 'Nova Lite extraction failed. Check the Lambda logs and retry.');
      if (ingestion.status === 'processing') throw new Error('Extraction is still running. Refresh this page shortly to review the results.');
      const staged = await fetchExtractions(selectedId, { status: 'pending_review' });
      setRecords(await fetchExtractions(selectedId));
      setReview(staged);
      setNotice(`Extraction finished · ${staged.length} records are ready for review.`);
      await loadDocuments();
    } catch (err) {
      await loadDocuments();
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="database-page">
      <div className="database-heading">
        <div><p className="section-kicker">DynamoDB · document registry</p><h2>Document Database</h2>
          <p>Browse OCR metadata and extracted records by document ID.</p></div>
        <button className="clear-btn" onClick={loadDocuments} disabled={loading}>↻ Refresh</button>
      </div>
      {error && <div className="message error-message" role="alert">{error}</div>}
      {notice && <div className="database-notice" role="status">{notice}</div>}
      <div className="database-layout">
        <aside className="database-doc-list" aria-label="Documents">
          <h3>All documents <span>{documents.length}</span></h3>
          {loading && <p className="database-muted">Loading records…</p>}
          {!loading && documents.length === 0 && <p className="database-muted">No documents found.</p>}
          {documents.map((document) => (
            <button key={document.document_id} className={`database-doc-row ${selectedId === document.document_id ? 'selected' : ''}`}
              onClick={() => setSelectedId(document.document_id)}>
              <strong>{document.filename || document.document_name || 'Untitled document'}</strong>
              <code>{document.document_id}</code>
              <span>{display(document.status)} · {document.total_pages || 0} pages</span>
            </button>
          ))}
        </aside>
        <div className="database-detail">
          {!selected && <div className="database-empty">Select a document to inspect its database record.</div>}
          {selected && <>
            <div className="database-detail-heading">
              <div><p className="section-kicker">Selected document</p><h3>{selected.filename || selected.document_name}</h3>
                <code>{selected.document_id}</code></div>
              <button className="process-button" onClick={ingest} disabled={busy || selected.status !== 'completed' || ['processing', 'pending_review'].includes(selected.ingestion_status)}>
                {busy ? 'Nova Lite extracting…' : '↻ Ingest OCR data'}
              </button>
              {selected.ingestion_status === 'pending_review' && <button className="clear-btn" onClick={async () => setReview(await fetchExtractions(selectedId, { status: 'pending_review' }))}>Review pending</button>}
            </div>
            <div className="database-metadata-grid">
              {[
                  ['Status', selected.status], ['Pages', selected.total_pages],
                ['Text blocks', selected.text_blocks_count ?? selected.element_count],
                ['Layout regions', selected.layout_regions_count], ['Tables', selected.table_count],
                ['Figures', selected.figure_count], ['Ingestion', selected.ingestion_status],
                ['Created', selected.uploaded_at || selected.created_at],
              ].map(([label, value]) => <div key={label}><span>{label}</span><strong>{display(value)}</strong></div>)}
            </div>
            <div className="database-record-heading"><h3>Extracted records</h3><span>{records.length} records</span></div>
            {records.length === 0
              ? <div className="database-empty">No entity records yet. Use “Ingest OCR data” after OCR completes.</div>
              : <div className="database-record-list">{records.map((record) => (
                <article className="database-record" key={record.theme_entity_id}>
                  <div><span className="ocr-type-tag">{record.theme || 'Custom'}</span><span className="ocr-page-tag">Page {record.pdf_page_num || '—'}</span></div>
                  <code>{record.theme_entity_id}</code>
                  <p>{record.extracted_fact || record.extracted_text || (record.table_data ? `${record.table_data.length} table rows` : record.content_type || 'Structured record')}</p>
                  <small>{record.review_status || 'approved'} · {record.content_type || record.entity_type} · {Object.values(record.spatial_units || {}).join(', ') || 'No location tagged'}</small>
                </article>
              ))}</div>}
          </>}
        </div>
      </div>
      {review && <ExtractionReviewModal documentId={selectedId} records={review} taxonomy={taxonomy} onClose={() => setReview(null)} onChanged={async () => { setRecords(await fetchExtractions(selectedId)); await loadDocuments(); }} />}
    </section>
  );
}
