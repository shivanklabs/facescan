// useCurrentUser: the signed-in user's flags (null when signed out). No email is ever returned.
// The flags are loaded once by CurrentUserProvider and shared by every page and the menu, so a
// page view asks the server once instead of once per component.
import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { api, ApiError } from '@/lib/serverApi';

export interface Me {
  user_id: string;
  auth_method: 'magic_link' | 'passkey'; // magic_link: staff key, no passkey yet
  has_passkey: boolean;
  age_answered: boolean;
  is_minor: boolean;
  can_create_events: boolean;
  is_admin?: boolean;
  is_staff?: boolean; // organiser or photographer of an event
  // Set by the server only (never chosen in the browser).
  role?: 'guest' | 'organiser' | 'photographer' | 'admin';
  status?: 'active' | 'pending' | 'rejected' | 'suspended';
  display_name?: string | null;
  home?: string; // this person's dashboard
}

export interface CurrentUser {
  me: Me | null;
  loading: boolean;
  reload: () => void;
}

const CurrentUserContext = createContext<CurrentUser | null>(null);

/**
 * Purpose: load the signed-in user's flags once and share them with every component below.
 * Inputs: children. Outputs: provider element.
 * Side effects: GET /auth/me on mount and on every reload(); a 401 means signed out (me = null).
 */
export function CurrentUserProvider({ children }: { children: ReactNode }) {
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

  // loading goes back to true so pages wait for the fresh flags instead of acting on old ones
  // (for example redirecting to sign-in right after a sign-in).
  const reload = useCallback(() => {
    setLoading(true);
    setVersion((v) => v + 1);
  }, []);
  const value = useMemo(() => ({ me, loading, reload }), [me, loading, reload]);
  return createElement(CurrentUserContext.Provider, { value }, children);
}

/**
 * Purpose: read the shared signed-in user's flags.
 * Inputs: none. Outputs: { me, loading, reload }.
 * Side effects: none (throws if used outside CurrentUserProvider, a programming error).
 */
export function useCurrentUser(): CurrentUser {
  const value = useContext(CurrentUserContext);
  if (!value) throw new Error('useCurrentUser must be used inside CurrentUserProvider');
  return value;
}
