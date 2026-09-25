import React, { useRef, useState } from 'react';
import './App.css';

const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';
const ACCEPTED_TYPES = '.png,.jpg,.jpeg,.tif,.tiff,.webp,.pdf';

function App() {
  const [file, setFile] = useState(null);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef(null);

  const chooseFile = (nextFile) => {
    if (!nextFile) return;
    const isSupported = /\.(pdf|png|jpe?g|tiff?|webp)$/i.test(nextFile.name);
    if (!isSupported) {
      setError('Please choose a PDF, PNG, JPEG, TIFF, or WebP file.');
      return;
    }
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
      const response = await fetch(`${API_URL}/process-ocr`, {
        method: 'POST',
        body: formData,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || 'OCR processing failed.');
      setResult(payload);
    } catch (err) {
      setError(err.message || 'Could not connect to the OCR service.');
    } finally {
      setLoading(false);
    }
  };

  const formatConfidence = (confidence) => `${Math.round((confidence || 0) * 100)}%`;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-mark">B</div>
        <div>
          <p className="eyebrow">BREATHE / RESEARCH TOOLS</p>
          <h1>Archive Lens</h1>
        </div>
        <span className="service-status"><span /> OCR service ready</span>
      </header>

      <section className="intro">
        <div>
          <p className="section-kicker">Document intelligence</p>
          <h2>Turn historical pages<br /><em>into searchable evidence.</em></h2>
          <p className="intro-copy">Upload a scan or a multi-page PDF. Surya OCR will identify the text, page structure, confidence, and location of every extracted passage.</p>
        </div>
        <div className="intro-stats"><strong>01</strong><span>Upload<br />your source</span><strong>02</strong><span>Review<br />the result</span></div>
      </section>

      <section className="workspace">
        <form onSubmit={handleUpload}>
          <div
            className={`dropzone ${isDragging ? 'is-dragging' : ''} ${file ? 'has-file' : ''}`}
            onDragOver={(event) => { event.preventDefault(); setIsDragging(true); }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={(event) => { event.preventDefault(); setIsDragging(false); chooseFile(event.dataTransfer.files[0]); }}
            onClick={() => inputRef.current?.click()}
            role="button"
            tabIndex="0"
            onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') inputRef.current?.click(); }}
          >
            <input ref={inputRef} type="file" accept={ACCEPTED_TYPES} onChange={(event) => chooseFile(event.target.files[0])} />
            <div className="upload-icon">↑</div>
            {file ? (
              <><p className="drop-title">{file.name}</p><p className="drop-note">{(file.size / 1024 / 1024).toFixed(2)} MB · Ready to process</p></>
            ) : (
              <><p className="drop-title">Drop a document here</p><p className="drop-note">or click to browse · PDF, PNG, JPEG, TIFF, WebP</p></>
            )}
          </div>
          <div className="action-row">
            <p className="limit-note"><span className="lock-icon">◈</span> Files are processed locally by your OCR service</p>
            <button className="process-button" type="submit" disabled={!file || loading}>
              {loading ? <><span className="spinner" /> Reading document</> : <>Extract text <span>→</span></>}
            </button>
          </div>
        </form>

        {error && <div className="message error-message" role="alert">{error}</div>}

        {result && (
          <section className="results-panel">
            <div className="results-heading">
              <div><p className="section-kicker">Extraction complete</p><h3>{result.filename}</h3></div>
              <div className="result-count"><strong>{result.elements?.length || 0}</strong><span>text blocks<br />across {result.total_pages} page{result.total_pages === 1 ? '' : 's'}</span></div>
            </div>
            <div className="result-list">
              {result.elements?.length ? result.elements.map((element, index) => (
                <article className="result-item" key={`${element.page}-${index}`}>
                  <div className="item-meta"><span>PAGE {String(element.page).padStart(2, '0')}</span><span>{formatConfidence(element.confidence)} confidence</span></div>
                  <p>{element.text}</p>
                  <small>bbox [{element.bbox.map((value) => Math.round(value)).join(', ')}]</small>
                </article>
              )) : <p className="empty-result">No text was detected in this document.</p>}
            </div>
            <details className="json-details"><summary>View raw JSON payload <span>+</span></summary><pre>{JSON.stringify(result, null, 2)}</pre></details>
          </section>
        )}
      </section>
      <footer>ARCHIVE LENS <span>·</span> OCR EXTRACTION WORKSPACE <span>·</span> BREATHE WP1</footer>
    </main>
  );
}

export default App;