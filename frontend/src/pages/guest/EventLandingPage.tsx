// EventLandingPage: opened from an invite link (/e/:eventId?k=secret).
// No email required. Returns directly to gallery on return visits with valid session cookie.
import { useEffect, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMe } from '@/features/auth/useMe';
import type { GuestStatus } from '@/features/consent/useGuestStatus';
import { api } from '@/lib/apiClient';

/**
 * Purpose: Landing page for guest invite links without email requirement.
 * Inputs: eventId from route params, secret k from search params.
 * Outputs: Event welcome card with Continue button, or immediate redirect to gallery on return visit.
 * Side Effects: Calls join API to create anonymous session cookie if not authenticated.
 */
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
    if (!eventId || !key) return;
    api<{ name: string }>(`/invites/${eventId}/resolve`, { json: { key } })
      .then((event) => setEventName(event.name))
      .catch(() => setInvalid(true));
  }, [eventId, key]);

  useEffect(() => {
    if (loading || !me || !eventId) return;
    api<GuestStatus>(`/events/${eventId}/guest-status`)
      .then((status) => {
        if (status.consent_current) {
          navigate(`/events/${eventId}/gallery`, { replace: true });
        }
      })
      .catch(() => {
        // Not yet a member or error - continue on page
      });
  }, [me, loading, eventId, navigate]);

  async function join() {
    try {
      await api(`/invites/${eventId}/join`, { json: { key } });
      await reload();
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
      <button type="button" onClick={() => void join()}>
        {t('landing.continue')}
      </button>
    </>
  );
}
