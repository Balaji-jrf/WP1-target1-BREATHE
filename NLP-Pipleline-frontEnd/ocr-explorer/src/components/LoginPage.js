import { useState } from 'react';
import { useAuth } from '../AuthContext';

export default function LoginPage() {
  const { login, signup } = useAuth();
  const [mode, setMode]   = useState('login'); // 'login' | 'signup'
  const [form, setForm]   = useState({ username: '', password: '', email: '' });
  const [busy, setBusy]   = useState(false);
  const [error, setError] = useState('');

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      if (mode === 'login') {
        await login(form.username, form.password);
      } else {
        if (!form.email) { setError('Email is required'); setBusy(false); return; }
        await signup(form.username, form.password, form.email);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <div className="auth-brand">
          <div className="brand-mark">B</div>
          <div>
            <p className="eyebrow">BREATHE · WP1 · NLP PIPELINE</p>
            <h1 className="auth-title">Archive Lens</h1>
          </div>
        </div>

        <div className="auth-tabs">
          <button className={`auth-tab ${mode === 'login' ? 'active' : ''}`} onClick={() => { setMode('login'); setError(''); }}>Sign in</button>
          <button className={`auth-tab ${mode === 'signup' ? 'active' : ''}`} onClick={() => { setMode('signup'); setError(''); }}>Create account</button>
        </div>

        <form className="auth-form" onSubmit={submit}>
          <label>
            Username
            <input autoFocus value={form.username} onChange={set('username')} placeholder="your-username" autoComplete="username" required />
          </label>

          {mode === 'signup' && (
            <label>
              Email
              <input type="email" value={form.email} onChange={set('email')} placeholder="you@example.com" autoComplete="email" required />
            </label>
          )}

          <label>
            Password
            <input type="password" value={form.password} onChange={set('password')} placeholder={mode === 'signup' ? 'Min 6 characters' : '••••••••'} autoComplete={mode === 'signup' ? 'new-password' : 'current-password'} required />
          </label>

          {error && <p className="auth-error">{error}</p>}

          <button className="process-button auth-submit" disabled={busy}>
            {busy ? <><span className="spinner" /> {mode === 'login' ? 'Signing in…' : 'Creating account…'}</> : mode === 'login' ? 'Sign in →' : 'Create account →'}
          </button>
        </form>

        {mode === 'signup' && (
          <p className="auth-note">After sign-up, an admin must grant you tab access before you can use the app.</p>
        )}
      </div>
    </div>
  );
}
