import { useState, useCallback, useEffect, useRef } from 'react';
import { fetchDocuments, fetchDocument } from '../api';
import Modal from './Modal';

const formatDate = (iso) =>
  new Date(iso).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' });

const formatConfidence = (c) => `${Math.round((parseFloat(c) || 0) * 100)}%`;

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
      <div className="modal-ocr-list">
        {item.elements?.length
          ? item.elements.map((el, i) => (
            <article className="modal-ocr-item" key={`ocr-${el.page}-${i}`}>
              <div className="modal-ocr-item-meta">
                <span>PAGE {String(el.page).padStart(2, '0')}</span>
                <span>{formatConfidence(el.confidence)} confidence</span>
              </div>
              <p>{el.text}</p>
            </article>
          ))
          : <p className="empty-result" style={{ padding: '24px' }}>No text detected.</p>
        }
      </div>
    </Modal>
  );
}

const COMPARE_MIN = { w: 520, h: 340 };
const COMPARE_DEFAULT = { w: Math.min(1100, window.innerWidth - 48), h: Math.min(680, window.innerHeight - 80) };

function CompareView({ item, docUrl, onClose }) {
  const isPdf = /\.pdf$/i.test(item?.filename || '');

  // size & position state
  const [size, setSize] = useState(COMPARE_DEFAULT);
  const [pos, setPos] = useState({
    x: Math.round((window.innerWidth - COMPARE_DEFAULT.w) / 2),
    y: Math.round((window.innerHeight - COMPARE_DEFAULT.h) / 2),
  });

  // drag-to-move
  const dragRef = useRef(null);
  const onDragMouseDown = (e) => {
    if (e.target.closest('button,a,iframe')) return;
    e.preventDefault();
    const startX = e.clientX - pos.x;
    const startY = e.clientY - pos.y;
    const onMove = (ev) => setPos({ x: ev.clientX - startX, y: ev.clientY - startY });
    const onUp = () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  // resize handle (bottom-right corner)
  const onResizeMouseDown = (e) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startY = e.clientY;
    const startW = size.w;
    const startH = size.h;
    const onMove = (ev) => setSize({
      w: Math.max(COMPARE_MIN.w, startW + ev.clientX - startX),
      h: Math.max(COMPARE_MIN.h, startH + ev.clientY - startY),
    });
    const onUp = () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  return (
    <div className="compare-backdrop" onClick={onClose}>
      <div
        className="compare-popup"
        style={{ width: size.w, height: size.h, left: pos.x, top: pos.y }}
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
        <div className="compare-body">
          <div className="compare-pane">
            <div className="compare-pane-label">Original Document</div>
            {docUrl
              ? isPdf
                ? <iframe src={docUrl} title="Document" className="compare-iframe" />
                : <img src={docUrl} alt={item.filename} className="compare-img" />
              : <div className="compare-no-doc">No document URL available</div>
            }
          </div>
          <div className="compare-pane">
            <div className="compare-pane-label">OCR Text — {item.elements?.length || 0} blocks</div>
            <div className="compare-ocr-scroll">
              {item.elements?.length
                ? item.elements.map((el, i) => (
                  <article className="compare-ocr-item" key={`cmp-${el.page}-${i}`}>
                    <div className="compare-ocr-meta">
                      <span>P{String(el.page).padStart(2, '0')}</span>
                      <span>{formatConfidence(el.confidence)}</span>
                    </div>
                    <p>{el.text}</p>
                  </article>
                ))
                : <p className="empty-result" style={{ padding: '24px' }}>No text detected.</p>
              }
            </div>
          </div>
        </div>
        <div className="compare-resize-handle" onMouseDown={onResizeMouseDown} title="Drag to resize" />
      </div>
    </div>
  );
}

function HistoryCard({ item, onViewDoc, onViewOcr, onCompare, isLoading }) {
  const size = formatSize(item.file_size_bytes);
  const time = formatTime(item.processing_time_seconds);
  return (
    <div className={`history-card ${isLoading ? 'is-selected' : ''}`}>
      <div className="hcard-top">
        <span className="hcard-name">Doc - {item.filename}</span>
        <span className="hcard-pages">{item.total_pages}-pages</span>
      </div>
      <div className="hcard-meta">
        <span>{item.element_count} text blocks</span>
        {size && <span>📄 {size}</span>}
        {time && <span>⏱ {time}</span>}
        <span>{formatDate(item.uploaded_at)}</span>
      </div>
      <p className="hcard-id">{item.document_id}</p>
      <div className="hcard-actions">
        <button className="hcard-btn" onClick={() => onViewDoc(item.document_id)} disabled={isLoading}>
          {isLoading ? <><span className="spinner" /> …</> : '⊞ Doc'}
        </button>
        <button className="hcard-btn" onClick={() => onViewOcr(item.document_id)} disabled={isLoading}>
          {isLoading ? <><span className="spinner" /> …</> : '≡ OCR'}
        </button>
        <button className="hcard-btn hcard-btn-compare" onClick={() => onCompare(item.document_id)} disabled={isLoading}>
          {isLoading ? <><span className="spinner" /> …</> : '⧉ Compare'}
        </button>
      </div>
    </div>
  );
}

export default function HistoryTab({ onCountChange }) {
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [fetchingId, setFetchingId] = useState(null);
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

  const closeAll = () => { setShowDoc(false); setShowOcr(false); setShowCompare(false); };

  return (
    <section className="history-panel">
      <div className="history-header">
        <div>
          <p className="section-kicker">Document archive</p>
          <h2 className="history-title">Processed Documents</h2>
          <p className="intro-copy">
            Browse all extracted documents. View the original file, read the OCR text, or use Compare to see both side-by-side.
          </p>
        </div>
        <button className="clear-btn" onClick={loadHistory} disabled={historyLoading}>
          {historyLoading ? 'Loading…' : '↻ Refresh'}
        </button>
      </div>

      {historyError && <div className="message error-message">{historyError}</div>}
      {fetchError && <div className="message error-message">{fetchError}</div>}

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
            <div className="history-grid">
              {history.map((item) => (
                <HistoryCard
                  key={item.document_id}
                  item={item}
                  isLoading={fetchingId === item.document_id}
                  onViewDoc={(id) => loadAndOpen(id, 'doc')}
                  onViewOcr={(id) => loadAndOpen(id, 'ocr')}
                  onCompare={(id) => loadAndOpen(id, 'compare')}
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
