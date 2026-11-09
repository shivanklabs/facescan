// PasskeyOffer: after a guest's first sign-in, offer a passkey for faster sign-in next time.
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { registerPasskey } from '@/lib/passkeys';
import { useMe } from './useMe';

export function PasskeyOffer() {
  const { t } = useTranslation();
  const { me, reload } = useMe();
  const [state, setState] = useState<'idle' | 'done' | 'error'>('idle');

  if (!me || me.has_passkey) return null;
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
              .catch(() => setState('error'));
          }}
        >
          {t('passkey.add')}
        </button>
      )}
      {state === 'error' && <p role="alert">{t('common.error')}</p>}
    </aside>
  );
}
