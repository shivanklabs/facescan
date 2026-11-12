// ClaimReviewPage: guests' photo claims that the live check could not confirm. The organiser
// sees the photo with the claimed face boxed and a per-event label ("Guest #1"), never an
// email, and approves (photo released to that guest) or rejects.
import { useCallback, useEffect, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useCurrentUser } from '@/features/auth/useCurrentUser';
import { api, ApiError } from '@/lib/serverApi';

interface FaceBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface ClaimItem {
  claim_id: string;
  photo_id: string;
  photo_url: string;
  face_bbox: FaceBox | null;
  photo_width: number | null;
  photo_height: number | null;
  claimant_email: string;
  reason: string;
  created_at: string;
}

/**
 * Purpose: the face box in percent of the photo, so it stays on the face at any display size.
 * Inputs: claim. Outputs: CSS position or null when the size or box is unknown.
 * Side effects: none.
 */
export function boxStyle(claim: ClaimItem): Record<string, string> | null {
  const box = claim.face_bbox;
  if (!box || !claim.photo_width || !claim.photo_height) return null;
  return {
    left: `${(box.x / claim.photo_width) * 100}%`,
    top: `${(box.y / claim.photo_height) * 100}%`,
    width: `${(box.width / claim.photo_width) * 100}%`,
    height: `${(box.height / claim.photo_height) * 100}%`,
  };
}

/**
 * Purpose: claim review page for organisers.
 * Inputs: eventId (route). Outputs: page element.
 * Side effects: GET claims; POST review (approve releases the photo to the guest).
 */
export default function ClaimReviewPage() {
  const { t } = useTranslation();
  const { eventId = '' } = useParams();
  const { me, loading } = useCurrentUser();
  const [claims, setClaims] = useState<ClaimItem[] | null>(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    api<{ claims: ClaimItem[] }>(`/events/${eventId}/claims`)
      .then((data) => setClaims(data.claims))
      .catch((err: unknown) =>
        setError(
          err instanceof ApiError && err.status === 403
            ? t('owner.needStrongSignIn')
            : t('common.error'),
        ),
      );
  }, [eventId, t]);

  useEffect(() => {
    if (me) load();
  }, [me, load]);

  async function review(claimId: string, action: 'approve' | 'reject') {
    setBusy(claimId);
    setMessage('');
    try {
      await api(`/events/${eventId}/claims/${claimId}/review`, { json: { action } });
      setMessage(action === 'approve' ? t('review.approved') : t('review.rejected'));
      load();
    } catch {
      setMessage(t('common.error'));
    } finally {
      setBusy(null);
    }
  }

  if (!loading && !me) return <Navigate to="/" replace />;

  return (
    <section className="st-page st-page-wide">
      <div>
        <Link to="/studio" className="st-back">
          ← {t('owner.backToStudio')}
        </Link>
        <p className="st-eyebrow">{t('review.eyebrow')}</p>
        <h1>{t('review.title')}</h1>
        <p className="st-lead-small">{t('review.intro')}</p>
      </div>
      {error && <p className="st-alert">{error}</p>}
      {message && (
        <p className="st-note" role="status">
          {message}
        </p>
      )}
      {claims?.length === 0 && <p className="st-note">{t('review.none')}</p>}
      {claims && claims.length > 0 && (
        <div className="st-candidates">
          {claims.map((claim) => {
            const style = boxStyle(claim);
            return (
              <div key={claim.claim_id} className="st-card st-claim">
                <figure className="st-candidate">
                  {claim.photo_url && <img src={claim.photo_url} alt="" />}
                  {style && <span className="st-face-box is-picked" style={style} />}
                </figure>
                <p>
                  <strong>{claim.claimant_email}</strong>
                  <span className="st-count"> · {new Date(claim.created_at).toLocaleString()}</span>
                </p>
                <div className="st-form-row">
                  <button
                    type="button"
                    className="st-btn st-btn-primary st-btn-small"
                    disabled={busy === claim.claim_id}
                    onClick={() => void review(claim.claim_id, 'approve')}
                  >
                    {t('review.approve')}
                  </button>
                  <button
                    type="button"
                    className="st-btn st-btn-ghost st-btn-small"
                    disabled={busy === claim.claim_id}
                    onClick={() => void review(claim.claim_id, 'reject')}
                  >
                    {t('review.reject')}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
