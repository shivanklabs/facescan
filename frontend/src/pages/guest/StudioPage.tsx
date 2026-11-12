// StudioPage (milestone 7): the owner's one admin page. Create an event, drop photos, watch
// them become searchable. Uploads go in small batches into one collection, which is then
// approved so the worker indexes it (no separate approval step for the owner).
import { useCallback, useEffect, useRef, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMe } from '@/features/auth/useMe';
import { api, ApiError } from '@/lib/apiClient';

interface EventItem {
  event_id: string;
  name: string;
  status: string;
  created_at: string;
  roles: string[];
}

interface Status {
  quarantined: number;
  approved: number;
  indexed: number;
  failed: number;
}

interface UploadResult {
  collection_id: string;
  photo_ids: string[];
  already_uploaded: number[];
}

const BATCH_SIZE = 8;
const ACCEPTED = ['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif'];
const POLL_MS = 4000;

/**
 * Purpose: owner page: events list, new event, drag-and-drop upload with progress.
 * Inputs: none. Outputs: page element.
 * Side effects: creates events, uploads photos, approves collections, polls status.
 */
export default function StudioPage() {
  const { t } = useTranslation();
  const { me, loading } = useMe();
  const [events, setEvents] = useState<EventItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [message, setMessage] = useState('');
  const input = useRef<HTMLInputElement>(null);

  const loadEvents = useCallback(() => {
    api<{ events: EventItem[] }>('/events')
      .then((value) => {
        const mine = value.events.filter((e) => e.roles.includes('organiser'));
        setEvents(mine);
        setActiveId((current) => current ?? mine[0]?.event_id ?? null);
      })
      .catch(() => setMessage(t('studio.loadFailed')));
  }, [t]);

  useEffect(() => {
    if (me?.can_create_events) loadEvents();
  }, [me, loadEvents]);

  useEffect(() => {
    if (!activeId) return;
    let active = true;
    const tick = () =>
      api<Status>(`/events/${activeId}/processing-status`)
        .then((value) => {
          if (active) setStatus(value);
        })
        .catch(() => {
          if (active) setStatus(null);
        });
    void tick();
    const timer = window.setInterval(() => {
      void tick();
      loadEvents();
    }, POLL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [activeId, loadEvents]);

  if (!loading && !me) return <Navigate to="/" replace />;
  if (!loading && me && !me.can_create_events) {
    return (
      <section className="st-empty">
        <div>
          <h1>{t('studio.notAllowedTitle')}</h1>
          <p>{t('studio.notAllowedText')}</p>
        </div>
      </section>
    );
  }

  async function createEvent() {
    const clean = name.trim();
    if (!clean) return;
    setBusy(true);
    setMessage('');
    try {
      const created = await api<EventItem>('/events', {
        json: { name: clean.slice(0, 200), country: 'IN' },
      });
      setName('');
      setActiveId(created.event_id);
      loadEvents();
    } catch {
      setMessage(t('studio.createFailed'));
    } finally {
      setBusy(false);
    }
  }

  async function upload(list: FileList | File[]) {
    if (!activeId) return;
    const files = Array.from(list).filter((f) => ACCEPTED.includes(f.type));
    if (files.length === 0) {
      setMessage(t('studio.noImages'));
      return;
    }
    setBusy(true);
    setMessage('');
    setProgress({ done: 0, total: files.length });
    let collectionId: string | null = null;
    let skipped = 0;
    try {
      for (let i = 0; i < files.length; i += BATCH_SIZE) {
        const form = new FormData();
        if (collectionId) form.append('collection_id', collectionId);
        for (const file of files.slice(i, i + BATCH_SIZE)) form.append('files', file, file.name);
        const result = await api<UploadResult>(`/events/${activeId}/photos`, {
          body: form,
          timeoutMs: 300_000,
        });
        collectionId = result.collection_id;
        skipped += result.already_uploaded.length;
        setProgress({ done: Math.min(i + BATCH_SIZE, files.length), total: files.length });
      }
      if (collectionId) {
        await api(`/events/${activeId}/collections/${collectionId}/approve`, { method: 'POST' });
      }
      setMessage(
        skipped > 0
          ? t('studio.uploadedSkipped', { count: files.length - skipped, skipped })
          : t('studio.uploaded', { count: files.length }),
      );
    } catch (error) {
      if (collectionId) {
        // Approve what did arrive, so nothing uploaded is left waiting.
        await api(`/events/${activeId}/collections/${collectionId}/approve`, {
          method: 'POST',
        }).catch(() => undefined);
      }
      setMessage(
        error instanceof ApiError && error.status === 413
          ? t('studio.tooLarge')
          : t('studio.uploadFailed'),
      );
    } finally {
      setBusy(false);
      setProgress(null);
      if (input.current) input.current.value = '';
      loadEvents();
    }
  }

  const active = events.find((e) => e.event_id === activeId) ?? null;
  const total = status ? status.quarantined + status.approved + status.indexed + status.failed : 0;
  const ready = status && total > 0 ? Math.round((status.indexed / total) * 100) : 0;

  return (
    <div className="st-admin">
      <div>
        <p className="st-eyebrow">{t('studio.eyebrow')}</p>
        <h1>{t('studio.title')}</h1>
      </div>

      <div className="st-card">
        <form
          className="st-form-row"
          onSubmit={(event) => {
            event.preventDefault();
            void createEvent();
          }}
        >
          <label className="st-visually-hidden" htmlFor="event-name">
            {t('studio.eventName')}
          </label>
          <input
            id="event-name"
            className="st-input"
            type="text"
            maxLength={200}
            placeholder={t('studio.eventNamePlaceholder')}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <button type="submit" className="st-btn st-btn-primary" disabled={busy || !name.trim()}>
            {t('studio.create')}
          </button>
        </form>
      </div>

      {events.length > 0 && (
        <div className="st-events" role="list">
          {events.map((event) => (
            <button
              type="button"
              role="listitem"
              key={event.event_id}
              className={`st-event-card ${event.event_id === activeId ? 'is-active' : ''}`}
              onClick={() => setActiveId(event.event_id)}
            >
              <strong>{event.name}</strong>
              <span
                className={`st-badge ${
                  event.status === 'searchable'
                    ? 'is-ready'
                    : event.status === 'indexing'
                      ? 'is-busy'
                      : ''
                }`}
              >
                ● {t(`studio.status.${event.status}`, { defaultValue: event.status })}
              </span>
            </button>
          ))}
        </div>
      )}

      {active && (
        <div className="st-card st-stack-gap">
          <h2>{active.name}</h2>
          <label
            className={`st-drop ${over ? 'is-over' : ''}`}
            onDragOver={(event) => {
              event.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={(event) => {
              event.preventDefault();
              setOver(false);
              if (!busy) void upload(event.dataTransfer.files);
            }}
          >
            <input
              ref={input}
              type="file"
              accept={ACCEPTED.join(',')}
              multiple
              className="st-visually-hidden"
              disabled={busy}
              onChange={(event) => {
                if (event.target.files) void upload(event.target.files);
              }}
            />
            <span aria-hidden="true">⇪</span>
            <strong>{t('studio.dropTitle')}</strong>
            <span>{t('studio.dropText')}</span>
          </label>

          {progress && (
            <div>
              <p className="st-count">
                {t('studio.uploading', { done: progress.done, total: progress.total })}
              </p>
              <div className="st-progress">
                <div style={{ width: `${(progress.done / progress.total) * 100}%` }} />
              </div>
            </div>
          )}

          {status && total > 0 && (
            <>
              <div>
                <p className="st-count">{t('studio.readyPercent', { percent: ready })}</p>
                <div className="st-progress">
                  <div style={{ width: `${ready}%` }} />
                </div>
              </div>
              <div className="st-stats">
                <div className="st-stat">
                  <b>{total}</b>
                  <span>{t('studio.statTotal')}</span>
                </div>
                <div className="st-stat">
                  <b>{status.indexed}</b>
                  <span>{t('studio.statReady')}</span>
                </div>
                <div className="st-stat">
                  <b>{status.approved + status.quarantined}</b>
                  <span>{t('studio.statWaiting')}</span>
                </div>
                <div className="st-stat">
                  <b>{status.failed}</b>
                  <span>{t('studio.statFailed')}</span>
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {message && (
        <p className="st-note" role="status">
          {message}
        </p>
      )}
    </div>
  );
}
