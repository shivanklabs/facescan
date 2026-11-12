// AdminHealthPage: the platform admin's health view in the site design: disk space, the last
// backup, and per-event counts (photos by stage, indexing progress, job failures, pending
// claims, "Not me"). Counts only: no photos, faces or personal data are shown.
import { useCallback, useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { StaffKeysCard } from '@/features/auth/StaffKeyForm';
import { useCurrentUser } from '@/features/auth/useCurrentUser';
import { api, ApiError } from '@/lib/serverApi';

interface EventHealth {
  event_id: string;
  name: string;
  status: string;
  photos_quarantined: number;
  photos_approved: number;
  photos_indexed: number;
  photos_failed: number;
  photos_total: number;
  indexing_progress_pct: number;
  job_failures: number;
  pending_claims: number;
  not_me_count: number;
}

interface SystemHealth {
  disk_free_bytes: number;
  disk_total_bytes: number;
  disk_used_percent: number;
  last_backup_at: string | null;
}

interface HealthResponse {
  events: EventHealth[];
  system: SystemHealth;
}

const BACKUP_WARN_HOURS = 24;
const DISK_WARN_PERCENT = 85;
const REFRESH_MS = 30_000;

/**
 * Purpose: bytes as a short human size (GB with one decimal, or MB).
 * Inputs: byte count. Outputs: text. Side effects: none.
 */
export function formatBytes(bytes: number): string {
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
}

/**
 * Purpose: whether the last backup is missing or older than a day (shown as a warning).
 * Inputs: ISO time or null, now (ms). Outputs: boolean. Side effects: none.
 */
export function backupIsStale(lastBackupAt: string | null, now: number = Date.now()): boolean {
  if (!lastBackupAt) return true;
  return now - new Date(lastBackupAt).getTime() > BACKUP_WARN_HOURS * 3600 * 1000;
}

/**
 * Purpose: admin health page.
 * Inputs: none (reads the session). Outputs: page element.
 * Side effects: GET /admin/health now and every 30 s.
 */
export default function AdminHealthPage() {
  const { t } = useTranslation();
  const { me, loading } = useCurrentUser();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    api<HealthResponse>('/admin/health')
      .then((value) => {
        setHealth(value);
        setError('');
      })
      .catch((err: unknown) =>
        setError(
          err instanceof ApiError && err.status === 403
            ? t('health.notAllowed')
            : t('common.error'),
        ),
      );
  }, [t]);

  useEffect(() => {
    if (!me) return;
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [me, load]);

  if (!loading && !me) return <Navigate to="/" replace />;

  const system = health?.system;
  const stale = system ? backupIsStale(system.last_backup_at) : false;

  return (
    <section className="st-page st-page-wide">
      <div>
        <p className="st-eyebrow">{t('health.eyebrow')}</p>
        <h1>{t('health.title')}</h1>
        <p className="st-lead-small">{t('health.intro')}</p>
      </div>
      {error && <p className="st-alert">{error}</p>}
      {system && (
        <div className="st-stats">
          <div
            className={`st-stat ${system.disk_used_percent >= DISK_WARN_PERCENT ? 'is-warn' : ''}`}
          >
            <b>{formatBytes(system.disk_free_bytes)}</b>
            <span>
              {t('health.diskFree', {
                used: Math.round(system.disk_used_percent),
                total: formatBytes(system.disk_total_bytes),
              })}
            </span>
          </div>
          <div className={`st-stat ${stale ? 'is-warn' : ''}`}>
            <b>
              {system.last_backup_at
                ? new Date(system.last_backup_at).toLocaleString()
                : t('health.never')}
            </b>
            <span>{stale ? t('health.backupOld') : t('health.backupOk')}</span>
          </div>
          <div className="st-stat">
            <b>{health.events.length}</b>
            <span>{t('health.events')}</span>
          </div>
        </div>
      )}
      {health && <StaffKeysCard roles={['organiser', 'admin']} />}
      {health?.events.length === 0 && <p className="st-note">{t('health.noEvents')}</p>}
      {health && health.events.length > 0 && (
        <div className="st-events">
          {health.events.map((event) => (
            <div key={event.event_id} className="st-card st-claim">
              <h2>{event.name}</h2>
              <span className="st-pill">
                {t(`studio.status.${event.status}`, { defaultValue: event.status })}
              </span>
              <div style={{ width: '100%' }}>
                <p className="st-count">
                  {t('studio.readyPercent', { percent: Math.round(event.indexing_progress_pct) })}
                </p>
                <div className="st-progress">
                  <div style={{ width: `${Math.min(100, event.indexing_progress_pct)}%` }} />
                </div>
              </div>
              <ul className="st-list st-health-list">
                <li>
                  {t('studio.statTotal')}: <b>{event.photos_total}</b>
                </li>
                <li>
                  {t('studio.statReady')}: <b>{event.photos_indexed}</b>
                </li>
                <li>
                  {t('studio.statWaiting')}:{' '}
                  <b>{event.photos_quarantined + event.photos_approved}</b>
                </li>
                <li className={event.photos_failed > 0 ? 'is-warn' : ''}>
                  {t('studio.statFailed')}: <b>{event.photos_failed}</b>
                </li>
                <li className={event.job_failures > 0 ? 'is-warn' : ''}>
                  {t('health.jobFailures')}: <b>{event.job_failures}</b>
                </li>
                <li>
                  {t('health.pendingClaims')}: <b>{event.pending_claims}</b>
                </li>
                <li>
                  {t('health.notMe')}: <b>{event.not_me_count}</b>
                </li>
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
