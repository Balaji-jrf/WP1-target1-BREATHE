import { useEffect, useMemo, useState } from 'react';

const TABS = [
  { id: 'text', label: 'Text', icon: 'T' },
  { id: 'layout', label: 'Layout', icon: '▤' },
  { id: 'tables', label: 'Tables', icon: '▦' },
  { id: 'figures', label: 'Figures', icon: '▧' },
];
const EMPTY_ARRAY = [];

const formatConfidence = (value) =>
  `${Math.round((Number.parseFloat(value) || 0) * 100)}%`;

const formatPage = (page) => `Page ${String(page || 1).padStart(2, '0')}`;
const formatBbox = (bbox = []) =>
  bbox.map((value) => Number(value).toFixed(3)).join(', ');

export default function OcrResults({ result, pageFilter, onPageFilterChange }) {
  const [activeTab, setActiveTab] = useState('text');
  const [internalPage, setInternalPage] = useState('all');
  const [search, setSearch] = useState('');
  const [resultPage, setResultPage] = useState(0);
  const selectedPage = pageFilter ?? internalPage;
  const textElements = Array.isArray(result.elements) ? result.elements : EMPTY_ARRAY;
  const layoutBlocks = Array.isArray(result.layout) ? result.layout : EMPTY_ARRAY;
  const tables = Array.isArray(result.tables) ? result.tables : EMPTY_ARRAY;
  const figures = Array.isArray(result.figures) ? result.figures : EMPTY_ARRAY;
  const pages = useMemo(() => Array.from(new Set([
    ...textElements.map((item) => item.page || 1),
    ...layoutBlocks.map((item) => item.page || 1),
    ...tables.map((item) => item.page || 1),
    ...figures.map((item) => item.page || 1),
  ])).sort((a, b) => a - b), [textElements, layoutBlocks, tables, figures]);

  const visibleItems = useMemo(() => {
    const source = {
      text: textElements,
      layout: layoutBlocks,
      tables,
      figures,
    }[activeTab];
    const query = search.trim().toLocaleLowerCase();
    return source.filter((item) => {
      const pageMatches = selectedPage === 'all' || String(item.page || 1) === selectedPage;
      if (!pageMatches) return false;
      if (!query) return true;
      const searchable = activeTab === 'tables'
        ? JSON.stringify(item.rows || [])
        : `${item.text || ''} ${item.type || ''}`;
      return searchable.toLocaleLowerCase().includes(query);
    });
  }, [activeTab, figures, layoutBlocks, search, selectedPage, tables, textElements]);
  const pageSize = 50;
  const pageCount = Math.ceil(visibleItems.length / pageSize);
  const displayItems = visibleItems.slice(resultPage * pageSize, (resultPage + 1) * pageSize);

  useEffect(() => {
    setResultPage(0);
    setSearch('');
  }, [pageFilter]);

  const changeTab = (tab) => {
    setActiveTab(tab);
    setResultPage(0);
    setSearch('');
  };
  const changePage = (page) => {
    if (onPageFilterChange) onPageFilterChange(page);
    else setInternalPage(page);
    setResultPage(0);
  };

  return (
    <section className="ocr-results" aria-label="OCR analysis results">
      <div className="ocr-results-header">
        <div>
          <p className="section-kicker">Structured extraction</p>
          <h4>Explore what Textract found</h4>
        </div>
        <label className="ocr-page-select">
          <span>Page</span>
          <select value={selectedPage} onChange={(event) => {
            changePage(event.target.value);
          }}>
            {!onPageFilterChange && <option value="all">All pages</option>}
            {pages.map((page) => (
              <option key={page} value={String(page)}>{formatPage(page)}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="ocr-summary-grid">
        <div className="ocr-summary-card"><strong>{textElements.length.toLocaleString()}</strong><span>Text blocks</span></div>
        <div className="ocr-summary-card"><strong>{layoutBlocks.length.toLocaleString()}</strong><span>Layout regions</span></div>
        <div className="ocr-summary-card"><strong>{tables.length.toLocaleString()}</strong><span>Tables</span></div>
        <div className="ocr-summary-card"><strong>{figures.length.toLocaleString()}</strong><span>Figures</span></div>
      </div>

      <div className="ocr-toolbar">
        <div className="ocr-tabs" role="tablist" aria-label="OCR result types">
          {TABS.map((tab) => {
            const count = {
              text: textElements.length,
              layout: layoutBlocks.length,
              tables: tables.length,
              figures: figures.length,
            }[tab.id];
            return (
              <button
                type="button"
                key={tab.id}
                role="tab"
                aria-selected={activeTab === tab.id}
                className={`ocr-tab ${activeTab === tab.id ? 'is-active' : ''}`}
                onClick={() => changeTab(tab.id)}
              >
                <span className="ocr-tab-icon" aria-hidden="true">{tab.icon}</span>
                {tab.label}
              </button>
            );
          })}
        </div>
        {(activeTab === 'text' || activeTab === 'layout' || activeTab === 'tables') && (
          <label className="ocr-search">
            <span aria-hidden="true">⌕</span>
            <input
              type="search"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setResultPage(0);
              }}
              placeholder={`Search ${activeTab}…`}
              aria-label={`Search ${activeTab}`}
            />
          </label>
        )}
      </div>

      <div className={`ocr-content ocr-content-${activeTab}`} role="tabpanel">
        {visibleItems.length === 0 ? (
          <div className="ocr-empty">
            <span className="ocr-empty-mark" aria-hidden="true">{TABS.find((tab) => tab.id === activeTab)?.icon}</span>
            <strong>{search ? 'No matching results' : `No ${activeTab} detected`}</strong>
            <p>{search ? 'Try another search term or page.' : 'Textract did not return any items in this category.'}</p>
          </div>
        ) : activeTab === 'text' ? (
          <div className="ocr-text-grid">
            {displayItems.map((item, index) => {
              const confidence = Math.min(100, Math.max(0, Number(item.confidence || 0) * 100));
              return (
                <article className="ocr-text-card" key={`${item.page}-${index}`}>
                  <div className="ocr-card-topline">
                    <span className="ocr-page-tag">{formatPage(item.page)}</span>
                    <span className={`ocr-confidence ${confidence < 70 ? 'is-low' : ''}`}>
                      {formatConfidence(item.confidence)} <span>confidence</span>
                    </span>
                  </div>
                  <p className="ocr-text-content">{item.text}</p>
                  <div
                    className="ocr-confidence-track"
                    role="img"
                    aria-label={`${formatConfidence(item.confidence)} confidence`}
                  >
                    <span style={{ width: `${confidence}%` }} />
                  </div>
                  <small className="ocr-bbox">Position <code>[{formatBbox(item.bbox)}]</code></small>
                </article>
              );
            })}
          </div>
        ) : activeTab === 'layout' ? (
          <div className="ocr-layout-grid">
            {displayItems.map((item, index) => (
              <article className="ocr-layout-card" key={`${item.page}-${item.type}-${index}`}>
                <div className="ocr-layout-symbol" aria-hidden="true">▤</div>
                <div className="ocr-layout-body">
                  <div className="ocr-card-topline">
                    <span className="ocr-type-tag">{(item.type || 'region').replaceAll('_', ' ')}</span>
                    <span className="ocr-page-tag">{formatPage(item.page)}</span>
                  </div>
                  <p>{item.text || 'No associated text'}</p>
                  <small className="ocr-bbox">Position <code>[{formatBbox(item.bbox)}]</code></small>
                </div>
              </article>
            ))}
          </div>
        ) : activeTab === 'tables' ? (
          <div className="ocr-tables-grid">
            {displayItems.map((table, index) => (
              <article className="ocr-table-card" key={`${table.page}-${index}`}>
                <div className="ocr-table-heading">
                  <span className="ocr-type-tag">Detected table {index + 1}</span>
                  <span className="ocr-page-tag">{formatPage(table.page)}</span>
                </div>
                <div className="ocr-table-scroll">
                  <table>
                    <tbody>
                      {(table.rows || []).map((row, rowIndex) => (
                        <tr key={`row-${rowIndex}`}>
                          {row.map((cell, cellIndex) => {
                            const Cell = rowIndex === 0 ? 'th' : 'td';
                            return <Cell key={`cell-${rowIndex}-${cellIndex}`}>{cell || '—'}</Cell>;
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <small className="ocr-bbox">Position <code>[{formatBbox(table.bbox)}]</code></small>
              </article>
            ))}
          </div>
        ) : (
          <>
            {result.figure_presigned_url_error && (
              <p className="ocr-figure-error" role="alert">{result.figure_presigned_url_error}</p>
            )}
            <div className="ocr-figure-grid">
              {displayItems.map((figure, index) => (
                <article className="ocr-figure-card" key={`${figure.page}-${index}`}>
                  {figure.image_url
                    ? <img className="ocr-figure-image" src={figure.image_url} alt={`Detected figure on ${formatPage(figure.page)}`} />
                    : <div className="ocr-figure-preview" aria-hidden="true"><span>▧</span></div>
                  }
                  <div className="ocr-figure-info">
                    <span className="ocr-type-tag">Figure region {index + 1}</span>
                    <span className="ocr-page-tag">{formatPage(figure.page)}</span>
                  </div>
                  <p>{figure.crop_status === 'failed'
                    ? 'The figure was detected, but its image could not be saved.'
                    : figure.image_url
                      ? 'Saved crop from the source page.'
                      : figure.crop_status === 'saved'
                        ? 'The crop is saved, but its preview is unavailable.'
                        : 'Figure location detected on the source page.'}</p>
                  <small className="ocr-bbox">Position <code>[{formatBbox(figure.bbox)}]</code></small>
                </article>
              ))}
            </div>
          </>
        )}
      </div>

      {visibleItems.length > 0 && (
        <div className="ocr-results-footer">
          <span>
            Showing <strong>{(resultPage * pageSize + 1).toLocaleString()}–{Math.min((resultPage + 1) * pageSize, visibleItems.length).toLocaleString()}</strong> of{' '}
            <strong>{visibleItems.length.toLocaleString()}</strong> matching /{' '}
            <strong>{({
            text: textElements.length,
            layout: layoutBlocks.length,
            tables: tables.length,
            figures: figures.length,
            })[activeTab].toLocaleString()}</strong> {activeTab} results
          </span>
          {pageCount > 1 && (
            <div className="ocr-pagination">
              <button
                type="button"
                disabled={resultPage === 0}
                onClick={() => setResultPage((current) => current - 1)}
              >
                ← Previous
              </button>
              <span>{resultPage + 1} / {pageCount}</span>
              <button
                type="button"
                disabled={resultPage >= pageCount - 1}
                onClick={() => setResultPage((current) => current + 1)}
              >
                Next →
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
