import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createDocumentRecord, deleteDocumentRecord, fetchDocuments, fetchDocumentRecords, fetchDocumentRecordsPage, fetchIngestionStatus, fetchTaxonomy, ingestDocumentToDatabase, updateDocumentRecord } from '../api';
import ExtractionReviewModal from './ExtractionReviewModal';

const display = (value) => value == null || value === '' ? '—' : String(value);
const DEFAULT_RECORD_COLUMN_WIDTHS = [85, 190, 170, 760, 190, 150, 150, 120];
const RECORD_COLUMN_WIDTHS_KEY = 'breathe-database-record-column-widths';

export default function DatabaseTab({ initialDocumentId = '' }) {
  const [documents, setDocuments] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [records, setRecords] = useState([]);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [pageIndex, setPageIndex] = useState(0);
  const [pageCursors, setPageCursors] = useState(['']);
  const [nextPageToken, setNextPageToken] = useState('');
  const [sort, setSort] = useState({ key: 'pdf_page_num', direction: 'asc' });
  const [tableContentWidth, setTableContentWidth] = useState(0);
  const [columnWidths, setColumnWidths] = useState(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(RECORD_COLUMN_WIDTHS_KEY));
      return Array.isArray(saved) && saved.length === DEFAULT_RECORD_COLUMN_WIDTHS.length
        ? saved.map((width, index) => Math.max(index === 3 ? 280 : 80, Number(width) || DEFAULT_RECORD_COLUMN_WIDTHS[index]))
        : DEFAULT_RECORD_COLUMN_WIDTHS;
    } catch { return DEFAULT_RECORD_COLUMN_WIDTHS; }
  });
  const topScrollRef = useRef(null);
  const tableScrollRef = useRef(null);
  const tableRef = useRef(null);
  const resizingColumnRef = useRef(null);
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
  const sortedRecords = useMemo(() => [...records].sort((left, right) => {
    const a = left[sort.key] ?? (sort.key === 'extracted_fact' ? left.extracted_text : '');
    const b = right[sort.key] ?? (sort.key === 'extracted_fact' ? right.extracted_text : '');
    const compare = sort.key === 'pdf_page_num'
      ? (Number(a) || 0) - (Number(b) || 0)
      : String(a || '').localeCompare(String(b || ''), undefined, { numeric: true, sensitivity: 'base' });
    return sort.direction === 'asc' ? compare : -compare;
  }), [records, sort]);

  const updateTableWidth = useCallback(() => {
    const width = tableRef.current?.scrollWidth || 0;
    setTableContentWidth((current) => current === width ? current : width);
  }, []);

  useEffect(() => {
    const table = tableRef.current;
    const wrapper = tableScrollRef.current;
    if (!table || !wrapper) return undefined;
    updateTableWidth();
    const observer = new ResizeObserver(updateTableWidth);
    observer.observe(table);
    observer.observe(wrapper);
    window.addEventListener('resize', updateTableWidth);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', updateTableWidth);
    };
  }, [records, editingId, filters, selectedId, columnWidths, updateTableWidth]);

  useEffect(() => {
    const resize = (event) => {
      const drag = resizingColumnRef.current;
      if (!drag) return;
      const next = Math.max(drag.index === 3 ? 280 : 80, drag.width + event.clientX - drag.startX);
      setColumnWidths((current) => current.map((width, index) => index === drag.index ? next : width));
    };
    const stopResize = () => {
      if (!resizingColumnRef.current) return;
      resizingColumnRef.current = null;
      document.body.classList.remove('is-resizing-record-column');
    };
    window.addEventListener('pointermove', resize);
    window.addEventListener('pointerup', stopResize);
    window.addEventListener('pointercancel', stopResize);
    return () => {
      window.removeEventListener('pointermove', resize);
      window.removeEventListener('pointerup', stopResize);
      window.removeEventListener('pointercancel', stopResize);
    };
  }, []);

  useEffect(() => {
    try { window.localStorage.setItem(RECORD_COLUMN_WIDTHS_KEY, JSON.stringify(columnWidths)); } catch { /* Keep resizing available when storage is disabled. */ }
  }, [columnWidths]);

  const startColumnResize = (index, event) => {
    event.preventDefault();
    event.stopPropagation();
    resizingColumnRef.current = { index, startX: event.clientX, width: columnWidths[index] };
    document.body.classList.add('is-resizing-record-column');
  };
  const resizeColumnByKeyboard = (index, event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const delta = event.key === 'ArrowRight' ? 20 : -20;
    setColumnWidths((current) => current.map((width, column) => column === index
      ? Math.max(column === 3 ? 280 : 80, width + delta)
      : width));
  };
  const resetColumnWidths = () => setColumnWidths(DEFAULT_RECORD_COLUMN_WIDTHS);

  const toggleSort = (key) => setSort((current) => ({
    key,
    direction: current.key === key && current.direction === 'asc' ? 'desc' : 'asc',
  }));
  const sortMark = (key) => sort.key === key ? (sort.direction === 'asc' ? ' ↑' : ' ↓') : ' ↕';
  const syncHorizontalScroll = (source, target) => {
    if (!target) return;
    const sourceRange = source.scrollWidth - source.clientWidth;
    const targetRange = target.scrollWidth - target.clientWidth;
    const targetPosition = sourceRange > 0 ? Math.round((source.scrollLeft / sourceRange) * targetRange) : 0;
    if (target.scrollLeft !== targetPosition) target.scrollLeft = targetPosition;
  };
  const syncTopScroll = (event) => {
    syncHorizontalScroll(event.currentTarget, tableScrollRef.current);
  };
  const syncTableScroll = (event) => {
    syncHorizontalScroll(event.currentTarget, topScrollRef.current);
  };
  const changeFilters = (nextFilters) => {
    setPageCursors(['']);
    setPageIndex(0);
    setFilters(nextFilters);
  };
  const selectDocument = (documentId) => {
    setPageCursors(['']);
    setPageIndex(0);
    setSelectedId(documentId);
  };

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
    if (!selectedId) { setRecords([]); setNextPageToken(''); setRecordsLoading(false); return undefined; }
    setError('');
    setRecordsLoading(true);
    fetchDocumentRecordsPage(selectedId, filters, pageCursors[pageIndex] || '')
      .then((result) => {
        if (active) {
          setRecords(result.records);
          setNextPageToken(result.nextToken);
        }
      })
      .catch((err) => { if (active) setError(err.message); })
      .finally(() => { if (active) setRecordsLoading(false); });
    return () => { active = false; };
  }, [selectedId, filters, pageIndex, pageCursors]);

  const reloadRecords = async () => {
    const result = await fetchDocumentRecordsPage(selectedId, filters, pageCursors[pageIndex] || '');
    setRecords(result.records);
    setNextPageToken(result.nextToken);
  };

  const refreshDatabase = async () => {
    await loadDocuments();
    if (!selectedId) return;
    setRecordsLoading(true);
    setPageIndex(0);
    setPageCursors(['']);
    try {
      const result = await fetchDocumentRecordsPage(selectedId, filters, '');
      setRecords(result.records);
      setNextPageToken(result.nextToken);
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setRecordsLoading(false);
    }
  };

  const openPendingReview = async () => {
    if (!selectedId) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const pending = await fetchDocumentRecords(selectedId, { status: 'pending_review' });
      if (!pending.length) {
        await loadDocuments();
        setNotice('No records are waiting for review. Document status refreshed.');
        return;
      }
      setReview(pending);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

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
      await reloadRecords();
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
          <button className="clear-btn" onClick={refreshDatabase} disabled={loading || recordsLoading}>↻ Refresh</button>
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
              onClick={() => selectDocument(document.document_id)}>
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
              {(selected.ingestion_status === 'pending_review' || Number(selected.pending_review_count) > 0) && <button className="process-button" onClick={openPendingReview} disabled={busy}>
                {busy ? 'Loading pending records…' : `Review & approve remaining (${selected.pending_review_count ?? 'pending'})`}
              </button>}
            </div>
            <div className="database-metadata-grid">
              {[
                  ['Status', selected.status], ['Pages', selected.total_pages],
                ['Text blocks', selected.text_blocks_count ?? selected.element_count],
                ['Layout regions', selected.layout_regions_count], ['Tables', selected.table_count],
                  ['Figures', selected.figure_count], ['Ingestion', selected.ingestion_status],
                ['Pending review', selected.pending_review_count],
                ['Created', selected.uploaded_at || selected.created_at],
              ].map(([label, value]) => <div key={label}><span>{label}</span><strong>{display(value)}</strong></div>)}
            </div>
            <div className="database-record-heading"><h3>Extracted records</h3><span>{recordsLoading ? 'Loading…' : `${records.length} shown · page ${pageIndex + 1}`}</span></div>
            <p className="record-review-legend"><strong>Legacy</strong> means this older record has no review status. <strong>Approved</strong> means a reviewer approved it for use. Pending records are waiting for review.</p>
            <div className="record-filter-bar">
              <label>Document<select value={selectedId} onChange={(event) => selectDocument(event.target.value)}>{documents.map((item) => <option key={item.document_id} value={item.document_id}>{item.filename || item.document_name} · {item.document_id}</option>)}</select></label>
              <label>Theme<select value={filters.theme} onChange={(event) => changeFilters({ ...filters, theme: event.target.value })}><option value="">All themes</option>{taxonomy.map((entry) => <option key={entry.entity_key} value={entry.entity_key}>{entry.display_name}</option>)}</select></label>
              <label>Page<input type="number" min="1" value={filters.pdf_page_num} onChange={(event) => changeFilters({ ...filters, pdf_page_num: event.target.value })} placeholder="Any" /></label>
              <label>Location<input value={filters.location} onChange={(event) => changeFilters({ ...filters, location: event.target.value })} placeholder="District, taluk, town…" /></label>
              <label>Content type<select value={filters.content_type} onChange={(event) => changeFilters({ ...filters, content_type: event.target.value })}><option value="">All types</option>{['narrative_text', 'table', 'figure_crop', 'appendix'].map((type) => <option key={type}>{type}</option>)}</select></label>
              <label>Entity type<input value={filters.entity_type} onChange={(event) => changeFilters({ ...filters, entity_type: event.target.value })} placeholder="Any" /></label>
              <label>Review status<select value={filters.status} onChange={(event) => changeFilters({ ...filters, status: event.target.value })}>{['all', 'pending_review', 'approved', 'edited_and_approved'].map((status) => <option key={status} value={status}>{status === 'all' ? 'All records' : status.replaceAll('_', ' ')}</option>)}</select></label>
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
            {recordsLoading && records.length === 0 ? <div className="database-empty">Loading extracted records…</div> : records.length === 0 ? <div className="database-empty">{error ? 'Records could not be loaded. Check the error above and refresh to retry.' : 'No records match the selected filters.'}</div> : <>
              <div className="records-table-tools"><span>Drag a column edge to resize it · Arrow keys also work · Sort applies to this page</span><button type="button" className="clear-btn" onClick={resetColumnWidths}>Reset column widths</button></div>
              <div className="records-top-scroll" ref={topScrollRef} onScroll={syncTopScroll} aria-label="Scroll table horizontally">
                <div style={{ width: `${tableContentWidth}px` }} />
              </div>
              <div className="records-table-wrap" ref={tableScrollRef} onScroll={syncTableScroll}><table ref={tableRef} className="records-table"><colgroup>{columnWidths.map((width, index) => <col key={index} style={{ width: `${width}px` }} />)}</colgroup><thead><tr>
                {['Page', 'Theme', 'Type', 'Extracted fact / content', 'Location', 'Period', 'Review', 'Actions'].map((label, index) => <th key={label} aria-sort={index === 0 && sort.key === 'pdf_page_num' ? (sort.direction === 'asc' ? 'ascending' : 'descending') : index === 3 && sort.key === 'extracted_fact' ? (sort.direction === 'asc' ? 'ascending' : 'descending') : index === 5 && sort.key === 'temporal_period' ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  {index === 0 || index === 3 || index === 5
                    ? <button className="table-sort" onClick={() => toggleSort(index === 0 ? 'pdf_page_num' : index === 3 ? 'extracted_fact' : 'temporal_period')}>{label}{sortMark(index === 0 ? 'pdf_page_num' : index === 3 ? 'extracted_fact' : 'temporal_period')}</button>
                    : <span className="table-heading-label">{label}</span>}
                  {index < 7 && <span className="column-resize-handle" role="separator" aria-orientation="vertical" aria-label={`Resize ${label} column`} tabIndex="0" onPointerDown={(event) => startColumnResize(index, event)} onKeyDown={(event) => resizeColumnByKeyboard(index, event)} />}
                </th>)}
              </tr></thead><tbody>
              {sortedRecords.map((record) => <tr key={record.theme_entity_id}>
                <td>{editingId === record.theme_entity_id ? <input type="number" value={draft.pdf_page_num ?? record.pdf_page_num ?? ''} onChange={(event) => setDraft({ ...draft, pdf_page_num: event.target.value })} /> : record.pdf_page_num || '—'}</td>
                <td>{editingId === record.theme_entity_id ? <select value={draft.theme || record.theme} onChange={(event) => setDraft({ ...draft, theme: event.target.value })}>{taxonomy.map((entry) => <option key={entry.entity_key} value={entry.entity_key}>{entry.display_name}</option>)}{!taxonomy.some((entry) => entry.entity_key === record.theme) && <option value={record.theme}>{record.theme}</option>}</select> : record.theme}</td>
                <td>{record.content_type || record.entity_type || '—'}</td>
                <td className="record-fact-cell">{editingId === record.theme_entity_id ? <textarea rows="4" value={draft.extracted_fact ?? record.extracted_fact ?? record.extracted_text ?? ''} onChange={(event) => setDraft({ ...draft, extracted_fact: event.target.value })} /> : <><strong>{record.extracted_fact || record.extracted_text || (record.table_data ? `${record.table_data.length} table rows` : record.content_type || 'Structured record')}</strong>{record.table_data && <details><summary>View table data</summary><pre>{JSON.stringify(record.table_data, null, 2)}</pre></details>}{record.source_text_chunk && <details><summary>Source</summary><p>{record.source_text_chunk}</p></details>}{record.s3_crop_url && <a href={record.s3_crop_url} target="_blank" rel="noreferrer">Figure crop</a>}</>}</td>
                <td>{editingId === record.theme_entity_id ? <input value={draft.district ?? record.spatial_units?.district ?? ''} onChange={(event) => setDraft({ ...draft, district: event.target.value })} /> : Object.values(record.spatial_units || {}).join(', ') || '—'}</td>
                <td>{editingId === record.theme_entity_id ? <input value={draft.temporal_period ?? record.temporal_period ?? ''} onChange={(event) => setDraft({ ...draft, temporal_period: event.target.value })} /> : record.temporal_period || '—'}</td>
                <td><span className={`record-status ${record.review_status || 'legacy'}`} title={record.review_status === 'approved' ? 'Reviewed and approved for use' : record.review_status === 'pending_review' ? 'Waiting for human review' : 'Older record created before review status was added'}>{record.review_status === 'approved' ? 'Approved' : record.review_status === 'pending_review' ? 'Pending review' : record.review_status === 'edited_and_approved' ? 'Edited & approved' : 'Legacy'}</span></td>
                <td className="record-actions">{editingId === record.theme_entity_id ? <><button onClick={() => saveRecordEdit(record)} disabled={busy}>Save</button><button onClick={() => { setEditingId(''); setDraft({}); }}>Cancel</button></> : <><button onClick={() => { setEditingId(record.theme_entity_id); setDraft({}); }}>Edit</button><button className="entity-delete" onClick={() => removeRecord(record)} disabled={busy}>Delete</button></>}</td>
              </tr>)}
            </tbody></table></div>
            <div className="records-pagination">
              <span>{records.length} records on this page</span>
              <div>
                <button type="button" className="clear-btn" onClick={() => setPageIndex((current) => Math.max(0, current - 1))} disabled={recordsLoading || pageIndex === 0}>← Previous</button>
                <span>Page {pageIndex + 1}</span>
                <button type="button" className="clear-btn" onClick={() => {
                  if (!nextPageToken) return;
                  setPageCursors((current) => [...current.slice(0, pageIndex + 1), nextPageToken]);
                  setPageIndex((current) => current + 1);
                }} disabled={recordsLoading || !nextPageToken}>Next →</button>
              </div>
            </div>
            </>}
          </>}
        </div>
      </div>
      {review && <ExtractionReviewModal documentId={selectedId} records={review} taxonomy={taxonomy} onClose={() => setReview(null)} onChanged={async () => { await reloadRecords(); await loadDocuments(); }} />}
    </section>
  );
}
