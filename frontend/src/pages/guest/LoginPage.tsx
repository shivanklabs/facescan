// LoginPage: the front door. Guests start or come back with a passkey only (fingerprint or
// face unlock; no email, no password); staff sign in with a key FaceScan gave them.
// Signed-in people go straight to their photos (or the invite link they came from).
import { useEffect, useState } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useCurrentUser } from '@/features/auth/useCurrentUser';
import { devSignIn, signInConfig } from '@/features/auth/googleSignIn';
import { StaffKeyForm } from '@/features/auth/StaffKeyForm';
import {
  openInBrowserUrl,
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

/**
 * Purpose: landing page with passkey sign-up / sign-in for guests and a staff key form.
 * Inputs: optional ?next= (an invite link to return to). Outputs: page element.
 * Side effects: WebAuthn ceremonies; session cookie; redirects when signed in.
 */
export default function LoginPage() {
  const { t } = useTranslation();
  const { me, loading } = useCurrentUser();
  const [params] = useSearchParams();
  const destination = afterSignInPath(params.get('next'));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [devLogin, setDevLogin] = useState(false);
  const [devEmail, setDevEmail] = useState('');
  const [devBusy, setDevBusy] = useState(false);
  // Staff (organisers, photographers, admins) sign in with a key FaceScan gave them.
  const [staffOpen, setStaffOpen] = useState(false);
  const supported = passkeysSupported();

  useEffect(() => {
    if (loading || me) return;
    let active = true;
    signInConfig()
      .then((config) => {
        if (active) setDevLogin(config.dev_login);
      })
      .catch(() => undefined); // only decides whether the dev sign-in box is shown
    return () => {
      active = false;
    };
  }, [loading, me]);

  if (!loading && me) return <Navigate to={destination} replace />;

  /**
   * Purpose: dev-only sign-in with an email (local testing without a passkey device).
   * Inputs: none (reads the form state). Outputs: none.
   * Side effects: starts a session, then reloads into the gallery.
   */
  /**
   * Purpose: run a passkey ceremony (new guest or returning person), then load the next page.
   * Inputs: the ceremony. Outputs: none. Side effects: session cookie; full page load.
   */
  async function withPasskey(action: () => Promise<unknown>) {
    setBusy(true);
    setError('');
    try {
      await action();
      window.location.assign(destination); // full load so the header sees the new session
    } catch {
      setError(t('welcome.passkeyFailed'));
      setBusy(false);
    }
  }

  async function submitDevLogin() {
    if (!devEmail.trim()) return;
    setDevBusy(true);
    setError('');
    try {
      await devSignIn(devEmail.trim());
      window.location.assign(destination);
    } catch {
      setError(t('welcome.devFailed'));
      setDevBusy(false);
    }
  }

  return (
    <section className="st-hero">
      <div>
        <p className="st-eyebrow">{t('welcome.eyebrow')}</p>
        <h1>
          {t('welcome.titleStart')} <em>{t('welcome.titleAccent')}</em>
        </h1>
        <p className="st-lead">{t('welcome.lead')}</p>
        <div className="st-signin">
          {supported ? (
            <div className="st-form-row">
              <button
                type="button"
                className="st-btn st-btn-primary"
                disabled={busy}
                onClick={() => void withPasskey(signUpWithPasskey)}
              >
                {t('welcome.start')}
              </button>
              <button
                type="button"
                className="st-btn st-btn-ghost"
                disabled={busy}
                onClick={() => void withPasskey(signInWithPasskey)}
              >
                {t('welcome.comeBack')}
              </button>
            </div>
          ) : (
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
          )}
          <p className="st-fine">{t('welcome.passkeyHint')}</p>
          {devLogin && (
            <form
              className="st-card st-stack-gap"
              onSubmit={(event) => {
                event.preventDefault();
                void submitDevLogin();
              }}
            >
              <p className="st-badge is-busy">{t('welcome.devTitle')}</p>
              <label className="st-visually-hidden" htmlFor="dev-email">
                {t('welcome.devEmail')}
              </label>
              <div className="st-form-row">
                <input
                  id="dev-email"
                  className="st-input"
                  type="email"
                  autoComplete="email"
                  placeholder={t('welcome.devEmail')}
                  value={devEmail}
                  onChange={(event) => setDevEmail(event.target.value)}
                />
                <button
                  type="submit"
                  className="st-btn st-btn-primary"
                  disabled={devBusy || !devEmail.trim()}
                >
                  {t('welcome.devButton')}
                </button>
              </div>
              <p className="st-fine">{t('welcome.devHint')}</p>
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
              {t('staff.open')}
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
      <div className="st-stack" aria-hidden="true">
        <div className="st-polaroid">
          <div className="st-polaroid-img" />
          <span className="st-polaroid-caption">{t('welcome.caption1')}</span>
        </div>
        <div className="st-polaroid">
          <div className="st-polaroid-img" />
          <span className="st-polaroid-caption">{t('welcome.caption2')}</span>
        </div>
        <div className="st-polaroid">
          <div className="st-polaroid-img" />
          <span className="st-polaroid-caption">{t('welcome.caption3')}</span>
        </div>
        <div className="st-scan-chip">
          <span className="st-dot" />
          {t('welcome.chip')}
        </div>
      </div>
    </section>
  );
}
