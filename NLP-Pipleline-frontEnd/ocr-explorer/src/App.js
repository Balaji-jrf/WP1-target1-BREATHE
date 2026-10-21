import React, { useState } from 'react';
import './App.css';
import ExtractTab from './components/ExtractTab';
import HistoryTab from './components/HistoryTab';

export default function App() {
  const [tab, setTab] = useState('extract');
  const [historyCount, setHistoryCount] = useState(0);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-mark">B</div>
        <div>
          <p className="eyebrow">BREATHE · WP1 · NLP PIPELINE</p>
          <h1>Archive Lens</h1>
        </div>
        <nav className="tab-nav">
          <button className={tab === 'extract' ? 'tab active' : 'tab'} onClick={() => setTab('extract')}>
            Input Extract
          </button>
          <button className={tab === 'llm' ? 'tab active' : 'tab'} onClick={() => setTab('llm')}>
            LLM Model
          </button>
          <button className={tab === 'atlas' ? 'tab active' : 'tab'} onClick={() => setTab('atlas')}>
            Atlas
          </button>
          <button className={tab === 'history' ? 'tab active' : 'tab'} onClick={() => setTab('history')}>
            History {historyCount > 0 && <span className="badge">{historyCount}</span>}
          </button>
        </nav>
        <span className="service-status"><span /> Textract ready</span>
        <span className="service-status llm-status"><span className="dot-orange" /> LLM Model — ongoing</span>
      </header>

      {tab === 'extract' && <ExtractTab />}
      {tab === 'history' && <HistoryTab onCountChange={setHistoryCount} />}
    </main>
  );
}
