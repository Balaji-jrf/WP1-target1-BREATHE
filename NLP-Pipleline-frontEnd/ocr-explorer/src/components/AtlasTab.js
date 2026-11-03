import { useEffect, useRef } from 'react';

const ATLAS_APP_URL = process.env.REACT_APP_ATLAS_APP_URL
  || (process.env.NODE_ENV === 'development'
    ? 'http://localhost:5173'
    : 'https://main.d1g1u8rtpgjn48.amplifyapp.com');

export default function AtlasTab({ auth }) {
  const frameRef = useRef(null);
  const atlasOrigin = (() => {
    try {
      const url = new URL(ATLAS_APP_URL);
      return ['https:', 'http:'].includes(url.protocol) ? url.origin : '';
    } catch { return ''; }
  })();

  useEffect(() => {
    if (!atlasOrigin || !auth?.token) return undefined;
    const forwardAtlasAuth = (event) => {
      if (
        event.origin !== atlasOrigin
        || event.source !== frameRef.current?.contentWindow
        || event.data?.type !== 'BREATHE_ATLAS_READY'
      ) return;
      event.source.postMessage({
        type: 'BREATHE_ATLAS_AUTH',
        token: auth.token,
        username: auth.username,
      }, atlasOrigin);
    };
    window.addEventListener('message', forwardAtlasAuth);
    return () => window.removeEventListener('message', forwardAtlasAuth);
  }, [atlasOrigin, auth?.token, auth?.username]);

  if (!ATLAS_APP_URL || !atlasOrigin) {
    return (
      <section className="atlas-page">
        <p className="section-kicker">Independent microfrontend</p>
        <h2>Atlas app is not configured</h2>
        <p className="atlas-intro">Set <code>REACT_APP_ATLAS_APP_URL</code> to the Atlas app URL. For local development, run the Atlas Vite app on port 5173.</p>
      </section>
    );
  }

  return (
    <iframe
      ref={frameRef}
      src={ATLAS_APP_URL}
      title="BREATHE Research Atlas"
      className="atlas-app-frame"
      allow="clipboard-read; clipboard-write"
      referrerPolicy="strict-origin"
    />
  );
}
