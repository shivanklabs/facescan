// StudioLayout: the shell of the simple site (milestone 7): brand, account actions, page.
import { Link, Outlet, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMe } from '@/features/auth/useMe';
import { forgetGoogleSelection } from '@/features/simple/googleSignIn';
import { api } from '@/lib/apiClient';
import '@/styles/studio.css';

/**
 * Purpose: page frame with the FaceScan brand and, when signed in, links and sign-out.
 * Inputs: none (reads the session). Outputs: layout element. Side effects: sign-out request.
 */
export function StudioLayout() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { me } = useMe();

  async function signOut() {
    try {
      await api('/auth/logout', { method: 'POST' });
    } finally {
      forgetGoogleSelection();
      navigate('/', { replace: true });
      window.location.reload();
    }
  }

  return (
    <div className="studio">
      <header className="st-bar">
        <Link to={me ? '/photos' : '/'} className="st-brand" aria-label="FaceScan">
          <span className="st-brand-mark" aria-hidden="true" />
          FaceScan
        </Link>
        <nav className="st-bar-actions" aria-label={t('studio.nav')}>
          <button
            type="button"
            className="st-btn st-btn-ghost st-btn-small"
            onClick={() => void i18n.changeLanguage(i18n.language === 'hi' ? 'en' : 'hi')}
          >
            {t('common.language')}
          </button>
          {me?.can_create_events && (
            <Link to="/studio" className="st-btn st-btn-ghost st-btn-small">
              {t('studio.adminLink')}
            </Link>
          )}
          {me && (
            <button
              type="button"
              className="st-btn st-btn-ghost st-btn-small"
              onClick={() => void signOut()}
            >
              {t('common.signOut')}
            </button>
          )}
        </nav>
      </header>
      <main className="st-main">
        <Outlet />
      </main>
    </div>
  );
}
