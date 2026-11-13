// SiteLayout: the frame of the new website (milestone 7) for every page: brand, account actions,
// a menu that shows each person only what their role allows, the page, and the help bubble.
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { HelpChat } from '@/components/site/HelpChat';
import { useCurrentUser, type Me } from '@/features/auth/useCurrentUser';
import { api } from '@/lib/serverApi';
import '@/styles/site.css';

interface MenuItem {
  to: string;
  label: string;
}

/**
 * Purpose: the role-based menu under the top bar. Everyone signed in sees their own photos,
 *   scan and privacy; event owners and photographers also see Studio; platform admins
 *   also see Health.
 *   The server still checks every permission; the menu only hides what would be refused.
 * Inputs: the signed-in user's flags. Outputs: nav element. Side effects: none.
 */
function SiteMenu({ me }: { me: Me }) {
  const { t } = useTranslation();
  const items: MenuItem[] = [
    { to: '/photos', label: t('menu.photos') },
    { to: '/find', label: t('menu.find') },
    { to: '/scan', label: t('menu.scan') },
    { to: '/privacy', label: t('menu.privacy') },
  ];
  if (me.can_create_events || me.is_staff) {
    items.push({ to: '/studio', label: t('menu.studio') });
  }
  if (me.is_admin) items.push({ to: '/admin', label: t('menu.admin') });
  return (
    <nav className="st-menu" aria-label={t('menu.label')}>
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) => (isActive ? 'st-menu-link is-active' : 'st-menu-link')}
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}

/**
 * Purpose: page frame with the FaceScan brand and, when signed in, the menu and sign-out.
 * Inputs: none (reads the session). Outputs: layout element. Side effects: sign-out request.
 */
export function SiteLayout() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { me, loading } = useCurrentUser();

  async function signOut() {
    try {
      await api('/auth/logout', { method: 'POST' });
    } finally {
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
          {me && (
            <button
              type="button"
              className="st-btn st-btn-ghost st-btn-small"
              onClick={() => void signOut()}
            >
              {t('common.signOut')}
            </button>
          )}
          {!me && !loading && pathname !== '/' && (
            <Link to="/" className="st-btn st-btn-primary st-btn-small">
              {t('loginAs.navButton')}
            </Link>
          )}
        </nav>
      </header>
      {me && <SiteMenu me={me} />}
      <main className="st-main">
        <Outlet />
      </main>
      <HelpChat />
    </div>
  );
}
