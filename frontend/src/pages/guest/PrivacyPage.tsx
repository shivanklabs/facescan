// PrivacyPage: the person's face-search consent per event (turn off / turn back on) and
// signing out on all devices. Turning off hides that event's photos and keeps it out of new
// scans; nothing is deleted, so turning it back on brings the photos back.
import { useCallback, useEffect, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useCurrentUser } from '@/features/auth/useCurrentUser';
import { api, ApiError } from '@/lib/serverApi';

interface ConsentRecord {
  event_id: string;
  event_name: string;
  purpose: string;
  granted_at: string;
  withdrawn_at: string | null;
}

export interface EventConsent {
  eventId: string;
  eventName: string;
  active: boolean;
  since: string;
}

/**
 * Purpose: reduce the consent history to one line per event: on if any face-search consent
 *   is active, otherwise off since the latest withdrawal. (A new notice version closes the old
 *   row and opens a new one, so the raw history can show "withdrawn" rows the person never
 *   withdrew.)
 * Inputs: consent records. Outputs: one EventConsent per event, sorted by event name.
 * Side effects: none.
 */
export function consentPerEvent(records: ConsentRecord[]): EventConsent[] {
  const byEvent = new Map<string, EventConsent>();
  for (const r of records) {
    if (r.purpose !== 'face_search') continue;
    const active = r.withdrawn_at === null;
    const since = active ? r.granted_at : (r.withdrawn_at ?? r.granted_at);
    const seen = byEvent.get(r.event_id);
    if (!seen || (active && !seen.active) || (active === seen.active && since > seen.since)) {
      byEvent.set(r.event_id, { eventId: r.event_id, eventName: r.event_name, active, since });
    }
  }
  return [...byEvent.values()].sort((a, b) => a.eventName.localeCompare(b.eventName));
}

/**
 * Purpose: privacy settings page in the new design.
 * Inputs: none (reads the session). Outputs: page element.
 * Side effects: GET /me/consents and /consent/notice; POST withdraw / consent / logout-everywhere.
 */
export default function PrivacyPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { me, loading } = useCurrentUser();
  const [events, setEvents] = useState<EventConsent[] | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmText, setConfirmText] = useState('');
  const [deleteError, setDeleteError] = useState('');
  const [deleted, setDeleted] = useState(false);

  const load = useCallback(() => {
    api<{ consents: ConsentRecord[] }>('/me/consents')
      .then((data) => setEvents(consentPerEvent(data.consents)))
      .catch(() => setEvents([]));
  }, []);

  useEffect(() => {
    if (me) load();
  }, [me, load]);

  async function turnOff(eventId: string) {
    if (!window.confirm(t('privacyPage.confirmOff'))) return;
    setBusy(eventId);
    try {
      await api(`/events/${eventId}/consent/withdraw`, { method: 'POST' });
      setMessage(t('privacyPage.doneOff'));
      load();
    } catch {
      setMessage(t('common.error'));
    } finally {
      setBusy(null);
    }
  }

  async function turnOn(eventId: string) {
    setBusy(eventId);
    try {
      const notice = await api<{ version: string }>('/consent/notice');
      await api(`/events/${eventId}/consent`, {
        json: {
          notice_version: notice.version,
          language: i18n.language === 'hi' ? 'hi' : 'en',
          face_search: true,
        },
      });
      setMessage(t('privacyPage.doneOn'));
      load();
    } catch {
      setMessage(t('common.error'));
    } finally {
      setBusy(null);
    }
  }

  async function signOutEverywhere() {
    await api('/auth/logout-everywhere', { method: 'POST' }).catch(() => undefined);
    navigate('/', { replace: true });
    window.location.reload();
  }

  /**
   * Purpose: erase my account and my data (typed "DELETE"); event photos stay.
   * Inputs: none (reads the typed text). Outputs: none.
   * Side effects: POST /auth/delete-account (session ends); shows the goodbye screen.
   */
  async function deleteAccount() {
    if (confirmText !== 'DELETE') return;
    setDeleteError('');
    try {
      await api('/auth/delete-account', { json: { confirm: 'DELETE' } });
      setDeleted(true);
    } catch (error) {
      setDeleteError(
        error instanceof ApiError && error.status === 409
          ? t('deleteAccount.ownerBlocked')
          : t('common.error'),
      );
    }
  }

  // Shown before the signed-out redirect: the session has just ended on purpose.
  if (deleted) {
    return (
      <section className="st-page">
        <h1>{t('deleteAccount.doneTitle')}</h1>
        <p className="st-lead-small">{t('deleteAccount.doneText')}</p>
        <p className="st-note">{t('deleteAccount.passkeyNote')}</p>
        <button
          type="button"
          className="st-btn st-btn-primary"
          onClick={() => window.location.assign('/')}
        >
          {t('deleteAccount.home')}
        </button>
      </section>
    );
  }

  if (!loading && !me) return <Navigate to="/" replace />;

  return (
    <section className="st-page">
      <div>
        <p className="st-eyebrow">{t('privacyPage.eyebrow')}</p>
        <h1>{t('privacyPage.title')}</h1>
        <p className="st-lead-small">{t('privacyPage.intro')}</p>
      </div>
      {message && (
        <p className="st-note" role="status">
          {message}
        </p>
      )}
      {events === null && <p className="st-note">{t('common.loading')}</p>}
      {events?.length === 0 && <p className="st-note">{t('privacyPage.none')}</p>}
      {events && events.length > 0 && (
        <ul className="st-list">
          {events.map((event) => (
            <li key={event.eventId} className="st-card st-list-row">
              <div>
                <strong>{event.eventName}</strong>
                <span className={event.active ? 'st-pill is-on' : 'st-pill'}>
                  {event.active ? t('privacyPage.on') : t('privacyPage.off')}
                </span>
              </div>
              {event.active ? (
                <button
                  type="button"
                  className="st-btn st-btn-ghost st-btn-small"
                  disabled={busy === event.eventId}
                  onClick={() => void turnOff(event.eventId)}
                >
                  {t('privacyPage.turnOff')}
                </button>
              ) : (
                <button
                  type="button"
                  className="st-btn st-btn-primary st-btn-small"
                  disabled={busy === event.eventId}
                  onClick={() => void turnOn(event.eventId)}
                >
                  {t('privacyPage.turnOn')}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="st-card">
        <p>{t('privacyPage.everywhereText')}</p>
        <button
          type="button"
          className="st-btn st-btn-ghost"
          onClick={() => void signOutEverywhere()}
        >
          {t('common.signOutEverywhere')}
        </button>
      </div>
      <div className="st-card st-claim st-danger-zone">
        <h2>{t('deleteAccount.title')}</h2>
        <p className="st-lead-small">{t('deleteAccount.what')}</p>
        <label className="st-field">
          <span>{t('deleteAccount.typeToConfirm')}</span>
          <input
            className="st-input"
            autoComplete="off"
            spellCheck={false}
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
          />
        </label>
        <button
          type="button"
          className="st-btn st-btn-danger"
          disabled={confirmText !== 'DELETE'}
          onClick={() => void deleteAccount()}
        >
          {t('deleteAccount.button')}
        </button>
        {deleteError && (
          <p className="st-alert" role="alert">
            {deleteError}
          </p>
        )}
      </div>
    </section>
  );
}
