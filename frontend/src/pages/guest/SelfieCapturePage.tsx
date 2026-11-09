// SelfieCapturePage: camera only (getUserMedia), no file upload. Captures 4 frames over about
// two seconds into memory and sends them as raw bytes. Nothing is stored in the browser.
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useGuestStatus } from '@/features/consent/useGuestStatus';
import { api, ApiError } from '@/lib/apiClient';
import { captureFrames, framesBody, FRAMES_CONTENT_TYPE } from '@/lib/frames';

type Phase = 'idle' | 'ready' | 'capturing' | 'searching';

export default function SelfieCapturePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { eventId = '' } = useParams();
  const { status } = useGuestStatus(eventId);
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [message, setMessage] = useState('');

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

  async function capture() {
    if (!video.current) return;
    setPhase('capturing');
    try {
      const frames = await captureFrames(video.current);
      setPhase('searching');
      const body = await framesBody(frames);
      const result = await api<{ release: string[]; some_photos_missing: boolean }>(
        `/events/${eventId}/search`,
        { body, headers: { 'Content-Type': FRAMES_CONTENT_TYPE }, timeoutMs: 120_000 },
      );
      stream.current?.getTracks().forEach((track) => track.stop());
      navigate(`/events/${eventId}/gallery`, {
        state: { someMissing: result.some_photos_missing },
      });
    } catch (error) {
      setPhase('ready');
      if (error instanceof ApiError && error.status === 429) setMessage(t('selfie.tooMany'));
      else if (error instanceof ApiError && error.status === 422) setMessage(error.detail);
      else setMessage(t('common.error'));
    }
  }

  return (
    <>
      <h1>{t('selfie.title')}</h1>
      {status?.dev_without_liveness && <p className="banner warn">{t('selfie.devBanner')}</p>}
      {status && !status.search_enabled && <p role="alert">{t('selfie.disabled')}</p>}
      {status && !status.event_searchable && <p role="status">{t('landing.notReady')}</p>}
      <p>{t('selfie.instructions')}</p>
      <video ref={video} className="camera" playsInline muted aria-label={t('selfie.title')} />
      {phase === 'idle' && (
        <button type="button" onClick={() => void startCamera()}>
          {t('selfie.start')}
        </button>
      )}
      {phase === 'ready' && (
        <button type="button" onClick={() => void capture()} disabled={!status?.search_enabled}>
          {t('selfie.capture')}
        </button>
      )}
      {phase === 'capturing' && <p role="status">{t('selfie.capturing')}</p>}
      {phase === 'searching' && <p role="status">{t('selfie.searching')}</p>}
      {message && <p role="alert">{message}</p>}
    </>
  );
}
