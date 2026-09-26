import React, { useRef, useState, useEffect, useCallback } from 'react';
import './App.css';

const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';
const ACCEPTED_TYPES = '.png,.jpg,.jpeg,.tif,.tiff,.webp,.pdf';

function App() {
  const [tab, setTab] = useState('extract');
  const [file, setFile] = useState(null);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyItem, setHistoryItem] = useState(null);
  const [historyError, setHistoryError] = useState('');
  const [docUrl, setDocUrl] = useState(null);
  const inputRef = useRef(null);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError('');
    try {
      const res = await fetch(`${API_URL}/documents`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to load history');
      setHistory(data.documents || []);
    } catch (err) {
      setHistoryError(err.message);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  // Load history from DynamoDB when History tab is opened
  useEffect(() => {
    if (tab === 'history') loadHistory();
  }, [tab, loadHistory]);

  const chooseFile = (nextFile) => {
    if (!nextFile) return;
    const isSupported = /\.(pdf|png|jpe?g|tiff?|webp)$/i.test(nextFile.name);
    if (!isSupported) { setError('Please choose a PDF, PNG, JPEG, TIFF, or WebP file.'); return; }
    setFile(nextFile);
    setResult(null);
    setError('');
  };

  const handleUpload = async (e) => {
    e.preventDefault();
    if (!file || loading) return;
    const formData = new FormData();
    formData.append('file', file);
    setLoading(true);
    setError('');
    try {
      const response = await fetch(`${API_URL}/process-ocr`, { method: 'POST', body: formData });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || 'OCR processing failed.');
      setResult(payload);
    } catch (err) {
      setError(err.message || 'Could not connect to the OCR service.');
    } finally {
      setLoading(false);
    }
  };

  const fetchHistoryItem = async (document_id) => {
    setHistoryLoading(true);
    setHistoryItem(null);
    setDocUrl(null);
    try {
      const response = await fetch(`${API_URL}/document/${document_id}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || 'Failed to fetch document.');
      setDocUrl(payload.s3_presigned_url || null);
      setHistoryItem(payload.ocr_result || payload);
    } catch (err) {
      setHistoryItem({ error: err.message });
    } finally {
      setHistoryLoading(false);
    }
  };

  const formatConfidence = (c) => `${Math.round((parseFloat(c) || 0) * 100)}%`;
  const formatDate = (iso) => new Date(iso).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' });

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-mark">B</div>
        <div>
          <p className="eyebrow">BREATHE · WP1 · NLP PIPELINE</p>
          <h1>Archive Lens</h1>
        </div>
        <nav className="tab-nav">
          <button className={tab === 'extract' ? 'tab active' : 'tab'} onClick={() => setTab('extract')}>Extract</button>
          <button className={tab === 'history' ? 'tab active' : 'tab'} onClick={() => { setTab('history'); setHistoryItem(null); }}>
            History {history.length > 0 && <span className="badge">{history.length}</span>}
          </button>
        </nav>
        <span className="service-status"><span /> Textract ready</span>
        <span className="service-status nlp-status"><span className="dot-orange" /> NLP Pipeline — ongoing</span>
      </header>

      {tab === 'extract' && (
        <>
          <section className="intro">
            <div>
              <p className="section-kicker">Historical Document Intelligence</p>
              <h2>Digitise archival records<br /><em>into structured evidence.</em></h2>
              <p className="intro-copy">
                Part of the BREATHE project's NLP pipeline for analysing historical policy documents,
                archival material, speeches, and grey literature. Upload a scanned document — AWS Textract
                extracts every text passage with page location, confidence score, and bounding coordinates,
                ready for downstream NLP analysis and historical database creation.
              </p>
            </div>
            <div className="intro-stats">
              <strong>01</strong><span>Upload<br />document</span>
              <strong>02</strong><span>Extract<br />text</span>
              <strong>03</strong><span>Feed<br />NLP pipeline</span>
              <strong>04</strong><span>Going<br />On</span>
            </div>
          </section>

          <section className="workspace">
            <form onSubmit={handleUpload}>
              <div
                className={`dropzone ${isDragging ? 'is-dragging' : ''} ${file ? 'has-file' : ''}`}
                onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={(e) => { e.preventDefault(); setIsDragging(false); chooseFile(e.dataTransfer.files[0]); }}
                onClick={() => inputRef.current?.click()}
                role="button" tabIndex="0"
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click(); }}
              >
                <input ref={inputRef} type="file" accept={ACCEPTED_TYPES} onChange={(e) => chooseFile(e.target.files[0])} />
                <div className="upload-icon">↑</div>
                {file
                  ? <><p className="drop-title">{file.name}</p><p className="drop-note">{(file.size / 1024 / 1024).toFixed(2)} MB · Ready to process</p></>
                  : <><p className="drop-title">Drop a document here</p><p className="drop-note">or click to browse · PDF, PNG, JPEG, TIFF, WebP · max 10 MB</p></>
                }
              </div>
              <div className="action-row">
                <p className="limit-note"><span className="lock-icon">◈</span> Processed via Amazon Textract + NLP Pipeline · Stored securely in S3 + DynamoDB</p>
                <button className="process-button" type="submit" disabled={!file || loading}>
                  {loading ? <><span className="spinner" /> Extracting text…</> : <>Extract text <span>→</span></>}
                </button>
              </div>
            </form>

            {error && <div className="message error-message" role="alert">{error}</div>}

            {result && (
              <section className="results-panel">
                <div className="results-heading">
                  <div>
                    <p className="section-kicker">Extraction complete</p>
                    <h3>{result.filename}</h3>
                    <p className="doc-id">ID: {result.document_id}</p>
                  </div>
                  <div className="result-count">
                    <strong>{result.elements?.length || 0}</strong>
                    <span>text blocks<br />across {result.total_pages} page{result.total_pages === 1 ? '' : 's'}</span>
                  </div>
                </div>
                <div className="result-list">
                  {result.elements?.length
                    ? result.elements.map((el, i) => (
                      <article className="result-item" key={`${el.page}-${i}`}>
                        <div className="item-meta"><span>PAGE {String(el.page).padStart(2, '0')}</span><span>{formatConfidence(el.confidence)} confidence</span></div>
                        <p>{el.text}</p>
                        <small>bbox [{el.bbox.map(v => parseFloat(v).toFixed(3)).join(', ')}]</small>
                      </article>
                    ))
                    : <p className="empty-result">No text was detected in this document.</p>
                  }
                </div>
                <details className="json-details"><summary>View raw JSON payload <span>+</span></summary><pre>{JSON.stringify(result, null, 2)}</pre></details>
              </section>
            )}
          </section>
        </>
      )}

      {tab === 'history' && (
        <section className="history-panel">
          <div className="history-header">
            <div>
              <p className="section-kicker">Document archive</p>
              <h2 className="history-title">Processed Documents</h2>
              <p className="intro-copy">All documents extracted and stored in the database. Click any entry to reload its full OCR result.</p>
            </div>
            <button className="clear-btn" onClick={loadHistory} disabled={historyLoading}>
              {historyLoading ? 'Loading…' : '↻ Refresh'}
            </button>
          </div>

          {historyError && <div className="message error-message">{historyError}</div>}

          {historyLoading && !history.length
            ? <div className="empty-history"><p>Loading documents from database…</p></div>
            : history.length === 0
              ? <div className="empty-history"><p>No documents in the database yet.</p><p>Go to the Extract tab to upload your first document.</p></div>
              : (
              <div className="history-grid">
                {history.map((item) => (
                  <div className="history-card" key={item.document_id} onClick={() => fetchHistoryItem(item.document_id)}>
                    <div className="hcard-top">
                      <span className="hcard-name">{item.filename}</span>
                      <span className="hcard-pages">{item.total_pages}p</span>
                    </div>
                    <div className="hcard-meta">
                      <span>{item.element_count} text blocks</span>
                      <span>{formatDate(item.uploaded_at)}</span>
                    </div>
                    <p className="hcard-id">{item.document_id}</p>
                  </div>
                ))}
              </div>
            )
          }

          {historyLoading && <div className="message">Loading document…</div>}

          {historyItem && !historyItem.error && (
            <section className="results-panel">
              <div className="results-heading">
                <div><p className="section-kicker">Retrieved from database</p><h3>{historyItem.filename}</h3></div>
                <div className="result-count">
                  <strong>{historyItem.elements?.length || 0}</strong>
                  <span>text blocks<br />across {historyItem.total_pages} page{historyItem.total_pages === 1 ? '' : 's'}</span>
                </div>
              </div>

              {/* Document viewer */}
              {docUrl && (
                <div className="doc-viewer">
                  <div className="doc-viewer-bar">
                    <span className="section-kicker">Original document</span>
                    <a href={docUrl} target="_blank" rel="noreferrer" className="doc-open-btn">Open in new tab ↗</a>
                  </div>
                  {/\.pdf$/i.test(historyItem.filename)
                    ? <iframe src={docUrl} title="Document preview" className="doc-iframe" />
                    : <img src={docUrl} alt={historyItem.filename} className="doc-img" />
                  }
                </div>
              )}

              <div className="result-list">
                {historyItem.elements?.map((el, i) => (
                  <article className="result-item" key={`h-${el.page}-${i}`}>
                    <div className="item-meta"><span>PAGE {String(el.page).padStart(2, '0')}</span><span>{formatConfidence(el.confidence)} confidence</span></div>
                    <p>{el.text}</p>
                    <small>bbox [{el.bbox.map(v => parseFloat(v).toFixed(3)).join(', ')}]</small>
                  </article>
                ))}
              </div>
              <details className="json-details"><summary>View raw JSON payload <span>+</span></summary><pre>{JSON.stringify(historyItem, null, 2)}</pre></details>
            </section>
          )}

          {historyItem?.error && <div className="message error-message">{historyItem.error}</div>}
        </section>
      )}

      {/* <footer>
        BREATHE <span>·</span> WP1 NLP PIPELINE <span>·</span> HISTORICAL DOCUMENT OCR <span>·</span> POWERED BY AMAZON TEXTRACT
      </footer> */}
    </main>
  );
}

export default App;
