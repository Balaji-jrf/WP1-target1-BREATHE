import { useRef, useState } from 'react';
import { uploadDocument } from '../api';
import ResultPanel from './ResultPanel';

const ACCEPTED_TYPES = '.png,.jpg,.jpeg,.tif,.tiff,.webp,.pdf';

export default function ExtractTab() {
  const [file, setFile] = useState(null);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef(null);

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
    setLoading(true);
    setError('');
    try {
      const payload = await uploadDocument(file);
      setResult(payload);
    } catch (err) {
      setError(err.message || 'Could not connect to the OCR service.');
    } finally {
      setLoading(false);
    }
  };

  return (
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

        {/* After upload, s3_presigned_url is returned by the processor Lambda */}
        <ResultPanel
          result={result}
          docUrl={result?.s3_presigned_url || null}
          kicker="Extraction complete"
        />
      </section>
    </>
  );
}
