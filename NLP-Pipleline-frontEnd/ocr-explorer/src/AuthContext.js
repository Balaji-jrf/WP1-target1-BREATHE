import { createContext, useCallback, useContext, useState } from 'react';

const API_URL = process.env.REACT_APP_API_URL || 'https://o0dqnexpsg.execute-api.ap-southeast-2.amazonaws.com/prod';
const KEY = 'breathe_auth';

function load() {
  try { return JSON.parse(localStorage.getItem(KEY)) || null; } catch { return null; }
}

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [auth, setAuth] = useState(load);

  const persist = (data) => {
    if (data) localStorage.setItem(KEY, JSON.stringify(data));
    else localStorage.removeItem(KEY);
    setAuth(data);
  };

  const login = useCallback(async (username, password) => {
    const res  = await fetch(`${API_URL}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Login failed');
    persist(data);
    return data;
  }, []);

  const signup = useCallback(async (username, password, email) => {
    const res  = await fetch(`${API_URL}/auth/signup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, email }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Signup failed');
    persist(data);
    return data;
  }, []);

  const logout = useCallback(() => persist(null), []);

  const can = useCallback((tab) => {
    if (!auth) return false;
    if (auth.role === 'admin') return true;
    return (auth.permissions || []).includes(tab);
  }, [auth]);

  return (
    <AuthContext.Provider value={{ auth, login, signup, logout, can }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() { return useContext(AuthContext); }
