// Passkeys (WebAuthn) with @simplewebauthn/browser. The server creates and checks every
// challenge; this file only runs the browser ceremony.
import {
  startAuthentication,
  startRegistration,
  type PublicKeyCredentialCreationOptionsJSON,
  type PublicKeyCredentialRequestOptionsJSON,
} from '@simplewebauthn/browser';
import { api } from './serverApi';

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

export async function signInWithPasskey(): Promise<{ next: string }> {
  const optionsJSON = await api<PublicKeyCredentialRequestOptionsJSON>(
    '/auth/passkey/login/options',
    { method: 'POST' },
  );
  const credential = await startAuthentication({ optionsJSON });
  return api<{ next: string }>('/auth/passkey/login/verify', { json: { credential } });
}
