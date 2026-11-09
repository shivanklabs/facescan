// useGuestStatus: the guest's state in an event (age, consent, search availability).
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/apiClient';

export interface GuestStatus {
  roles: string[];
  age_answered: boolean;
  is_minor: boolean;
  consent_current: boolean;
  notice_version: string;
  search_enabled: boolean;
  dev_without_liveness: boolean;
  event_searchable: boolean;
}

export function useGuestStatus(eventId: string) {
  const [status, setStatus] = useState<GuestStatus | null>(null);
  const [error, setError] = useState(false);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    api<GuestStatus>(`/events/${eventId}/guest-status`)
      .then(setStatus)
      .catch(() => setError(true));
  }, [eventId, version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { status, error, reload };
}
