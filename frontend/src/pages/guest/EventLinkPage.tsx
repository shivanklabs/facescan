// EventLinkPage: where a QR code or invite link lands (/e/:eventId?k=key). Shows the event's
// name; signed-out people sign in first and come back here; signed-in people join the event
// and go to the face scan, then straight to this event's photos.
import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useCurrentUser } from '@/features/auth/useCurrentUser';
import { api } from '@/lib/serverApi';

type LinkState = 'checking' | 'invalid' | 'ready';

/**
 * Purpose: the invite landing page in the site design.
 * Inputs: eventId (route) and k (invite key, query). Outputs: page element.
 * Side effects: POST /invites/{id}/resolve; when signed in, POST /invites/{id}/join (adds the
 *   person as a guest of this event; never creates an account) and GET /discover/photos.
 */
export default function EventLinkPage() {
  const { t } = useTranslation();
  const { eventId = '' } = useParams();
  const [params] = useSearchParams();
  const key = params.get('k') ?? '';
  const { me, loading } = useCurrentUser();
  const [state, setState] = useState<LinkState>(key ? 'checking' : 'invalid');
  const [eventName, setEventName] = useState('');
  const [hasPhotos, setHasPhotos] = useState(false);

  useEffect(() => {
    if (!eventId || !key) return;
    let active = true;
    api<{ name: string }>(`/invites/${eventId}/resolve`, { json: { key } })
      .then((event) => {
        if (!active) return;
        setEventName(event.name);
        setState('ready');
      })
      .catch(() => {
        if (active) setState('invalid');
      });
    return () => {
      active = false;
    };
  }, [eventId, key]);

  useEffect(() => {
    if (state !== 'ready' || loading || !me) return;
    let active = true;
    api(`/invites/${eventId}/join`, { json: { key } })
      .then(() => api<{ events: { event_id: string }[] }>('/discover/photos'))
      .then((data) => {
        if (active) setHasPhotos(data.events.some((e) => e.event_id === eventId));
      })
      .catch(() => undefined); // staff of the event or a network error: the scan still works
    return () => {
      active = false;
    };
  }, [state, loading, me, eventId, key]);

  if (state === 'invalid') {
    return (
      <section className="st-page">
        <p className="st-eyebrow">{t('eventLink.eyebrow')}</p>
        <h1>{t('eventLink.invalidTitle')}</h1>
        <p className="st-alert" role="alert">
          {t('eventLink.invalid')}
        </p>
      </section>
    );
  }

  if (state === 'checking' || loading) {
    return (
      <section className="st-page">
        <p className="st-note" role="status">
          {t('common.loading')}
        </p>
      </section>
    );
  }

  const here = `/e/${eventId}?k=${encodeURIComponent(key)}`;
  return (
    <section className="st-page">
      <div>
        <p className="st-eyebrow">{t('eventLink.eyebrow')}</p>
        <h1>{eventName}</h1>
        <p className="st-lead-small">{t('eventLink.intro')}</p>
      </div>
      <div className="st-card st-claim">
        {!me && (
          <>
            <p>{t('eventLink.signInFirst')}</p>
            <Link className="st-btn st-btn-primary" to={`/?next=${encodeURIComponent(here)}`}>
              {t('eventLink.signIn')}
            </Link>
          </>
        )}
        {me && hasPhotos && (
          <>
            <p>{t('eventLink.foundAlready')}</p>
            <Link className="st-btn st-btn-primary" to={`/photos/${eventId}`}>
              {t('eventLink.seePhotos')}
            </Link>
            <Link className="st-btn st-btn-ghost" to={`/scan?event=${eventId}`}>
              {t('eventLink.scanAgain')}
            </Link>
          </>
        )}
        {me && !hasPhotos && (
          <>
            <p>{t('eventLink.scanNow')}</p>
            <Link className="st-btn st-btn-primary" to={`/scan?event=${eventId}`}>
              {t('eventLink.scan')}
            </Link>
          </>
        )}
      </div>
    </section>
  );
}
