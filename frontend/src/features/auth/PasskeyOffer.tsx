// PasskeyOffer: after a guest's first scan, offer a passkey for faster return visits.
// If the browser cannot do passkeys (e.g. in-app browser), skips silently without error.
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { browserSupportsWebAuthn } from '@simplewebauthn/browser';
import { registerPasskey } from '@/lib/passkeys';
import { useCurrentUser } from './useCurrentUser';

/**
 * Purpose: Prompt guests to register a passkey on their phone after their scan.
 * Inputs: None (reads signed-in user status).
 * Outputs: Passkey offer card, or null if unsupported or already registered.
 * Side Effects: Registers WebAuthn credential and updates session on user action.
 */
export function PasskeyOffer() {
  const { t } = useTranslation();
  const { me, reload } = useCurrentUser();
  const [state, setState] = useState<'idle' | 'done'>('idle');

  if (!browserSupportsWebAuthn() || !me || me.has_passkey) return null;
  return (
    <aside className="card">
      <h3>{t('passkey.offerTitle')}</h3>
      <p>{t('passkey.offerText')}</p>
      {state === 'done' ? (
        <p role="status">{t('passkey.added')}</p>
      ) : (
        <button
          type="button"
          className="secondary"
          onClick={() => {
            registerPasskey()
              .then(() => {
                setState('done');
                reload();
              })
              .catch(() => {
                // If browser or platform cannot complete passkey, skip silently without error
                setState('idle');
              });
          }}
        >
          {t('passkey.add')}
        </button>
      )}
    </aside>
  );
}
