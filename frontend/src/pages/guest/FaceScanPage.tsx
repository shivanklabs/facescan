// FaceScanPage (milestone 7): one live scan (look straight, blink) searched in every event.
// Frames are captured into memory and sent once; nothing is saved in the browser.
import { useEffect, useRef, useState } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useCurrentUser } from '@/features/auth/useCurrentUser';
import { api, ApiError } from '@/lib/serverApi';
import {
  captureBlinkScan,
  captureFrames as captureTimedFrames,
  framesBody,
  FRAMES_CONTENT_TYPE,
  ScanStep,
} from '@/lib/cameraFrames';
import type { LiveStep } from '@/lib/liveBlink';

/**
 * Purpose: load the live-blink module (MediaPipe) only when it is used, so the rest of the
 *   site stays small. Inputs: none. Outputs: the module. Side effects: network fetch once.
 */
const liveBlinkModule = () => import('@/lib/liveBlink');

type Phase = 'intro' | 'camera' | 'capturing' | 'searching';

/** Scan methods the owner is comparing (milestone 7); the most stable one is kept later. */
export type ScanMethod = 'live' | 'burst' | 'look';
const METHODS: ScanMethod[] = ['live', 'burst', 'look'];
const METHOD_KEY = 'facescan.scanMethod';

/**
 * Purpose: the method chosen last time on this device (browser storage may be blocked).
 * Inputs: none. Outputs: ScanMethod ('live' by default). Side effects: reads localStorage.
 */
function savedMethod(): ScanMethod {
  try {
    const value = window.localStorage.getItem(METHOD_KEY);
    return METHODS.find((m) => m === value) ?? 'live';
  } catch {
    return 'live';
  }
}

interface Notice {
  version: string;
  texts: Record<string, Record<string, string>>;
}

interface ScanResult {
  released: number;
  events_with_photos: number;
  events_searched: number;
}

/**
 * Purpose: map a backend rejection to a friendly, translated message.
 * Inputs: translator, API error. Outputs: message text. Side effects: none.
 */
function messageFor(t: (key: string) => string, error: unknown): string {
  if (!(error instanceof ApiError)) {
    return `${t('scan.errGeneric')} (${error instanceof Error ? error.message : 'unknown'})`;
  }
  if (error.status === 429) return t('scan.errTooMany');
  if (error.status === 409) return t('scan.errNotice');
  if (error.status === 401) return t('scan.errSignedOut');
  const d = error.detail.toLowerCase();
  if (d.includes('spoof')) return t('scan.errSpoof');
  if (d.includes('blink')) return t('scan.errBlink');
  if (d.includes('dark')) return t('scan.errDark');
  if (d.includes('blurry')) return t('scan.errBlurry');
  if (d.includes('bright') || d.includes('overexposed')) return t('scan.errBright');
  if (d.includes('face')) return t('scan.errFace');
  // Unknown problem: keep the status code visible so it can be reported.
  return `${t('scan.errGeneric')} (${error.status || 'network'}: ${error.detail})`;
}

/**
 * Purpose: camera page: consent tick, guided capture, search, then go to the photos page.
 * Inputs: none. Outputs: page element.
 * Side effects: camera access while open (stopped on leave); one search request.
 */
export default function FaceScanPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  // From a QR link: after the scan, show that event's photos first.
  const [params] = useSearchParams();
  const focusEvent = params.get('event');
  const { me, loading } = useCurrentUser();
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const [phase, setPhase] = useState<Phase>('intro');
  const [step, setStep] = useState<LiveStep | ScanStep['key'] | null>(null);
  const abort = useRef<AbortController | null>(null);
  const [flash, setFlash] = useState(0);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [agreed, setAgreed] = useState(false);
  const [message, setMessage] = useState('');
  const [method, setMethod] = useState<ScanMethod>(savedMethod);
  const [usedFallback, setUsedFallback] = useState(false);

  /**
   * Purpose: switch the scan method and remember it on this device.
   * Inputs: method. Outputs: none. Side effects: writes localStorage when allowed.
   */
  function chooseMethod(next: ScanMethod) {
    setMethod(next);
    setMessage('');
    setUsedFallback(false);
    try {
      window.localStorage.setItem(METHOD_KEY, next);
    } catch {
      // storage blocked: the choice simply is not remembered
    }
  }

  useEffect(() => {
    let active = true;
    api<Notice>('/consent/notice')
      .then((value) => {
        if (active) setNotice(value);
      })
      .catch(() => {
        if (active) setMessage(t('scan.errGeneric'));
      });
    return () => {
      active = false;
    };
  }, [t]);

  useEffect(
    () => () => {
      abort.current?.abort();
      stream.current?.getTracks().forEach((track) => track.stop());
    },
    [],
  );

  function stopCamera() {
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
  }

  async function openCamera() {
    setMessage('');
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 960 } },
        audio: false,
      });
      setPhase('camera');
      // Start loading the on-device blink model now, so it is ready when the scan starts.
      if (method === 'live') {
        liveBlinkModule()
          .then((mod) => mod.loadLandmarker())
          .catch(() => undefined);
      }
      // Wait one frame for the <video> to mount, then attach the stream.
      requestAnimationFrame(() => {
        if (video.current && stream.current) {
          video.current.srcObject = stream.current;
          void video.current.play();
        }
      });
    } catch {
      setMessage(t('scan.errCamera'));
    }
  }

  /**
   * Purpose: capture the frames for the chosen method: live blink detection on the phone
   *   (timed burst when the on-device model cannot run), the timed burst, or 'look' (three
   *   front frames, no blink).
   * Inputs: playing video. Outputs: JPEG frames. Side effects: updates the on-screen step.
   */
  async function captureFrames(source: HTMLVideoElement): Promise<Blob[]> {
    abort.current = new AbortController();
    const timedBurst = () =>
      captureBlinkScan(source, (key) => {
        setStep(key);
        setFlash((n) => n + 1);
      });
    if (method === 'burst') return timedBurst();
    if (method === 'look') {
      setStep('lookStraight');
      await new Promise((resolve) => setTimeout(resolve, 700));
      const frames = await captureTimedFrames(source, 3, 450);
      setFlash((n) => n + 1);
      return frames;
    }
    let live: Awaited<ReturnType<typeof liveBlinkModule>>;
    try {
      live = await liveBlinkModule();
    } catch {
      setUsedFallback(true);
      return timedBurst();
    }
    try {
      return await live.captureWithLiveBlink(
        source,
        (next) => {
          setStep(next);
          if (next === 'eyesOpen') setFlash((n) => n + 1);
        },
        abort.current.signal,
      );
    } catch (error) {
      if (!(error instanceof live.LiveBlinkUnavailableError)) throw error;
      setUsedFallback(true);
      return timedBurst();
    }
  }

  async function scan() {
    if (!video.current || !notice) return;
    setMessage('');
    setPhase('capturing');
    let frames: Blob[];
    try {
      frames = await captureFrames(video.current);
    } catch (error) {
      // No face or no blink in time: keep the camera on so the person can simply try again.
      setStep(null);
      setPhase('camera');
      if (error instanceof Error && (error.message === 'no_face' || error.message === 'no_blink')) {
        setMessage(error.message === 'no_face' ? t('scan.errFace') : t('scan.errBlink'));
      } else {
        setMessage(messageFor(t, error));
      }
      return;
    }
    try {
      setPhase('searching');
      stopCamera();
      const body = await framesBody(frames);
      const result = await api<ScanResult>(
        `/discover/search?notice_version=${encodeURIComponent(notice.version)}` +
          `&method=${method === 'look' ? 'look' : 'blink'}`,
        { body, headers: { 'Content-Type': FRAMES_CONTENT_TYPE }, timeoutMs: 180_000 },
      );
      const target = focusEvent ? `/photos/${encodeURIComponent(focusEvent)}` : '/photos';
      navigate(target, { replace: true, state: { scanned: true, found: result.released } });
    } catch (error) {
      setMessage(messageFor(t, error));
      setStep(null);
      setPhase('intro');
    }
  }

  if (!loading && !me) return <Navigate to="/" replace />;

  const lang = i18n.language === 'hi' ? 'hi' : 'en';
  const noticeText = notice?.texts[lang]?.notice ?? '';
  const stepText =
    step === 'blink'
      ? t('scan.stepBlink')
      : step === 'findFace'
        ? t('scan.stepFindFace')
        : step === 'eyesOpen'
          ? t('scan.stepDone')
          : step
            ? t('scan.stepStraight')
            : t('scan.ready');

  if (phase === 'searching') {
    return (
      <section className="st-searching" aria-live="polite">
        <div>
          <div className="st-aperture" aria-hidden="true" />
          <h1>{t('scan.searchingTitle')}</h1>
          <p className="st-count">{t('scan.searchingText')}</p>
        </div>
      </section>
    );
  }

  return (
    <section className={`st-scan ${phase === 'intro' ? '' : 'is-camera'}`}>
      <div className="st-viewfinder st-corners">
        {phase === 'intro' ? (
          <div className="st-viewfinder-empty">
            <div>
              <div className="st-empty-frame" aria-hidden="true">
                ◎
              </div>
              <p>{t('scan.cameraOff')}</p>
            </div>
          </div>
        ) : (
          <>
            <video ref={video} playsInline muted aria-label={t('scan.title')} />
            <div className={`st-oval ${phase === 'capturing' ? 'is-live' : ''}`} />
            <div key={flash} className={`st-flash ${flash > 0 ? 'is-on' : ''}`} />
            <div className="st-prompt" role="status" aria-live="assertive">
              {phase === 'capturing' ? stepText : t('scan.ready')}
            </div>
          </>
        )}
      </div>

      <div className="st-scan-side">
        <p className="st-eyebrow">{t('scan.eyebrow')}</p>
        <h1>{t('scan.title')}</h1>
        <ul className="st-tips">
          <li>
            <span aria-hidden="true">☀</span>
            {t('scan.tipLight')}
          </li>
          <li>
            <span aria-hidden="true">◐</span>
            {t('scan.tipFace')}
          </li>
          <li>
            <span aria-hidden="true">◉</span>
            {t('scan.tipBlink')}
          </li>
        </ul>

        <div className="st-stack-gap">
          {(phase === 'intro' || phase === 'camera') && (
            <div className="st-methods" role="radiogroup" aria-label={t('scan.methodLabel')}>
              {METHODS.map((m) => (
                <button
                  key={m}
                  type="button"
                  role="radio"
                  aria-checked={method === m}
                  className={`st-method ${method === m ? 'is-active' : ''}`}
                  onClick={() => chooseMethod(m)}
                >
                  <strong>{t(`scan.method.${m}`)}</strong>
                  <span>{t(`scan.methodHint.${m}`)}</span>
                </button>
              ))}
            </div>
          )}
          {usedFallback && <p className="st-note">{t('scan.usedFallback')}</p>}
          {phase === 'intro' && (
            <>
              <div className="st-card">
                <label className="st-check">
                  <input
                    type="checkbox"
                    checked={agreed}
                    onChange={(event) => setAgreed(event.target.checked)}
                  />
                  <span>{t('scan.agree')}</span>
                </label>
                {noticeText && (
                  <details className="st-details">
                    <summary>{t('scan.readNotice')}</summary>
                    <p>{noticeText}</p>
                  </details>
                )}
              </div>
              <button
                type="button"
                className="st-btn st-btn-primary"
                disabled={!agreed || !notice}
                onClick={() => void openCamera()}
              >
                {t('scan.openCamera')}
              </button>
            </>
          )}
          {phase === 'camera' && (
            <button type="button" className="st-btn st-btn-primary" onClick={() => void scan()}>
              {t('scan.start')}
            </button>
          )}
          {phase === 'capturing' && <p className="st-note">{t('scan.holdStill')}</p>}
          {message && (
            <p className="st-alert" role="alert">
              {message}
            </p>
          )}
          <p className="st-fine">{t('scan.privacy')}</p>
        </div>
      </div>
    </section>
  );
}
