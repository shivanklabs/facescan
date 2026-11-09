// EventLandingPage: opened from an invite link (/e/:eventId?k=secret). Shows the event, asks
// the guest to sign in, then joins them to the event and continues to consent.
import { useEffect, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { SignInForm } from '@/features/auth/SignInForm';
import { useMe } from '@/features/auth/useMe';
import { api } from '@/lib/apiClient';

export default function EventLandingPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { eventId = '' } = useParams();
  const [params] = useSearchParams();
  const key = params.get('k') ?? '';
  const { me, loading, reload } = useMe();
  const [eventName, setEventName] = useState<string | null>(null);
  const [invalid, setInvalid] = useState(false);

  useEffect(() => {
    api<{ name: string }>(`/invites/${eventId}/resolve`, { json: { key } })
      .then((event) => setEventName(event.name))
      .catch(() => setInvalid(true));
  }, [eventId, key]);

  async function join() {
    try {
      await api(`/invites/${eventId}/join`, { json: { key } });
      navigate(`/events/${eventId}/consent`);
    } catch {
      setInvalid(true);
    }
  }

  if (invalid) return <p role="alert">{t('landing.invalid')}</p>;
  if (eventName === null || loading) return <p>{t('common.loading')}</p>;
  return (
    <>
      <p>{t('landing.intro')}</p>
      <h1>{eventName}</h1>
      {me ? (
        <button type="button" onClick={() => void join()}>
          {t('landing.continue')}
        </button>
      ) : (
        <>
          <p>{t('landing.signInToContinue')}</p>
          <SignInForm
            next={`/e/${eventId}?k=${encodeURIComponent(key)}`}
            onSignedIn={() => reload()}
          />
        </>
      )}
    </>
  );
}
