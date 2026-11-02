import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchTaxonomy, saveUserRecord } from '../api';

const TABS = [
  { id: 'text',    label: 'Text',    icon: 'T' },
  { id: 'layout',  label: 'Layout',  icon: '▤' },
  { id: 'tables',  label: 'Tables',  icon: '▦' },
  { id: 'figures', label: 'Figures', icon: '▧' },
];
const EMPTY = [];
const fmtConf  = (v) => `${Math.round((parseFloat(v) || 0) * 100)}%`;
const fmtPage  = (p) => `Page ${String(p || 1).padStart(2, '0')}`;
const fmtBbox  = (b = []) => b.map((v) => Number(v).toFixed(3)).join(', ');

const PRESET_TAGS = ['Historical Document', 'Policy Document', 'Grey Literature', 'Speech / Transcript', 'Archival Record'];

/* ── Right-click save form ── */
function SaveForm({ item, type, documentId, taxonomy, onSaved, onClose }) {
  const [name, setName]       = useState('');
  const [tag, setTag]         = useState(PRESET_TAGS[0]);
  const [customTag, setCustomTag] = useState('');
  const [tagMode, setTagMode] = useState('preset');
  const [theme, setTheme]     = useState(taxonomy.find((t) => t.is_active)?.entity_key || '');
  const [desc, setDesc]       = useState('');
  const [busy, setBusy]       = useState(false);
  const [err, setErr]         = useState('');
  const ref = useRef(null);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);

  const submit = async (e) => {
    e.preventDefault();
    if (!documentId) { setErr('No document selected.'); return; }
    setBusy(true); setErr('');
    const finalTag = tagMode === 'custom' ? customTag.trim() : tag;
    const entityId = `USER_SAVED#${Date.now()}`;
    const fact = type === 'tables'
      ? (item.rows || []).flat().filter(Boolean).slice(0, 10).join(' | ')
      : type === 'figures'
        ? (item.image_url || item.image_s3_key || `Figure page ${item.page}`)
        : (item.text || '');
    try {
      await saveUserRecord(documentId, {
        theme_entity_id: `${theme || 'USER_SAVED'}#${entityId}`,
        theme: theme || 'USER_SAVED',
        entity_type: 'user_saved',
        content_type: type === 'tables' ? 'table' : type === 'figures' ? 'figure_crop' : 'narrative_text',
        extracted_fact: desc || fact,
        source_text_chunk: fact,
        pdf_page_num: item.page || 1,
        original_page_num: item.page || 1,
        bbox: item.bbox || null,
        spatial_units: {},
        temporal_period: '',
        review_status: 'approved',
        custom_attributes: { user_name: name, user_tag: finalTag, original_type: type },
        ...(type === 'figures' && item.image_url ? { s3_crop_url: item.image_url } : {}),
      });
      onSaved();
      onClose();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="ocr-save-overlay" onClick={onClose}>
      <form className="ocr-save-form" ref={ref} onSubmit={submit} onClick={(e) => e.stopPropagation()}>
        <div className="ocr-save-header">
          <span>Save to Database</span>
          <button type="button" className="ocr-save-close" onClick={onClose}>✕</button>
        </div>
        {err && <p className="ocr-save-err">{err}</p>}

        <label>Name / Label
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Bidar district rainfall data" required />
        </label>

        <label>Tag
          <select value={tagMode === 'custom' ? '__custom__' : tag} onChange={(e) => {
            if (e.target.value === '__custom__') setTagMode('custom');
            else { setTagMode('preset'); setTag(e.target.value); }
          }}>
            {PRESET_TAGS.map((t) => <option key={t} value={t}>{t}</option>)}
            <option value="__custom__">Other (custom)…</option>
          </select>
          {tagMode === 'custom' && <input value={customTag} onChange={(e) => setCustomTag(e.target.value)} placeholder="Custom tag" />}
        </label>

        {taxonomy.length > 0 && (
          <label>Theme
            <select value={theme} onChange={(e) => setTheme(e.target.value)}>
              {taxonomy.filter((t) => t.is_active).map((t) => <option key={t.entity_key} value={t.entity_key}>{t.display_name}</option>)}
            </select>
          </label>
        )}

        <label>Description (optional)
          <textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={2} placeholder="Add a note about this item…" />
        </label>

        {/* Preview */}
        <div className="ocr-save-preview">
          <span>Saving: {type} · Page {item.page || 1}</span>
          {type === 'figures' && item.image_url && <img src={item.image_url} alt="figure" className="ocr-save-thumb" />}
        </div>

        <div className="ocr-save-actions">
          <button type="button" className="db-btn" onClick={onClose}>Cancel</button>
          <button className="db-btn db-btn-primary" disabled={busy}>{busy ? 'Saving…' : '💾 Save to Database'}</button>
        </div>
      </form>
    </div>
  );
}

/* ── Context menu ── */
function ContextMenu({ x, y, onSave, onView, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [onClose]);
  return (
    <ul className="ocr-ctx-menu" ref={ref} style={{ top: y, left: x }}>
      {onView && <li onClick={() => { onView(); onClose(); }}>👁 View in document</li>}
      <li onClick={() => { onSave(); onClose(); }}>💾 Save to database</li>
    </ul>
  );
}

export default function OcrResults({ result, pageFilter, onPageFilterChange, onElementClick, activeElement }) {
  const [activeTab,    setActiveTab]    = useState('text');
  const [internalPage, setInternalPage] = useState('all');
  const [search,       setSearch]       = useState('');
  const [resultPage,   setResultPage]   = useState(0);
  const [ctxMenu,      setCtxMenu]      = useState(null);  // { x, y, item, type }
  const [saveForm,     setSaveForm]     = useState(null);  // { item, type }
  const [taxonomy,     setTaxonomy]     = useState([]);
  const [saveNotice,   setSaveNotice]   = useState('');

  useEffect(() => { fetchTaxonomy().then(setTaxonomy).catch(() => {}); }, []);

  const selectedPage = pageFilter ?? internalPage;
  const textElements = Array.isArray(result.elements) ? result.elements : EMPTY;
  const layoutBlocks = Array.isArray(result.layout)   ? result.layout   : EMPTY;
  const tables       = Array.isArray(result.tables)   ? result.tables   : EMPTY;
  const figures      = Array.isArray(result.figures)  ? result.figures  : EMPTY;

  const pages = useMemo(() => Array.from(new Set([
    ...textElements.map((i) => i.page || 1),
    ...layoutBlocks.map((i) => i.page || 1),
    ...tables.map((i)       => i.page || 1),
    ...figures.map((i)      => i.page || 1),
  ])).sort((a, b) => a - b), [textElements, layoutBlocks, tables, figures]);

  const visibleItems = useMemo(() => {
    const src = { text: textElements, layout: layoutBlocks, tables, figures }[activeTab];
    const q   = search.trim().toLowerCase();
    return src.filter((item) => {
      if (selectedPage !== 'all' && String(item.page || 1) !== selectedPage) return false;
      if (!q) return true;
      const s = activeTab === 'tables' ? JSON.stringify(item.rows || []) : `${item.text || ''} ${item.type || ''}`;
      return s.toLowerCase().includes(q);
    });
  }, [activeTab, figures, layoutBlocks, search, selectedPage, tables, textElements]);

  const pageSize    = 50;
  const pageCount   = Math.ceil(visibleItems.length / pageSize);
  const displayItems = visibleItems.slice(resultPage * pageSize, (resultPage + 1) * pageSize);

  useEffect(() => { setResultPage(0); setSearch(''); }, [pageFilter]);

  const changeTab  = (t) => { setActiveTab(t); setResultPage(0); setSearch(''); };
  const changePage = (p) => { if (onPageFilterChange) onPageFilterChange(p); else setInternalPage(p); setResultPage(0); };

  const handleClick = (item, type) => {
    if (!onElementClick) return;
    onElementClick({
      document_id: result.document_id,
      filename:    result.filename,
      docUrl:      result.s3_presigned_url || null,
      type, page: item.page || 1, bbox: item.bbox || [],
      text: type === 'tables' ? (item.rows || []).flat().filter(Boolean).slice(0, 6).join(' · ') : (item.text || ''),
    });
  };

  const handleRightClick = (e, item, type) => {
    e.preventDefault();
    setCtxMenu({ x: e.clientX, y: e.clientY, item, type });
  };

  const isActive = (item, type) =>
    activeElement && activeElement.type === type &&
    activeElement.page === (item.page || 1) &&
    JSON.stringify(activeElement.bbox) === JSON.stringify(item.bbox || []);

  const cc = onElementClick ? ' ocr-card-clickable' : '';

  const cardProps = (item, type) => ({
    onContextMenu: (e) => handleRightClick(e, item, type),
    onClick:       () => handleClick(item, type),
    title:         'Click to view · Right-click to save to database',
    role:          'button',
    tabIndex:      0,
    onKeyDown:     (e) => { if (e.key === 'Enter' || e.key === ' ') handleClick(item, type); },
  });

  return (
    <section className="ocr-results" aria-label="OCR analysis results">
      {saveNotice && (
        <div className="ocr-save-notice" onClick={() => setSaveNotice('')}>{saveNotice} ✕</div>
      )}

      <div className="ocr-results-header">
        <div>
          <p className="section-kicker">Structured extraction</p>
          <h4>Explore what Textract found</h4>
        </div>
        <label className="ocr-page-select">
          <span>Page</span>
          <select value={selectedPage} onChange={(e) => changePage(e.target.value)}>
            {!onPageFilterChange && <option value="all">All pages</option>}
            {pages.map((p) => <option key={p} value={String(p)}>{fmtPage(p)}</option>)}
          </select>
        </label>
      </div>

      <div className="ocr-search-bar-wrap">
        <label className="ocr-search-bar">
          <span className="ocr-search-bar-icon">⌕</span>
          <input type="search" value={search}
            onChange={(e) => { setSearch(e.target.value); setResultPage(0); }}
            placeholder={`Search ${activeTab}…`} />
          {search && <button className="ocr-search-bar-clear" onClick={() => { setSearch(''); setResultPage(0); }} type="button">✕</button>}
        </label>
      </div>

      <div className="ocr-summary-grid">
        <div className="ocr-summary-card"><strong>{textElements.length.toLocaleString()}</strong><span>Text blocks</span></div>
        <div className="ocr-summary-card"><strong>{layoutBlocks.length.toLocaleString()}</strong><span>Layout regions</span></div>
        <div className="ocr-summary-card"><strong>{tables.length.toLocaleString()}</strong><span>Tables</span></div>
        <div className="ocr-summary-card"><strong>{figures.length.toLocaleString()}</strong><span>Figures</span></div>
      </div>

      <div className="ocr-toolbar">
        <div className="ocr-tabs" role="tablist">
          {TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={activeTab === t.id}
              className={`ocr-tab ${activeTab === t.id ? 'is-active' : ''}`}
              onClick={() => changeTab(t.id)}>
              <span className="ocr-tab-icon">{t.icon}</span>{t.label}
              <span className="ocr-tab-count">
                {{ text: textElements, layout: layoutBlocks, tables, figures }[t.id].length}
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className={`ocr-content ocr-content-${activeTab}`} role="tabpanel">
        {visibleItems.length === 0 ? (
          <div className="ocr-empty">
            <span className="ocr-empty-mark">{TABS.find((t) => t.id === activeTab)?.icon}</span>
            <strong>{search ? 'No matching results' : `No ${activeTab} detected`}</strong>
            <p>{search ? 'Try another search term.' : 'Textract found nothing in this category.'}</p>
          </div>

        ) : activeTab === 'text' ? (
          <div className="ocr-text-grid">
            {displayItems.map((item, i) => {
              const conf = Math.min(100, Math.max(0, Number(item.confidence || 0) * 100));
              return (
                <article key={`${item.page}-${i}`}
                  className={`ocr-text-card${cc}${isActive(item, 'text') ? ' ocr-card-active' : ''}`}
                  {...cardProps(item, 'text')}>
                  <div className="ocr-card-topline">
                    <span className="ocr-page-tag">{fmtPage(item.page)}</span>
                    <span className={`ocr-confidence ${conf < 70 ? 'is-low' : ''}`}>{fmtConf(item.confidence)} <span>conf</span></span>
                  </div>
                  <p className="ocr-text-content">{item.text}</p>
                  <div className="ocr-confidence-track"><span style={{ width: `${conf}%` }} /></div>
                  <small className="ocr-bbox">bbox <code>[{fmtBbox(item.bbox)}]</code></small>
                </article>
              );
            })}
          </div>

        ) : activeTab === 'layout' ? (
          <div className="ocr-layout-grid">
            {displayItems.map((item, i) => (
              <article key={`${item.page}-${i}`}
                className={`ocr-layout-card${cc}${isActive(item, 'layout') ? ' ocr-card-active' : ''}`}
                {...cardProps(item, 'layout')}>
                <div className="ocr-layout-symbol">▤</div>
                <div className="ocr-layout-body">
                  <div className="ocr-card-topline">
                    <span className="ocr-type-tag">{(item.type || 'region').replaceAll('_', ' ')}</span>
                    <span className="ocr-page-tag">{fmtPage(item.page)}</span>
                  </div>
                  <p>{item.text || 'No associated text'}</p>
                  <small className="ocr-bbox">bbox <code>[{fmtBbox(item.bbox)}]</code></small>
                </div>
              </article>
            ))}
          </div>

        ) : activeTab === 'tables' ? (
          <div className="ocr-tables-grid">
            {displayItems.map((table, i) => (
              <article key={`${table.page}-${i}`}
                className={`ocr-table-card${cc}${isActive(table, 'tables') ? ' ocr-card-active' : ''}`}
                {...cardProps(table, 'tables')}>
                <div className="ocr-table-heading">
                  <span className="ocr-type-tag">Table {i + 1}</span>
                  <span className="ocr-page-tag">{fmtPage(table.page)}</span>
                </div>
                <div className="ocr-table-scroll">
                  <table><tbody>
                    {(table.rows || []).map((row, ri) => (
                      <tr key={ri}>{row.map((cell, ci) => {
                        const C = ri === 0 ? 'th' : 'td';
                        return <C key={ci}>{cell || '—'}</C>;
                      })}</tr>
                    ))}
                  </tbody></table>
                </div>
                <small className="ocr-bbox">bbox <code>[{fmtBbox(table.bbox)}]</code></small>
              </article>
            ))}
          </div>

        ) : (
          <>
            {result.figure_presigned_url_error && (
              <p className="ocr-figure-error">{result.figure_presigned_url_error}</p>
            )}
            <div className="ocr-figure-grid">
              {displayItems.map((fig, i) => (
                <article key={`${fig.page}-${i}`}
                  className={`ocr-figure-card${cc}${isActive(fig, 'figures') ? ' ocr-card-active' : ''}`}
                  {...cardProps(fig, 'figures')}>
                  {fig.image_url
                    ? <img className="ocr-figure-image" src={fig.image_url} alt={`Figure page ${fig.page}`} loading="lazy" />
                    : <div className="ocr-figure-preview"><span>▧</span></div>}
                  <div className="ocr-figure-info">
                    <span className="ocr-type-tag">Figure {i + 1}</span>
                    <span className="ocr-page-tag">{fmtPage(fig.page)}</span>
                  </div>
                  <p>{fig.image_url ? 'Saved crop from source page.' : fig.crop_status === 'saved' ? 'Crop saved, preview unavailable.' : 'Figure location detected.'}</p>
                  <small className="ocr-bbox">bbox <code>[{fmtBbox(fig.bbox)}]</code></small>
                </article>
              ))}
            </div>
          </>
        )}
      </div>

      {visibleItems.length > 0 && (
        <div className="ocr-results-footer">
          <span>Showing <strong>{resultPage * pageSize + 1}–{Math.min((resultPage + 1) * pageSize, visibleItems.length)}</strong> of <strong>{visibleItems.length}</strong></span>
          {pageCount > 1 && (
            <div className="ocr-pagination">
              <button disabled={resultPage === 0} onClick={() => setResultPage((c) => c - 1)}>← Prev</button>
              <span>{resultPage + 1} / {pageCount}</span>
              <button disabled={resultPage >= pageCount - 1} onClick={() => setResultPage((c) => c + 1)}>Next →</button>
            </div>
          )}
        </div>
      )}

      {/* Context menu */}
      {ctxMenu && (
        <ContextMenu
          x={ctxMenu.x} y={ctxMenu.y}
          onSave={() => setSaveForm({ item: ctxMenu.item, type: ctxMenu.type })}
          onView={onElementClick ? () => handleClick(ctxMenu.item, ctxMenu.type) : null}
          onClose={() => setCtxMenu(null)}
        />
      )}

      {/* Save form */}
      {saveForm && (
        <SaveForm
          item={saveForm.item}
          type={saveForm.type}
          documentId={result.document_id}
          taxonomy={taxonomy}
          onSaved={() => setSaveNotice('✓ Saved to database successfully!')}
          onClose={() => setSaveForm(null)}
        />
      )}
    </section>
  );
}
