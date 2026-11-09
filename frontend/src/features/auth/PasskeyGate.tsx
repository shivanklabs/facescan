// PasskeyGate: staff pages need a passkey session. Signed-out users see the sign-in form;
// users signed in with a magic link register a passkey (or sign in with an existing one).
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { registerPasskey, signInWithPasskey } from '@/lib/passkeys';
import { SignInForm } from './SignInForm';
import { useMe } from './useMe';

export function PasskeyGate({ next, children }: { next: string; children: ReactNode }) {
  const { t } = useTranslation();
  const { me, loading, reload } = useMe();
  const [error, setError] = useState('');

  if (loading) return <p>{t('common.loading')}</p>;
  if (!me) return <SignInForm next={next} onSignedIn={() => reload()} />;
  if (me.auth_method === 'passkey') return <>{children}</>;

  async function run(action: () => Promise<unknown>) {
    setError('');
    try {
      await action();
      reload();
    } catch {
      setError(t('common.error'));
    }
  }

  return (
    <section className="card">
      <p>{t('passkey.staffRequired')}</p>
      {me.has_passkey ? (
        <button type="button" onClick={() => void run(signInWithPasskey)}>
          {t('passkey.useIt')}
        </button>
      ) : (
        <button type="button" onClick={() => void run(registerPasskey)}>
          {t('passkey.add')}
        </button>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
