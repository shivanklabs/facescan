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
 * Purpose: the role-based menu under the top bar. Everyone signed in sees their own photos
 *   (the scan opens from there) and privacy; event owners and photographers also see Studio; platform admins
 *   also see Health.
 *   The server still checks every permission; the menu only hides what would be refused.
 * Inputs: the signed-in user's flags. Outputs: nav element. Side effects: none.
 */
function SiteMenu({ me }: { me: Me }) {
  const { t } = useTranslation();
  const items: MenuItem[] = [
    { to: '/photos', label: t('menu.photos') },
    { to: '/find', label: t('menu.find') },
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

export type EventTheme = 'wedding' | 'college' | 'corporate' | 'birthday' | 'default';

/** Words in an event's name (English, Hindi, Hinglish) that pick its look. First match wins. */
const THEME_WORDS: [EventTheme, RegExp][] = [
  [
    'wedding',
    /wedding|shaadi|shadi|vivah|haldi|mehndi|mehendi|sangeet|baraat|engagement|sagai|reception|nikah|शादी|विवाह|हल्दी|संगीत/i,
  ],
  ['birthday', /birthday|bday|b'day|anniversary|baby shower|janamdin|जन्मदिन|सालगिरह/i],
  [
    'college',
    /college|university|campus|school|fest\b|freshers|farewell|convocation|annual day|कॉलेज/i,
  ],
  [
    'corporate',
    /corporate|conference|summit|meetup|offsite|seminar|workshop|expo|launch|company|office|town ?hall/i,
  ],
];

/**
 * Purpose: the look of an event's pages, chosen automatically from its name (no setting).
 * Inputs: event name (or undefined while loading). Outputs: an EventTheme.
 * Side effects: none.
 */
export function eventThemeFor(name: string | undefined | null): EventTheme {
  if (!name) return 'default';
  return THEME_WORDS.find(([, words]) => words.test(name))?.[0] ?? 'default';
}

/**
 * Purpose: while an event's page is open, dress the whole site (top bar included) in that
 *   event's theme; back to the normal look when the page closes. UI only.
 * Inputs: the event's name. Outputs: none.
 * Side effects: sets / removes data-event-theme on <html>.
 */
export function useEventTheme(name: string | undefined | null) {
  const theme = eventThemeFor(name);
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.eventTheme = theme;
    return () => {
      delete root.dataset.eventTheme;
    };
  }, [theme]);
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
    if (typeof window.matchMedia !== 'function') return; // very old browsers, test runners
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
