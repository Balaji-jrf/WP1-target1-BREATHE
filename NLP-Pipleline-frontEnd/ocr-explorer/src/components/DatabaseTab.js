import { useCallback, useEffect, useState } from 'react';
import { createDocumentRecord, deleteDocumentRecord, fetchDocuments, fetchDocumentRecords, fetchIngestionStatus, fetchTaxonomy, ingestDocumentToDatabase, updateDocumentRecord } from '../api';
import ExtractionReviewModal from './ExtractionReviewModal';

const display = (value) => value == null || value === '' ? '—' : String(value);

export default function DatabaseTab({ initialDocumentId = '' }) {
  const [documents, setDocuments] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [records, setRecords] = useState([]);
  const [documentSearch, setDocumentSearch] = useState('');
  const [filters, setFilters] = useState({ theme: '', pdf_page_num: '', location: '', content_type: '', entity_type: '', status: 'all' });
  const [editingId, setEditingId] = useState('');
  const [draft, setDraft] = useState({});
  const [adding, setAdding] = useState(false);
  const [newRecord, setNewRecord] = useState({ theme: '', pdf_page_num: '', extracted_fact: '', district: '', temporal_period: '', content_type: 'narrative_text' });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [review, setReview] = useState(null);
  const [taxonomy, setTaxonomy] = useState([]);

  const selected = documents.find((document) => document.document_id === selectedId);
  const visibleDocuments = documents.filter((document) => `${document.filename || document.document_name || ''} ${document.document_id}`.toLowerCase().includes(documentSearch.toLowerCase()));

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
    fetchDocumentRecords(selectedId, filters)
      .then((result) => { if (active) setRecords(result); })
      .catch((err) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [selectedId, filters]);

  const reloadRecords = async () => setRecords(await fetchDocumentRecords(selectedId, filters));

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
      const staged = await fetchDocumentRecords(selectedId, { status: 'pending_review' });
      setRecords(await fetchDocumentRecords(selectedId, filters));
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

  const saveRecordEdit = async (record) => {
    setBusy(true); setError('');
    try {
      const spatial = { ...(record.spatial_units || {}) };
      if (draft.district !== undefined) {
        if (draft.district.trim()) spatial.district = draft.district.trim(); else delete spatial.district;
      }
      const updated = await updateDocumentRecord(selectedId, record.theme_entity_id, {
        theme: draft.theme || record.theme,
        pdf_page_num: draft.pdf_page_num ? Number(draft.pdf_page_num) : record.pdf_page_num,
        extracted_fact: draft.extracted_fact ?? record.extracted_fact ?? '',
        temporal_period: draft.temporal_period ?? record.temporal_period ?? '',
        spatial_units: spatial,
      });
      setRecords((current) => current.map((item) => item.theme_entity_id === record.theme_entity_id ? updated : item));
      setEditingId(''); setDraft({}); setNotice('Record updated.');
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const removeRecord = async (record) => {
    if (!window.confirm(`Delete record ${record.theme_entity_id}?`)) return;
    setBusy(true); setError('');
    try { await deleteDocumentRecord(selectedId, record.theme_entity_id); await reloadRecords(); setNotice('Record deleted.'); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const addRecord = async (event) => {
    event.preventDefault(); setBusy(true); setError('');
    const theme = newRecord.theme || taxonomy.find((item) => item.is_active)?.entity_key;
    const themeEntityId = `${theme}#MANUAL_${Date.now()}`;
    const pageNumber = Number(newRecord.pdf_page_num) || undefined;
    try {
      await createDocumentRecord(selectedId, {
        theme_entity_id: themeEntityId, theme, entity_type: 'manual_fact',
        content_type: newRecord.content_type, extracted_fact: newRecord.extracted_fact,
        ...(pageNumber ? { pdf_page_num: pageNumber, original_page_num: pageNumber } : {}),
        spatial_units: newRecord.district.trim() ? { district: newRecord.district.trim() } : {},
        temporal_period: newRecord.temporal_period, review_status: 'pending_review', custom_attributes: {},
      });
      setAdding(false); setNewRecord({ theme: '', pdf_page_num: '', extracted_fact: '', district: '', temporal_period: '', content_type: 'narrative_text' });
      await reloadRecords(); await loadDocuments(); setNotice('Record added as pending review.');
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
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
          <input className="database-doc-search" value={documentSearch} onChange={(event) => setDocumentSearch(event.target.value)} placeholder="Filter documents…" aria-label="Filter documents" />
          {loading && <p className="database-muted">Loading records…</p>}
          {!loading && visibleDocuments.length === 0 && <p className="database-muted">No documents match this filter.</p>}
          {visibleDocuments.map((document) => (
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
              {selected.ingestion_status === 'pending_review' && <button className="clear-btn" onClick={async () => setReview(await fetchDocumentRecords(selectedId, { status: 'pending_review' }))}>Review pending</button>}
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
            <div className="record-filter-bar">
              <label>Document<select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>{documents.map((item) => <option key={item.document_id} value={item.document_id}>{item.filename || item.document_name} · {item.document_id}</option>)}</select></label>
              <label>Theme<select value={filters.theme} onChange={(event) => setFilters({ ...filters, theme: event.target.value })}><option value="">All themes</option>{taxonomy.map((entry) => <option key={entry.entity_key} value={entry.entity_key}>{entry.display_name}</option>)}</select></label>
              <label>Page<input type="number" min="1" value={filters.pdf_page_num} onChange={(event) => setFilters({ ...filters, pdf_page_num: event.target.value })} placeholder="Any" /></label>
              <label>Location<input value={filters.location} onChange={(event) => setFilters({ ...filters, location: event.target.value })} placeholder="District, taluk, town…" /></label>
              <label>Content type<select value={filters.content_type} onChange={(event) => setFilters({ ...filters, content_type: event.target.value })}><option value="">All types</option>{['narrative_text', 'table', 'figure_crop', 'appendix'].map((type) => <option key={type}>{type}</option>)}</select></label>
              <label>Entity type<input value={filters.entity_type} onChange={(event) => setFilters({ ...filters, entity_type: event.target.value })} placeholder="Any" /></label>
              <label>Review status<select value={filters.status} onChange={(event) => setFilters({ ...filters, status: event.target.value })}>{['all', 'pending_review', 'approved', 'edited_and_approved'].map((status) => <option key={status} value={status}>{status === 'all' ? 'All records' : status.replaceAll('_', ' ')}</option>)}</select></label>
              <button className="process-button" onClick={() => setAdding(true)} disabled={!selectedId}>＋ Add record</button>
            </div>
            {adding && <form className="record-add-form" onSubmit={addRecord}>
              <label>Theme<select required value={newRecord.theme || taxonomy.find((item) => item.is_active)?.entity_key || ''} onChange={(event) => setNewRecord({ ...newRecord, theme: event.target.value })}>{taxonomy.filter((item) => item.is_active).map((entry) => <option key={entry.entity_key} value={entry.entity_key}>{entry.display_name}</option>)}</select></label>
              <label>Page<input type="number" min="1" value={newRecord.pdf_page_num} onChange={(event) => setNewRecord({ ...newRecord, pdf_page_num: event.target.value })} /></label>
              <label>Fact<textarea required value={newRecord.extracted_fact} onChange={(event) => setNewRecord({ ...newRecord, extracted_fact: event.target.value })} /></label>
              <label>District<input value={newRecord.district} onChange={(event) => setNewRecord({ ...newRecord, district: event.target.value })} /></label>
              <label>Period<input value={newRecord.temporal_period} onChange={(event) => setNewRecord({ ...newRecord, temporal_period: event.target.value })} /></label>
              <div><button className="process-button" disabled={busy}>Save record</button><button type="button" className="clear-btn" onClick={() => setAdding(false)}>Cancel</button></div>
            </form>}
            {records.length === 0 ? <div className="database-empty">No records match the selected filters.</div> : <div className="records-table-wrap"><table className="records-table"><thead><tr><th>Page</th><th>Theme</th><th>Type</th><th>Extracted fact / content</th><th>Location</th><th>Period</th><th>Review</th><th>Actions</th></tr></thead><tbody>
              {records.map((record) => <tr key={record.theme_entity_id}>
                <td>{editingId === record.theme_entity_id ? <input type="number" value={draft.pdf_page_num ?? record.pdf_page_num ?? ''} onChange={(event) => setDraft({ ...draft, pdf_page_num: event.target.value })} /> : record.pdf_page_num || '—'}</td>
                <td>{editingId === record.theme_entity_id ? <select value={draft.theme || record.theme} onChange={(event) => setDraft({ ...draft, theme: event.target.value })}>{taxonomy.map((entry) => <option key={entry.entity_key} value={entry.entity_key}>{entry.display_name}</option>)}{!taxonomy.some((entry) => entry.entity_key === record.theme) && <option value={record.theme}>{record.theme}</option>}</select> : record.theme}</td>
                <td>{record.content_type || record.entity_type || '—'}</td>
                <td className="record-fact-cell">{editingId === record.theme_entity_id ? <textarea rows="4" value={draft.extracted_fact ?? record.extracted_fact ?? record.extracted_text ?? ''} onChange={(event) => setDraft({ ...draft, extracted_fact: event.target.value })} /> : <><strong>{record.extracted_fact || record.extracted_text || (record.table_data ? `${record.table_data.length} table rows` : record.content_type || 'Structured record')}</strong>{record.table_data && <details><summary>View table data</summary><pre>{JSON.stringify(record.table_data, null, 2)}</pre></details>}{record.source_text_chunk && <details><summary>Source</summary><p>{record.source_text_chunk}</p></details>}{record.s3_crop_url && <a href={record.s3_crop_url} target="_blank" rel="noreferrer">Figure crop</a>}</>}</td>
                <td>{editingId === record.theme_entity_id ? <input value={draft.district ?? record.spatial_units?.district ?? ''} onChange={(event) => setDraft({ ...draft, district: event.target.value })} /> : Object.values(record.spatial_units || {}).join(', ') || '—'}</td>
                <td>{editingId === record.theme_entity_id ? <input value={draft.temporal_period ?? record.temporal_period ?? ''} onChange={(event) => setDraft({ ...draft, temporal_period: event.target.value })} /> : record.temporal_period || '—'}</td>
                <td><span className={`record-status ${record.review_status || 'legacy'}`}>{record.review_status || 'legacy'}</span></td>
                <td className="record-actions">{editingId === record.theme_entity_id ? <><button onClick={() => saveRecordEdit(record)} disabled={busy}>Save</button><button onClick={() => { setEditingId(''); setDraft({}); }}>Cancel</button></> : <><button onClick={() => { setEditingId(record.theme_entity_id); setDraft({}); }}>Edit</button><button className="entity-delete" onClick={() => removeRecord(record)} disabled={busy}>Delete</button></>}</td>
              </tr>)}
            </tbody></table></div>}
          </>}
        </div>
      </div>
      {review && <ExtractionReviewModal documentId={selectedId} records={review} taxonomy={taxonomy} onClose={() => setReview(null)} onChanged={async () => { setRecords(await fetchDocumentRecords(selectedId, filters)); await loadDocuments(); }} />}
    </section>
  );
}
