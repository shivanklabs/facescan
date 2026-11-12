// SiteLayout: the frame of the new guest website (milestone 7) for every page: brand, account actions, page.
import { Link, Outlet, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useCurrentUser } from '@/features/auth/useCurrentUser';
import { forgetGoogleSelection } from '@/features/auth/googleSignIn';
import { api } from '@/lib/serverApi';
import '@/styles/site.css';

/**
 * Purpose: page frame with the FaceScan brand and, when signed in, links and sign-out.
 * Inputs: none (reads the session). Outputs: layout element. Side effects: sign-out request.
 */
export function SiteLayout() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { me } = useCurrentUser();

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
