import { useState, useCallback, useEffect, useRef } from 'react';
import { fetchDocuments, fetchDocument, downloadOcrJson, deleteDocument } from '../api';
import { useAuth } from '../AuthContext';
import { getDocumentTagOptions, UNTAGGED_FILTER } from '../documentTags';
import DocViewer from './DocViewer';
import Modal from './Modal';
import OcrResults from './OcrResults';

const formatDate = (iso) =>
  new Date(iso).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' });

const formatSize = (bytes) => {
  if (!bytes) return null;
  const b = Number(bytes);
  if (b >= 1024 * 1024) return `${(b / 1024 / 1024).toFixed(2)} MB`;
  if (b >= 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${b} B`;
};

const formatTime = (secs) => {
  if (!secs) return null;
  const s = Number(secs);
  return s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${s}s`;
};

function DocModal({ item, docUrl, onClose }) {
  const isPdf = /\.pdf$/i.test(item?.filename || '');
  const size = formatSize(item.file_size_bytes);
  const time = formatTime(item.processing_time_seconds);
  return (
    <Modal title={item.filename} onClose={onClose}>
      <div className="modal-doc-meta">
        <span>{item.total_pages} page{item.total_pages === 1 ? '' : 's'}</span>
        <span>{item.elements?.length || 0} text blocks</span>
        {size && <span>📄 {size}</span>}
        {time && <span>⏱ {time} to process</span>}
        <span>ID: {item.document_id}</span>
        <a href={docUrl} target="_blank" rel="noreferrer" className="doc-open-btn">Open in new tab ↗</a>
      </div>
      {isPdf
        ? <iframe src={docUrl} title="Document preview" className="modal-iframe" />
        : <img src={docUrl} alt={item.filename} className="modal-img" />
      }
    </Modal>
  );
}

function OcrModal({ item, onClose }) {
  const size = formatSize(item.file_size_bytes);
  const time = formatTime(item.processing_time_seconds);
  return (
    <Modal title={`OCR — ${item.filename}`} onClose={onClose}>
      <div className="modal-ocr-meta">
        <span>{item.total_pages} page{item.total_pages === 1 ? '' : 's'}</span>
        <span>{item.elements?.length || 0} text blocks</span>
        {size && <span>📄 {size}</span>}
        {time && <span>⏱ {time} to process</span>}
        {item.uploaded_at && <span>{new Date(item.uploaded_at).toLocaleString('en-IN')}</span>}
      </div>
      <OcrResults result={item} />
      <details className="modal-json-details">
        <summary>View raw JSON payload</summary>
        <pre className="modal-json">{JSON.stringify(item, null, 2)}</pre>
      </details>
    </Modal>
  );
}

const COMPARE_MIN = { w: 520, h: 340 };
const COMPARE_DEFAULT = {
  w: Math.min(1500, Math.max(Math.min(COMPARE_MIN.w, window.innerWidth - 24), window.innerWidth - 48)),
  h: Math.min(880, Math.max(Math.min(COMPARE_MIN.h, window.innerHeight - 24), window.innerHeight - 48)),
};

function CompareView({ item, docUrl, onClose }) {
  const [activeElement, setActiveElement] = useState(null);

  // size & position state
  const [size, setSize] = useState(COMPARE_DEFAULT);
  const [ocrPaneWidth, setOcrPaneWidth] = useState(Math.round(COMPARE_DEFAULT.w * 0.46));
  const [pos, setPos]   = useState({
    x: Math.round((window.innerWidth  - COMPARE_DEFAULT.w) / 2),
    y: Math.round((window.innerHeight - COMPARE_DEFAULT.h) / 2),
  });

  // drag-to-move
  const dragRef = useRef(null);
  const compareBodyRef = useRef(null);
  const paneResizeRef = useRef(null);
  const onDragMouseDown = (e) => {
    if (e.target.closest('button,a,iframe,canvas')) return;
    e.preventDefault();
    const startX = e.clientX - pos.x;
    const startY = e.clientY - pos.y;
    const onMove = (ev) => setPos({
      x: Math.max(12, Math.min(window.innerWidth - size.w - 12, ev.clientX - startX)),
      y: Math.max(12, Math.min(window.innerHeight - size.h - 12, ev.clientY - startY)),
    });
    const onUp   = () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  // resize handle
  const onResizeMouseDown = (e) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX, startY = e.clientY, startW = size.w, startH = size.h;
    const maxW = Math.max(1, window.innerWidth - pos.x - 12);
    const maxH = Math.max(1, window.innerHeight - pos.y - 12);
    const minW = Math.min(COMPARE_MIN.w, maxW);
    const minH = Math.min(COMPARE_MIN.h, maxH);
    const onMove = (ev) => setSize({
      w: Math.max(minW, Math.min(maxW, startW + ev.clientX - startX)),
      h: Math.max(minH, Math.min(maxH, startH + ev.clientY - startY)),
    });
    const onUp = () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const onPaneResizeStart = (e) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    paneResizeRef.current = { startX: e.clientX, startWidth: ocrPaneWidth };
  };
  const onPaneResize = (e) => {
    if (!paneResizeRef.current) return;
    const bodyWidth = compareBodyRef.current?.clientWidth || size.w;
    const maxWidth = Math.max(220, bodyWidth - 248);
    setOcrPaneWidth(Math.max(220, Math.min(maxWidth, paneResizeRef.current.startWidth + e.clientX - paneResizeRef.current.startX)));
  };
  const onPaneResizeEnd = () => { paneResizeRef.current = null; };
  const onPaneResizeKeyDown = (e) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    e.preventDefault();
    const bodyWidth = compareBodyRef.current?.clientWidth || size.w;
    const maxWidth = Math.max(220, bodyWidth - 248);
    const delta = e.key === 'ArrowRight' ? 20 : -20;
    setOcrPaneWidth((width) => Math.max(220, Math.min(maxWidth, width + delta)));
  };

  const handleElementClick = (element) => {
    setActiveElement(element);
  };

  return (
    <div className="compare-backdrop" onClick={onClose}>
      <div
        className="compare-popup"
        style={{ width: size.w, height: size.h, left: pos.x, top: pos.y, '--compare-ocr-width': `${ocrPaneWidth}px` }}
        onClick={(e) => e.stopPropagation()}
        ref={dragRef}
      >
        <div className="compare-bar" onMouseDown={onDragMouseDown}>
          <span className="compare-title">⧉ Compare — {item.filename}</span>
          <div className="compare-bar-right">
            <span className="compare-meta">
              {item.elements?.length || 0} blocks · {item.total_pages}p
              {formatSize(item.file_size_bytes) && ` · ${formatSize(item.file_size_bytes)}`}
              {formatTime(item.processing_time_seconds) && ` · ⏱${formatTime(item.processing_time_seconds)}`}
            </span>
            {docUrl && <a href={docUrl} target="_blank" rel="noreferrer" className="doc-open-btn">Open ↗</a>}
            <button className="modal-close" onClick={onClose}>✕</button>
          </div>
        </div>

        <div className="compare-body" ref={compareBodyRef}>
          {/* Left pane — OCR results */}
          <div className="compare-pane compare-pane-ocr">
            <div className="compare-pane-label">
              <span>OCR Extraction</span>
              <span className="compare-page-hint">Click any card to highlight in document →</span>
            </div>
            <div className="compare-ocr-scroll compare-ocr-explorer">
              <OcrResults
                result={item}
                onElementClick={handleElementClick}
                activeElement={activeElement}
              />
            </div>
          </div>

          <div
            className="compare-pane-resize-handle"
            role="separator"
            aria-label="Resize OCR and document panes"
            aria-orientation="vertical"
            aria-valuenow={ocrPaneWidth}
            tabIndex={0}
            onPointerDown={onPaneResizeStart}
            onPointerMove={onPaneResize}
            onPointerUp={onPaneResizeEnd}
            onPointerCancel={onPaneResizeEnd}
            onLostPointerCapture={onPaneResizeEnd}
            onKeyDown={onPaneResizeKeyDown}
          />

          {/* Right pane — DocViewer with bbox highlight */}
          <div className="compare-pane compare-pane-doc">
            <div className="compare-pane-label">
              <span>Document Viewer</span>
              {activeElement && (
                <span className="compare-page-hint">
                  Page {activeElement.page} · {activeElement.type}
                </span>
              )}
            </div>
            <div className="compare-docviewer-wrap">
              <DocViewer
                element={activeElement}
                onClose={() => setActiveElement(null)}
              />
            </div>
          </div>
        </div>

        <div className="compare-resize-handle" onMouseDown={onResizeMouseDown} title="Drag to resize" />
      </div>
    </div>
  );
}

function HistoryCard({ item, onViewDoc, onViewOcr, onCompare, onDatabase, isLoading, onDownload, isDownloading, isDeleting, deletionInProgress, onDelete, canDelete }) {
  const size = formatSize(item.file_size_bytes);
  const time = formatTime(item.processing_time_seconds);
  const busy = isLoading || isDownloading || deletionInProgress;
  return (
    <div className={`history-card ${isLoading ? 'is-selected' : ''}`}>
      <div className="hcard-top">
        <span className="hcard-name">Doc - {item.doc_name || item.filename}</span>
        <span className="hcard-pages">{item.total_pages}-pages</span>
      </div>
      {item.doc_tag && <span className="hcard-tag">{item.doc_tag}</span>}
      <div className="hcard-meta">
        <span>{item.element_count} text blocks</span>
        {size && <span>📄 {size}</span>}
        {time && <span>⏱ {time}</span>}
        <span>{formatDate(item.uploaded_at)}</span>
      </div>
      <p className="hcard-id">Doc-ID - {item.document_id}</p>
      <div className="hcard-actions">
        <button className="hcard-btn" onClick={() => onViewDoc(item.document_id)} disabled={busy}>
          {isLoading ? <><span className="spinner" /> …</> : '⊞ Doc'}
        </button>
        <button className="hcard-btn" onClick={() => onViewOcr(item.document_id)} disabled={busy}>
          {isLoading ? <><span className="spinner" /> …</> : '≡ OCR'}
        </button>
        <button className="hcard-btn hcard-btn-compare" onClick={() => onCompare(item.document_id)} disabled={busy}>
          {isLoading ? <><span className="spinner" /> …</> : '⧉ Compare'}
        </button>
        <button className="hcard-btn hcard-btn-download" onClick={() => onDownload(item.document_id)} disabled={busy}>
          {isDownloading ? <><span className="spinner" /> …</> : '⬇ JSON'}
        </button>
      </div>
        <button className="hcard-btn hcard-btn-download hcard-btn-database" onClick={() => onDatabase(item.document_id)} disabled={busy}>
          Database
        </button>
        {canDelete && (
          <button className="hcard-btn hcard-btn-delete" onClick={() => onDelete(item)} disabled={busy}>
            {isDeleting ? <><span className="spinner" /> Deleting…</> : 'Delete document'}
          </button>
        )}
    </div>
  );
}

export default function HistoryTab({ onCountChange, onDatabase }) {
  const { auth } = useAuth();
  const [history, setHistory] = useState([]);
  const [tagFilter, setTagFilter] = useState('all');
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [fetchingId, setFetchingId] = useState(null);
  const [downloadingId, setDownloadingId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [fetchError, setFetchError] = useState('');

  const [showDoc, setShowDoc] = useState(false);
  const [showOcr, setShowOcr] = useState(false);
  const [showCompare, setShowCompare] = useState(false);
  const [activeItem, setActiveItem] = useState(null);
  const [activeDocUrl, setActiveDocUrl] = useState(null);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError('');
    try {
      const docs = await fetchDocuments();
      setHistory(docs);
      onCountChange?.(docs.length);
    } catch (err) {
      setHistoryError(err.message);
    } finally {
      setHistoryLoading(false);
    }
  }, [onCountChange]);

  useEffect(() => { loadHistory(); }, [loadHistory]);

  const loadAndOpen = async (document_id, mode) => {
    setFetchingId(document_id);
    setFetchError('');
    try {
      const payload = await fetchDocument(document_id);
      setActiveItem({ ...payload, document_id: payload.document_id || document_id });
      setActiveDocUrl(payload.s3_presigned_url || null);
      if (mode === 'doc') setShowDoc(true);
      if (mode === 'ocr') setShowOcr(true);
      if (mode === 'compare') setShowCompare(true);
    } catch (err) {
      setFetchError(err.message);
    } finally {
      setFetchingId(null);
    }
  };

  const handleDownload = async (document_id) => {
    setDownloadingId(document_id);
    setFetchError('');
    try {
      await downloadOcrJson(document_id);
    } catch (err) {
      setFetchError(err.message);
    } finally {
      setDownloadingId(null);
    }
  };

  const closeAll = () => { setShowDoc(false); setShowOcr(false); setShowCompare(false); };
  const handleDelete = async (item) => {
    const name = item.doc_name || item.filename || item.document_id;
    if (!window.confirm(`Permanently delete "${name}" and its OCR files and extracted records? This cannot be undone.`)) return;
    setDeletingId(item.document_id);
    setFetchError('');
    try {
      await deleteDocument(item.document_id);
      const nextHistory = history.filter((document) => document.document_id !== item.document_id);
      setHistory(nextHistory);
      onCountChange?.(nextHistory.length);
      if (activeItem?.document_id === item.document_id) {
        closeAll();
        setActiveItem(null);
        setActiveDocUrl(null);
      }
    } catch (err) {
      setFetchError(err.message);
    } finally {
      setDeletingId(null);
    }
  };
  const tagOptions = getDocumentTagOptions(history);
  const visibleHistory = history.filter((item) => {
    const tag = item.doc_tag?.trim() || '';
    return tagFilter === 'all' || (tagFilter === UNTAGGED_FILTER ? !tag : tag === tagFilter);
  });

  return (
    <section className="history-panel">
      <div className="history-header">
        <div>
          <h2 className="history-title">Processed Documents</h2>
          <p className="intro-copy">
            Browse all extracted documents, OCR output JSON. <br></br>View the original file, read the OCR text, layouts, tables, images <br></br>Use Compare to see both side-by-side, understand better
          </p>
        </div>
        <button className="clear-btn" onClick={() => onDatabase?.('')}>Main Database</button>
        <button className="clear-btn" onClick={loadHistory} disabled={historyLoading}>
          {historyLoading ? 'Loading…' : '↻ Refresh'}
        </button>
      </div>

      {historyError && <div className="message error-message">{historyError}</div>}
      {fetchError && <div className="message error-message">{fetchError}</div>}

      {history.length > 0 && (
        <div className="history-filters">
          <label htmlFor="history-tag-filter">Document tag</label>
          <select id="history-tag-filter" value={tagFilter} onChange={(event) => setTagFilter(event.target.value)}>
            {tagOptions.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
          </select>
        </div>
      )}

      {historyLoading && !history.length
        ? <div className="empty-history"><p>Loading documents from database…</p></div>
        : history.length === 0
          ? (
            <div className="empty-history">
              <p>No documents in the database yet.</p>
              <p>Go to the Extract tab to upload your first document.</p>
            </div>
          )
          : (
            visibleHistory.length === 0
              ? <div className="empty-history"><p>No documents match this tag.</p></div>
              : <div className="history-grid">
              {visibleHistory.map((item) => (
                <HistoryCard
                  key={item.document_id}
                  item={item}
                  isLoading={fetchingId === item.document_id}
                  isDownloading={downloadingId === item.document_id}
                  isDeleting={deletingId === item.document_id}
                  deletionInProgress={Boolean(deletingId)}
                  canDelete={auth?.role === 'admin'}
                  onViewDoc={(id) => loadAndOpen(id, 'doc')}
                  onViewOcr={(id) => loadAndOpen(id, 'ocr')}
                  onCompare={(id) => loadAndOpen(id, 'compare')}
                  onDatabase={(id) => onDatabase?.(id)}
                  onDownload={handleDownload}
                  onDelete={handleDelete}
                />
              ))}
            </div>
          )
      }

      {showDoc && activeItem && activeDocUrl && (
        <DocModal item={activeItem} docUrl={activeDocUrl} onClose={closeAll} />
      )}
      {showOcr && activeItem && (
        <OcrModal item={activeItem} onClose={closeAll} />
      )}
      {showCompare && activeItem && (
        <CompareView item={activeItem} docUrl={activeDocUrl} onClose={closeAll} />
      )}
    </section>
  );
}
