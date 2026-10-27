import { useState } from 'react';
import DocViewer from './DocViewer';
import Modal from './Modal';
import OcrResults from './OcrResults';

function ResultActions({ result, docUrl }) {
  const [showDoc, setShowDoc] = useState(false);
  const [showOcr, setShowOcr] = useState(false);
  const isPdf = /\.pdf$/i.test(result?.filename || '');

  return (
    <>
      <div className="result-actions">
        {docUrl && (
          <button className="action-btn" onClick={() => setShowDoc(true)}>⊞ View Doc</button>
        )}
        <button className="action-btn" onClick={() => setShowOcr(true)}>≡ View OCR Data</button>
        {docUrl && (
          <a
            className="action-btn"
            href={docUrl}
            download={result?.filename}
            onClick={(e) => e.stopPropagation()}
          >
            ↓ Download
          </a>
        )}
      </div>

      {showDoc && docUrl && (
        <Modal title={result.filename} onClose={() => setShowDoc(false)}>
          <div className="modal-doc-meta">
            <span>{result.total_pages} page{result.total_pages === 1 ? '' : 's'}</span>
            <span>{result.elements?.length || 0} text blocks</span>
            {result.document_id && <span>ID: {result.document_id}</span>}
            <a href={docUrl} target="_blank" rel="noreferrer" className="doc-open-btn">Open full ↗</a>
          </div>
          {isPdf
            ? <iframe src={docUrl} title="Document" className="modal-iframe" />
            : <img src={docUrl} alt={result.filename} className="modal-img" />
          }
        </Modal>
      )}

      {showOcr && (
        <Modal title={`OCR Data — ${result.filename}`} onClose={() => setShowOcr(false)}>
          <div className="modal-ocr-meta">
            <span>{result.total_pages} page{result.total_pages === 1 ? '' : 's'}</span>
            <span>{result.elements?.length || 0} text blocks</span>
            {result.uploaded_at && <span>{new Date(result.uploaded_at).toLocaleString('en-IN')}</span>}
          </div>
          <OcrResults result={result} />
          <details className="modal-json-details">
            <summary>View raw JSON payload</summary>
            <pre className="modal-json">{JSON.stringify(result, null, 2)}</pre>
          </details>
        </Modal>
      )}
    </>
  );
}

export default function ResultPanel({ result, docUrl, kicker }) {
  const [activeElement, setActiveElement] = useState(null);

  if (!result) return null;

  return (
    <section className="results-panel">
      <div className="results-heading">
        <div>
          <p className="section-kicker">{kicker || 'Extraction complete'}</p>
          <h3>{result.filename}</h3>
          {result.document_id && <p className="doc-id">ID: {result.document_id}</p>}
        </div>
        <div className="result-count">
          <strong>{result.elements?.length || 0}</strong>
          <span>text blocks<br />across {result.total_pages} page{result.total_pages === 1 ? '' : 's'}</span>
        </div>
      </div>

      {/* ── Side-by-side: OCR left, DocViewer right ── */}
      <div className="results-split-layout">
        <div className="results-split-ocr">
          <OcrResults
            result={result}
            onElementClick={setActiveElement}
            activeElement={activeElement}
          />
          <details className="json-details">
            <summary>View raw JSON payload <span>+</span></summary>
            <pre>{JSON.stringify(result, null, 2)}</pre>
          </details>
          <ResultActions result={result} docUrl={docUrl} />
        </div>

        <div className="results-split-viewer">
          <DocViewer
            element={activeElement}
            onClose={() => setActiveElement(null)}
          />
        </div>
      </div>
    </section>
  );
}
