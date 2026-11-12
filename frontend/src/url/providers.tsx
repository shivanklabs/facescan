// Providers: global context for the whole app: language (i18n) and the signed-in user.
import type { ReactNode } from 'react';
import { I18nextProvider } from 'react-i18next';
import { CurrentUserProvider } from '@/features/auth/useCurrentUser';
import { i18n } from '@/language-files';

/**
 * Purpose: wrap the app in the language and signed-in-user providers.
 * Inputs: children. Outputs: provider tree. Side effects: one GET /auth/me (see provider).
 */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <I18nextProvider i18n={i18n}>
      <CurrentUserProvider>{children}</CurrentUserProvider>
    </I18nextProvider>
  );
}
