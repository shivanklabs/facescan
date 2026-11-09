// SignInForm: email magic link, or a passkey. `next` is where the link returns the user.
import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '@/lib/apiClient';
import { signInWithPasskey } from '@/lib/passkeys';

interface Props {
  next: string;
  onSignedIn: (next: string) => void;
}

export function SignInForm({ next, onSignedIn }: Props) {
  const { t } = useTranslation();
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function sendLink(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api('/auth/magic-link', { json: { email, next } });
      setSent(true);
    } catch {
      setError(t('common.error'));
    } finally {
      setBusy(false);
    }
  }

  async function signInPasskey() {
    setError('');
    try {
      await signInWithPasskey();
      onSignedIn(next);
    } catch {
      setError(t('common.error'));
    }
  }

  return (
    <section className="card">
      <h2>{t('signIn.title')}</h2>
      {sent ? (
        <>
          <p role="status">{t('signIn.linkSent')}</p>
          {import.meta.env.DEV && <p className="hint">{t('signIn.devHint')}</p>}
        </>
      ) : (
        <form onSubmit={(e) => void sendLink(e)}>
          <label htmlFor="email">{t('signIn.email')}</label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <button type="submit" disabled={busy}>
            {t('signIn.sendLink')}
          </button>
        </form>
      )}
      <p className="hint">{t('signIn.orPasskey')}</p>
      <button type="button" className="secondary" onClick={() => void signInPasskey()}>
        {t('signIn.passkey')}
      </button>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
