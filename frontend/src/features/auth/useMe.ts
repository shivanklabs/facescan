// useMe: the signed-in user's flags (null when signed out). No email is ever returned.
import { useCallback, useEffect, useState } from 'react';
import { api, ApiError } from '@/lib/apiClient';

export interface Me {
  user_id: string;
  auth_method: 'magic_link' | 'passkey';
  has_passkey: boolean;
  age_answered: boolean;
  is_minor: boolean;
  can_create_events: boolean;
}

export function useMe(): { me: Me | null; loading: boolean; reload: () => void } {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let active = true;
    api<Me>('/auth/me')
      .then((value) => {
        if (active) setMe(value);
      })
      .catch((error: unknown) => {
        if (active && error instanceof ApiError) setMe(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { me, loading, reload };
}
