// MyPhotosPage: the events dashboard. One card per event I was found in (only those events),
// with a cover photo and how many photos; a card opens that event's 3D gallery.
// Friendly empty state when nothing matched yet.
import { useCallback, useEffect, useState } from 'react';
import { Link, Navigate, useLocation, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useCurrentUser } from '@/features/auth/useCurrentUser';
import { api } from '@/lib/serverApi';

interface Photo {
  photo_id: string;
  url: string;
}

interface EventPhotos {
  event_id: string;
  event_name: string;
  photos: Photo[];
}

/**
 * Purpose: the events dashboard. Inputs: optional ?event= (older links) which now opens that
 *   event's gallery directly. Outputs: page element.
 * Side effects: GET /discover/photos.
 */
export default function MyPhotosPage() {
  const { t } = useTranslation();
  const location = useLocation();
  const [params] = useSearchParams();
  const legacyEvent = params.get('event');
  const scanState = location.state as { scanned?: boolean } | null;
  const { me, loading: meLoading } = useCurrentUser();
  const [events, setEvents] = useState<EventPhotos[] | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    api<{ events: EventPhotos[] }>('/discover/photos')
      .then((value) => setEvents(value.events.filter((e) => e.photos.length > 0)))
      .catch(() => setError(t('photos.loadFailed')));
  }, [t]);

  useEffect(() => {
    if (me && !legacyEvent) load();
  }, [me, legacyEvent, load]);

  if (!meLoading && !me) return <Navigate to="/" replace />;
  if (legacyEvent) {
    return (
      <Navigate to={`/photos/${encodeURIComponent(legacyEvent)}`} replace state={location.state} />
    );
  }

  if (error && !events) {
    return (
      <section className="st-empty">
        <div>
          <p className="st-alert" role="alert">
            {error}
          </p>
          <button type="button" className="st-btn st-btn-ghost" onClick={load}>
            {t('photos.retry')}
          </button>
        </div>
      </section>
    );
  }

  if (!events) {
    return (
      <section className="st-searching" aria-busy="true">
        <div className="st-aperture" aria-hidden="true" />
      </section>
    );
  }

  const total = events.reduce((sum, group) => sum + group.photos.length, 0);

  if (total === 0) {
    return (
      <section className="st-empty">
        <div>
          <div className="st-empty-frame" aria-hidden="true">
            ✦
          </div>
          <h1>{scanState?.scanned ? t('photos.noneTitle') : t('photos.firstTitle')}</h1>
          <p>{scanState?.scanned ? t('photos.noneText') : t('photos.firstText')}</p>
          <Link to="/scan" className="st-btn st-btn-primary">
            {scanState?.scanned ? t('photos.scanAgain') : t('photos.scanNow')}
          </Link>
        </div>
      </section>
    );
  }

  return (
    <>
      <div className="st-gallery-head">
        <div>
          <p className="st-eyebrow">{t('photos.eyebrow')}</p>
          <h1>{t('photos.title')}</h1>
          <p className="st-count">{t('photos.count', { count: total, events: events.length })}</p>
        </div>
        <div className="st-bar-actions">
          <Link to="/scan" className="st-btn st-btn-ghost">
            {t('photos.scanAgain')}
          </Link>
        </div>
      </div>
      <div className="st-event-cards">
        {events.map((group, i) => (
          <Link
            key={group.event_id}
            to={`/photos/${group.event_id}`}
            className="st-event-tile"
            style={{ animationDelay: `${Math.min(i, 8) * 60}ms` }}
          >
            <span className="st-event-cover" aria-hidden="true">
              {group.photos.slice(0, 3).map((photo, k) => (
                <img key={photo.photo_id} src={photo.url} alt="" loading="lazy" data-layer={k} />
              ))}
            </span>
            <strong>{group.event_name}</strong>
            <span className="st-count">{t('photos.inEvent', { count: group.photos.length })}</span>
          </Link>
        ))}
      </div>
    </>
  );
}
