import { useRef, useState } from 'react';
import { saveDocumentMeta, uploadDocument } from '../api';
import ResultPanel from './ResultPanel';
import { PRESET_DOCUMENT_TAGS } from '../documentTags';

const ACCEPTED_TYPES = '.png,.jpg,.jpeg,.tif,.tiff,.pdf';

export default function ExtractTab() {
  const [file, setFile]           = useState(null);
  const [docName, setDocName]     = useState('');
  const [tagMode, setTagMode]     = useState('preset'); // 'preset' | 'custom'
  const [tag, setTag]             = useState(PRESET_DOCUMENT_TAGS[0]);
  const [customTag, setCustomTag] = useState('');
  const [result, setResult]       = useState(null);
  const [loading, setLoading]     = useState(false);
  const [loadingMsg, setLoadingMsg] = useState('Extracting text…');
  const [error, setError]         = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef(null);

  const chooseFile = (nextFile) => {
    if (!nextFile) return;
    const isSupported = /\.(pdf|png|jpe?g|tiff?)$/i.test(nextFile.name);
    if (!isSupported) { setError('Please choose a PDF, PNG, JPEG, or TIFF file.'); return; }
    setFile(nextFile);
    if (!docName) setDocName(nextFile.name.replace(/\.[^.]+$/, ''));
    setResult(null);
    setError('');
  };

  const handleUpload = async (e) => {
    e.preventDefault();
    if (!file || loading) return;
    const finalTag = tagMode === 'custom' ? customTag.trim() : tag;
    if (!finalTag) { setError('Please enter a document tag.'); return; }
    setLoading(true);
    setLoadingMsg('Extracting text…');
    setError('');
    try {
      const documentMeta = { doc_name: docName.trim() || file.name, doc_tag: finalTag };
      const payload = await uploadDocument(file, (msg) => setLoadingMsg(msg));
      try {
        await saveDocumentMeta(payload.document_id, documentMeta);
      } catch (metaErr) {
        setResult({ ...payload, ...documentMeta });
        setError(`OCR completed, but the document name and tag could not be saved: ${metaErr.message}`);
        return;
      }
      setResult({ ...payload, doc_name: docName.trim() || file.name, doc_tag: finalTag });
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
          <strong>01</strong><span>Upload Historical<br />Document</span>
          <strong>02</strong><span>Extract<br />Text, Layouts, Tables, Images</span>
          <strong>03</strong><span>Feed<br />LLM Pipeline</span>
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
              : <><p className="drop-title">Drop a document here</p><p className="drop-note">or click to browse · PDF, PNG, JPEG, TIFF</p></>
            }
          </div>

          {/* Document metadata fields */}
          {file && (
            <div className="doc-meta-fields">
              <label className="doc-meta-label">
                Document name
                <input
                  className="doc-meta-input"
                  value={docName}
                  onChange={(e) => setDocName(e.target.value)}
                  placeholder="Enter a descriptive name for this document"
                />
              </label>

              <label className="doc-meta-label">
                Document tag
                <div className="doc-tag-row">
                  <select
                    className="doc-meta-input"
                    value={tagMode === 'custom' ? '__custom__' : tag}
                    onChange={(e) => {
                      if (e.target.value === '__custom__') { setTagMode('custom'); }
                      else { setTagMode('preset'); setTag(e.target.value); }
                    }}
                  >
                    {PRESET_DOCUMENT_TAGS.map((t) => <option key={t} value={t}>{t}</option>)}
                    <option value="__custom__">Other (enter custom tag)…</option>
                  </select>
                  {tagMode === 'custom' && (
                    <input
                      className="doc-meta-input"
                      value={customTag}
                      onChange={(e) => setCustomTag(e.target.value)}
                      placeholder="Enter custom tag"
                      autoFocus
                    />
                  )}
                </div>
              </label>
            </div>
          )}

          <div className="action-row">
            <p className="limit-note"><span className="lock-icon">◈</span> Processed via Amazon Textract (OCR Engine) + LLM Pipeline · Stored securely in S3 + DynamoDB</p>
            <button className="process-button" type="submit" disabled={!file || loading}>
              {loading ? <><span className="spinner" /> {loadingMsg}</> : <>Extract text <span>→</span></>}
            </button>
          </div>
        </form>

        {error && <div className="message error-message" role="alert">{error}</div>}

        <ResultPanel
          result={result}
          docUrl={result?.s3_presigned_url || null}
          kicker="Extraction complete"
        />
      </section>
    </>
  );
}
