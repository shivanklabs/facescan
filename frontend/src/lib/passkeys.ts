// Passkeys (WebAuthn) with @simplewebauthn/browser. The server creates and checks every
// challenge; this file only runs the browser ceremony.
import {
  startAuthentication,
  startRegistration,
  type PublicKeyCredentialCreationOptionsJSON,
  type PublicKeyCredentialRequestOptionsJSON,
} from '@simplewebauthn/browser';
import { api, ApiError } from './serverApi';

export async function registerPasskey(): Promise<void> {
  const optionsJSON = await api<PublicKeyCredentialCreationOptionsJSON>(
    '/auth/passkey/register/options',
    { method: 'POST' },
  );
  const credential = await startRegistration({ optionsJSON });
  await api('/auth/passkey/register/verify', { json: { credential } });
}

export type AccountType = 'guest' | 'organiser' | 'photographer';

export interface NewAccount {
  name: string;
  email: string;
  account_type: AccountType;
  accept_privacy: true;
}

/**
 * Purpose: the common registration: name, email and account type, then a passkey. The server
 *   creates the account only when the passkey verifies; organisers and photographers start
 *   pending until an admin approves them. Admin can never be chosen here.
 * Inputs: the new account's details. Outputs: where the server sends this person next.
 * Side effects: WebAuthn create; session cookie.
 */
export async function signUpWithPasskey(account: NewAccount): Promise<{ next: string }> {
  const optionsJSON = await api<PublicKeyCredentialCreationOptionsJSON>('/auth/register/options', {
    json: account,
  });
  const credential = await startRegistration({ optionsJSON });
  return api<{ next: string }>('/auth/register', { json: { ...account, credential } });
}

/**
 * Purpose: whether this browser can use passkeys at all. In-app browsers (WhatsApp,
 *   Instagram, Facebook) often cannot; the page then asks to open the link in Chrome/Safari.
 * Inputs: none. Outputs: boolean. Side effects: none.
 */
export function passkeysSupported(): boolean {
  return typeof window !== 'undefined' && typeof window.PublicKeyCredential === 'function';
}

/**
 * Purpose: a link that opens this page in Chrome on Android (intent URL); elsewhere the plain
 *   address, for "copy link" or "open in browser".
 * Inputs: current URL. Outputs: URL string. Side effects: none.
 */
export function openInBrowserUrl(href: string): string {
  const url = new URL(href);
  if (!/Android/i.test(navigator.userAgent)) return url.toString();
  const rest = `${url.host}${url.pathname}${url.search}`;
  return `intent://${rest}#Intent;scheme=${url.protocol.replace(':', '')};package=com.android.chrome;end`;
}

/**
 * Purpose: which message explains a failed passkey sign-in / sign-up, so people know what to
 *   do: the window was closed or this device has no passkey for this site address, the
 *   passkey is not registered here, the email is taken, bad input, or the server is down.
 * Inputs: the error thrown by the ceremony or the API. Outputs: a translation key.
 * Side effects: none.
 */
export function passkeyErrorKey(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.detail === 'email_taken') return 'account.emailTaken';
    if (error.status === 422) return 'account.invalid';
    if (error.status === 400) return 'welcome.passkeyUnknown';
    if (error.status >= 500) return 'welcome.serverDown';
    return 'welcome.passkeyFailed';
  }
  if (error instanceof Error && (error.name === 'NotAllowedError' || error.name === 'AbortError')) {
    return 'welcome.passkeyCancelled';
  }
  if (error instanceof TypeError) return 'welcome.serverDown'; // the request never arrived
  return 'welcome.passkeyFailed';
}

export async function signInWithPasskey(): Promise<{ next: string }> {
  const optionsJSON = await api<PublicKeyCredentialRequestOptionsJSON>(
    '/auth/passkey/login/options',
    { method: 'POST' },
  );
  const credential = await startAuthentication({ optionsJSON });
  return api<{ next: string }>('/auth/passkey/login/verify', { json: { credential } });
}
