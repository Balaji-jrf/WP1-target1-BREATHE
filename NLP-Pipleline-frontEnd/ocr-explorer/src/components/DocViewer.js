import { useEffect, useRef, useState } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';

// Use CDN-hosted worker — avoids .mjs MIME type issues on Amplify/CloudFront
pdfjs.GlobalWorkerOptions.workerSrc =
  'https://unpkg.com/pdfjs-dist@4.8.69/build/pdf.worker.min.mjs';

const TYPE_LABELS = {
  text: 'Text Block',
  layout: 'Layout Region',
  tables: 'Table',
  figures: 'Figure',
};

const TYPE_COLOURS = {
  text:    '#3e7955',
  layout:  '#c46545',
  tables:  '#4a7aaa',
  figures: '#8b5ca8',
};

/**
 * DocViewer — right-side PDF viewer with bbox highlight.
 *
 * Props:
 *   element   — { page, bbox, type, text, filename, docUrl } | null
 *   onClose   — () => void
 */
export default function DocViewer({ element, onClose }) {
  const [pageCount, setPageCount] = useState(0);
  const [page, setPage]           = useState(1);
  const [error, setError]         = useState('');
  const [width, setWidth]         = useState(320);
  const wrapRef  = useRef(null);
  const paneRef  = useRef(null);

  // sync page when element changes
  useEffect(() => {
    if (element) {
      setPage(Number(element.page) || 1);
      setError('');
    }
  }, [element]);

  // measure available width for the PDF canvas
  useEffect(() => {
    if (!paneRef.current) return;
    const ro = new ResizeObserver(([entry]) => {
      setWidth(Math.max(200, entry.contentRect.width - 2));
    });
    ro.observe(paneRef.current);
    return () => ro.disconnect();
  }, [element]);

  if (!element) {
    return (
      <aside className="dv-panel dv-placeholder-panel">
        <div className="dv-placeholder">
          <div className="dv-placeholder-icon">📄</div>
          <p>Click any text block,<br />layout region, table<br />or figure to view it here</p>
        </div>
      </aside>
    );
  }

  const isPdf    = /\.pdf$/i.test(element.filename || '');
  const hasBbox  = Array.isArray(element.bbox) && element.bbox.length === 4;
  const colour   = TYPE_COLOURS[element.type] || '#d46a46';
  const typeLabel = TYPE_LABELS[element.type] || element.type || '';

  return (
    <aside className="dv-panel">
      {/* Header */}
      <div className="dv-head">
        <div className="dv-head-left">
          <span className="dv-type-tag" style={{ background: colour + '22', color: colour }}>
            {typeLabel}
          </span>
          <span className="dv-filename">{element.filename}</span>
        </div>
        <button className="dv-close" onClick={onClose} aria-label="Close viewer">×</button>
      </div>

      {/* Meta */}
      <div className="dv-meta">
        <span>Page {element.page}</span>
        {hasBbox && (
          <span>
            bbox [{element.bbox.map((v) => Number(v).toFixed(3)).join(', ')}]
          </span>
        )}
      </div>

      {/* Extracted text preview */}
      {element.text && (
        <div className="dv-text-preview">
          <p className="dv-text-label">Extracted text</p>
          <p className="dv-text-content">{element.text}</p>
        </div>
      )}

      {error && <p className="dv-error">{error}</p>}

      {/* Page nav */}
      {isPdf && pageCount > 0 && (
        <div className="dv-page-nav">
          <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>← Prev</button>
          <span>Page {page}{pageCount ? ` of ${pageCount}` : ''}</span>
          <button disabled={page >= pageCount} onClick={() => setPage((p) => p + 1)}>Next →</button>
        </div>
      )}

      {/* PDF render with bbox overlay */}
      <div className="dv-body" ref={paneRef}>
        {element.docUrl ? (
          isPdf ? (
            <div className="dv-doc-wrap" ref={wrapRef}>
              <Document
                file={element.docUrl}
                onLoadSuccess={({ numPages }) => {
                  setPageCount(numPages);
                  setPage(Math.min(Number(element.page) || 1, numPages));
                }}
                onLoadError={(e) => setError(`PDF error: ${e.message}`)}
                loading={<p className="dv-loading">Loading document…</p>}
              >
                <div className="dv-pdf-wrap">
                  <Page
                    pageNumber={page}
                    width={width}
                    renderTextLayer={false}
                    renderAnnotationLayer={false}
                  />
                  {/* Bbox highlight — only on the matching page */}
                  {hasBbox && page === (Number(element.page) || 1) && (
                    <div
                      className="dv-bbox"
                      style={{
                        borderColor: colour,
                        background:  colour + '28',
                        left:   `${element.bbox[0] * 100}%`,
                        top:    `${element.bbox[1] * 100}%`,
                        width:  `${(element.bbox[2] - element.bbox[0]) * 100}%`,
                        height: `${(element.bbox[3] - element.bbox[1]) * 100}%`,
                      }}
                    />
                  )}
                </div>
              </Document>
            </div>
          ) : (
            /* Image document */
            <div className="dv-doc-wrap" style={{ position: 'relative' }}>
              <img src={element.docUrl} alt={element.filename} style={{ display: 'block', width: '100%' }} />
              {hasBbox && (
                <div
                  className="dv-bbox"
                  style={{
                    borderColor: colour,
                    background:  colour + '28',
                    left:   `${element.bbox[0] * 100}%`,
                    top:    `${element.bbox[1] * 100}%`,
                    width:  `${(element.bbox[2] - element.bbox[0]) * 100}%`,
                    height: `${(element.bbox[3] - element.bbox[1]) * 100}%`,
                  }}
                />
              )}
            </div>
          )
        ) : (
          <p className="dv-loading">No document URL available for this record.</p>
        )}
      </div>
    </aside>
  );
}
