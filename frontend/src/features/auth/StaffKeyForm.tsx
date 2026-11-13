// StaffKeyForm: staff sign-in with a one-time key FaceScan generated (no email). Returning
// staff use their passkey. After a first key sign-in, Studio asks them to add a passkey.
import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { api, ApiError } from '@/lib/serverApi';
import { passkeyErrorKey, signInWithPasskey } from '@/lib/passkeys';

interface Props {
  onSignedIn: (next: string) => void;
}

const KEY_PATTERN = /^FS-[A-Za-z0-9_-]{16,97}$/;

/**
 * Purpose: staff sign-in form (key or passkey).
 * Inputs: onSignedIn(next path). Outputs: form element.
 * Side effects: POST /auth/staff-key (session cookie); WebAuthn passkey sign-in.
 */
export function StaffKeyForm({ onSignedIn }: Props) {
  const { t } = useTranslation();
  const [key, setKey] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submitKey(event: FormEvent) {
    event.preventDefault();
    const clean = key.trim();
    if (!KEY_PATTERN.test(clean)) {
      setError(t('staff.keyFormat'));
      return;
    }
    setBusy(true);
    setError('');
    try {
      const result = await api<{ next: string }>('/auth/staff-key', { json: { key: clean } });
      onSignedIn(result.next || '/studio');
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 400 ? t('staff.keyInvalid') : t('common.error'),
      );
      setBusy(false);
    }
  }

  async function signInPasskey() {
    setError('');
    try {
      await signInWithPasskey();
      onSignedIn('/studio');
    } catch (err) {
      setError(t(passkeyErrorKey(err)));
    }
  }

  return (
    <div className="st-card st-claim st-staff">
      <p className="st-badge">{t('staff.title')}</p>
      <form className="st-form-row" onSubmit={(e) => void submitKey(e)}>
        <label className="st-visually-hidden" htmlFor="staff-key">
          {t('staff.key')}
        </label>
        <input
          id="staff-key"
          className="st-input"
          autoComplete="off"
          spellCheck={false}
          required
          placeholder={t('staff.keyHint')}
          value={key}
          onChange={(e) => setKey(e.target.value)}
        />
        <button type="submit" className="st-btn st-btn-primary" disabled={busy}>
          {t('staff.useKey')}
        </button>
      </form>
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

type StaffRole = 'admin' | 'organiser' | 'photographer';

interface StaffKeyItem {
  key_id: string;
  role: StaffRole;
  event_id: string | null;
  expires_at: string;
  used: boolean;
  revoked: boolean;
}

/**
 * Purpose: create one-time staff keys (shown once, to send to the person) and list or turn
 *   off the ones this person made. Admins: owner/admin keys; organisers: photographer keys
 *   for one event.
 * Inputs: roles that may be created here, eventId (photographer keys).
 * Outputs: card element. Side effects: staff-key API calls; clipboard on copy.
 */
export function StaffKeysCard({ roles, eventId }: { roles: StaffRole[]; eventId?: string }) {
  const { t } = useTranslation();
  const [role, setRole] = useState<StaffRole>(roles[0] ?? 'photographer');
  const [keys, setKeys] = useState<StaffKeyItem[]>([]);
  const [newKey, setNewKey] = useState('');
  const [message, setMessage] = useState('');

  const load = useCallback(() => {
    api<{ keys: StaffKeyItem[] }>('/auth/staff-keys')
      .then((data) =>
        setKeys(data.keys.filter((k) => (eventId ? k.event_id === eventId : k.event_id === null))),
      )
      .catch(() => setKeys([]));
  }, [eventId]);

  useEffect(load, [load]);

  async function create() {
    setMessage('');
    try {
      const made = await api<{ key: string }>('/auth/staff-keys', {
        json: { role, event_id: role === 'photographer' ? (eventId ?? null) : null },
      });
      setNewKey(made.key);
      load();
    } catch {
      setMessage(t('staffKeys.createFailed'));
    }
  }

  async function revoke(keyId: string) {
    try {
      await api(`/auth/staff-keys/${keyId}/revoke`, { method: 'POST' });
      load();
    } catch {
      setMessage(t('common.error'));
    }
  }

  const status = (k: StaffKeyItem) =>
    k.used
      ? t('staffKeys.used')
      : k.revoked
        ? t('staffKeys.revoked')
        : new Date(k.expires_at) < new Date()
          ? t('staffKeys.expired')
          : t('staffKeys.waiting');

  return (
    <div className="st-card st-claim">
      <h2>{t('staffKeys.title')}</h2>
      <p className="st-lead-small">{t('staffKeys.intro')}</p>
      <div className="st-form-row">
        {roles.length > 1 && (
          <select
            className="st-input"
            aria-label={t('settings.role')}
            value={role}
            onChange={(e) => setRole(e.target.value as StaffRole)}
          >
            {roles.map((r) => (
              <option key={r} value={r}>
                {t(`staffKeys.role.${r}`)}
              </option>
            ))}
          </select>
        )}
        <button type="button" className="st-btn st-btn-primary" onClick={() => void create()}>
          {roles.length === 1
            ? t('staffKeys.createFor', { role: t(`staffKeys.role.${roles[0]}`) })
            : t('staffKeys.create')}
        </button>
      </div>
      {newKey && (
        <div className="st-invite-new">
          <p className="st-note">{t('staffKeys.shownOnce')}</p>
          <code className="st-code">{newKey}</code>
          <button
            type="button"
            className="st-btn st-btn-ghost st-btn-small"
            onClick={() => void navigator.clipboard?.writeText(newKey)}
          >
            {t('settings.copyLink')}
          </button>
        </div>
      )}
      {keys.length > 0 && (
        <ul className="st-list">
          {keys.map((k) => (
            <li key={k.key_id} className="st-list-row">
              <span>
                <strong>{t(`staffKeys.role.${k.role}`)}</strong>{' '}
                <span className="st-pill">{status(k)}</span>
              </span>
              {!k.used && !k.revoked && (
                <button
                  type="button"
                  className="st-btn st-btn-ghost st-btn-small"
                  onClick={() => void revoke(k.key_id)}
                >
                  {t('settings.revoke')}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {message && <p className="st-alert">{message}</p>}
    </div>
  );
}
