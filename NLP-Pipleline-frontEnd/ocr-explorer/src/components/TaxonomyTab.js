import { useCallback, useEffect, useState } from 'react';
import { deleteTaxonomy, fetchTaxonomy, saveTaxonomy } from '../api';

const EMPTY = { entity_key: '', display_name: '', description: '', keywords: '', extraction_guidance: '', is_active: true };

export default function TaxonomyTab() {
  const [items, setItems] = useState([]);
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => setItems(await fetchTaxonomy()), []);
  useEffect(() => { load().catch((err) => setError(err.message)); }, [load]);

  const submit = async (event) => {
    event.preventDefault(); setBusy(true); setError(''); setNotice('');
    try {
      await saveTaxonomy({ ...form, entity_key: form.entity_key.toUpperCase(), keywords: form.keywords.split(',').map((word) => word.trim()).filter(Boolean) });
      setForm(EMPTY); await load(); setNotice('Active entity taxonomy saved.');
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const remove = async (key) => {
    setBusy(true); setError('');
    try { await deleteTaxonomy(key); await load(); setNotice(`${key} removed from the active taxonomy.`); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return <section className="database-page">
    <header className="database-heading"><div><p className="section-kicker">DynamoDB · global extraction schema</p><h2>Active entity taxonomy</h2><p>These definitions guide Nova Lite extraction for every document.</p></div><button className="clear-btn" onClick={() => load()} disabled={busy}>↻ Refresh</button></header>
    {error && <div className="message error-message" role="alert">{error}</div>}
    {notice && <div className="database-notice" role="status">{notice}</div>}
    <section className="taxonomy-manager">
      <form className="taxonomy-form" onSubmit={submit}>
        <input required placeholder="ENTITY_KEY" value={form.entity_key} onChange={(event) => setForm({ ...form, entity_key: event.target.value })} />
        <input required placeholder="Display name" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} />
        <input required placeholder="Description and extraction scope" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
        <input placeholder="Keywords, comma separated" value={form.keywords} onChange={(event) => setForm({ ...form, keywords: event.target.value })} />
        <input placeholder="Extraction guidance" value={form.extraction_guidance} onChange={(event) => setForm({ ...form, extraction_guidance: event.target.value })} />
        <label className="taxonomy-active-check"><input type="checkbox" checked={form.is_active} onChange={(event) => setForm({ ...form, is_active: event.target.checked })} /> Active for future ingestion</label>
        <button className="process-button" disabled={busy}>{busy ? 'Saving…' : 'Add / update theme'}</button>
      </form>
      <div className="taxonomy-list">{items.map((entry) => <article key={entry.entity_key}>
        <strong>{entry.display_name}</strong><code>{entry.entity_key}</code>
        <span>{entry.is_active ? 'Active' : 'Inactive'} · {entry.description}</span>
        {entry.keywords?.length > 0 && <small>Keywords: {entry.keywords.join(', ')}</small>}
        <button className="hcard-btn entity-delete" onClick={() => remove(entry.entity_key)} disabled={busy}>Delete</button>
      </article>)}</div>
    </section>
  </section>;
}
