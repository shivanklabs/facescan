// MagicLinkForm: staff sign-in in the site design: an email link (staff addresses only; the
// server never says whether an address is known) or a passkey. `next` is where the email
// link returns the person.
import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '@/lib/serverApi';
import { signInWithPasskey } from '@/lib/passkeys';

interface Props {
  next: string;
  onSignedIn: (next: string) => void;
}

/**
 * Purpose: staff sign-in form (email link or passkey).
 * Inputs: next path, onSignedIn callback (passkey sign-in finished).
 * Outputs: form element. Side effects: POST /auth/magic-link; WebAuthn passkey sign-in.
 */
export function MagicLinkForm({ next, onSignedIn }: Props) {
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
    <div className="st-card st-claim st-staff">
      <p className="st-badge">{t('staff.title')}</p>
      {sent ? (
        <>
          <p role="status">{t('signIn.linkSent')}</p>
          {import.meta.env.DEV && <p className="st-fine">{t('signIn.devHint')}</p>}
        </>
      ) : (
        <form className="st-form-row" onSubmit={(e) => void sendLink(e)}>
          <label className="st-visually-hidden" htmlFor="staff-email">
            {t('signIn.email')}
          </label>
          <input
            id="staff-email"
            className="st-input"
            type="email"
            autoComplete="email"
            required
            placeholder={t('signIn.email')}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <button type="submit" className="st-btn st-btn-primary" disabled={busy}>
            {t('signIn.sendLink')}
          </button>
        </form>
      )}
      <button
        type="button"
        className="st-btn st-btn-ghost st-btn-small"
        onClick={() => void signInPasskey()}
      >
        {t('signIn.passkey')}
      </button>
      {error && (
        <p className="st-alert" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
