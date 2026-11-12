// MyPhotosPage (milestone 7): all of my photos, grouped by event, with a full-screen viewer,
// original-quality downloads and "Not me". Shows a friendly empty state when nothing matched.
import { useCallback, useEffect, useState } from 'react';
import { Link, Navigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useCurrentUser } from '@/features/auth/useCurrentUser';
import { api, apiUrl } from '@/lib/serverApi';

interface Photo {
  photo_id: string;
  url: string;
  expires_at: string;
  download_path: string;
}

interface EventPhotos {
  event_id: string;
  event_name: string;
  photos: Photo[];
}

interface Viewing {
  eventIndex: number;
  photoIndex: number;
}

const DOWNLOAD_GAP_MS = 400;

/**
 * Purpose: start a browser download of one original photo.
 * Inputs: the API download path. Outputs: none.
 * Side effects: clicks a temporary same-origin link (the session cookie authorises it).
 */
function downloadOriginal(path: string): void {
  const link = document.createElement('a');
  link.href = apiUrl(path);
  link.download = '';
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
}

/**
 * Purpose: the gallery page. Inputs: none. Outputs: page element.
 * Side effects: loads photos; downloads; "Not me" requests.
 */
export default function MyPhotosPage() {
  const { t } = useTranslation();
  const location = useLocation();
  const scanState = location.state as { scanned?: boolean } | null;
  const { me, loading: meLoading } = useCurrentUser();
  const [events, setEvents] = useState<EventPhotos[] | null>(null);
  const [error, setError] = useState('');
  const [viewing, setViewing] = useState<Viewing | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  const load = useCallback(() => {
    api<{ events: EventPhotos[] }>('/discover/photos')
      .then((value) => setEvents(value.events))
      .catch(() => setError(t('photos.loadFailed')));
  }, [t]);

  useEffect(() => {
    if (me) load();
  }, [me, load]);

  const close = useCallback(() => {
    setViewing(null);
    setMenuOpen(false);
  }, []);

  const step = useCallback(
    (delta: number) => {
      if (!viewing || !events) return;
      const list = events[viewing.eventIndex]?.photos ?? [];
      if (list.length === 0) return;
      setMenuOpen(false);
      setViewing({
        eventIndex: viewing.eventIndex,
        photoIndex: (viewing.photoIndex + delta + list.length) % list.length,
      });
    },
    [viewing, events],
  );

  useEffect(() => {
    if (!viewing) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') close();
      if (event.key === 'ArrowRight') step(1);
      if (event.key === 'ArrowLeft') step(-1);
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [viewing, close, step]);

  if (!meLoading && !me) return <Navigate to="/" replace />;

  async function downloadAll(group: EventPhotos) {
    for (const photo of group.photos) {
      downloadOriginal(photo.download_path);
      await new Promise((resolve) => setTimeout(resolve, DOWNLOAD_GAP_MS));
    }
  }

  async function notMe(eventId: string, photoId: string) {
    try {
      await api(`/events/${eventId}/gallery/${photoId}/not-me`, { method: 'POST' });
      close();
      setEvents(
        (current) =>
          current
            ?.map((group) =>
              group.event_id === eventId
                ? { ...group, photos: group.photos.filter((p) => p.photo_id !== photoId) }
                : group,
            )
            .filter((group) => group.photos.length > 0) ?? null,
      );
    } catch {
      setError(t('photos.notMeFailed'));
    }
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

  const viewedGroup = viewing ? events[viewing.eventIndex] : undefined;
  const viewedPhoto = viewing ? viewedGroup?.photos[viewing.photoIndex] : undefined;
  const current = viewedGroup && viewedPhoto ? { group: viewedGroup, photo: viewedPhoto } : null;

  return (
    <>
      <div className="st-gallery-head">
        <div>
          <p className="st-eyebrow">{t('photos.eyebrow')}</p>
          <h1>{t('photos.title')}</h1>
          <p className="st-count">{t('photos.count', { count: total, events: events.length })}</p>
        </div>
        <Link to="/scan" className="st-btn st-btn-ghost">
          {t('photos.scanAgain')}
        </Link>
      </div>
      {error && (
        <p className="st-alert" role="alert">
          {error}
        </p>
      )}

      {events.map((group, eventIndex) => (
        <section className="st-event" key={group.event_id}>
          <div className="st-event-head">
            <h2>
              {group.event_name}
              <small>{t('photos.inEvent', { count: group.photos.length })}</small>
            </h2>
            <button
              type="button"
              className="st-btn st-btn-ghost st-btn-small"
              onClick={() => void downloadAll(group)}
            >
              ↓ {t('photos.downloadAll')}
            </button>
          </div>
          <div className="st-grid">
            {group.photos.map((photo, photoIndex) => (
              <button
                type="button"
                className="st-tile"
                key={photo.photo_id}
                style={{ animationDelay: `${Math.min(photoIndex, 12) * 40}ms` }}
                onClick={() => setViewing({ eventIndex, photoIndex })}
                aria-label={t('photos.open', { n: photoIndex + 1 })}
              >
                <img src={photo.url} alt="" loading="lazy" />
              </button>
            ))}
          </div>
        </section>
      ))}

      {current && (
        <div
          className="st-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={current.group.event_name}
        >
          <div className="st-lightbox-top">
            <span>
              {current.group.event_name} · {(viewing?.photoIndex ?? 0) + 1}/
              {current.group.photos.length}
            </span>
            <button
              type="button"
              className="st-icon-btn"
              onClick={close}
              aria-label={t('photos.close')}
            >
              ✕
            </button>
          </div>
          <div
            className="st-lightbox-stage"
            onTouchStart={(event) => {
              const x = event.touches[0]?.clientX ?? 0;
              event.currentTarget.dataset.startX = String(x);
            }}
            onTouchEnd={(event) => {
              const start = Number(event.currentTarget.dataset.startX ?? '0');
              const end = event.changedTouches[0]?.clientX ?? start;
              if (Math.abs(end - start) > 50) step(end < start ? 1 : -1);
            }}
          >
            <img src={current.photo.url} alt="" />
            {current.group.photos.length > 1 && (
              <>
                <button
                  type="button"
                  className="st-icon-btn st-nav st-nav-prev"
                  onClick={() => step(-1)}
                  aria-label={t('photos.previous')}
                >
                  ‹
                </button>
                <button
                  type="button"
                  className="st-icon-btn st-nav st-nav-next"
                  onClick={() => step(1)}
                  aria-label={t('photos.next')}
                >
                  ›
                </button>
              </>
            )}
          </div>
          <div className="st-lightbox-bottom">
            <div className="st-menu">
              <button
                type="button"
                className="st-icon-btn"
                onClick={() => setMenuOpen((open) => !open)}
                aria-expanded={menuOpen}
                aria-label={t('photos.more')}
              >
                ⋯
              </button>
              {menuOpen && (
                <div className="st-menu-list">
                  <button
                    type="button"
                    onClick={() => void notMe(current.group.event_id, current.photo.photo_id)}
                  >
                    {t('photos.notMe')}
                  </button>
                </div>
              )}
            </div>
            <button
              type="button"
              className="st-btn st-btn-primary"
              onClick={() => downloadOriginal(current.photo.download_path)}
            >
              ↓ {t('photos.download')}
            </button>
          </div>
        </div>
      )}
    </>
  );
}
