// UploadStatusPage: how far an event's photos are: ready, waiting, failed (with Retry) and
// near-duplicates. Refreshes every few seconds while the page is open.
import { useCallback, useEffect, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useCurrentUser } from '@/features/auth/useCurrentUser';
import { api, ApiError } from '@/lib/serverApi';

interface FailedPhoto {
  id: string;
  filename: string;
  created_at: string;
}

interface ProcessingStatus {
  quarantined: number;
  approved: number;
  indexed: number;
  failed: number;
  near_duplicates: number;
  failed_photos: FailedPhoto[];
}

const POLL_MS = 4000;

/**
 * Purpose: processing status page for owners and photographers.
 * Inputs: eventId (route). Outputs: page element.
 * Side effects: polls GET processing-status; POST retry for a failed photo.
 */
export default function UploadStatusPage() {
  const { t } = useTranslation();
  const { eventId = '' } = useParams();
  const { me, loading } = useCurrentUser();
  const [status, setStatus] = useState<ProcessingStatus | null>(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [retrying, setRetrying] = useState<string | null>(null);

  const load = useCallback(() => {
    api<ProcessingStatus>(`/events/${eventId}/processing-status`)
      .then((value) => {
        setStatus(value);
        setError('');
      })
      .catch((err: unknown) =>
        setError(
          err instanceof ApiError && err.status === 403
            ? t('owner.needStrongSignIn')
            : t('common.error'),
        ),
      );
  }, [eventId, t]);

  useEffect(() => {
    if (!me) return;
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [me, load]);

  async function retry(photoId: string) {
    setRetrying(photoId);
    setMessage('');
    try {
      await api(`/events/${eventId}/photos/${photoId}/retry`, { method: 'POST' });
      setMessage(t('status.retryQueued'));
      load();
    } catch {
      setMessage(t('status.retryFailed'));
    } finally {
      setRetrying(null);
    }
  }

  if (!loading && !me) return <Navigate to="/" replace />;

  const total = status ? status.quarantined + status.approved + status.indexed + status.failed : 0;
  const ready = status && total > 0 ? Math.round((status.indexed / total) * 100) : 0;

  return (
    <section className="st-page">
      <div>
        <Link to="/studio" className="st-back">
          ← {t('owner.backToStudio')}
        </Link>
        <p className="st-eyebrow">{t('status.eyebrow')}</p>
        <h1>{t('status.title')}</h1>
      </div>
      {error && <p className="st-alert">{error}</p>}
      {message && (
        <p className="st-note" role="status">
          {message}
        </p>
      )}
      {status && (
        <>
          <div className="st-card st-claim">
            <p className="st-count">{t('studio.readyPercent', { percent: ready })}</p>
            <div className="st-progress" aria-label={t('studio.readyPercent', { percent: ready })}>
              <div style={{ width: `${ready}%` }} />
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
          </div>
          {status.near_duplicates > 0 && (
            <p className="st-note">
              {t('status.nearDuplicates', { count: status.near_duplicates })}
            </p>
          )}
          {status.failed_photos.length > 0 && (
            <div className="st-card st-claim">
              <h2>{t('status.failedTitle')}</h2>
              <ul className="st-list">
                {status.failed_photos.map((photo) => (
                  <li key={photo.id} className="st-list-row">
                    <span>
                      <strong>{photo.filename}</strong>
                      <span className="st-count">
                        {new Date(photo.created_at).toLocaleString()}
                      </span>
                    </span>
                    <button
                      type="button"
                      className="st-btn st-btn-ghost st-btn-small"
                      disabled={retrying === photo.id}
                      onClick={() => void retry(photo.id)}
                    >
                      {t('status.retry')}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  );
}
