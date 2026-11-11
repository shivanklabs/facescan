// EventSetupPage (organiser): create an event, create and revoke guest invite links (shown as
// text once; QR codes come later), add photographers. Requires a passkey session.
import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PasskeyGate } from '@/features/auth/PasskeyGate';
import { api } from '@/lib/apiClient';

interface EventSummary {
  event_id: string;
  name: string;
  status: string;
  roles: string[];
}

interface Invite {
  invite_id: string;
  expires_at: string;
  revoked: boolean;
  url: string | null;
}

function EventAdmin({ event }: { event: EventSummary }) {
  const { t } = useTranslation();
  const [invites, setInvites] = useState<Invite[]>([]);
  const [newUrl, setNewUrl] = useState('');
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState('');

  const loadInvites = useCallback(() => {
    api<{ invites: Invite[] }>(`/events/${event.event_id}/invites`)
      .then((data) => setInvites(data.invites))
      .catch(() => setInvites([]));
  }, [event.event_id]);

  useEffect(loadInvites, [loadInvites]);

  async function createInvite() {
    const invite = await api<Invite>(`/events/${event.event_id}/invites`, { json: {} });
    setNewUrl(invite.url ?? '');
    loadInvites();
  }

  async function revoke(inviteId: string) {
    await api(`/events/${event.event_id}/invites/${inviteId}/revoke`, { method: 'POST' });
    loadInvites();
  }

  async function addPhotographer(e: FormEvent) {
    e.preventDefault();
    try {
      await api(`/events/${event.event_id}/members`, { json: { email, role: 'photographer' } });
      setMessage(t('organiser.added'));
      setEmail('');
    } catch {
      setMessage(t('common.error'));
    }
  }

  return (
    <section className="card">
      <h2>{event.name}</h2>
      <p>
        {t('organiser.status')}: {event.status}
      </p>
      <div className="row">
        <Link to={`/organiser/events/${event.event_id}/approval`}>{t('organiser.approval')}</Link>
        <Link to={`/photographer/events/${event.event_id}/upload`}>{t('organiser.upload')}</Link>
        <Link to={`/photographer/events/${event.event_id}/status`}>Processing Status</Link>
      </div>
      <h3>{t('organiser.invites')}</h3>
      <button type="button" onClick={() => void createInvite()}>
        {t('organiser.createInvite')}
      </button>
      {newUrl && (
        <p className="banner">
          {t('organiser.inviteOnce')} <code className="copyable">{newUrl}</code>
        </p>
      )}
      <ul className="list">
        {invites.map((invite) => (
          <li key={invite.invite_id}>
            {t('organiser.expires')}: {new Date(invite.expires_at).toLocaleString()}{' '}
            {invite.revoked ? (
              t('organiser.revoked')
            ) : (
              <button type="button" className="link" onClick={() => void revoke(invite.invite_id)}>
                {t('organiser.revoke')}
              </button>
            )}
          </li>
        ))}
      </ul>
      <h3>{t('organiser.addPhotographer')}</h3>
      <form onSubmit={(e) => void addPhotographer(e)}>
        <label htmlFor={`email-${event.event_id}`}>{t('organiser.photographerEmail')}</label>
        <input
          id={`email-${event.event_id}`}
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <button type="submit">{t('organiser.add')}</button>
      </form>
      {message && <p role="status">{message}</p>}
    </section>
  );
}

function Events() {
  const { t } = useTranslation();
  const [events, setEvents] = useState<EventSummary[]>([]);
  const [name, setName] = useState('');
  const [country, setCountry] = useState('IN');
  const [error, setError] = useState('');

  const load = useCallback(() => {
    api<{ events: EventSummary[] }>('/events')
      .then((data) => setEvents(data.events.filter((e) => e.roles.includes('organiser'))))
      .catch(() => setEvents([]));
  }, []);

  useEffect(load, [load]);

  async function create(e: FormEvent) {
    e.preventDefault();
    setError('');
    try {
      await api('/events', { json: { name, country } });
      setName('');
      load();
    } catch {
      setError(t('common.error'));
    }
  }

  return (
    <>
      <form className="card" onSubmit={(e) => void create(e)}>
        <h2>{t('organiser.create')}</h2>
        <label htmlFor="event-name">{t('organiser.name')}</label>
        <input
          id="event-name"
          required
          maxLength={200}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <label htmlFor="event-country">{t('organiser.country')}</label>
        <input
          id="event-country"
          required
          pattern="[A-Za-z]{2}"
          maxLength={2}
          value={country}
          onChange={(e) => setCountry(e.target.value)}
        />
        <button type="submit">{t('organiser.create')}</button>
        {error && <p role="alert">{error}</p>}
      </form>
      {events.map((event) => (
        <EventAdmin key={event.event_id} event={event} />
      ))}
    </>
  );
}

export default function EventSetupPage() {
  const { t } = useTranslation();
  return (
    <>
      <h1>{t('organiser.title')}</h1>
      <PasskeyGate next="/organiser">
        <Events />
      </PasskeyGate>
    </>
  );
}
