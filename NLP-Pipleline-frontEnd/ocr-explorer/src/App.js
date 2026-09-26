import { useState } from 'react';
import './App.css';
import ExtractTab from './components/ExtractTab';
import HistoryTab from './components/HistoryTab';

export default function App() {
  const [tab, setTab] = useState('extract');

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-mark">B</div>
        <div>
          <p className="eyebrow">BREATHE · WP1 · NLP PIPELINE</p>
          <h1>Archive Lens</h1>
        </div>
        <nav className="tab-nav">
          <button
            className={tab === 'extract' ? 'tab active' : 'tab'}
            onClick={() => setTab('extract')}
          >
            Extract
          </button>
          <button
            className={tab === 'history' ? 'tab active' : 'tab'}
            onClick={() => setTab('history')}
          >
            History
          </button>
        </nav>
        <span className="service-status"><span /> Textract ready</span>
        <span className="service-status nlp-status"><span className="dot-orange" /> NLP Pipeline — ongoing</span>
      </header>

      {tab === 'extract' && <ExtractTab />}
      {tab === 'history' && <HistoryTab />}
    </main>
  );
}
