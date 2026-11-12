// Google sign-in (milestone 7): loads Google Identity Services from Google's own script URL
// (no npm package), renders the official button and hands the ID token to the backend, which
// verifies it. The token is never stored in the browser.
import { api } from '@/lib/serverApi';

const GIS_SRC = 'https://accounts.google.com/gsi/client';
const LOAD_TIMEOUT_MS = 15_000;

interface CredentialResponse {
  credential?: string;
}

interface GoogleAccountsId {
  initialize(config: {
    client_id: string;
    callback: (response: CredentialResponse) => void;
    ux_mode?: 'popup';
    auto_select?: boolean;
    cancel_on_tap_outside?: boolean;
  }): void;
  renderButton(
    parent: HTMLElement,
    options: {
      type?: 'standard';
      theme?: 'outline' | 'filled_blue' | 'filled_black';
      size?: 'large' | 'medium';
      shape?: 'pill' | 'rectangular';
      text?: 'continue_with' | 'signin_with';
      logo_alignment?: 'left' | 'center';
      width?: number;
    },
  ): void;
  disableAutoSelect(): void;
}

interface GoogleWindow {
  google?: { accounts?: { id?: GoogleAccountsId } };
}

let loading: Promise<GoogleAccountsId> | null = null;

/**
 * Purpose: load the Google Identity Services script once.
 * Inputs: none. Outputs: the google.accounts.id API.
 * Side effects: appends one <script> tag; rejects after a timeout or a load error.
 */
export function loadGoogleIdentity(): Promise<GoogleAccountsId> {
  const existing = (window as unknown as GoogleWindow).google?.accounts?.id;
  if (existing) return Promise.resolve(existing);
  if (loading) return loading;
  loading = new Promise<GoogleAccountsId>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error('google_timeout')), LOAD_TIMEOUT_MS);
    const script = document.createElement('script');
    script.src = GIS_SRC;
    script.async = true;
    script.defer = true;
    script.onload = () => {
      window.clearTimeout(timer);
      const id = (window as unknown as GoogleWindow).google?.accounts?.id;
      if (id) resolve(id);
      else reject(new Error('google_unavailable'));
    };
    script.onerror = () => {
      window.clearTimeout(timer);
      reject(new Error('google_unavailable'));
    };
    document.head.appendChild(script);
  });
  loading.catch(() => {
    loading = null; // allow a retry after a failure
  });
  return loading;
}

export interface SignInConfig {
  client_id: string;
  dev_login: boolean;
}

/**
 * Purpose: sign-in options from the backend: the Google client id ("" when not configured) and
 *   whether the dev-only email sign-in exists (local development only).
 * Inputs: none. Outputs: SignInConfig. Side effects: one GET request.
 */
export function signInConfig(): Promise<SignInConfig> {
  return api<SignInConfig>('/auth/google/config');
}

/**
 * Purpose: dev-only email sign-in (the backend refuses it outside FACESCAN_ENV=dev).
 * Inputs: email address. Outputs: none. Side effects: starts a session (cookie).
 */
export async function devSignIn(email: string): Promise<void> {
  await api('/auth/dev-login', { json: { email } });
}

/**
 * Purpose: render the Google button into `parent`; on success, the backend starts a session.
 * Inputs: parent element, client id, success and error callbacks.
 * Outputs: none. Side effects: renders Google's iframe button; POSTs the ID token once.
 */
export async function renderGoogleButton(
  parent: HTMLElement,
  clientId: string,
  onSignedIn: () => void,
  onError: (reason: string) => void,
): Promise<void> {
  const id = await loadGoogleIdentity();
  id.initialize({
    client_id: clientId,
    ux_mode: 'popup',
    auto_select: false,
    cancel_on_tap_outside: true,
    callback: (response) => {
      if (!response.credential) {
        onError('google_cancelled');
        return;
      }
      api('/auth/google', { json: { credential: response.credential } })
        .then(onSignedIn)
        .catch(() => onError('google_rejected'));
    },
  });
  parent.replaceChildren();
  id.renderButton(parent, {
    type: 'standard',
    theme: 'filled_black',
    size: 'large',
    shape: 'pill',
    text: 'continue_with',
    logo_alignment: 'left',
    width: Math.min(320, parent.clientWidth || 320),
  });
}

/**
 * Purpose: stop Google from auto-selecting the account after sign-out.
 * Inputs: none. Outputs: none. Side effects: none when the script never loaded.
 */
export function forgetGoogleSelection(): void {
  (window as unknown as GoogleWindow).google?.accounts?.id?.disableAutoSelect();
}
