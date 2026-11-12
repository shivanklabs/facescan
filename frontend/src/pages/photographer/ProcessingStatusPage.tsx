// ProcessingStatusPage: Photographer dashboard showing photo processing counts and failed photos with retry.
// Requires passkey session.
import { useCallback, useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PasskeyGate } from '@/features/auth/PasskeyGate';
import { api } from '@/lib/serverApi';

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

function ProcessingStatusView({ eventId }: { eventId: string }) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<ProcessingStatus | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState(false);

  const loadStatus = useCallback(() => {
    api<ProcessingStatus>(`/events/${eventId}/processing-status`)
      .then((data) => {
        setStatus(data);
        setError(false);
      })
      .catch(() => setError(true));
  }, [eventId]);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  async function handleRetry(photoId: string) {
    setRetryingId(photoId);
    setMessage(null);
    try {
      await api<{ photo_id: string; status: string; message: string }>(
        `/events/${eventId}/photos/${photoId}/retry`,
        { method: 'POST' },
      );
      setMessage('Photo queued for retry.');
      loadStatus();
    } catch {
      setMessage('Failed to retry photo.');
    } finally {
      setRetryingId(null);
    }
  }

  if (error) {
    return <p role="alert">{t('common.error')}</p>;
  }

  if (!status) {
    return <p>{t('common.loading')}</p>;
  }

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <h2>Processing Status</h2>
        <Link to={`/photographer/events/${eventId}/upload`} className="link">
          Upload Photos
        </Link>
      </div>

      <div
        className="grid-summary"
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
          gap: '1rem',
          margin: '1rem 0',
        }}
      >
        <div
          className="metric-box"
          style={{ padding: '0.75rem', border: '1px solid #ccc', borderRadius: '4px' }}
        >
          <h3>Quarantined</h3>
          <p style={{ fontSize: '1.5rem', margin: 0 }}>{status.quarantined}</p>
        </div>
        <div
          className="metric-box"
          style={{ padding: '0.75rem', border: '1px solid #ccc', borderRadius: '4px' }}
        >
          <h3>Approved</h3>
          <p style={{ fontSize: '1.5rem', margin: 0 }}>{status.approved}</p>
        </div>
        <div
          className="metric-box"
          style={{ padding: '0.75rem', border: '1px solid #ccc', borderRadius: '4px' }}
        >
          <h3>Indexed</h3>
          <p style={{ fontSize: '1.5rem', margin: 0 }}>{status.indexed}</p>
        </div>
        <div
          className="metric-box"
          style={{
            padding: '0.75rem',
            border: '1px solid #ccc',
            borderRadius: '4px',
            borderColor: status.failed > 0 ? '#d9534f' : '#ccc',
          }}
        >
          <h3>Failed</h3>
          <p
            style={{
              fontSize: '1.5rem',
              margin: 0,
              color: status.failed > 0 ? '#d9534f' : 'inherit',
            }}
          >
            {status.failed}
          </p>
        </div>
        <div
          className="metric-box"
          style={{ padding: '0.75rem', border: '1px solid #ccc', borderRadius: '4px' }}
        >
          <h3>Near-duplicates</h3>
          <p style={{ fontSize: '1.5rem', margin: 0 }}>{status.near_duplicates}</p>
        </div>
      </div>

      {message && (
        <p role="status" className="banner">
          {message}
        </p>
      )}

      <h3>Failed Photos ({status.failed_photos.length})</h3>
      {status.failed_photos.length === 0 ? (
        <p>No failed photos. All files processed successfully or are pending approval.</p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: '0.5rem' }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #ddd', textAlign: 'left' }}>
              <th style={{ padding: '8px' }}>Filename / Object Key</th>
              <th style={{ padding: '8px' }}>Upload Time</th>
              <th style={{ padding: '8px' }}>Action</th>
            </tr>
          </thead>
          <tbody>
            {status.failed_photos.map((photo) => (
              <tr key={photo.id} style={{ borderBottom: '1px solid #eee' }}>
                <td style={{ padding: '8px', wordBreak: 'break-all' }}>{photo.filename}</td>
                <td style={{ padding: '8px' }}>{new Date(photo.created_at).toLocaleString()}</td>
                <td style={{ padding: '8px' }}>
                  <button
                    type="button"
                    onClick={() => void handleRetry(photo.id)}
                    disabled={retryingId === photo.id}
                  >
                    {retryingId === photo.id ? 'Retrying…' : 'Retry'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function ProcessingStatusPage() {
  const { eventId } = useParams<{ eventId: string }>();
  if (!eventId) return null;
  return (
    <PasskeyGate next={`/photographer/events/${eventId}/status`}>
      <ProcessingStatusView eventId={eventId} />
    </PasskeyGate>
  );
}
