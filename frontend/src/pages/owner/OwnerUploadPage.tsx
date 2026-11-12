// OwnerUploadPage (milestone 7): the owner's one admin page. Create an event, drop photos, watch
// them become searchable. Uploads go in small batches into one collection, which is then
// approved so the worker indexes it (no separate approval step for the owner).
// Uploads survive bad connections: every request is retried a few times; files over 5 MB go
// in 5 MB pieces that resume after a reload; dropping the same photos again skips the ones
// the server already has (it recognises them by their SHA-256).
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useCurrentUser } from '@/features/auth/useCurrentUser';
import { registerPasskey, signInWithPasskey } from '@/lib/passkeys';
import { api, ApiError } from '@/lib/serverApi';

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
const CHUNK_BYTES = 5 * 1024 * 1024; // must match the server's CHUNK_SIZE
const RETRIES = 3;

interface ChunkInit {
  upload_id: string;
  collection_id: string;
  received_chunks: number[];
}

/**
 * Purpose: run a request again after a short wait when the network or server failed
 *   (no connection, timeout, 429 too many requests, 5xx). Other errors fail at once.
 * Inputs: the request, an optional wait function (tests). Outputs: the request's result.
 * Side effects: waits 2 s, 4 s, 8 s between attempts.
 */
export async function withRetry<T>(
  work: () => Promise<T>,
  wait: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
): Promise<T> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await work();
    } catch (error) {
      const status = error instanceof ApiError ? error.status : 0;
      const retryable = status === 0 || status === 429 || status >= 500;
      if (!retryable || attempt >= RETRIES) throw error;
      await wait(2000 * 2 ** attempt);
    }
  }
}

/**
 * Purpose: SHA-256 of a file as lower-case hex (the server checks it after the last piece).
 * Inputs: file. Outputs: hex string. Side effects: reads the file into memory once.
 */
async function sha256Hex(file: Blob): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Purpose: remember / forget the upload id of a big file so a reload can resume it.
 * Inputs: key, value. Outputs: stored value or null. Side effects: localStorage (optional:
 *   private windows may block it; then a reload simply starts that file again).
 */
function readResume(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
function writeResume(key: string, value: string | null): void {
  try {
    if (value) window.localStorage.setItem(key, value);
    else window.localStorage.removeItem(key);
  } catch {
    // storage blocked: resume after reload is not available, uploading still works
  }
}

/**
 * Purpose: upload one big file in 5 MB pieces, skipping pieces the server already has.
 * Inputs: event id, file, collection id to join (or null). Outputs: the collection id used
 *   and whether the server already had this photo.
 * Side effects: upload session, chunk and complete requests; localStorage resume entry.
 */
async function uploadInChunks(
  eventId: string,
  file: File,
  collectionId: string | null,
): Promise<{ collectionId: string; skipped: boolean }> {
  const sha = await sha256Hex(file);
  const total = Math.max(1, Math.ceil(file.size / CHUNK_BYTES));
  const resumeKey = `fs-upload:${eventId}:${sha}`;
  let uploadId = readResume(resumeKey);
  let received: number[] = [];
  if (uploadId) {
    try {
      const state = await api<{ status: string; received_chunks: number[] }>(
        `/events/${eventId}/uploads/${uploadId}`,
      );
      if (state.status === 'in_progress') received = state.received_chunks;
      else uploadId = null;
    } catch {
      uploadId = null;
    }
  }
  if (!uploadId) {
    const init = await withRetry(() =>
      api<ChunkInit>(`/events/${eventId}/uploads/init`, {
        json: {
          collection_id: collectionId,
          filename: file.name.slice(0, 255),
          byte_size: file.size,
          total_chunks: total,
          expected_sha256: sha,
        },
      }),
    );
    uploadId = init.upload_id;
    received = init.received_chunks;
    writeResume(resumeKey, uploadId);
  }
  const id = uploadId;
  for (let index = 0; index < total; index += 1) {
    if (received.includes(index)) continue;
    const part = file.slice(index * CHUNK_BYTES, (index + 1) * CHUNK_BYTES);
    await withRetry(() =>
      api(`/events/${eventId}/uploads/${id}/chunk?chunk_index=${index}`, {
        body: part,
        headers: { 'Content-Type': 'application/octet-stream' },
        timeoutMs: 120_000,
      }),
    );
  }
  const done = await withRetry(() =>
    api<{ collection_id: string; already_uploaded: boolean }>(
      `/events/${eventId}/uploads/${id}/complete`,
      { method: 'POST', timeoutMs: 120_000 },
    ),
  );
  writeResume(resumeKey, null);
  return { collectionId: done.collection_id, skipped: done.already_uploaded };
}
const ACCEPTED = ['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif'];
const POLL_MS = 4000;

/**
 * Purpose: owner page: events list, new event, drag-and-drop upload with progress.
 * Inputs: none. Outputs: page element.
 * Side effects: creates events, uploads photos, approves collections, polls status.
 */
export default function OwnerUploadPage() {
  const { t } = useTranslation();
  const { me, loading, reload } = useCurrentUser();
  const [passkeyError, setPasskeyError] = useState('');
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
        // Organisers see their events; photographers see the events they upload to.
        const mine = value.events.filter(
          (e) => e.roles.includes('organiser') || e.roles.includes('photographer'),
        );
        setEvents(mine);
        setActiveId((current) => current ?? mine[0]?.event_id ?? null);
      })
      .catch(() => setMessage(t('studio.loadFailed')));
  }, [t]);

  useEffect(() => {
    if (me?.can_create_events || me?.is_staff) loadEvents();
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
  if (!loading && me && !me.can_create_events && !me.is_staff) {
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
    const isOrganiser =
      events.find((e) => e.event_id === activeId)?.roles.includes('organiser') ?? false;
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
    let done = 0;
    const small = files.filter((f) => f.size <= CHUNK_BYTES);
    const big = files.filter((f) => f.size > CHUNK_BYTES);
    try {
      for (let i = 0; i < small.length; i += BATCH_SIZE) {
        const batch = small.slice(i, i + BATCH_SIZE);
        const joinId = collectionId;
        const result = await withRetry(() => {
          // A fresh form each attempt: a request body can be sent only once.
          const form = new FormData();
          if (joinId) form.append('collection_id', joinId);
          for (const file of batch) form.append('files', file, file.name);
          return api<UploadResult>(`/events/${activeId}/photos`, {
            body: form,
            timeoutMs: 300_000,
          });
        });
        collectionId = result.collection_id;
        skipped += result.already_uploaded.length;
        done += batch.length;
        setProgress({ done, total: files.length });
      }
      for (const file of big) {
        const result = await uploadInChunks(activeId, file, collectionId);
        collectionId = result.collectionId;
        if (result.skipped) skipped += 1;
        done += 1;
        setProgress({ done, total: files.length });
      }
      // Organisers' uploads are approved at once; a photographer's wait for the organiser
      // (Upload status page), as the server requires.
      if (collectionId && isOrganiser) {
        const approveId = collectionId;
        await withRetry(() =>
          api(`/events/${activeId}/collections/${approveId}/approve`, { method: 'POST' }),
        );
      }
      setMessage(
        !isOrganiser
          ? t('studio.uploadedForApproval', { count: files.length - skipped })
          : skipped > 0
            ? t('studio.uploadedSkipped', { count: files.length - skipped, skipped })
            : t('studio.uploaded', { count: files.length }),
      );
    } catch (error) {
      if (collectionId && isOrganiser) {
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
  const activeIsOrganiser = active?.roles.includes('organiser') ?? false;
  const total = status ? status.quarantined + status.approved + status.indexed + status.failed : 0;
  const ready = status && total > 0 ? Math.round((status.indexed / total) * 100) : 0;

  return (
    <div className="st-admin">
      <div>
        <p className="st-eyebrow">{t('studio.eyebrow')}</p>
        <h1>{t('studio.title')}</h1>
      </div>

      {/* An email-link session is not strong enough for staff actions: add or use a passkey. */}
      {me?.auth_method === 'magic_link' && (
        <div className="st-card st-claim">
          <p>{t('staff.passkeyNeeded')}</p>
          <button
            type="button"
            className="st-btn st-btn-primary"
            onClick={() => {
              setPasskeyError('');
              (me.has_passkey ? signInWithPasskey() : registerPasskey().then(signInWithPasskey))
                .then(() => reload())
                .catch(() => setPasskeyError(t('common.error')));
            }}
          >
            {me.has_passkey ? t('passkey.useIt') : t('passkey.add')}
          </button>
          {passkeyError && <p className="st-alert">{passkeyError}</p>}
        </div>
      )}

      {me?.can_create_events && (
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
      )}

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
          <nav className="st-form-row" aria-label={t('owner.eventLinks')}>
            {activeIsOrganiser && (
              <Link
                to={`/studio/events/${active.event_id}/settings`}
                className="st-btn st-btn-ghost st-btn-small"
              >
                {t('owner.settings')}
              </Link>
            )}
            <Link
              to={`/studio/events/${active.event_id}/status`}
              className="st-btn st-btn-ghost st-btn-small"
            >
              {t('owner.status')}
            </Link>
            {activeIsOrganiser && (
              <Link
                to={`/studio/events/${active.event_id}/claims`}
                className="st-btn st-btn-ghost st-btn-small"
              >
                {t('owner.claims')}
              </Link>
            )}
          </nav>
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
