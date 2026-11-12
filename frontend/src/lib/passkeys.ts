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

/**
 * Purpose: passkey-only sign-up for a new guest (no email). The account is created by the
 *   server only when this passkey verifies.
 * Inputs: none. Outputs: where to go next. Side effects: WebAuthn create; session cookie.
 */
export async function signUpWithPasskey(): Promise<{ next: string }> {
  const optionsJSON = await api<PublicKeyCredentialCreationOptionsJSON>(
    '/auth/guest/passkey/options',
    { method: 'POST' },
  );
  const credential = await startRegistration({ optionsJSON });
  return api<{ next: string }>('/auth/guest/passkey/verify', { json: { credential } });
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
