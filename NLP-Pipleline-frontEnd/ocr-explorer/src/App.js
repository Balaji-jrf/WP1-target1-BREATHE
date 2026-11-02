import React, { useState } from 'react';
import './App.css';
import { AuthProvider, useAuth } from './AuthContext';
import LoginPage from './components/LoginPage';
import ExtractTab from './components/ExtractTab';
import HistoryTab from './components/HistoryTab';
import DatabaseTab from './components/DatabaseTab';
import TaxonomyTab from './components/TaxonomyTab';
import AdminPanel from './components/AdminPanel';
import AtlasTab from './components/AtlasTab';

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
      <div className="llm-frame-bar">
        <button className="llm-back-btn" onClick={onBack} type="button">← Archive Lens</button>
        <span className="llm-frame-label"><span className="llm-frame-dot" />BREATHE · WP1 · Document AI Chat</span>
        <a href={LLM_APP_URL} target="_blank" rel="noreferrer" className="llm-open-btn">Open in new tab ↗</a>
      </div>
      <iframe src={LLM_APP_URL} title="BREATHE Document AI Chat" className="llm-frame" allow="clipboard-write" />
    </div>
  );
}

function AppShell() {
  const { auth, logout, can } = useAuth();
  const [tab, setTab]                   = useState('extract');
  const [historyCount, setHistoryCount] = useState(0);
  const [databaseDocumentId, setDatabaseDocumentId] = useState('');

  if (!auth) return <LoginPage />;

  // If user has no permissions yet (non-admin, freshly signed up)
  const isAdmin = auth.role === 'admin';
  // Redirect to first accessible tab if current tab is not accessible
  const firstAccessible = isAdmin ? 'extract'
    : (auth.permissions || []).find((p) => ['extract','history','database','taxonomy','llm','atlas'].includes(p)) || 'extract';

  const safeTab = (isAdmin || can(tab) || tab === 'admin') ? tab : firstAccessible;
  if (safeTab !== tab) setTab(safeTab);

  return (
    <main className={`app-shell ${tab === 'llm' ? 'app-shell-fullframe' : ''} ${tab === 'database' ? 'app-shell-database' : ''}`}>
      {tab !== 'llm' && (
        <header className="topbar">
          <div className="brand-mark">B</div>
          <div>
            <p className="eyebrow">BREATHE · WP1 · NLP PIPELINE</p>
            <h1>Archive Lens</h1>
          </div>
          <nav className="tab-nav">
            {can('extract')  && <button className={tab === 'extract'  ? 'tab active' : 'tab'} onClick={() => setTab('extract')}>Input Extract</button>}
            {can('history')  && <button className={tab === 'history'  ? 'tab active' : 'tab'} onClick={() => setTab('history')}>OCR History {historyCount > 0 && <span className="badge">{historyCount}</span>}</button>}
            {can('llm')      && <button className={tab === 'llm'      ? 'tab active' : 'tab'} onClick={() => setTab('llm')}>Chat with AI ↗</button>}
            {can('database') && <button className={tab === 'database' ? 'tab active' : 'tab'} onClick={() => setTab('database')}>AI-Extraction &amp; Database</button>}
            {can('taxonomy') && <button className={tab === 'taxonomy' ? 'tab active' : 'tab'} onClick={() => setTab('taxonomy')}>Active entity taxonomy</button>}
            {can('atlas')    && <button className={tab === 'atlas'    ? 'tab active' : 'tab'} onClick={() => setTab('atlas')}>Atlas</button>}
            {isAdmin         && <button className={tab === 'admin'    ? 'tab active' : 'tab'} onClick={() => setTab('admin')}>⚙ Users</button>}
          </nav>
          <div className="topbar-user">
            <span className="user-chip">{auth.username} {isAdmin && <span className="user-role-badge">admin</span>}</span>
            <button className="clear-btn topbar-logout" onClick={logout}>Sign out</button>
          </div>
          {/* <span className="service-status"><span /> Textract ready</span>
          <span className="service-status llm-status"><span className="dot-orange" /> LLM Model — ongoing</span> */}
        </header>
      )}

      {tab === 'extract'  && can('extract')  && <ExtractTab />}
      {tab === 'history'  && can('history')  && <HistoryTab onCountChange={setHistoryCount} onDatabase={(id) => { setDatabaseDocumentId(id || ''); setTab('database'); }} />}
      {tab === 'llm'      && can('llm')      && <LlmTab onBack={() => setTab('extract')} />}
      {tab === 'database' && can('database') && <DatabaseTab initialDocumentId={databaseDocumentId} />}
      {tab === 'taxonomy' && can('taxonomy') && <TaxonomyTab />}
      {tab === 'atlas'    && can('atlas')    && <AtlasTab />}
      {tab === 'admin'    && isAdmin         && <AdminPanel />}
    </main>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  );
}
