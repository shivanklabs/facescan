// GalleryPage: the guest's own released photos (web copies through short-lived signed URLs),
// a download link per photo (full resolution, metadata stripped), and the passkey offer.
import { useEffect, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PasskeyOffer } from '@/features/auth/PasskeyOffer';
import { api, apiUrl } from '@/lib/serverApi';

interface GalleryPhoto {
  photo_id: string;
  url: string;
  download_path: string;
}

interface ClaimNotice {
  claim_id: string;
  photo_id: string;
  status: string;
  reason: string;
  reviewed_at: string | null;
}

interface GalleryData {
  photos: GalleryPhoto[];
  rejections?: ClaimNotice[];
}

export default function GalleryPage() {
  const { t } = useTranslation();
  const { eventId = '' } = useParams();
  const location = useLocation();
  const someMissing = (location.state as { someMissing?: boolean } | null)?.someMissing === true;
  const [photos, setPhotos] = useState<GalleryPhoto[] | null>(null);
  const [rejections, setRejections] = useState<ClaimNotice[]>([]);
  const [error, setError] = useState(false);
  const [submittingNotMe, setSubmittingNotMe] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  useEffect(() => {
    api<GalleryData>(`/events/${eventId}/gallery`)
      .then((data) => {
        setPhotos(data.photos);
        setRejections(data.rejections || []);
      })
      .catch(() => setError(true));
  }, [eventId]);

  async function handleNotMe(photoId: string) {
    if (!window.confirm(t('gallery.notMeConfirm'))) return;
    setSubmittingNotMe(photoId);
    setActionMessage(null);
    try {
      await api(`/events/${eventId}/gallery/${photoId}/not-me`, { method: 'POST' });
      setPhotos((prev) => (prev ? prev.filter((p) => p.photo_id !== photoId) : []));
      setActionMessage(t('gallery.notMeDone'));
    } catch {
      setError(true);
    } finally {
      setSubmittingNotMe(null);
    }
  }

  return (
    <>
      <h1>{t('gallery.title')}</h1>
      {error && <p role="alert">{t('common.error')}</p>}
      {actionMessage && (
        <p role="status" className="banner" style={{ marginBottom: '16px' }}>
          {actionMessage}
        </p>
      )}
      {photos === null && !error && <p>{t('common.loading')}</p>}
      {rejections.length > 0 && (
        <div role="status" className="banner claim-rejections" style={{ marginBottom: '16px' }}>
          {rejections.map((rej) => (
            <p key={rej.claim_id}>{t('gallery.rejectionNotice')}</p>
          ))}
        </div>
      )}
      {photos !== null && photos.length === 0 && <p>{t('gallery.empty')}</p>}
      {someMissing && <p className="banner">{t('gallery.missing')}</p>}
      <ul className="gallery">
        {photos?.map((photo) => (
          <li key={photo.photo_id}>
            <img src={photo.url} alt="" loading="lazy" />
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '6px' }}>
              <a href={apiUrl(photo.download_path)} download>
                {t('gallery.download')}
              </a>
              <button
                type="button"
                onClick={() => void handleNotMe(photo.photo_id)}
                disabled={submittingNotMe === photo.photo_id}
                style={{ fontSize: '0.85rem', padding: '2px 8px' }}
              >
                {t('gallery.notMe')}
              </button>
            </div>
          </li>
        ))}
      </ul>
      <p>
        <Link to={`/events/${eventId}/selfie`}>{t('gallery.searchAgain')}</Link>
      </p>
      <PasskeyOffer />
    </>
  );
}
