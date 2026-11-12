// EventSettingsPage: one owner page for an event's settings: invite links with a printable QR,
// sections, the search window, photographer keys (no emails) and camera clocks.
// Every action is checked again by the server (organiser of this event, strong sign-in).
import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { QRCodeCanvas } from 'qrcode.react';
import { StaffKeysCard } from '@/features/auth/StaffKeyForm';
import { useCurrentUser } from '@/features/auth/useCurrentUser';
import { api, ApiError } from '@/lib/serverApi';

interface EventInfo {
  event_id: string;
  name: string;
  search_window_ends_at: string | null;
}

interface Invite {
  invite_id: string;
  expires_at: string;
  revoked: boolean;
  url: string | null;
}

interface Section {
  id: string;
  name: string;
  starts_at: string | null;
  ends_at: string | null;
  photo_count: number;
}

interface Camera {
  camera_make: string | null;
  camera_model: string;
  camera_serial: string | null;
  photo_count: number;
  offset_seconds: number;
}

/**
 * Purpose: convert an ISO time to the value a datetime-local input shows (local time).
 * Inputs: ISO string or null. Outputs: 'YYYY-MM-DDTHH:mm' or ''. Side effects: none.
 */
export function toLocalInput(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * Purpose: convert a datetime-local value back to an ISO time with the browser's time zone.
 * Inputs: input value. Outputs: ISO string or null when empty. Side effects: none.
 */
export function fromLocalInput(value: string): string | null {
  return value ? new Date(value).toISOString() : null;
}

/**
 * Purpose: a short message for a failed owner action (sign-in strength, permission, other).
 * Inputs: t, error. Outputs: text. Side effects: none.
 */
function ownerError(t: (key: string) => string, error: unknown): string {
  if (error instanceof ApiError && error.status === 403) return t('owner.needStrongSignIn');
  return t('common.error');
}

/**
 * Purpose: invite links: create one (shown once, with a QR to print), list and revoke.
 * Inputs: event. Outputs: card element. Side effects: invite API calls.
 */
function InvitesCard({ event }: { event: EventInfo }) {
  const { t } = useTranslation();
  const [invites, setInvites] = useState<Invite[]>([]);
  const [newUrl, setNewUrl] = useState('');
  const [days, setDays] = useState('30');
  const [message, setMessage] = useState('');

  const load = useCallback(() => {
    api<{ invites: Invite[] }>(`/events/${event.event_id}/invites`)
      .then((data) => setInvites(data.invites))
      .catch((error: unknown) => setMessage(ownerError(t, error)));
  }, [event.event_id, t]);

  useEffect(load, [load]);

  async function create() {
    setMessage('');
    try {
      const invite = await api<Invite>(`/events/${event.event_id}/invites`, {
        json: { days: Number(days) },
      });
      setNewUrl(invite.url ?? '');
      load();
    } catch (error) {
      setMessage(ownerError(t, error));
    }
  }

  async function revoke(inviteId: string) {
    if (!window.confirm(t('settings.revokeConfirm'))) return;
    try {
      await api(`/events/${event.event_id}/invites/${inviteId}/revoke`, { method: 'POST' });
      load();
    } catch (error) {
      setMessage(ownerError(t, error));
    }
  }

  function downloadQr() {
    const canvas = document.getElementById('invite-qr');
    if (!(canvas instanceof HTMLCanvasElement)) return;
    const link = document.createElement('a');
    link.href = canvas.toDataURL('image/png');
    link.download = `${event.name.replace(/[^\w-]+/g, '_')}-invite-qr.png`;
    link.click();
  }

  return (
    <div className="st-card st-claim">
      <h2>{t('settings.invitesTitle')}</h2>
      <p className="st-lead-small">{t('settings.invitesText')}</p>
      <div className="st-form-row">
        <label className="st-field">
          <span>{t('settings.validDays')}</span>
          <select className="st-input" value={days} onChange={(e) => setDays(e.target.value)}>
            {['7', '30', '90'].map((d) => (
              <option key={d} value={d}>
                {t('settings.days', { count: Number(d) })}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="st-btn st-btn-primary" onClick={() => void create()}>
          {t('settings.createInvite')}
        </button>
      </div>
      {newUrl && (
        <div className="st-invite-new">
          <p className="st-note">{t('settings.inviteOnce')}</p>
          <div className="st-qr">
            <QRCodeCanvas id="invite-qr" value={newUrl} size={220} level="M" marginSize={2} />
          </div>
          <code className="st-code">{newUrl}</code>
          <div className="st-form-row">
            <button type="button" className="st-btn st-btn-primary" onClick={downloadQr}>
              {t('settings.downloadQr')}
            </button>
            <button
              type="button"
              className="st-btn st-btn-ghost"
              onClick={() => void navigator.clipboard?.writeText(newUrl)}
            >
              {t('settings.copyLink')}
            </button>
          </div>
        </div>
      )}
      {invites.length > 0 && (
        <ul className="st-list">
          {invites.map((invite) => (
            <li key={invite.invite_id} className="st-list-row">
              <span>
                {t('settings.expires')}: {new Date(invite.expires_at).toLocaleDateString()}
              </span>
              {invite.revoked ? (
                <span className="st-pill">{t('settings.revoked')}</span>
              ) : (
                <button
                  type="button"
                  className="st-btn st-btn-ghost st-btn-small"
                  onClick={() => void revoke(invite.invite_id)}
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

/**
 * Purpose: sections (parts of the event with times) used by "Find more" filters.
 * Inputs: event id. Outputs: card element. Side effects: section API calls.
 */
function SectionsCard({ eventId }: { eventId: string }) {
  const { t } = useTranslation();
  const [sections, setSections] = useState<Section[]>([]);
  const [name, setName] = useState('');
  const [starts, setStarts] = useState('');
  const [ends, setEnds] = useState('');
  const [message, setMessage] = useState('');

  const load = useCallback(() => {
    api<{ sections: Section[] }>(`/events/${eventId}/sections`)
      .then((data) => setSections(data.sections))
      .catch((error: unknown) => setMessage(ownerError(t, error)));
  }, [eventId, t]);

  useEffect(load, [load]);

  async function add(e: FormEvent) {
    e.preventDefault();
    setMessage('');
    if (starts && ends && ends <= starts) {
      setMessage(t('settings.endsAfterStarts'));
      return;
    }
    try {
      await api(`/events/${eventId}/sections`, {
        json: {
          name: name.trim(),
          starts_at: fromLocalInput(starts),
          ends_at: fromLocalInput(ends),
        },
      });
      setName('');
      setStarts('');
      setEnds('');
      load();
    } catch (error) {
      setMessage(ownerError(t, error));
    }
  }

  return (
    <div className="st-card st-claim">
      <h2>{t('settings.sectionsTitle')}</h2>
      <p className="st-lead-small">{t('settings.sectionsText')}</p>
      {sections.length > 0 && (
        <ul className="st-list">
          {sections.map((s) => (
            <li key={s.id} className="st-list-row">
              <span>
                <strong>{s.name}</strong>
                {s.starts_at && s.ends_at && (
                  <span className="st-pill">
                    {new Date(s.starts_at).toLocaleTimeString([], { timeStyle: 'short' })} –{' '}
                    {new Date(s.ends_at).toLocaleTimeString([], { timeStyle: 'short' })}
                  </span>
                )}
              </span>
              <span className="st-count">{t('settings.photoCount', { count: s.photo_count })}</span>
            </li>
          ))}
        </ul>
      )}
      <form className="st-form-row" onSubmit={(e) => void add(e)}>
        <label className="st-field st-field-grow">
          <span>{t('settings.sectionName')}</span>
          <input
            className="st-input"
            required
            maxLength={200}
            placeholder={t('settings.sectionHint')}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="st-field">
          <span>{t('settings.starts')}</span>
          <input
            className="st-input"
            type="datetime-local"
            value={starts}
            onChange={(e) => setStarts(e.target.value)}
          />
        </label>
        <label className="st-field">
          <span>{t('settings.ends')}</span>
          <input
            className="st-input"
            type="datetime-local"
            value={ends}
            onChange={(e) => setEnds(e.target.value)}
          />
        </label>
        <button type="submit" className="st-btn st-btn-primary" disabled={!name.trim()}>
          {t('settings.addSection')}
        </button>
      </form>
      {message && <p className="st-alert">{message}</p>}
    </div>
  );
}

/**
 * Purpose: the search window: after this time new face searches stop; galleries and downloads
 *   keep working. Inputs: event, onSaved. Outputs: card element. Side effects: one POST.
 */
function SearchWindowCard({ event, onSaved }: { event: EventInfo; onSaved: () => void }) {
  const { t } = useTranslation();
  const [value, setValue] = useState(toLocalInput(event.search_window_ends_at));
  const [message, setMessage] = useState('');

  async function save(next: string) {
    setMessage('');
    try {
      await api(`/events/${event.event_id}/search-window`, {
        json: { search_window_ends_at: fromLocalInput(next) },
      });
      setValue(next);
      setMessage(t('settings.saved'));
      onSaved();
    } catch (error) {
      setMessage(ownerError(t, error));
    }
  }

  return (
    <div className="st-card st-claim">
      <h2>{t('settings.windowTitle')}</h2>
      <p className="st-lead-small">{t('settings.windowText')}</p>
      <div className="st-form-row">
        <label className="st-field">
          <span>{t('settings.windowEnds')}</span>
          <input
            className="st-input"
            type="datetime-local"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        </label>
        <button type="button" className="st-btn st-btn-primary" onClick={() => void save(value)}>
          {t('settings.save')}
        </button>
        {event.search_window_ends_at && (
          <button type="button" className="st-btn st-btn-ghost" onClick={() => void save('')}>
            {t('settings.noEnd')}
          </button>
        )}
      </div>
      {message && <p className="st-note">{message}</p>}
    </div>
  );
}

/**
 * Purpose: camera clocks: shift a camera's photo times so all cameras line up (sections and
 *   time filters depend on it). Inputs: event id. Outputs: card element.
 * Side effects: GET cameras; POST offset (re-times that camera's photos).
 */
function CamerasCard({ eventId }: { eventId: string }) {
  const { t } = useTranslation();
  const [cameras, setCameras] = useState<Camera[] | null>(null);
  const [minutes, setMinutes] = useState<Record<string, string>>({});
  const [message, setMessage] = useState('');
  const keyOf = (c: Camera) => `${c.camera_model}|${c.camera_serial ?? ''}`;

  const load = useCallback(() => {
    api<{ cameras: Camera[] }>(`/events/${eventId}/cameras`)
      .then((data) => setCameras(data.cameras))
      .catch((error: unknown) => setMessage(ownerError(t, error)));
  }, [eventId, t]);

  useEffect(load, [load]);

  async function save(camera: Camera) {
    const raw = minutes[keyOf(camera)] ?? String(Math.round(camera.offset_seconds / 60));
    const value = Number(raw);
    if (!Number.isFinite(value) || Math.abs(value) > 1440) {
      setMessage(t('settings.offsetRange'));
      return;
    }
    setMessage('');
    try {
      const result = await api<{ updated_photos: number }>(`/events/${eventId}/cameras/offset`, {
        json: {
          camera_model: camera.camera_model,
          camera_serial: camera.camera_serial,
          offset_seconds: Math.round(value * 60),
        },
      });
      setMessage(t('settings.offsetSaved', { count: result.updated_photos }));
      load();
    } catch (error) {
      setMessage(ownerError(t, error));
    }
  }

  return (
    <div className="st-card st-claim">
      <h2>{t('settings.camerasTitle')}</h2>
      <p className="st-lead-small">{t('settings.camerasText')}</p>
      {cameras?.length === 0 && <p className="st-note">{t('settings.noCameras')}</p>}
      {cameras && cameras.length > 0 && (
        <ul className="st-list">
          {cameras.map((camera) => (
            <li key={keyOf(camera)} className="st-list-row">
              <span>
                <strong>
                  {[camera.camera_make, camera.camera_model].filter(Boolean).join(' ')}
                </strong>
                <span className="st-count">
                  {t('settings.photoCount', { count: camera.photo_count })}
                </span>
              </span>
              <span className="st-form-row">
                <label className="st-field">
                  <span>{t('settings.offsetMinutes')}</span>
                  <input
                    className="st-input st-input-short"
                    type="number"
                    step="1"
                    min={-1440}
                    max={1440}
                    value={minutes[keyOf(camera)] ?? String(Math.round(camera.offset_seconds / 60))}
                    onChange={(e) => setMinutes((m) => ({ ...m, [keyOf(camera)]: e.target.value }))}
                  />
                </label>
                <button
                  type="button"
                  className="st-btn st-btn-ghost st-btn-small"
                  onClick={() => void save(camera)}
                >
                  {t('settings.save')}
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {message && <p className="st-note">{message}</p>}
    </div>
  );
}

/**
 * Purpose: the event settings page for owners.
 * Inputs: eventId (route). Outputs: page element.
 * Side effects: loads the event; the cards above make their own calls.
 */
export default function EventSettingsPage() {
  const { t } = useTranslation();
  const { eventId = '' } = useParams();
  const { me, loading } = useCurrentUser();
  const [event, setEvent] = useState<EventInfo | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    api<EventInfo>(`/events/${eventId}`)
      .then(setEvent)
      .catch((err: unknown) => setError(ownerError(t, err)));
  }, [eventId, t]);

  useEffect(() => {
    if (me?.can_create_events) load();
  }, [me, load]);

  if (!loading && !me) return <Navigate to="/" replace />;

  return (
    <section className="st-page">
      <div>
        <Link to="/studio" className="st-back">
          ← {t('owner.backToStudio')}
        </Link>
        <p className="st-eyebrow">{t('settings.eyebrow')}</p>
        <h1>{event?.name ?? t('common.loading')}</h1>
      </div>
      {error && <p className="st-alert">{error}</p>}
      {event && (
        <>
          <InvitesCard event={event} />
          <SectionsCard eventId={eventId} />
          <SearchWindowCard
            key={event.search_window_ends_at ?? 'none'}
            event={event}
            onSaved={load}
          />
          <StaffKeysCard roles={['photographer']} eventId={eventId} />
          <CamerasCard eventId={eventId} />
        </>
      )}
    </section>
  );
}
