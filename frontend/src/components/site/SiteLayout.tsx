// SiteLayout: the frame of the new website (milestone 7) for every page: brand, account actions,
// a menu that shows each person only what their role allows, the page, and the help bubble.
import { useEffect } from 'react';
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
 * Purpose: tell people why staff pages are not open yet: waiting for approval, not approved,
 *   or suspended (the server enforces it; this only explains).
 * Inputs: the signed-in user. Outputs: note element or null. Side effects: none.
 */
function AccountStatusNote({ me }: { me: Me }) {
  const { t } = useTranslation();
  if (!me.status || me.status === 'active') return null;
  const staff = me.role === 'organiser' || me.role === 'photographer';
  if (me.status !== 'suspended' && !staff) return null;
  return (
    <p className="st-note st-status-note" role="status">
      {me.status === 'suspended'
        ? t('account.suspended')
        : t(`account.${me.status}`, { role: t(`account.type.${me.role ?? 'guest'}`) })}
    </p>
  );
}

/** Board tiles that lean towards the mouse (front page, My photos, gallery grid, Find more). */
const TILT_TILES = '.st-moment, .st-event-tile, .st-tile, .st-candidate';
const TILT_DEGREES = 9;

/**
 * Purpose: the board's 3D feel: the tile under the mouse leans towards it (perspective tilt,
 *   set as CSS variables; the CSS adds lift and a deeper shadow). Mouse only: touch screens
 *   get a pressed look from CSS. Off for people who ask for reduced motion.
 * Inputs: none. Outputs: none. Side effects: one pointer listener on the document while the
 *   site frame is shown; removed on unmount.
 */
function useBoardTilt() {
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let current: HTMLElement | null = null;
    const reset = (el: HTMLElement) => {
      el.style.removeProperty('--rx');
      el.style.removeProperty('--ry');
    };
    const move = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse') return;
      const target = event.target instanceof Element ? event.target : null;
      const tile = target?.closest<HTMLElement>(TILT_TILES) ?? null;
      if (current && current !== tile) reset(current);
      current = tile;
      if (!tile) return;
      const box = tile.getBoundingClientRect();
      const x = (event.clientX - box.left) / box.width - 0.5;
      const y = (event.clientY - box.top) / box.height - 0.5;
      tile.style.setProperty('--ry', `${(x * TILT_DEGREES).toFixed(2)}deg`);
      tile.style.setProperty('--rx', `${(-y * TILT_DEGREES).toFixed(2)}deg`);
    };
    const leave = () => {
      if (current) reset(current);
      current = null;
    };
    document.addEventListener('pointermove', move, { passive: true });
    document.documentElement.addEventListener('pointerleave', leave);
    return () => {
      document.removeEventListener('pointermove', move);
      document.documentElement.removeEventListener('pointerleave', leave);
      leave();
    };
  }, []);
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
  useBoardTilt();

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
              {t('account.navButton')}
            </Link>
          )}
        </nav>
      </header>
      {me && <SiteMenu me={me} />}
      {me && <AccountStatusNote me={me} />}
      <main className="st-main">
        <Outlet />
      </main>
      <HelpChat />
    </div>
  );
}
