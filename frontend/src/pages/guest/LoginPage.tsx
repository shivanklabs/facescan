// LoginPage: the front door. One sign-in for everyone (passkey); the server reads the
// person's role and status and opens their own dashboard. New people create an account
// (name, email, type: guest / organiser / photographer - never admin) with a passkey;
// organisers and photographers wait for an admin's approval. Staff keys still work.
// Beside it, a wall of event moments (FaceScan's own design: tiles of different heights;
// no other site's name, logo, colours or text).
// Signed-in people go straight to their photos (or the invite link they came from).
import { useState, type CSSProperties } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useCurrentUser } from '@/features/auth/useCurrentUser';
import { StaffKeyForm } from '@/features/auth/StaffKeyForm';
import {
  type AccountType,
  openInBrowserUrl,
  passkeyErrorKey,
  passkeysSupported,
  signInWithPasskey,
  signUpWithPasskey,
} from '@/lib/passkeys';

// Only an event invite link (from a QR code) may be a return address after sign-in; anything
// else falls back to My photos, so a crafted link cannot send people to another site.
const SAFE_NEXT = /^\/e\/[0-9a-f-]{36}(\?k=[A-Za-z0-9_-]{16,128})?$/;

/**
 * Purpose: where to go after sign-in: the invite link the person came from, else My photos.
 * Inputs: the raw ?next= value. Outputs: a same-site path. Side effects: none.
 */
export function afterSignInPath(next: string | null): string {
  return next && SAFE_NEXT.test(next) ? next : '/photos';
}

const ACCOUNT_TYPES: AccountType[] = ['guest', 'organiser', 'photographer'];

/**
 * The moments wall on the front page: colour tiles until real event photos are added.
 * Heights are counted in grid rows (s = 2, m = 3, l = 4) and every column adds up to the same
 * total (3 columns x 12 rows on wide screens, 2 x 18 on phones), so the columns start and end
 * on one line. col / phoneCol say which column a tile stands in.
 */
const MOMENTS: {
  key: string;
  size: 's' | 'm' | 'l';
  col: 1 | 2 | 3;
  phoneCol: 1 | 2;
  a: string;
  b: string;
  found?: boolean;
}[] = [
  { key: 'haldi', size: 'l', col: 1, phoneCol: 1, a: '#f6c445', b: '#d97a12', found: true },
  { key: 'baraat', size: 'm', col: 2, phoneCol: 1, a: '#e4572e', b: '#7a1d14' },
  { key: 'cake', size: 's', col: 3, phoneCol: 2, a: '#f3d9c4', b: '#c0896a' },
  { key: 'mehendi', size: 'm', col: 1, phoneCol: 1, a: '#7e9b4c', b: '#2f4a25' },
  { key: 'varmala', size: 'l', col: 2, phoneCol: 1, a: '#f1a7b5', b: '#b23a5b', found: true },
  { key: 'dance', size: 'l', col: 3, phoneCol: 2, a: '#3e5ba9', b: '#140f2e', found: true },
  { key: 'sangeet', size: 's', col: 1, phoneCol: 1, a: '#7a3f9d', b: '#251238' },
  { key: 'pheras', size: 's', col: 2, phoneCol: 1, a: '#ff9a3c', b: '#8c2d0c' },
  { key: 'reception', size: 'm', col: 3, phoneCol: 2, a: '#1f6f78', b: '#0b2730' },
  { key: 'bidaai', size: 'm', col: 1, phoneCol: 2, a: '#b7414b', b: '#3d0f16' },
  { key: 'family', size: 'm', col: 2, phoneCol: 2, a: '#c9a27c', b: '#5b3a24' },
  { key: 'candid', size: 'm', col: 3, phoneCol: 2, a: '#e8b04f', b: '#6b3b10' },
];

/**
 * Purpose: the account type to preselect from ?as= (a link like /?as=organiser opens the
 *   registration for that type). Admin is never offered. Inputs: raw value.
 * Outputs: an account type, or null for plain sign-in. Side effects: none.
 */
export function accountTypeFrom(value: string | null): AccountType | null {
  return ACCOUNT_TYPES.find((type) => type === value) ?? null;
}

/**
 * Purpose: landing page with passkey sign-up / sign-in for guests and a staff key form.
 * Inputs: optional ?next= (an invite link to return to). Outputs: page element.
 * Side effects: WebAuthn ceremonies; session cookie; redirects when signed in.
 */
export default function LoginPage() {
  const { t } = useTranslation();
  const { me, loading } = useCurrentUser();
  const [params] = useSearchParams();
  const invite = params.get('next');
  const preset = accountTypeFrom(params.get('as'));
  const destination = afterSignInPath(invite);
  const [mode, setMode] = useState<'signin' | 'register'>(preset ? 'register' : 'signin');
  const [accountType, setAccountType] = useState<AccountType>(preset ?? 'guest');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [staffOpen, setStaffOpen] = useState(false);
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const supported = passkeysSupported();

  // Signed in already: the invite link they came from, else their own dashboard.
  if (!loading && me) {
    return <Navigate to={invite ? destination : (me.home ?? '/photos')} replace />;
  }

  /**
   * Purpose: run a passkey ceremony (sign-in or new account), then open the page the server
   *   chose for this person's role (or the invite link they came from).
   * Inputs: the ceremony. Outputs: none. Side effects: session cookie; full page load.
   */
  async function withPasskey(action: () => Promise<{ next: string }>) {
    setBusy(true);
    setError('');
    try {
      const result = await action();
      // Full load so the header sees the new session.
      window.location.assign(invite ? destination : result.next || '/photos');
    } catch (err) {
      setError(t(passkeyErrorKey(err)));
      setBusy(false);
    }
  }

  return (
    <section className="st-landing">
      <div className="st-landing-card">
        <h1>{t('welcome.title')}</h1>
        <p className="st-lead">{t('welcome.lead')}</p>
        <div className="st-signin" id="signin">
          <div className="st-roles st-roles-2" role="tablist" aria-label={t('account.title')}>
            {(['signin', 'register'] as const).map((item) => (
              <button
                key={item}
                type="button"
                role="tab"
                aria-selected={mode === item}
                className={mode === item ? 'st-role is-active' : 'st-role'}
                onClick={() => {
                  setMode(item);
                  setError('');
                }}
              >
                {t(item === 'signin' ? 'account.signInTab' : 'account.registerTab')}
              </button>
            ))}
          </div>
          {!supported ? (
            <div className="st-card st-claim">
              <p className="st-alert">{t('welcome.noPasskeyHere')}</p>
              <a className="st-btn st-btn-primary" href={openInBrowserUrl(window.location.href)}>
                {t('welcome.openInBrowser')}
              </a>
              <button
                type="button"
                className="st-btn st-btn-ghost st-btn-small"
                onClick={() => void navigator.clipboard?.writeText(window.location.href)}
              >
                {t('welcome.copyLink')}
              </button>
            </div>
          ) : mode === 'signin' ? (
            <>
              <button
                type="button"
                className="st-btn st-btn-primary"
                disabled={busy}
                onClick={() => void withPasskey(signInWithPasskey)}
              >
                {t('account.signIn')}
              </button>
              <p className="st-fine">{t('account.signInHint')}</p>
            </>
          ) : (
            <form
              className="st-stack-gap"
              onSubmit={(event) => {
                event.preventDefault();
                void withPasskey(() =>
                  signUpWithPasskey({
                    name: name.trim(),
                    email: email.trim(),
                    account_type: accountType,
                    accept_privacy: true,
                  }),
                );
              }}
            >
              <label className="st-field">
                <span>{t('account.name')}</span>
                <input
                  className="st-input"
                  required
                  maxLength={80}
                  autoComplete="name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
              <label className="st-field">
                <span>{t('account.email')}</span>
                <input
                  className="st-input"
                  type="email"
                  required
                  maxLength={254}
                  autoComplete="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
              </label>
              <p className="st-roles-label" id="account-type">
                {t('account.typeLabel')}
              </p>
              <div className="st-roles" role="radiogroup" aria-labelledby="account-type">
                {ACCOUNT_TYPES.map((type) => (
                  <button
                    key={type}
                    type="button"
                    role="radio"
                    aria-checked={accountType === type}
                    className={accountType === type ? 'st-role is-active' : 'st-role'}
                    onClick={() => setAccountType(type)}
                  >
                    {t(`account.type.${type}`)}
                  </button>
                ))}
              </div>
              <p className="st-fine">
                {accountType === 'guest' ? t('welcome.passkeyHint') : t('account.staffHint')}
              </p>
              <label className="st-check st-check-small">
                <input
                  type="checkbox"
                  required
                  checked={agreed}
                  onChange={(event) => setAgreed(event.target.checked)}
                />
                <span>{t('account.privacyAgree')}</span>
              </label>
              <details className="st-details st-check-small">
                <summary>{t('account.privacyRead')}</summary>
                <p>{t('account.privacyText')}</p>
              </details>
              <button
                type="submit"
                className="st-btn st-btn-primary"
                disabled={busy || !name.trim() || !email.trim() || !agreed}
              >
                {t('account.create')}
              </button>
            </form>
          )}
          {error && (
            <p className="st-alert" role="alert">
              {error}
            </p>
          )}
          <p className="st-fine">{t('welcome.fine')}</p>
          {staffOpen ? (
            <StaffKeyForm onSignedIn={(next) => window.location.assign(next)} />
          ) : (
            <button
              type="button"
              className="st-btn st-btn-ghost st-btn-small"
              onClick={() => setStaffOpen(true)}
            >
              {t('account.staffKey')}
            </button>
          )}
        </div>
        <ol className="st-steps">
          <li>
            <b>1</b>
            {t('welcome.step1')}
          </li>
          <li>
            <b>2</b>
            {t('welcome.step2')}
          </li>
          <li>
            <b>3</b>
            {t('welcome.step3')}
          </li>
        </ol>
      </div>
      <div className="st-wall" aria-hidden="true">
        {MOMENTS.map((m, i) => (
          <figure
            key={m.key}
            className={`st-moment is-${m.size} ${m.found ? 'is-found' : ''}`}
            style={
              {
                '--a': m.a,
                '--b': m.b,
                '--col': m.col,
                '--pcol': m.phoneCol,
                animationDelay: `${i * 50}ms`,
              } as CSSProperties
            }
          >
            {m.found && <span className="st-moment-found">{t('welcome.found')}</span>}
            <figcaption>{t(`welcome.moment.${m.key}`)}</figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}
