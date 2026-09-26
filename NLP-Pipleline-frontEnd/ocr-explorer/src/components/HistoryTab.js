import { useState, useCallback, useEffect } from 'react';
import { fetchDocuments, fetchDocument } from '../api';
import Modal from './Modal';

const formatDate = (iso) =>
  new Date(iso).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' });

const formatConfidence = (c) => `${Math.round((parseFloat(c) || 0) * 100)}%`;

function DocModal({ item, docUrl, onClose }) {
  const isPdf = /\.pdf$/i.test(item?.filename || '');
  return (
    <Modal title={item.filename} onClose={onClose}>
      <div className="modal-doc-meta">
        <span>{item.total_pages} page{item.total_pages === 1 ? '' : 's'}</span>
        <span>{item.elements?.length || 0} text blocks</span>
        <span>ID: {item.document_id}</span>
        <a href={docUrl} target="_blank" rel="noreferrer" className="doc-open-btn">
          Open in new tab ↗
        </a>
      </div>
      {isPdf
        ? <iframe src={docUrl} title="Document preview" className="modal-iframe" />
        : <img src={docUrl} alt={item.filename} className="modal-img" />
      }
    </Modal>
  );
}

function OcrModal({ item, onClose }) {
  return (
    <Modal title={`OCR — ${item.filename}`} onClose={onClose}>
      <div className="modal-ocr-meta">
        <span>{item.total_pages} page{item.total_pages === 1 ? '' : 's'}</span>
        <span>{item.elements?.length || 0} text blocks</span>
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

function HistoryCard({ item, onViewDoc, onViewOcr, isLoading, isSelected }) {
  return (
    <div className={`history-card ${isSelected ? 'is-selected' : ''}`}>
      <div className="hcard-top">
        <span className="hcard-name">{item.filename}</span>
        <span className="hcard-pages">{item.total_pages}p</span>
      </div>
      <div className="hcard-meta">
        <span>{item.element_count} text blocks</span>
        <span>{formatDate(item.uploaded_at)}</span>
      </div>
      <p className="hcard-id">{item.document_id}</p>
      <div className="hcard-actions">
        <button
          className="hcard-btn"
          onClick={() => onViewDoc(item.document_id)}
          disabled={isLoading}
        >
          {isLoading ? <><span className="spinner" /> Loading…</> : '⊞ View Doc'}
        </button>
        <button
          className="hcard-btn"
          onClick={() => onViewOcr(item.document_id)}
          disabled={isLoading}
        >
          {isLoading ? <><span className="spinner" /> Loading…</> : '≡ View OCR'}
        </button>
      </div>
    </div>
  );
}

export default function HistoryTab() {
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');

  // Which card is currently being fetched
  const [fetchingId, setFetchingId] = useState(null);
  const [fetchError, setFetchError] = useState('');

  // Modal state
  const [showDoc, setShowDoc] = useState(false);
  const [showOcr, setShowOcr] = useState(false);
  const [activeItem, setActiveItem] = useState(null);
  const [activeDocUrl, setActiveDocUrl] = useState(null);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError('');
    try {
      const docs = await fetchDocuments();
      setHistory(docs);
    } catch (err) {
      setHistoryError(err.message);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => { loadHistory(); }, [loadHistory]);

  const loadAndOpen = async (document_id, mode) => {
    setFetchingId(document_id);
    setFetchError('');
    try {
      const payload = await fetchDocument(document_id);
      const ocrData = payload.ocr_result || payload;
      setActiveItem({ ...ocrData, document_id: ocrData.document_id || document_id });
      setActiveDocUrl(payload.s3_presigned_url || null);
      if (mode === 'doc') setShowDoc(true);
      if (mode === 'ocr') setShowOcr(true);
    } catch (err) {
      setFetchError(err.message);
    } finally {
      setFetchingId(null);
    }
  };

  const closeAll = () => {
    setShowDoc(false);
    setShowOcr(false);
  };

  return (
    <section className="history-panel">
      <div className="history-header">
        <div>
          <p className="section-kicker">Document archive</p>
          <h2 className="history-title">Processed Documents</h2>
          <p className="intro-copy">
            All documents extracted and stored in the database.
            Click View Doc to see the original file, or View OCR to read the extracted text.
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
                  isSelected={fetchingId === item.document_id}
                  onViewDoc={(id) => loadAndOpen(id, 'doc')}
                  onViewOcr={(id) => loadAndOpen(id, 'ocr')}
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
    </section>
  );
}
