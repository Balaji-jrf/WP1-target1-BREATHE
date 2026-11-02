import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../AuthContext';

const API_URL  = process.env.REACT_APP_API_URL || '';
const ALL_TABS = ['extract', 'history', 'database', 'taxonomy', 'llm', 'atlas'];
const TAB_LABELS = { extract: 'Input Extract', history: 'OCR History', database: 'AI-Extraction & Database', taxonomy: 'Entity Taxonomy', llm: 'Chat with AI', atlas: 'Atlas' };

export default function AdminPanel() {
  const { auth } = useAuth();
  const [users, setUsers]   = useState([]);
  const [busy, setBusy]     = useState(false);
  const [error, setError]   = useState('');
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    setBusy(true); setError('');
    try {
      const res  = await fetch(`${API_URL}/auth/admin/users`, { headers: { Authorization: `Bearer ${auth.token}` } });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to load users');
      setUsers(data.users);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }, [auth.token]);

  useEffect(() => { load(); }, [load]);

  const toggle = async (username, currentPerms, tab) => {
    const next = currentPerms.includes(tab)
      ? currentPerms.filter((t) => t !== tab)
      : [...currentPerms, tab];
    setBusy(true); setError(''); setNotice('');
    try {
      const res  = await fetch(`${API_URL}/auth/admin/users/${encodeURIComponent(username)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${auth.token}` },
        body: JSON.stringify({ permissions: next }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Update failed');
      setUsers((prev) => prev.map((u) => u.username === username ? { ...u, permissions: next } : u));
      setNotice(`Permissions updated for ${username}`);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const grantAll = async (username) => {
    setBusy(true); setError(''); setNotice('');
    try {
      const res  = await fetch(`${API_URL}/auth/admin/users/${encodeURIComponent(username)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${auth.token}` },
        body: JSON.stringify({ permissions: ALL_TABS }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Update failed');
      setUsers((prev) => prev.map((u) => u.username === username ? { ...u, permissions: ALL_TABS } : u));
      setNotice(`Full access granted to ${username}`);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const nonAdmins = users.filter((u) => u.role !== 'admin');

  return (
    <section className="database-page">
      <div className="database-heading">
        <div>
          <p className="section-kicker">Admin · User Management</p>
          <h2>User Access Control</h2>
          <p>Grant or revoke tab access for each user.</p>
        </div>
        <button className="clear-btn" onClick={load} disabled={busy}>↻ Refresh</button>
      </div>

      {error  && <div className="message error-message">{error}</div>}
      {notice && <div className="database-notice">{notice}</div>}

      {nonAdmins.length === 0 && !busy && (
        <div className="database-empty">No non-admin users yet. Share the signup link with your team.</div>
      )}

      <div className="admin-user-list">
        {nonAdmins.map((user) => (
          <div key={user.username} className="admin-user-card">
            <div className="admin-user-header">
              <div>
                <strong>{user.username}</strong>
                <span>{user.email}</span>
                <code>Joined {user.created_at ? new Date(user.created_at).toLocaleDateString('en-IN') : '—'}</code>
              </div>
              <button className="clear-btn" onClick={() => grantAll(user.username)} disabled={busy}>Grant all</button>
            </div>
            <div className="admin-perm-grid">
              {ALL_TABS.map((tab) => {
                const on = (user.permissions || []).includes(tab);
                return (
                  <button
                    key={tab}
                    className={`admin-perm-btn ${on ? 'on' : 'off'}`}
                    onClick={() => toggle(user.username, user.permissions || [], tab)}
                    disabled={busy}
                  >
                    {on ? '✓' : '✗'} {TAB_LABELS[tab]}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
