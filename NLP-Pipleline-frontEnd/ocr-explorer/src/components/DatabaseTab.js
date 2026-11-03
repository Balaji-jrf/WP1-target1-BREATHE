import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  approveDocumentRecords, createDocumentRecord, deleteDocumentRecord,
  fetchDocument, fetchDocuments, fetchDocumentRecords, fetchDocumentRecordsPage,
  fetchIngestionStatus, fetchTaxonomy, ingestDocumentToDatabase, updateDocumentRecord,
} from '../api';
import DocViewer from './DocViewer';
import { getDocumentTagOptions, UNTAGGED_FILTER } from '../documentTags';
import { DEFAULT_EXTRACTION_MODEL, EXTRACTION_MODELS } from '../extractionModels';

/* ── helpers ── */
const fmt = (v) => (v == null || v === '') ? '—' : String(v);
const fmtDate = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  return `${d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}  ${d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}`;
};
const tagLabel = (tag) => tag || 'Other';
const STATUS_COLOR = { completed: 'green', processing: 'orange', failed: 'red', pending_review: 'blue' };

export default function DatabaseTab({ initialDocumentId = '' }) {
  const [documents, setDocuments]       = useState([]);
  const [tagFilter, setTagFilter]       = useState('all');
  const [selectedId, setSelectedId]     = useState('');
  const [records, setRecords]           = useState([]);
  const [recordStats, setRecordStats]   = useState(null);
  const [statsRefresh, setStatsRefresh] = useState(0);
  const [recLoading, setRecLoading]     = useState(false);
  const [pageIndex, setPageIndex]       = useState(0);
  const [pageCursors, setPageCursors]   = useState(['']);
  const [nextToken, setNextToken]       = useState('');
  const [sort, setSort]                 = useState({ key: 'pdf_page_num', dir: 'asc' });
  const [filters, setFilters]           = useState({ theme: '', pdf_page_num: '', location: '', entity_type: '', status: 'all' });
  const [docSearch, setDocSearch]       = useState('');
  const [editingId, setEditingId]       = useState('');
  const [draft, setDraft]               = useState({});
  const [adding, setAdding]             = useState(false);
  const [newRec, setNewRec]             = useState({ theme: '', pdf_page_num: '', extracted_fact: '', district: '', temporal_period: '', content_type: 'narrative_text' });
  const [selected, setSelected]         = useState(new Set());
  const [loading, setLoading]           = useState(true);
  const [busy, setBusy]                 = useState(false);
  const [error, setError]               = useState('');
  const [notice, setNotice]             = useState('');
  const [taxonomy, setTaxonomy]         = useState([]);
  const [extractionModel, setExtractionModel] = useState(DEFAULT_EXTRACTION_MODEL);
  const [viewerEl, setViewerEl]         = useState(null);  // record clicked for PDF view
  const [docPresignedUrl, setDocPresignedUrl] = useState(null);
  const [documentsPaneWidth, setDocumentsPaneWidth] = useState(250);
  const [viewerPaneWidth, setViewerPaneWidth] = useState(390);
  const layoutRef = useRef(null);
  const splitLayoutRef = useRef(null);
  const tableScrollRef = useRef(null);
  const resizingRef = useRef(null);

  const doc = documents.find((d) => d.document_id === selectedId);

  /* ── fetch presigned URL when doc selected ── */
  useEffect(() => {
    if (!selectedId) { setDocPresignedUrl(null); return; }
    let active = true;
    fetchDocument(selectedId)
      .then((d) => { if (active) setDocPresignedUrl(d.s3_presigned_url || null); })
      .catch(() => {});
    return () => { active = false; };
  }, [selectedId]);

  useEffect(() => {
    if (docPresignedUrl) {
      setViewerEl((current) => current ? { ...current, docUrl: docPresignedUrl } : current);
    }
  }, [docPresignedUrl]);

  const openViewer = (rec) => {
    setViewerEl({
      _id:      rec.theme_entity_id,
      page:     Number(rec.pdf_page_num) || 1,
      bbox:     rec.bbox || null,
      type:     rec.content_type === 'table' ? 'tables' : rec.content_type === 'figure_crop' ? 'figures' : 'text',
      text:     rec.extracted_fact || rec.extracted_text || '',
      filename: doc?.filename || '',
      docUrl:   docPresignedUrl,
    });
  };

  const allTags = useMemo(() => getDocumentTagOptions(documents), [documents]);

  /* ── filtered doc list ── */
  const visibleDocs = useMemo(() => documents.filter((d) => {
    const savedTag = d.doc_tag?.trim() || '';
    const matchTag = tagFilter === 'all'
      || (tagFilter === UNTAGGED_FILTER ? !savedTag : savedTag === tagFilter);
    const matchSearch = `${d.doc_name || d.filename || ''} ${d.document_id}`.toLowerCase().includes(docSearch.toLowerCase());
    return matchTag && matchSearch;
  }), [documents, tagFilter, docSearch]);

  /* ── sorted records ── */
  const sortedRecords = useMemo(() => [...records].sort((a, b) => {
    const va = a[sort.key] ?? ''; const vb = b[sort.key] ?? '';
    const cmp = sort.key === 'pdf_page_num' ? (Number(va) || 0) - (Number(vb) || 0)
      : String(va).localeCompare(String(vb), undefined, { numeric: true, sensitivity: 'base' });
    return sort.dir === 'asc' ? cmp : -cmp;
  }), [records, sort]);

  const toggleSort = (key) => setSort((s) => ({ key, dir: s.key === key && s.dir === 'asc' ? 'desc' : 'asc' }));
  const sortIcon = (key) => sort.key === key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : '';

  const changeFilters = (f) => { setPageCursors(['']); setPageIndex(0); setFilters(f); };
  const selectDoc = (id) => { setPageCursors(['']); setPageIndex(0); setSelectedId(id); setSelected(new Set()); setEditingId(''); setViewerEl(null); };
  const startResize = (pane, event) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    resizingRef.current = {
      pane,
      startX: event.clientX,
      startWidth: pane === 'documents' ? documentsPaneWidth : viewerPaneWidth,
    };
  };
  const resizePane = (event) => {
    const resizing = resizingRef.current;
    if (!resizing) return;
    const delta = event.clientX - resizing.startX;
    if (resizing.pane === 'documents') {
      const availableWidth = layoutRef.current?.clientWidth || window.innerWidth;
      const maxWidth = Math.max(180, Math.min(480, availableWidth - 708));
      setDocumentsPaneWidth(Math.max(180, Math.min(maxWidth, resizing.startWidth + delta)));
      return;
    }
    const availableWidth = splitLayoutRef.current?.clientWidth || window.innerWidth;
    const maxWidth = Math.max(280, Math.min(760, availableWidth - 398));
    setViewerPaneWidth(Math.max(280, Math.min(maxWidth, resizing.startWidth - delta)));
  };
  const stopResize = () => { resizingRef.current = null; };
  const resizeWithKeyboard = (pane, event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const amount = event.key === 'ArrowRight' ? 20 : -20;
    if (pane === 'documents') {
      const availableWidth = layoutRef.current?.clientWidth || window.innerWidth;
      const maxWidth = Math.max(180, Math.min(480, availableWidth - 708));
      setDocumentsPaneWidth((width) => Math.max(180, Math.min(maxWidth, width + amount)));
      return;
    }
    const availableWidth = splitLayoutRef.current?.clientWidth || window.innerWidth;
    const maxWidth = Math.max(280, Math.min(760, availableWidth - 398));
    setViewerPaneWidth((width) => Math.max(280, Math.min(maxWidth, width - amount)));
  };

  /* ── load documents ── */
  const loadDocs = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const result = await fetchDocuments();
      setDocuments(result);
      setSelectedId((cur) => initialDocumentId || cur || result[0]?.document_id || '');
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, [initialDocumentId]);

  useEffect(() => {
    loadDocs();
    fetchTaxonomy().then(setTaxonomy).catch((e) => setError(e.message));
  }, [loadDocs]);

  /* ── poll if OCR processing ── */
  useEffect(() => {
    if (!selectedId || doc?.status !== 'processing') return;
    const t = setInterval(async () => {
      try { const r = await fetchDocuments(); setDocuments(r); } catch { /* ignore */ }
    }, 5000);
    return () => clearInterval(t);
  }, [selectedId, doc?.status]);

  /* ── load records ── */
  useEffect(() => {
    if (!selectedId) { setRecords([]); setNextToken(''); return; }
    let active = true;
    setRecLoading(true);
    fetchDocumentRecordsPage(selectedId, filters, pageCursors[pageIndex] || '')
      .then((r) => { if (active) { setRecords(r.records); setNextToken(r.nextToken); } })
      .catch((e) => { if (active) setError(e.message); })
      .finally(() => { if (active) setRecLoading(false); });
    return () => { active = false; };
  }, [selectedId, filters, pageIndex, pageCursors]);

  useEffect(() => {
    if (!selectedId) { setRecordStats(null); return; }
    let active = true;
    fetchDocumentRecords(selectedId)
      .then((allRecords) => {
        if (!active) return;
        setRecordStats({
          total: allRecords.length,
          pending: allRecords.filter((record) => record.review_status === 'pending_review').length,
          approved: allRecords.filter((record) => ['approved', 'edited_and_approved'].includes(record.review_status)).length,
        });
      })
      .catch((e) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [selectedId, statsRefresh]);

  const reloadRecords = async () => {
    const r = await fetchDocumentRecordsPage(selectedId, filters, pageCursors[pageIndex] || '');
    setRecords(r.records); setNextToken(r.nextToken);
    setStatsRefresh((version) => version + 1);
  };

  /* ── ingest ── */
  const ingest = async () => {
    if (!selectedId || !canIngest) return;
    if (isReingest && !window.confirm('Re-ingestion replaces the existing AI-extracted facts, including reviewed facts, and removes legacy table/figure records. New facts will return to review. Continue?')) return;
    setBusy(true); setError(''); setNotice('');
    try {
      await ingestDocumentToDatabase(selectedId, extractionModel);
      setNotice('AI extraction started…');
      let status = { status: 'processing' };
      for (let i = 0; i < 180 && status.status === 'processing'; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        status = await fetchIngestionStatus(selectedId);
      }
      if (status.status === 'failed') throw new Error(status.error || 'Extraction failed.');
      if (status.status === 'processing') throw new Error('Still running — refresh shortly.');
      const pending = await fetchDocumentRecords(selectedId, { status: 'pending_review' });
      await reloadRecords();
      await loadDocs();
      setNotice(`Extraction done · ${pending.length} records ready for review.`);
    } catch (e) { await loadDocs(); setError(e.message); }
    finally { setBusy(false); }
  };

  /* ── multi-select helpers ── */
  const allIds = sortedRecords.map((r) => r.theme_entity_id);
  const allChecked = allIds.length > 0 && allIds.every((id) => selected.has(id));
  const toggleAll = () => setSelected(allChecked ? new Set() : new Set(allIds));
  const toggleOne = (id) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  /* ── bulk actions ── */
  const bulkApprove = async () => {
    if (!selected.size) return;
    setBusy(true); setError('');
    try {
      await approveDocumentRecords(selectedId, [...selected]);
      setSelected(new Set()); await reloadRecords(); await loadDocs();
      setNotice(`${selected.size} records approved.`);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const approveAllPending = async () => {
    if (!selectedId || !recordStats?.pending) return;
    if (!window.confirm(`Approve all ${recordStats.pending} pending facts for this document?`)) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await approveDocumentRecords(selectedId, [], { approveAll: true });
      setSelected(new Set()); await reloadRecords(); await loadDocs();
      setNotice(`${result.approved_count} pending facts approved.`);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const bulkDelete = async () => {
    if (!selected.size || !window.confirm(`Delete ${selected.size} record(s)?`)) return;
    setBusy(true); setError('');
    try {
      for (const id of selected) await deleteDocumentRecord(selectedId, id);
      setSelected(new Set()); await reloadRecords(); await loadDocs();
      setNotice('Records deleted.');
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const deleteAllRecords = async () => {
    if (!selectedId || !recordStats?.total) return;
    const confirmed = window.confirm(
      `Delete all ${recordStats.total} records for this document? This also deletes manually added records and cannot be undone.`,
    );
    if (!confirmed) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const allRecords = await fetchDocumentRecords(selectedId);
      for (const record of allRecords) {
        await deleteDocumentRecord(selectedId, record.theme_entity_id);
      }
      setSelected(new Set()); setEditingId(''); setAdding(false);
      await reloadRecords(); await loadDocs();
      setNotice(`${allRecords.length} records deleted.`);
    } catch (e) {
      setError(`Could not delete all records. Some records may already have been deleted. ${e.message}`);
      setStatsRefresh((version) => version + 1);
      await reloadRecords();
    } finally { setBusy(false); }
  };

  /* ── single record edit/delete ── */
  const saveEdit = async (record) => {
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
      setRecords((cur) => cur.map((r) => r.theme_entity_id === record.theme_entity_id ? updated : r));
      setStatsRefresh((version) => version + 1);
      setEditingId(''); setDraft({}); setNotice('Record updated.');
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const removeOne = async (record) => {
    if (!window.confirm(`Delete this record?`)) return;
    setBusy(true); setError('');
    try { await deleteDocumentRecord(selectedId, record.theme_entity_id); await reloadRecords(); await loadDocs(); setNotice('Deleted.'); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const approveOne = async (record) => {
    setBusy(true); setError('');
    try {
      await approveDocumentRecords(selectedId, [record.theme_entity_id]);
      await reloadRecords(); await loadDocs(); setNotice('Record approved.');
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  /* ── add record ── */
  const addRecord = async (e) => {
    e.preventDefault(); setBusy(true); setError('');
    const theme = newRec.theme || taxonomy.find((t) => t.is_active)?.entity_key;
    const id = `${theme}#MANUAL_${Date.now()}`;
    try {
      await createDocumentRecord(selectedId, {
        theme_entity_id: id, theme, entity_type: 'manual_fact',
        content_type: newRec.content_type, extracted_fact: newRec.extracted_fact,
        ...(newRec.pdf_page_num ? { pdf_page_num: Number(newRec.pdf_page_num), original_page_num: Number(newRec.pdf_page_num) } : {}),
        spatial_units: newRec.district.trim() ? { district: newRec.district.trim() } : {},
        temporal_period: newRec.temporal_period, review_status: 'pending_review', custom_attributes: {},
      });
      setAdding(false);
      setNewRec({ theme: '', pdf_page_num: '', extracted_fact: '', district: '', temporal_period: '', content_type: 'narrative_text' });
      await reloadRecords(); await loadDocs(); setNotice('Record added.');
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const metaItems = doc ? [
    ['OCR status', <span className={`db-status-pill ${STATUS_COLOR[doc.status] || ''}`}>{fmt(doc.status)}</span>],
    ['Ingestion', <span className={`db-status-pill ${STATUS_COLOR[doc.ingestion_status] || ''}`}>{fmt(doc.ingestion_status)}</span>],
    ['Pending review', fmt(recordStats?.pending)],
    ['Approved', fmt(recordStats?.approved)],
    ['Total extracted facts', fmt(recordStats?.total)],
    ['Created', fmtDate(doc.uploaded_at || doc.created_at)],
  ] : [];

  const canIngest = doc && doc.status === 'completed' && doc.ingestion_status !== 'processing';
  const isReingest = doc?.ingestion_status && doc.ingestion_status !== 'processing';

  return (
    <div
      className="db-root"
      ref={layoutRef}
      style={{ '--db-documents-width': `${documentsPaneWidth}px` }}
    >
      {/* ── Left sidebar ── */}
      <aside className="db-sidebar">
        <div className="db-sidebar-head">
          <span className="db-sidebar-title">Documents <em>{documents.length}</em></span>
          <button className="db-icon-btn" onClick={loadDocs} disabled={loading} title="Refresh">↻</button>
        </div>

        <label className="db-tag-filter">
          <span>Document tag</span>
          <select value={tagFilter} onChange={(event) => setTagFilter(event.target.value)}>
            {allTags.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>

        <input className="db-search" value={docSearch} onChange={(e) => setDocSearch(e.target.value)} placeholder="Search documents…" />

        <div className="db-doc-list">
          {loading && <p className="db-muted">Loading…</p>}
          {!loading && visibleDocs.length === 0 && <p className="db-muted">No documents found.</p>}
          {visibleDocs.map((d) => (
            <button key={d.document_id} className={`db-doc-item ${selectedId === d.document_id ? 'active' : ''}`} onClick={() => selectDoc(d.document_id)}>
              <span className="db-doc-name">{d.doc_name || d.filename || 'Untitled'}</span>
              {d.doc_tag && <span className="db-doc-tag">{tagLabel(d.doc_tag)}</span>}
              <span className="db-doc-meta">{fmt(d.total_pages)}p · {fmt(d.status)}</span>
            </button>
          ))}
        </div>
      </aside>
      <div
        className="db-resize-handle db-resize-documents"
        role="separator"
        aria-label="Resize document list"
        aria-orientation="vertical"
        aria-valuenow={documentsPaneWidth}
        tabIndex={0}
        onPointerDown={(event) => startResize('documents', event)}
        onPointerMove={resizePane}
        onPointerUp={stopResize}
        onPointerCancel={stopResize}
        onLostPointerCapture={stopResize}
        onKeyDown={(event) => resizeWithKeyboard('documents', event)}
      />

      {/* ── Main content ── */}
      <main className="db-main">
        {error  && <div className="db-alert db-alert-error">{error}<button onClick={() => setError('')}>✕</button></div>}
        {notice && <div className="db-alert db-alert-ok">{notice}<button onClick={() => setNotice('')}>✕</button></div>}

        {!doc && !loading && (
          <div className="db-empty-state">
            <div className="db-empty-icon">📄</div>
            <p>Select a document from the left to view its database records.</p>
          </div>
        )}

        {doc && <>
          {/* ── Doc header ── */}
          {/* ── Doc header ── */}
          <div className="db-doc-header">
            <div className="db-doc-header-left">
              <h2 className="db-doc-title">{doc.doc_name || doc.filename}</h2>
              <div className="db-doc-header-meta">
                {doc.doc_tag && <span className="db-tag-badge">{doc.doc_tag}</span>}
                <code className="db-doc-id">{doc.document_id}</code>
              </div>
            </div>
            <div className="db-doc-header-actions">
              <label className="db-model-picker">
                <span>AI model</span>
                <select
                  value={extractionModel}
                  onChange={(event) => setExtractionModel(event.target.value)}
                  disabled={busy || doc.ingestion_status === 'processing'}
                  title="Llama 3.1 8B sends document text to Bedrock in the US West region."
                >
                  {EXTRACTION_MODELS.map((model) => (
                    <option key={model.id} value={model.id}>{model.label}</option>
                  ))}
                </select>
                {extractionModel === 'meta.llama3-1-8b-instruct-v1:0' && (
                  <small>Document text is sent to Bedrock in the US West region.</small>
                )}
              </label>
              <button className="db-btn db-btn-primary" onClick={ingest}
                disabled={busy || !canIngest}
                title={!canIngest ? (doc.ingestion_status === 'processing' ? 'AI extraction is already running' : 'OCR must complete before ingestion') : isReingest ? 'Run AI extraction again and refresh extracted records' : 'Run AI extraction'}>
                {busy ? <><span className="spinner" /> Extracting…</>
                  : doc.ingestion_status === 'processing' ? '⏳ Processing…'
                  : isReingest ? '↻ Re-Ingest & Extract'
                  : '⚡ Ingest & Extract'}
              </button>
              <button className="db-btn" onClick={loadDocs} disabled={loading}>↻ Refresh</button>
            </div>
          </div>

          {doc.status === 'processing' && (
            <div className="db-hint">OCR is still processing — ingestion will be available when complete. Auto-refreshing…</div>
          )}

          {/* ── Metadata strip ── */}
          <div className="db-meta-strip">
            {metaItems.map(([label, value]) => (
              <div key={label} className="db-meta-cell">
                <span className="db-meta-label">{label}</span>
                <span className="db-meta-value">{value}</span>
              </div>
            ))}
          </div>

          {/* ── Records + Viewer split ── */}
          <div
            className="db-split-layout"
            ref={splitLayoutRef}
            style={{ '--db-viewer-width': `${viewerPaneWidth}px` }}
          >
            <div className="db-records-section">
            <div className="db-records-toolbar">
              <span className="db-records-count">
              </span>

              {/* Filters */}
              <div className="db-filters">
                <select value={filters.theme} onChange={(e) => changeFilters({ ...filters, theme: e.target.value })}>
                  <option value="">All themes</option>
                  {taxonomy.map((t) => <option key={t.entity_key} value={t.entity_key}>{t.display_name}</option>)}
                </select>
                <select value={filters.status} onChange={(e) => changeFilters({ ...filters, status: e.target.value })}>
                  {['all', 'pending_review', 'approved', 'edited_and_approved'].map((s) => (
                    <option key={s} value={s}>{s === 'all' ? 'All statuses' : s.replace(/_/g, ' ')}</option>
                  ))}
                </select>
                <select value={filters.entity_type} onChange={(e) => changeFilters({ ...filters, entity_type: e.target.value })}>
                  <option value="">All sources</option>
                  <option value="thematic_fact">AI extracted</option>
                  <option value="user_saved">User saved</option>
                </select>
                <input value={filters.pdf_page_num} onChange={(e) => changeFilters({ ...filters, pdf_page_num: e.target.value })} placeholder="Doc page (PG)" title="PDF page number within this document" aria-label="PDF page number within this document" type="number" min="1" style={{ width: 120 }} />
                <input value={filters.location} onChange={(e) => changeFilters({ ...filters, location: e.target.value })} placeholder="Location…" style={{ width: 120 }} />
              </div>

              {/* Bulk actions */}
              {selected.size > 0 && (
                <div className="db-bulk-actions">
                  <span>{selected.size} selected</span>
                  <button className="db-btn db-btn-approve" onClick={bulkApprove} disabled={busy}>✓ Approve</button>
                  <button className="db-btn db-btn-danger" onClick={bulkDelete} disabled={busy}>✕ Delete</button>
                </div>
              )}

              {recordStats?.pending > 0 && (
                <button className="db-btn db-btn-approve" onClick={approveAllPending} disabled={busy || recLoading}>
                  ✓ Approve all {recordStats.pending} pending
                </button>
              )}
              <button
                className="db-btn db-btn-danger"
                onClick={deleteAllRecords}
                disabled={!selectedId || !recordStats?.total || busy || recLoading}
                title="Delete every extracted and manually added record for this document"
              >
                Delete all items
              </button>
              <button className="db-btn db-btn-primary" onClick={() => setAdding(true)} disabled={!selectedId}>＋ Add</button>
            </div>

            {/* Add record form */}
            {adding && (
              <form className="db-add-form" onSubmit={addRecord}>
                <select required value={newRec.theme || taxonomy.find((t) => t.is_active)?.entity_key || ''} onChange={(e) => setNewRec({ ...newRec, theme: e.target.value })}>
                  {taxonomy.filter((t) => t.is_active).map((t) => <option key={t.entity_key} value={t.entity_key}>{t.display_name}</option>)}
                </select>
                <input type="number" min="1" value={newRec.pdf_page_num} onChange={(e) => setNewRec({ ...newRec, pdf_page_num: e.target.value })} placeholder="Doc page (PG)" title="PDF page number within this document" />
                <textarea required value={newRec.extracted_fact} onChange={(e) => setNewRec({ ...newRec, extracted_fact: e.target.value })} placeholder="Extracted fact…" rows={2} />
                <input value={newRec.district} onChange={(e) => setNewRec({ ...newRec, district: e.target.value })} placeholder="Location" />
                <input value={newRec.temporal_period} onChange={(e) => setNewRec({ ...newRec, temporal_period: e.target.value })} placeholder="Period" />
                <div className="db-add-form-actions">
                  <button className="db-btn db-btn-primary" disabled={busy}>Save</button>
                  <button type="button" className="db-btn" onClick={() => setAdding(false)}>Cancel</button>
                </div>
              </form>
            )}

            {/* Table */}
            {recLoading && records.length === 0
              ? <div className="db-empty-state"><p>Loading records…</p></div>
              : records.length === 0
                ? <div className="db-empty-state"><p>No records match the current filters.</p></div>
                : (
                  <div className="db-table-wrap" ref={tableScrollRef}>
                    <table className="db-table">
                      <thead>
                        <tr>
                          <th style={{ width: 36 }}><input type="checkbox" checked={allChecked} onChange={toggleAll} /></th>
                          <th style={{ width: 96 }}><button className="db-th-sort" title="Page number within this document" onClick={() => toggleSort('pdf_page_num')}>Doc PG{sortIcon('pdf_page_num')}</button></th>
                          <th style={{ width: 140 }}>Theme</th>
                          <th style={{ width: 110 }}>Type</th>
                          <th><button className="db-th-sort" onClick={() => toggleSort('extracted_fact')}>Extracted Fact{sortIcon('extracted_fact')}</button></th>
                          <th style={{ width: 130 }}>Location</th>
                          <th style={{ width: 110 }}><button className="db-th-sort" onClick={() => toggleSort('temporal_period')}>Period{sortIcon('temporal_period')}</button></th>
                          <th style={{ width: 110 }}>Review</th>
                          <th style={{ width: 130 }}>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sortedRecords.map((rec) => {
                          const isEditing = editingId === rec.theme_entity_id;
                          return (
                            <tr key={rec.theme_entity_id}
                              className={`db-table-row ${selected.has(rec.theme_entity_id) ? 'db-row-selected' : ''} ${viewerEl && viewerEl._id === rec.theme_entity_id ? 'db-row-viewing' : ''}`}
                              onContextMenu={(event) => {
                                if (isEditing || event.target.closest('button,input,textarea,select,a,summary')) return;
                                event.preventDefault();
                                openViewer(rec);
                              }}
                              title={!isEditing ? 'Right-click to view in PDF' : ''}
                            >
                              <td><input type="checkbox" checked={selected.has(rec.theme_entity_id)} onChange={() => toggleOne(rec.theme_entity_id)} /></td>
                              <td className="db-td-center">
                                {isEditing
                                  ? <input type="number" className="db-inline-input" value={draft.pdf_page_num ?? rec.pdf_page_num ?? ''} onChange={(e) => setDraft({ ...draft, pdf_page_num: e.target.value })} />
                                  : rec.pdf_page_num || '—'}
                              </td>
                              <td>
                                {isEditing
                                  ? <select className="db-inline-input" value={draft.theme || rec.theme} onChange={(e) => setDraft({ ...draft, theme: e.target.value })}>
                                      {taxonomy.map((t) => <option key={t.entity_key} value={t.entity_key}>{t.display_name}</option>)}
                                      {!taxonomy.some((t) => t.entity_key === rec.theme) && <option value={rec.theme}>{rec.theme}</option>}
                                    </select>
                                  : <span className="db-theme-tag">{rec.theme}</span>}
                              </td>
                              <td><span className="db-type-tag">{rec.content_type || rec.entity_type || '—'}</span></td>
                              <td className="db-td-fact">
                                {isEditing
                                  ? <textarea className="db-inline-textarea" rows={3} value={draft.extracted_fact ?? rec.extracted_fact ?? rec.extracted_text ?? ''} onChange={(e) => setDraft({ ...draft, extracted_fact: e.target.value })} />
                                  : <>
                                      <span>{rec.extracted_fact || rec.extracted_text || (rec.table_data ? `${rec.table_data.length} table rows` : '—')}</span>
                                      {rec.source_text_chunk && <details className="db-source-details"><summary>Source</summary><p>{rec.source_text_chunk}</p></details>}
                                    </>}
                              </td>
                              <td>
                                {isEditing
                                  ? <input className="db-inline-input" value={draft.district ?? rec.spatial_units?.district ?? ''} onChange={(e) => setDraft({ ...draft, district: e.target.value })} />
                                  : Object.values(rec.spatial_units || {}).join(', ') || '—'}
                              </td>
                              <td>
                                {isEditing
                                  ? <input className="db-inline-input" value={draft.temporal_period ?? rec.temporal_period ?? ''} onChange={(e) => setDraft({ ...draft, temporal_period: e.target.value })} />
                                  : rec.temporal_period || '—'}
                              </td>
                              <td>
                                <span className={`db-review-badge ${rec.review_status || 'legacy'}`}>
                                  {rec.review_status === 'approved' ? 'Approved'
                                    : rec.review_status === 'pending_review' ? 'Pending'
                                    : rec.review_status === 'edited_and_approved' ? 'Edited'
                                    : 'Legacy'}
                                </span>
                              </td>
                              <td className="db-td-actions">
                                {isEditing
                                  ? <>
                                      <button className="db-act-btn db-act-save" onClick={() => saveEdit(rec)} disabled={busy}>Save</button>
                                      <button className="db-act-btn" onClick={() => { setEditingId(''); setDraft({}); }}>Cancel</button>
                                    </>
                                  : <>
                                      <button className="db-act-btn" onClick={() => { setEditingId(rec.theme_entity_id); setDraft({}); }} disabled={busy}>Edit</button>
                                      {rec.review_status === 'pending_review' && <button className="db-act-btn db-act-approve" onClick={() => approveOne(rec)} disabled={busy}>✓ Approve</button>}
                                      <button className="db-act-btn db-act-delete" onClick={() => removeOne(rec)} disabled={busy}>✕ Delete</button>
                                    </>}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}

            {/* Pagination */}
            {records.length > 0 && (
              <div className="db-pagination">
                <button className="db-btn" onClick={() => setPageIndex((p) => Math.max(0, p - 1))} disabled={recLoading || pageIndex === 0}>← Prev</button>
                <span>Page {pageIndex + 1}</span>
                <button className="db-btn" onClick={() => {
                  if (!nextToken) return;
                  setPageCursors((c) => [...c.slice(0, pageIndex + 1), nextToken]);
                  setPageIndex((p) => p + 1);
                }} disabled={recLoading || !nextToken}>Next →</button>
              </div>
            )}
            </div>{/* end db-records-section */}

            <div
              className="db-resize-handle db-resize-viewer"
              role="separator"
              aria-label="Resize PDF viewer"
              aria-orientation="vertical"
              aria-valuenow={viewerPaneWidth}
              tabIndex={0}
              onPointerDown={(event) => startResize('viewer', event)}
              onPointerMove={resizePane}
              onPointerUp={stopResize}
              onPointerCancel={stopResize}
              onLostPointerCapture={stopResize}
              onKeyDown={(event) => resizeWithKeyboard('viewer', event)}
            />

            {/* PDF Viewer panel */}
            <div className="db-viewer-panel">
              <DocViewer
                element={viewerEl}
                onClose={() => setViewerEl(null)}
              />
            </div>
          </div>{/* end db-split-layout */}
        </>}
      </main>
    </div>
  );
}
