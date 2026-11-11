// HomePage: sign in, or list the user's events with the right link for each role.
import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { SignInForm } from '@/features/auth/SignInForm';
import { useMe } from '@/features/auth/useMe';
import { api } from '@/lib/apiClient';

interface EventSummary {
  event_id: string;
  name: string;
  status: string;
  roles: string[];
}

export default function HomePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { me, loading, reload } = useMe();
  const [events, setEvents] = useState<EventSummary[]>([]);

  useEffect(() => {
    if (!me) return;
    api<{ events: EventSummary[] }>('/events')
      .then((data) => setEvents(data.events))
      .catch(() => setEvents([]));
  }, [me]);

  if (loading) return <p>{t('common.loading')}</p>;
  return (
    <>
      <h1>{t('app.name')}</h1>
      <p>{t('app.tagline')}</p>
      {!me ? (
        <SignInForm
          next="/"
          onSignedIn={(next) => {
            reload();
            navigate(next);
          }}
        />
      ) : (
        <section>
          {(me.can_create_events || events.some((e) => e.roles.includes('organiser'))) && (
            <p>
              <Link to="/organiser">{t('organiser.title')}</Link>
            </p>
          )}
          {me.is_admin && (
            <p>
              <Link to="/admin">Admin Dashboard</Link>
            </p>
          )}
          <ul className="list">
            {events.map((e) => (
              <li key={e.event_id}>
                <strong>{e.name}</strong> ({e.roles.join(', ')})
                <div className="row">
                  {e.roles.includes('guest') && (
                    <Link to={`/events/${e.event_id}/gallery`}>{t('gallery.title')}</Link>
                  )}
                  {e.roles.includes('photographer') && (
                    <Link to={`/photographer/events/${e.event_id}/upload`}>
                      {t('organiser.upload')}
                    </Link>
                  )}
                  {e.roles.includes('organiser') && (
                    <Link to={`/organiser/events/${e.event_id}/approval`}>
                      {t('organiser.approval')}
                    </Link>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
