// AdminDashboardPage: Operational health monitoring for platform admins.
// Strictly displays aggregate counts, system metrics, and disk/backup state. No personal data.
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { PasskeyGate } from '@/features/auth/PasskeyGate';
import { api, ApiError } from '@/lib/serverApi';

interface EventHealthSummary {
  event_id: string;
  name: string;
  country: string;
  status: string;
  created_at: string;
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

interface SystemHealthSummary {
  disk_free_bytes: number;
  disk_total_bytes: number;
  disk_used_percent: number;
  last_backup_at: string | null;
}

interface AdminHealthResponse {
  events: EventHealthSummary[];
  system: SystemHealthSummary;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  const gb = mb / 1024;
  return `${gb.toFixed(2)} GB`;
}

function AdminHealthView() {
  const [data, setData] = useState<AdminHealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api<AdminHealthResponse>('/admin/health')
      .then((res) => {
        if (active) {
          setData(res);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (active) {
          if (err instanceof ApiError && err.status === 403) {
            setError('Access denied. Administrator privileges required.');
          } else {
            setError('Failed to load platform health data.');
          }
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  if (loading) return <p>Loading admin health data...</p>;
  if (error) return <div className="banner warn">{error}</div>;
  if (!data) return null;

  const { system, events } = data;

  return (
    <div>
      <h1>Platform Health Dashboard</h1>

      {/* System Operational Metrics */}
      <section className="card">
        <h2>Host & Storage Metrics</h2>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
            gap: '16px',
          }}
        >
          <div>
            <strong>Disk Space Free:</strong>
            <p>
              {formatBytes(system.disk_free_bytes)} / {formatBytes(system.disk_total_bytes)}
            </p>
            <div
              style={{ background: '#e5e7eb', borderRadius: '4px', height: '10px', width: '100%' }}
            >
              <div
                style={{
                  background: system.disk_used_percent > 85 ? '#ef4444' : '#3b82f6',
                  height: '100%',
                  borderRadius: '4px',
                  width: `${Math.min(100, Math.max(0, system.disk_used_percent))}%`,
                }}
              />
            </div>
            <span className="hint">{system.disk_used_percent}% used</span>
          </div>

          <div>
            <strong>Latest Backup:</strong>
            <p>
              {system.last_backup_at
                ? new Date(system.last_backup_at).toLocaleString()
                : 'No backups found'}
            </p>
          </div>
        </div>
      </section>

      {/* Per-Event Status and Counters */}
      <section className="card">
        <h2>Events ({events.length})</h2>
        {events.length === 0 ? (
          <p className="hint">No events created yet.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {events.map((ev) => (
              <div
                key={ev.event_id}
                style={{
                  border: '1px solid #d1d5db',
                  borderRadius: '6px',
                  padding: '12px',
                  backgroundColor: '#f9fafb',
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    flexWrap: 'wrap',
                  }}
                >
                  <h3 style={{ margin: '0 0 4px' }}>
                    {ev.name} <span className="hint">({ev.country})</span>
                  </h3>
                  <span
                    style={{
                      padding: '2px 8px',
                      borderRadius: '4px',
                      fontSize: '0.85rem',
                      fontWeight: 'bold',
                      background: ev.status === 'searchable' ? '#dcfce7' : '#f3f4f6',
                      color: ev.status === 'searchable' ? '#166534' : '#374151',
                    }}
                  >
                    {ev.status}
                  </span>
                </div>

                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
                    gap: '10px',
                    marginTop: '8px',
                    fontSize: '0.9rem',
                  }}
                >
                  <div>
                    <strong>Total Photos:</strong> {ev.photos_total}
                  </div>
                  <div>
                    <strong>Quarantined:</strong> {ev.photos_quarantined}
                  </div>
                  <div>
                    <strong>Approved:</strong> {ev.photos_approved}
                  </div>
                  <div>
                    <strong>Indexed:</strong> {ev.photos_indexed}
                  </div>
                  <div>
                    <strong>Failed Photos:</strong> {ev.photos_failed}
                  </div>
                  <div>
                    <strong>Indexing:</strong> {ev.indexing_progress_pct}%
                  </div>
                  <div>
                    <strong>Job Failures:</strong>{' '}
                    <span
                      style={{
                        color: ev.job_failures > 0 ? '#b91c1c' : 'inherit',
                        fontWeight: ev.job_failures > 0 ? 'bold' : 'normal',
                      }}
                    >
                      {ev.job_failures}
                    </span>
                  </div>
                  <div>
                    <strong>Pending Claims:</strong>{' '}
                    {ev.pending_claims > 0 ? (
                      <Link to={`/organiser/events/${ev.event_id}/claims`}>
                        {ev.pending_claims} pending
                      </Link>
                    ) : (
                      '0'
                    )}
                  </div>
                  <div>
                    <strong>"Not me" Reports:</strong> {ev.not_me_count}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

export default function AdminDashboardPage() {
  return (
    <PasskeyGate next="/admin">
      <AdminHealthView />
    </PasskeyGate>
  );
}
