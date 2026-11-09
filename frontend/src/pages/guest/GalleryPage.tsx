// GalleryPage: the guest's own released photos (web copies through short-lived signed URLs),
// a download link per photo (full resolution, metadata stripped), and the passkey offer.
import { useEffect, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PasskeyOffer } from '@/features/auth/PasskeyOffer';
import { api, apiUrl } from '@/lib/apiClient';

interface GalleryPhoto {
  photo_id: string;
  url: string;
  download_path: string;
}

export default function GalleryPage() {
  const { t } = useTranslation();
  const { eventId = '' } = useParams();
  const location = useLocation();
  const someMissing = (location.state as { someMissing?: boolean } | null)?.someMissing === true;
  const [photos, setPhotos] = useState<GalleryPhoto[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    api<{ photos: GalleryPhoto[] }>(`/events/${eventId}/gallery`)
      .then((data) => setPhotos(data.photos))
      .catch(() => setError(true));
  }, [eventId]);

  return (
    <>
      <h1>{t('gallery.title')}</h1>
      {error && <p role="alert">{t('common.error')}</p>}
      {photos === null && !error && <p>{t('common.loading')}</p>}
      {photos !== null && photos.length === 0 && <p>{t('gallery.empty')}</p>}
      {someMissing && <p className="banner">{t('gallery.missing')}</p>}
      <ul className="gallery">
        {photos?.map((photo) => (
          <li key={photo.photo_id}>
            <img src={photo.url} alt="" loading="lazy" />
            <a href={apiUrl(photo.download_path)} download>
              {t('gallery.download')}
            </a>
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
