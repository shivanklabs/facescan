// SelfieCapturePage: camera only (getUserMedia), no file upload. Guided face scan
// (look straight, blink, turn left, turn right) or front-only fallback into memory and
// sent as raw bytes. Nothing is stored in the browser.
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useGuestStatus } from '@/features/consent/useGuestStatus';
import { api, ApiError } from '@/lib/apiClient';
import { captureGuidedFrames, framesBody, FRAMES_CONTENT_TYPE, ScanStep } from '@/lib/frames';

type Phase = 'idle' | 'ready' | 'capturing' | 'searching';

export default function SelfieCapturePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { eventId = '' } = useParams();
  const { status } = useGuestStatus(eventId);
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [currentStep, setCurrentStep] = useState<ScanStep['key'] | null>(null);
  const [frontOnly, setFrontOnly] = useState(false);
  const [message, setMessage] = useState('');
  const [borderlineAttempts, setBorderlineAttempts] = useState(0);

  useEffect(
    () => () => {
      stream.current?.getTracks().forEach((track) => track.stop());
    },
    [],
  );

  async function startCamera() {
    setMessage('');
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      if (video.current) {
        video.current.srcObject = stream.current;
        await video.current.play();
      }
      setPhase('ready');
    } catch {
      setMessage(t('selfie.cameraError'));
    }
  }

  /**
   * Purpose: translate API rejection reason strings into user-friendly localized messages.
   * Inputs: detail string from backend UnprocessableError.
   * Outputs: localized UI error string.
   * Side effects: none.
   */
  function formatError(detail: string): string {
    const d = detail.toLowerCase();
    if (d.includes('spoof')) return t('selfie.errSpoof');
    if (d.includes('blurry')) return t('selfie.errBlurry');
    if (d.includes('too_dark')) return t('selfie.errDark');
    if (d.includes('overexposed') || d.includes('too_bright')) return t('selfie.errBright');
    if (d.includes('blink')) return t('selfie.errBlink');
    if (d.includes('turn') || d.includes('head_turn')) return t('selfie.errTurn');
    if (d.includes('face') || d.includes('sunglasses') || d.includes('mask')) {
      return t('selfie.errFaceCount');
    }
    return detail;
  }

  async function capture() {
    if (!video.current) return;
    setPhase('capturing');
    setMessage('');
    try {
      const frames = await captureGuidedFrames(
        video.current,
        (step) => setCurrentStep(step),
        frontOnly,
      );
      setPhase('searching');
      const body = await framesBody(frames);
      const url = `/events/${eventId}/search${frontOnly ? '?mode=front-only' : '?mode=guided'}`;
      const result = await api<{
        release: string[];
        some_photos_missing: boolean;
      }>(url, {
        body,
        headers: { 'Content-Type': FRAMES_CONTENT_TYPE },
        timeoutMs: 120_000,
      });

      const isBorderline = result.release.length === 0 && result.some_photos_missing;
      if (isBorderline) {
        if (borderlineAttempts === 0) {
          setBorderlineAttempts(1);
          setPhase('ready');
          setCurrentStep(null);
          setMessage(t('selfie.borderlineFirst'));
          return;
        } else {
          setPhase('idle');
          stream.current?.getTracks().forEach((track) => track.stop());
          setMessage(t('selfie.borderlineSecond'));
          return;
        }
      }

      stream.current?.getTracks().forEach((track) => track.stop());
      navigate(`/events/${eventId}/gallery`, {
        state: { someMissing: result.some_photos_missing },
      });
    } catch (error) {
      setPhase('ready');
      setCurrentStep(null);
      if (error instanceof ApiError && error.status === 429) {
        setMessage(t('selfie.tooMany'));
      } else if (error instanceof ApiError && error.status === 422) {
        setMessage(formatError(error.detail));
      } else {
        setMessage(t('common.error'));
      }
    }
  }

  function stepInstruction(step: ScanStep['key'] | null): string {
    if (step === 'lookStraight') return t('selfie.stepLookStraight');
    if (step === 'blink') return t('selfie.stepBlink');
    if (step === 'turnLeft') return t('selfie.stepTurnLeft');
    if (step === 'turnRight') return t('selfie.stepTurnRight');
    return t('selfie.capturing');
  }

  return (
    <>
      <h1>{t('selfie.title')}</h1>
      {status?.dev_without_liveness && <p className="banner warn">{t('selfie.devBanner')}</p>}
      {status && !status.search_enabled && <p role="alert">{t('selfie.disabled')}</p>}
      {status && !status.event_searchable && <p role="status">{t('landing.notReady')}</p>}
      <p>{t('selfie.instructions')}</p>

      <div className="guidance-box" role="region" aria-label={t('selfie.guidanceTitle')}>
        <strong>{t('selfie.guidanceTitle')}</strong>
        <ul>
          <li>{t('selfie.guidanceLighting')}</li>
          <li>{t('selfie.guidanceFace')}</li>
        </ul>
      </div>

      <video ref={video} className="camera" playsInline muted aria-label={t('selfie.title')} />

      {phase === 'capturing' && (
        <div className="banner step-prompt" role="status" aria-live="assertive">
          <strong>{stepInstruction(currentStep)}</strong>
        </div>
      )}

      {phase === 'idle' && (
        <button type="button" onClick={() => void startCamera()}>
          {t('selfie.start')}
        </button>
      )}

      {phase === 'ready' && (
        <div className="actions">
          <button type="button" onClick={() => void capture()} disabled={!status?.search_enabled}>
            {t('selfie.capture')}
          </button>
          <button type="button" className="secondary" onClick={() => setFrontOnly((prev) => !prev)}>
            {frontOnly ? t('selfie.guidedOption') : t('selfie.frontOnlyOption')}
          </button>
        </div>
      )}

      {phase === 'searching' && <p role="status">{t('selfie.searching')}</p>}
      {message && <p role="alert">{message}</p>}
      {/* TODO(#scene-search): /events/{id}/scene-search page is not built yet; hide button to avoid 404 */}
    </>
  );
}
