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

export async function signInWithPasskey(): Promise<{ next: string }> {
  const optionsJSON = await api<PublicKeyCredentialRequestOptionsJSON>(
    '/auth/passkey/login/options',
    { method: 'POST' },
  );
  const credential = await startAuthentication({ optionsJSON });
  return api<{ next: string }>('/auth/passkey/login/verify', { json: { credential } });
}
