import React, { lazy, Suspense, useEffect, useState } from 'react';
import './App.css';
import ExtractTab from './components/ExtractTab';
import HistoryTab from './components/HistoryTab';

const AtlasPage = lazy(() => import('./atlas/AtlasPage'));

function readCurrentTab() {
  const params = new URLSearchParams(window.location.search);
  const view = params.get('view');
  if (view === 'atlas' || view === 'extract' || view === 'history') return view;
  return params.has('layer') ? 'atlas' : 'extract';
}

export default function App() {
  const [tab, setTab] = useState(readCurrentTab);
  const [historyCount, setHistoryCount] = useState(0);

  useEffect(() => {
    const handlePopState = () => setTab(readCurrentTab());
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const navigateTab = (nextTab) => {
    setTab(nextTab);
    const params = new URLSearchParams(window.location.search);
    params.set('view', nextTab);
    window.history.pushState(null, '', `${window.location.pathname}?${params.toString()}${window.location.hash}`);
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-mark">B</div>
        <div>
          <p className="eyebrow">BREATHE · WP1 · SPATIAL ANALYTICS</p>
          <h1>Archive Lens</h1>
        </div>
        <nav className="tab-nav">
          <button className={tab === 'extract' ? 'tab active' : 'tab'} onClick={() => navigateTab('extract')}>
            Extract
          </button>
          <button className={tab === 'nlp' ? 'tab active' : 'tab'} onClick={() => navigateTab('nlp')}>
            NLP Pipeline
          </button>
          <button className={tab === 'atlas' ? 'tab active' : 'tab'} onClick={() => navigateTab('atlas')}>
            Atlas
          </button>
          <button className={tab === 'history' ? 'tab active' : 'tab'} onClick={() => navigateTab('history')}>
            History {historyCount > 0 && <span className="badge">{historyCount}</span>}
          </button>
        </nav>
        <span className="service-status"><span /> Textract ready</span>
        <span className="service-status nlp-status"><span className="dot-orange" /> NLP Pipeline — ongoing</span>
      </header>

      {tab === 'atlas' && (
        <Suspense fallback={<p className="atlas-loading" role="status">Loading Atlas boundaries…</p>}>
          <AtlasPage />
        </Suspense>
      )}
      {tab === 'extract' && <ExtractTab />}
      {tab === 'history' && <HistoryTab onCountChange={setHistoryCount} />}
    </main>
  );
}
