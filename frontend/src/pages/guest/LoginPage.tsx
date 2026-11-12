// LoginPage (milestone 7): the front door. Signed-out visitors see the hero and the Google
// button; signed-in people go straight to their photos.
import { useEffect, useRef, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useCurrentUser } from '@/features/auth/useCurrentUser';
import { devSignIn, renderGoogleButton, signInConfig } from '@/features/auth/googleSignIn';

/**
 * Purpose: landing page with Google sign-in.
 * Inputs: none. Outputs: page element.
 * Side effects: loads Google's script and renders its button; redirects when signed in.
 */
export default function LoginPage() {
  const { t } = useTranslation();
  const { me, loading } = useCurrentUser();
  const slot = useRef<HTMLDivElement>(null);
  const [error, setError] = useState('');
  const [configured, setConfigured] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [devLogin, setDevLogin] = useState(false);
  const [devEmail, setDevEmail] = useState('');
  const [devBusy, setDevBusy] = useState(false);

  useEffect(() => {
    if (loading || me || !slot.current) return;
    const target = slot.current;
    let active = true;
    setError('');
    signInConfig()
      .then((config) => {
        if (!active) return;
        setDevLogin(config.dev_login);
        if (!config.client_id) {
          setConfigured(false);
          return;
        }
        return renderGoogleButton(
          target,
          config.client_id,
          // Full load so the header picks up the new session.
          () => window.location.assign('/photos'),
          (reason) => {
            if (active) setError(reason === 'google_cancelled' ? '' : t('welcome.signInFailed'));
          },
        );
      })
      .catch(() => {
        if (active) setError(t('welcome.googleUnavailable'));
      });
    return () => {
      active = false;
    };
  }, [loading, me, t, attempt]);

  if (!loading && me) return <Navigate to="/photos" replace />;

  /**
   * Purpose: dev-only sign-in with an email (stand-in for Google in local development).
   * Inputs: none (reads the form state). Outputs: none.
   * Side effects: starts a session, then reloads into the gallery.
   */
  async function submitDevLogin() {
    if (!devEmail.trim()) return;
    setDevBusy(true);
    setError('');
    try {
      await devSignIn(devEmail.trim());
      window.location.assign('/photos');
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
          <div ref={slot} className="st-google-slot" aria-live="polite" />
          {!configured && !devLogin && <p className="st-alert">{t('welcome.notConfigured')}</p>}
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
            <>
              <p className="st-alert" role="alert">
                {error}
              </p>
              <button
                type="button"
                className="st-btn st-btn-ghost st-btn-small"
                onClick={() => setAttempt((n) => n + 1)}
              >
                {t('photos.retry')}
              </button>
            </>
          )}
          <p className="st-fine">{t('welcome.fine')}</p>
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
