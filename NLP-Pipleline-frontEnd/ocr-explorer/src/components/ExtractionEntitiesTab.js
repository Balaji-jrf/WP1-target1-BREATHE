import { useCallback, useEffect, useState } from 'react';
import { deleteExtraction, fetchDocuments, fetchExtractions, saveExtraction } from '../api';

const EMPTY_FORM = { theme_entity_id: '', theme: 'Climate_Heat', content_type: 'narrative_text', extracted_text: '', custom_attributes: {} };
const THEMES = ['Climate_Heat', 'Public_Health_Sanitation', 'Land_Use', 'Agriculture', 'Irrigation', 'Structural_Inequality', 'Policies', 'Tables', 'Figures'];

export default function ExtractionEntitiesTab({ initialDocumentId = '' }) {
  const [documents, setDocuments] = useState([]);
  const [documentId, setDocumentId] = useState('');
  const [records, setRecords] = useState([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [customAttributesText, setCustomAttributesText] = useState('{}');
  const [editing, setEditing] = useState(false);
  const [filterTheme, setFilterTheme] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const loadRecords = useCallback(async (id = documentId, theme = filterTheme) => {
    if (!id) { setRecords([]); return; }
    setError('');
    try { setRecords(await fetchExtractions(id, { theme })); }
    catch (err) { setError(err.message); }
  }, [documentId, filterTheme]);

  useEffect(() => {
    fetchDocuments().then((items) => {
      setDocuments(items);
      setDocumentId((current) => initialDocumentId || current || items[0]?.document_id || '');
    }).catch((err) => setError(err.message));
  }, [initialDocumentId]);
  useEffect(() => { loadRecords(documentId, filterTheme); }, [documentId, filterTheme, loadRecords]);

  const updateField = (event) => setForm((current) => ({ ...current, [event.target.name]: event.target.value }));
  const startEdit = (record) => {
    setForm({
      theme_entity_id: record.theme_entity_id,
      theme: record.theme || 'Policies',
      content_type: record.content_type || 'narrative_text',
      extracted_text: record.extracted_text || '',
      custom_attributes: record.custom_attributes || {},
      attributes: Object.fromEntries(Object.entries(record).filter(([key]) => !['id', 'document_id', 'theme_entity_id', 'theme', 'content_type', 'extracted_text', 'custom_attributes'].includes(key))),
    });
    setCustomAttributesText(JSON.stringify(record.custom_attributes || {}, null, 2));
    setEditing(true);
    setNotice('');
  };

  const submit = async (event) => {
    event.preventDefault();
    if (!documentId || !form.theme_entity_id.trim()) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const customAttributes = JSON.parse(customAttributesText || '{}');
      if (!customAttributes || Array.isArray(customAttributes) || typeof customAttributes !== 'object') {
        throw new Error('Custom attributes must be a JSON object.');
      }
      const { attributes = {}, ...fields } = form;
      await saveExtraction(documentId, { ...fields, custom_attributes: customAttributes, attributes: { ...attributes, content_type: fields.content_type, extracted_text: fields.extracted_text, custom_attributes: customAttributes } }, editing ? 'update' : 'create');
      setNotice(editing ? 'Extraction updated.' : 'Extraction added.');
      setForm(EMPTY_FORM); setEditing(false);
      setCustomAttributesText('{}');
      await loadRecords();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const remove = async (record) => {
    if (!documentId) return;
    setBusy(true); setError(''); setNotice('');
    try {
      await deleteExtraction(documentId, record.theme_entity_id);
      setNotice('Extraction deleted.');
      if (editing && form.theme_entity_id === record.theme_entity_id) { setForm(EMPTY_FORM); setEditing(false); }
      await loadRecords();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return (
    <section className="database-page">
      <div className="database-heading">
        <div><p className="section-kicker">DynamoDB · research entities</p><h2>Data-Extraction-entities</h2>
          <p>Review and maintain structured extraction records for each OCR document.</p></div>
      </div>
      {error && <div className="message error-message" role="alert">{error}</div>}
      {notice && <div className="database-notice" role="status">{notice}</div>}
      <div className="entity-controls">
        <label>Document<select value={documentId} onChange={(event) => setDocumentId(event.target.value)}>
          {documents.map((item) => <option key={item.document_id} value={item.document_id}>{item.filename} · {item.document_id}</option>)}
        </select></label>
        <label>Theme filter<select value={filterTheme} onChange={(event) => setFilterTheme(event.target.value)}>
          <option value="">All themes</option>{THEMES.map((theme) => <option key={theme}>{theme}</option>)}
        </select></label>
      </div>
      <div className="entity-workspace">
        <form className="entity-form" onSubmit={submit}>
          <h3>{editing ? 'Update extraction' : 'Add extraction'}</h3>
          <label>Theme/entity ID<input name="theme_entity_id" value={form.theme_entity_id} onChange={updateField} placeholder="CLIMATE#P05_001" required disabled={editing} /></label>
          <label>Theme<select name="theme" value={form.theme} onChange={updateField} disabled={editing}>{THEMES.map((theme) => <option key={theme}>{theme}</option>)}</select></label>
          <label>Content type<select name="content_type" value={form.content_type} onChange={updateField}>
            {['narrative_text', 'table', 'figure_crop', 'appendix'].map((type) => <option key={type}>{type}</option>)}
          </select></label>
          <label>Extracted text<textarea name="extracted_text" value={form.extracted_text} onChange={updateField} rows="5" placeholder="Passage, caption, or notes" /></label>
          <label>Custom attributes (JSON)<textarea value={customAttributesText} onChange={(event) => setCustomAttributesText(event.target.value)} rows="4" /></label>
          <div className="entity-form-actions">
            <button className="process-button" type="submit" disabled={busy || !documentId}>{busy ? 'Saving…' : editing ? 'Update record' : 'Add record'}</button>
            {editing && <button className="clear-btn" type="button" onClick={() => { setForm(EMPTY_FORM); setCustomAttributesText('{}'); setEditing(false); }}>Cancel</button>}
          </div>
        </form>
        <div className="entity-list">
          <div className="database-record-heading"><h3>Stored entities</h3><span>{records.length} records</span></div>
          {records.length === 0 && <div className="database-empty">No records match this document and theme.</div>}
          {records.map((record) => <article className="entity-row" key={record.theme_entity_id}>
            <div className="entity-row-top"><span className="ocr-type-tag">{record.theme}</span><span className="ocr-page-tag">Page {record.pdf_page_num || '—'}</span></div>
            <code>{record.theme_entity_id}</code>
            <p>{record.extracted_text || (record.table_data ? JSON.stringify(record.table_data) : 'No text content')}</p>
            <small>{record.content_type} · {record.entity_type}</small>
            <div className="entity-row-actions">
              <button className="hcard-btn" onClick={() => startEdit(record)}>Edit</button>
              <button className="hcard-btn entity-delete" onClick={() => remove(record)} disabled={busy}>Delete</button>
            </div>
          </article>)}
        </div>
      </div>
    </section>
  );
}
