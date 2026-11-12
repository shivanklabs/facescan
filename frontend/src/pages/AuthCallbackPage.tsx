// AuthCallbackPage: the magic link lands here with the token in the URL fragment (never sent
// to servers or logs). The token is removed from the address bar at once, then verified.
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { api } from '@/lib/serverApi';

export default function AuthCallbackPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [failed, setFailed] = useState(false);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return; // StrictMode runs effects twice; a link works only once
    started.current = true;
    const token = new URLSearchParams(window.location.hash.slice(1)).get('token') ?? '';
    window.history.replaceState(null, '', window.location.pathname);
    api<{ next: string }>('/auth/magic-link/verify', { json: { token } })
      .then((result) => navigate(result.next, { replace: true }))
      .catch(() => setFailed(true));
  }, [navigate]);

  return (
    <>
      <h1>{t('signIn.title')}</h1>
      <p role="status">{failed ? t('signIn.linkInvalid') : t('signIn.checking')}</p>
    </>
  );
}
