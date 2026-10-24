import { useMemo, useState } from 'react';
import { approveDocumentRecords, createDocumentRecord, deleteDocumentRecord, fetchDocumentRecords, updateDocumentRecord } from '../api';

export default function ExtractionReviewModal({ documentId, records, taxonomy, onClose, onChanged }) {
  const [items, setItems] = useState(records);
  const [selected, setSelected] = useState(() => new Set(records.map((item) => item.theme_entity_id)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const groups = useMemo(() => items.reduce((result, item) => {
    (result[item.theme] ||= []).push(item); return result;
  }, {}), [items]);

  const patch = async (item, attributes) => {
    setBusy(true); setError('');
    try {
      const updated = await updateDocumentRecord(documentId, item.theme_entity_id, attributes);
      setItems((current) => current.map((row) => row.theme_entity_id === item.theme_entity_id ? updated : row));
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };
  const remove = async (item) => {
    setBusy(true); setError('');
    try {
      await deleteDocumentRecord(documentId, item.theme_entity_id);
      setItems((current) => current.filter((row) => row.theme_entity_id !== item.theme_entity_id));
      setSelected((current) => { const next = new Set(current); next.delete(item.theme_entity_id); return next; });
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };
  const add = async () => {
    const theme = taxonomy.find((entry) => entry.is_active)?.entity_key;
    if (!theme) { setError('Add an active taxonomy entry first.'); return; }
    const id = `${theme}#MANUAL_${Date.now()}`;
    setBusy(true); setError('');
    try {
      const result = await createDocumentRecord(documentId, { theme_entity_id: id, theme, entity_type: 'manual_fact', content_type: 'narrative_text', extracted_fact: '', review_status: 'pending_review', spatial_units: {}, custom_attributes: {} });
      setItems((current) => [...current, result.record]);
      setSelected((current) => new Set([...current, id]));
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };
  const approve = async () => {
    if (!selected.size) { setError('Select at least one extraction to approve.'); return; }
    setBusy(true); setError('');
    try {
      const result = await approveDocumentRecords(documentId, [...selected]);
      await onChanged();
      if (result.pending_review_count > 0) {
        const pending = await fetchDocumentRecords(documentId, { status: 'pending_review' });
        setItems(pending);
        setSelected(new Set(pending.map((item) => item.theme_entity_id)));
        setError(`${result.approved_count} approved. ${result.pending_review_count} records still need review.`);
        return;
      }
      onClose();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return <div className="modal-overlay" role="presentation" onClick={onClose}>
    <section className="modal-box extraction-review" role="dialog" aria-modal="true" aria-label="Review OCR extractions" onClick={(event) => event.stopPropagation()}>
      <header className="modal-header"><div><span className="modal-title">Review extracted research facts</span><p>{items.length} staged records · select the facts to approve</p></div><button className="modal-close" onClick={onClose}>✕</button></header>
      <div className="modal-body extraction-review-body">
        {error && <div className="message error-message" role="alert">{error}</div>}
        {items.length === 0 && <p className="database-empty">No pending extractions remain.</p>}
        {Object.entries(groups).map(([theme, rows]) => <section className="review-theme-group" key={theme}><h3>{theme}</h3>
          {rows.map((item) => <article className="review-extraction" key={item.theme_entity_id}>
            <label className="review-select"><input type="checkbox" checked={selected.has(item.theme_entity_id)} onChange={(event) => setSelected((current) => { const next = new Set(current); event.target.checked ? next.add(item.theme_entity_id) : next.delete(item.theme_entity_id); return next; })} /> Page {item.pdf_page_num || '—'} · {item.theme_entity_id}</label>
            <div className="review-fields">
              <label>Theme<select value={item.theme} onChange={(event) => patch(item, { theme: event.target.value })}>{!taxonomy.some((entry) => entry.entity_key === item.theme) && <option value={item.theme}>{item.theme}</option>}{taxonomy.filter((entry) => entry.is_active).map((entry) => <option key={entry.entity_key} value={entry.entity_key}>{entry.display_name}</option>)}</select></label>
              <label>Extracted fact<textarea rows="3" value={item.extracted_fact || ''} onChange={(event) => setItems((current) => current.map((row) => row.theme_entity_id === item.theme_entity_id ? { ...row, extracted_fact: event.target.value } : row))} onBlur={(event) => patch(item, { extracted_fact: event.target.value })} /></label>
              <label>Spatial units (JSON)<input defaultValue={JSON.stringify(item.spatial_units || {})} onBlur={(event) => { try { patch(item, { spatial_units: JSON.parse(event.target.value) }); } catch { setError('Spatial units must be valid JSON.'); } }} /></label>
              <label>Period<input defaultValue={item.temporal_period || ''} onBlur={(event) => patch(item, { temporal_period: event.target.value })} /></label>
            </div>
            <details><summary>Source text</summary><p>{item.source_text_chunk || 'No source excerpt'}</p></details>
            <button className="hcard-btn entity-delete" disabled={busy} onClick={() => remove(item)}>Delete</button>
          </article>)}
        </section>)}
        <button className="clear-btn" onClick={add} disabled={busy}>＋ Add manual extraction</button>
      </div>
      <footer className="extraction-review-footer"><button className="clear-btn" onClick={onClose}>Close</button><button className="process-button" onClick={approve} disabled={busy || !selected.size}>{busy ? 'Saving…' : `Approve ${selected.size} selected`}</button></footer>
    </section>
  </div>;
}
