import { useCallback, useEffect, useState } from 'react';
import { deleteExtraction, deleteTaxonomy, fetchDocuments, fetchExtractions, fetchTaxonomy, saveExtraction, saveTaxonomy } from '../api';

const EMPTY_FORM = { theme_entity_id: '', theme: '', content_type: 'narrative_text', extracted_fact: '', custom_attributes: {} };

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
  const [taxonomy, setTaxonomy] = useState([]);
  const [taxForm, setTaxForm] = useState({ entity_key: '', display_name: '', description: '', keywords: '', extraction_guidance: '', is_active: true });

  const loadTaxonomy = useCallback(async () => {
    const entries = await fetchTaxonomy(); setTaxonomy(entries);
    setForm((current) => current.theme || !entries.some((item) => item.is_active) ? current : { ...current, theme: entries.find((item) => item.is_active).entity_key });
  }, []);

  const loadRecords = useCallback(async (id = documentId, theme = filterTheme) => {
    if (!id) { setRecords([]); return; }
    setError('');
    try { setRecords(await fetchExtractions(id, { theme, status: 'all' })); }
    catch (err) { setError(err.message); }
  }, [documentId, filterTheme]);

  useEffect(() => {
    fetchDocuments().then((items) => {
      setDocuments(items);
      setDocumentId((current) => initialDocumentId || current || items[0]?.document_id || '');
    }).catch((err) => setError(err.message));
    loadTaxonomy().catch((err) => setError(err.message));
  }, [initialDocumentId, loadTaxonomy]);
  useEffect(() => { loadRecords(documentId, filterTheme); }, [documentId, filterTheme, loadRecords]);

  const updateField = (event) => setForm((current) => ({ ...current, [event.target.name]: event.target.value }));
  const startEdit = (record) => {
    setForm({
      theme_entity_id: record.theme_entity_id,
      theme: record.theme || taxonomy.find((item) => item.is_active)?.entity_key || '',
      content_type: record.content_type || 'narrative_text',
      extracted_fact: record.extracted_fact || record.extracted_text || '',
      custom_attributes: record.custom_attributes || {},
      attributes: Object.fromEntries(Object.entries(record).filter(([key]) => !['id', 'document_id', 'theme_entity_id', 'theme', 'content_type', 'extracted_text', 'extracted_fact', 'custom_attributes'].includes(key))),
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
      await saveExtraction(documentId, { ...fields, review_status: 'pending_review', custom_attributes: customAttributes, attributes: { ...attributes, content_type: fields.content_type, extracted_fact: fields.extracted_fact, custom_attributes: customAttributes } }, editing ? 'update' : 'create');
      setNotice(editing ? 'Extraction updated.' : 'Extraction added.');
      setForm(EMPTY_FORM); setEditing(false);
      setCustomAttributesText('{}');
      await loadRecords(); await loadTaxonomy();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const saveTaxonomyEntry = async (event) => {
    event.preventDefault(); setBusy(true); setError(''); setNotice('');
    try {
      await saveTaxonomy({ ...taxForm, entity_key: taxForm.entity_key.toUpperCase(), keywords: taxForm.keywords.split(',').map((word) => word.trim()).filter(Boolean) });
      setTaxForm({ entity_key: '', display_name: '', description: '', keywords: '', extraction_guidance: '', is_active: true });
      await loadTaxonomy(); setNotice('Entity taxonomy saved.');
    } catch (err) { setError(err.message); } finally { setBusy(false); }
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
          <option value="">All themes</option>{taxonomy.map((entry) => <option key={entry.entity_key} value={entry.entity_key}>{entry.display_name}</option>)}
        </select></label>
      </div>
      <div className="entity-workspace">
        <form className="entity-form" onSubmit={submit}>
          <h3>{editing ? 'Update extraction' : 'Add extraction'}</h3>
          <label>Theme/entity ID<input name="theme_entity_id" value={form.theme_entity_id} onChange={updateField} placeholder="CLIMATE#P05_001" required disabled={editing} /></label>
          <label>Theme<select name="theme" value={form.theme} onChange={updateField} disabled={editing} required>{taxonomy.filter((item) => item.is_active).map((item) => <option key={item.entity_key} value={item.entity_key}>{item.display_name}</option>)}</select></label>
          <label>Content type<select name="content_type" value={form.content_type} onChange={updateField}>
            {['narrative_text', 'table', 'figure_crop', 'appendix'].map((type) => <option key={type}>{type}</option>)}
          </select></label>
          <label>Extracted fact<textarea name="extracted_fact" value={form.extracted_fact} onChange={updateField} rows="5" placeholder="Meaningful fact, caption, or notes" /></label>
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
            <p>{record.extracted_fact || record.extracted_text || (record.table_data ? JSON.stringify(record.table_data) : 'No text content')}</p>
            <small>{record.content_type} · {record.entity_type}</small>
            <div className="entity-row-actions">
              <button className="hcard-btn" onClick={() => startEdit(record)}>Edit</button>
              <button className="hcard-btn entity-delete" onClick={() => remove(record)} disabled={busy}>Delete</button>
            </div>
          </article>)}
        </div>
      </div>
      <section className="taxonomy-manager">
        <div className="database-record-heading"><h3>Active entity taxonomy</h3><span>Controls Nova Lite extraction prompts</span></div>
        <form className="taxonomy-form" onSubmit={saveTaxonomyEntry}>
          <input required placeholder="ENTITY_KEY" value={taxForm.entity_key} onChange={(event) => setTaxForm({ ...taxForm, entity_key: event.target.value })} />
          <input required placeholder="Display name" value={taxForm.display_name} onChange={(event) => setTaxForm({ ...taxForm, display_name: event.target.value })} />
          <input required placeholder="Description and extraction scope" value={taxForm.description} onChange={(event) => setTaxForm({ ...taxForm, description: event.target.value })} />
          <input placeholder="Keywords, comma separated" value={taxForm.keywords} onChange={(event) => setTaxForm({ ...taxForm, keywords: event.target.value })} />
          <button className="process-button" disabled={busy}>Add / update theme</button>
        </form>
        <div className="taxonomy-list">{taxonomy.map((entry) => <article key={entry.entity_key}><strong>{entry.display_name}</strong><code>{entry.entity_key}</code><span>{entry.is_active ? 'Active' : 'Inactive'} · {entry.description}</span><button className="hcard-btn entity-delete" onClick={async () => { try { await deleteTaxonomy(entry.entity_key); await loadTaxonomy(); } catch (err) { setError(err.message); } }}>Delete</button></article>)}</div>
      </section>
    </section>
  );
}
