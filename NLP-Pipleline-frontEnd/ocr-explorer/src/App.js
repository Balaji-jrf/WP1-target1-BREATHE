import React, { useState } from 'react';
import './App.css';
import ExtractTab from './components/ExtractTab';
import HistoryTab from './components/HistoryTab';
import DatabaseTab from './components/DatabaseTab';
import TaxonomyTab from './components/TaxonomyTab';

const LLM_APP_URL = process.env.REACT_APP_LLM_APP_URL || '';

function LlmTab({ onBack }) {
  if (!LLM_APP_URL) {
    return (
      <div className="llm-missing">
        <p className="section-kicker">Document AI Chat</p>
        <h2>LLM app URL not configured</h2>
        <p>Set <code>REACT_APP_LLM_APP_URL</code> in <code>.env</code> to point to your deployed chat app.</p>
        <button className="process-button" onClick={onBack}>← Back to Archive Lens</button>
      </div>
    );
  }

  return (
    <div className="llm-frame-shell">
      {/* Top bar — back button + context */}
      <div className="llm-frame-bar">
        <button className="llm-back-btn" onClick={onBack} type="button">
          ← Archive Lens
        </button>
        <span className="llm-frame-label">
          <span className="llm-frame-dot" />
          BREATHE · WP1 · Document AI Chat
        </span>
        <a
          href={LLM_APP_URL}
          target="_blank"
          rel="noreferrer"
          className="llm-open-btn"
        >
          Open in new tab ↗
        </a>
      </div>

      {/* Full-height iframe embedding the LLM app */}
      <iframe
        src={LLM_APP_URL}
        title="BREATHE Document AI Chat"
        className="llm-frame"
        allow="clipboard-write"
      />
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState('extract');
  const [historyCount, setHistoryCount] = useState(0);
  const [databaseDocumentId, setDatabaseDocumentId] = useState('');

  return (
    <main className={`app-shell ${tab === 'llm' ? 'app-shell-fullframe' : ''}`}>
      {/* Hide topbar when LLM tab is active — it has its own bar */}
      {tab !== 'llm' && (
        <header className="topbar">
          <div className="brand-mark">B</div>
          <div>
            <p className="eyebrow">BREATHE · WP1 · NLP PIPELINE</p>
            <h1>Archive Lens</h1>
          </div>
          <nav className="tab-nav">
            <button className={tab === 'extract'  ? 'tab active' : 'tab'} onClick={() => setTab('extract')}>
              Input Extract
            </button>
            <button className={tab === 'history'  ? 'tab active' : 'tab'} onClick={() => setTab('history')}>
              OCR History {historyCount > 0 && <span className="badge">{historyCount}</span>}
            </button>
            <button className={tab === 'llm'      ? 'tab active' : 'tab'} onClick={() => setTab('llm')}>
              Chat with AI ↗
            </button>
            <button className={tab === 'database' ? 'tab active' : 'tab'} onClick={() => setTab('database')}>
              AI-Extraction &amp; Database
            </button>
            <button className={tab === 'taxonomy' ? 'tab active' : 'tab'} onClick={() => setTab('taxonomy')}>
              Active entity taxonomy
            </button>
          </nav>
          <span className="service-status"><span /> Textract ready</span>
          <span className="service-status llm-status"><span className="dot-orange" /> LLM Model — ongoing</span>
        </header>
      )}

      {tab === 'extract'  && <ExtractTab />}
      {tab === 'history'  && (
        <HistoryTab
          onCountChange={setHistoryCount}
          onDatabase={(documentId) => { setDatabaseDocumentId(documentId || ''); setTab('database'); }}
        />
      )}
      {tab === 'llm'      && <LlmTab onBack={() => setTab('extract')} />}
      {tab === 'database' && <DatabaseTab initialDocumentId={databaseDocumentId} />}
      {tab === 'taxonomy' && <TaxonomyTab />}
    </main>
  );
}
