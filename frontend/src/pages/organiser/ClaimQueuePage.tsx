// ClaimQueuePage: event organiser reviews claims that failed automatic face match.
// Shows the photo, the claimed face marked, claimant email, and reason.
// Actions: Approve (releases to guest gallery with source='claim') or Reject.
import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PasskeyGate } from '@/features/auth/PasskeyGate';
import { api } from '@/lib/apiClient';

interface FaceBBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface ClaimItem {
  claim_id: string;
  photo_id: string;
  photo_url: string;
  face_id: string | null;
  face_bbox: FaceBBox | null;
  claimant_email: string;
  reason: string;
  status: string;
  created_at: string;
}

function ClaimQueue({ eventId }: { eventId: string }) {
  const { t } = useTranslation();
  const [claims, setClaims] = useState<ClaimItem[] | null>(null);
  const [error, setError] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [processingId, setProcessingId] = useState<string | null>(null);

  const loadClaims = useCallback(() => {
    api<{ claims: ClaimItem[] }>(`/events/${eventId}/claims`)
      .then((data) => setClaims(data.claims))
      .catch(() => setError(true));
  }, [eventId]);

  useEffect(() => {
    loadClaims();
  }, [loadClaims]);

  async function handleReview(claimId: string, action: 'approve' | 'reject') {
    setProcessingId(claimId);
    setActionMessage(null);
    try {
      await api<{ claim_id: string; status: string }>(
        `/events/${eventId}/claims/${claimId}/review`,
        {
          method: 'POST',
          body: JSON.stringify({ action }),
        },
      );
      setActionMessage(action === 'approve' ? t('claims.approved') : t('claims.rejected'));
      loadClaims();
    } catch {
      setActionMessage(t('common.error'));
    } finally {
      setProcessingId(null);
    }
  }

  return (
    <div className="claim-queue-page">
      <h1>{t('claims.title')}</h1>
      {error && <p role="alert">{t('common.error')}</p>}
      {actionMessage && (
        <p className="banner" role="status">
          {actionMessage}
        </p>
      )}
      {claims === null && !error && <p>{t('common.loading')}</p>}
      {claims !== null && claims.length === 0 && <p>{t('claims.empty')}</p>}

      <ul className="claim-list" style={{ listStyle: 'none', padding: 0 }}>
        {claims?.map((claim) => (
          <li
            key={claim.claim_id}
            style={{
              border: '1px solid #ccc',
              borderRadius: '8px',
              padding: '16px',
              marginBottom: '20px',
            }}
          >
            <div style={{ position: 'relative', display: 'inline-block', maxWidth: '480px' }}>
              <img
                src={claim.photo_url}
                alt=""
                style={{ maxWidth: '100%', height: 'auto', display: 'block', borderRadius: '4px' }}
              />
              {claim.face_bbox && (
                <div
                  title="Claimed Face"
                  style={{
                    position: 'absolute',
                    border: '3px solid #f59e0b',
                    backgroundColor: 'rgba(245, 158, 11, 0.2)',
                    left: `${Math.max(0, claim.face_bbox.x)}px`,
                    top: `${Math.max(0, claim.face_bbox.y)}px`,
                    width: `${claim.face_bbox.width}px`,
                    height: `${claim.face_bbox.height}px`,
                    pointerEvents: 'none',
                    boxSizing: 'border-box',
                  }}
                />
              )}
            </div>

            <div style={{ marginTop: '12px' }}>
              <p>
                <strong>{t('claims.claimant')}:</strong> {claim.claimant_email}
              </p>
              <p>
                <strong>{t('claims.reason')}:</strong>{' '}
                {claim.reason === 'front_only_scan'
                  ? t('claims.frontOnlyScan')
                  : t('claims.faceCheckFailed')}
              </p>
              <p>
                <small>{new Date(claim.created_at).toLocaleString()}</small>
              </p>
            </div>

            <div style={{ marginTop: '12px', display: 'flex', gap: '12px' }}>
              <button
                type="button"
                disabled={processingId === claim.claim_id}
                onClick={() => handleReview(claim.claim_id, 'approve')}
                style={{
                  backgroundColor: '#10b981',
                  color: 'white',
                  padding: '8px 16px',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer',
                }}
              >
                {t('claims.approve')}
              </button>
              <button
                type="button"
                disabled={processingId === claim.claim_id}
                onClick={() => handleReview(claim.claim_id, 'reject')}
                style={{
                  backgroundColor: '#ef4444',
                  color: 'white',
                  padding: '8px 16px',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer',
                }}
              >
                {t('claims.reject')}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function ClaimQueuePage() {
  const { eventId = '' } = useParams();
  return (
    <PasskeyGate next={`/organiser/events/${eventId}/claims`}>
      <ClaimQueue eventId={eventId} />
    </PasskeyGate>
  );
}
