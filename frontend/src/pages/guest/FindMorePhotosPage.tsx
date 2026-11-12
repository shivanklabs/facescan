// FindMorePhotosPage: "Photo not found?" Describe the moment (scene search), look at blurred
// previews, tap your own face, and confirm with a quick live scan (1:1 claim). A match is
// released to My photos at once; otherwise the organiser checks the claim.
import { useEffect, useRef, useState, type SyntheticEvent } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useCurrentUser } from '@/features/auth/useCurrentUser';
import { captureBlinkScan } from '@/lib/cameraFrames';
import { api, apiUrl, ApiError } from '@/lib/serverApi';

interface ConsentRecord {
  event_id: string;
  event_name: string;
  purpose: string;
  withdrawn_at: string | null;
}

interface Section {
  id: string;
  name: string;
}

interface CandidateFace {
  face_id: string;
  bbox: [number, number, number, number];
}

interface Candidate {
  photo_id: string;
  preview_url: string;
  faces: CandidateFace[];
  photo_width: number;
  photo_height: number;
}

interface Picked {
  photoId: string;
  faceId: string;
}

type ClaimState = 'idle' | 'camera' | 'sending' | 'released' | 'queued';

/**
 * Purpose: encode a JPEG blob as plain base64 (no data: prefix), as the claim API expects.
 * Inputs: blob. Outputs: base64 string. Side effects: none (memory only).
 */
function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '');
    reader.onerror = () => reject(new Error('could not read frame'));
    reader.readAsDataURL(blob);
  });
}

/**
 * Purpose: one blurred preview with a tappable box on every face.
 * Inputs: candidate, the picked face, onPick. Outputs: figure element.
 * Side effects: none. Boxes are placed in percent of the original size; if the preview is
 *   rotated relative to the stored size (EXIF), width and height are swapped.
 */
function CandidateCard({
  candidate,
  picked,
  onPick,
}: {
  candidate: Candidate;
  picked: Picked | null;
  onPick: (faceId: string) => void;
}) {
  const { t } = useTranslation();
  const [size, setSize] = useState({ w: candidate.photo_width, h: candidate.photo_height });

  function onLoad(event: SyntheticEvent<HTMLImageElement>) {
    const img = event.currentTarget;
    const previewWide = img.naturalWidth >= img.naturalHeight;
    const storedWide = candidate.photo_width >= candidate.photo_height;
    if (previewWide !== storedWide)
      setSize({ w: candidate.photo_height, h: candidate.photo_width });
  }

  return (
    <figure className="st-candidate">
      <img src={apiUrl(candidate.preview_url)} alt="" onLoad={onLoad} />
      {candidate.faces.map((face, index) => {
        const [x, y, w, h] = face.bbox;
        const active = picked?.photoId === candidate.photo_id && picked.faceId === face.face_id;
        return (
          <button
            key={face.face_id}
            type="button"
            className={active ? 'st-face-box is-picked' : 'st-face-box'}
            style={{
              left: `${(x / size.w) * 100}%`,
              top: `${(y / size.h) * 100}%`,
              width: `${(w / size.w) * 100}%`,
              height: `${(h / size.h) * 100}%`,
            }}
            aria-label={t('findMore.thisIsMe', { n: index + 1 })}
            aria-pressed={active}
            onClick={() => onPick(face.face_id)}
          />
        );
      })}
    </figure>
  );
}

/**
 * Purpose: scene search + claim page in the site design.
 * Inputs: none (reads the session). Outputs: page element.
 * Side effects: GET /me/consents, sections; POST scene-search and claims; camera while
 *   confirming (frames stay in memory and are sent once).
 */
export default function FindMorePhotosPage() {
  const { t } = useTranslation();
  const { me, loading } = useCurrentUser();
  const [events, setEvents] = useState<{ id: string; name: string }[] | null>(null);
  const [eventId, setEventId] = useState('');
  const [sections, setSections] = useState<Section[]>([]);
  const [sectionId, setSectionId] = useState('');
  const [query, setQuery] = useState('');
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [claim, setClaim] = useState<ClaimState>('idle');
  const [message, setMessage] = useState('');
  const video = useRef<HTMLVideoElement | null>(null);
  const stream = useRef<MediaStream | null>(null);

  useEffect(() => {
    if (!me) return;
    api<{ consents: ConsentRecord[] }>('/me/consents')
      .then((data) => {
        const byId = new Map<string, string>();
        for (const c of data.consents) {
          if (c.purpose === 'face_search' && c.withdrawn_at === null) {
            byId.set(c.event_id, c.event_name);
          }
        }
        const list = [...byId].map(([id, name]) => ({ id, name }));
        list.sort((a, b) => a.name.localeCompare(b.name));
        setEvents(list);
        const first = list[0];
        if (first) setEventId(first.id);
      })
      .catch(() => setEvents([]));
  }, [me]);

  useEffect(() => {
    if (!eventId) return;
    let active = true;
    api<{ sections: Section[] }>(`/events/${eventId}/sections`)
      .then((data) => {
        if (active) setSections(data.sections);
      })
      .catch(() => {
        if (active) setSections([]);
      });
    return () => {
      active = false;
    };
  }, [eventId]);

  /**
   * Purpose: switch event and forget the previous event's sections, results and pick.
   * Inputs: event id. Outputs: none. Side effects: state updates (sections reload).
   */
  function chooseEvent(id: string) {
    setEventId(id);
    setSections([]);
    setSectionId('');
    setCandidates(null);
    setPicked(null);
  }

  // Always release the camera when leaving the page.
  useEffect(() => () => stopCamera(), []);

  function stopCamera() {
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
  }

  async function search() {
    if (!eventId || query.trim().length < 2) return;
    setSearching(true);
    setMessage('');
    setPicked(null);
    setClaim('idle');
    try {
      const body: { query: string; section_id?: string } = { query: query.trim() };
      if (sectionId) body.section_id = sectionId;
      const result = await api<{ candidates: Candidate[] }>(`/events/${eventId}/scene-search`, {
        json: body,
      });
      setCandidates(result.candidates);
    } catch {
      setCandidates(null);
      setMessage(t('common.error'));
    } finally {
      setSearching(false);
    }
  }

  async function openCamera() {
    setMessage('');
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 960 } },
        audio: false,
      });
      setClaim('camera');
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

  async function confirmClaim() {
    if (!picked || !video.current) return;
    setClaim('sending');
    try {
      const blobs = await captureBlinkScan(video.current, () => undefined);
      stopCamera();
      const frames = await Promise.all(blobs.map(blobToBase64));
      const result = await api<{ status: string }>(`/events/${eventId}/scene-search/claims`, {
        json: { photo_id: picked.photoId, face_id: picked.faceId, frames, front_only: true },
        timeoutMs: 120_000,
      });
      setClaim(result.status === 'released' ? 'released' : 'queued');
    } catch (error) {
      stopCamera();
      setClaim('idle');
      const detail = error instanceof ApiError ? error.detail.toLowerCase() : '';
      if (error instanceof ApiError && error.status === 429) setMessage(t('scan.errTooMany'));
      else if (detail.includes('spoof')) setMessage(t('scan.errSpoof'));
      else if (detail.includes('blink')) setMessage(t('scan.errBlink'));
      else setMessage(t('findMore.claimFailed'));
    }
  }

  if (!loading && !me) return <Navigate to="/" replace />;

  return (
    <section className="st-page st-page-wide">
      <div>
        <p className="st-eyebrow">{t('findMore.eyebrow')}</p>
        <h1>{t('findMore.title')}</h1>
        <p className="st-lead-small">{t('findMore.intro')}</p>
      </div>

      {events?.length === 0 && (
        <p className="st-note">
          {t('findMore.noEvents')} <Link to="/scan">{t('findMore.scanFirst')}</Link>
        </p>
      )}

      {events && events.length > 0 && (
        <form
          className="st-card st-search-form"
          onSubmit={(e) => {
            e.preventDefault();
            void search();
          }}
        >
          <label className="st-field">
            <span>{t('findMore.event')}</span>
            <select
              className="st-input"
              value={eventId}
              onChange={(e) => chooseEvent(e.target.value)}
            >
              {events.map((ev) => (
                <option key={ev.id} value={ev.id}>
                  {ev.name}
                </option>
              ))}
            </select>
          </label>
          {sections.length > 0 && (
            <label className="st-field">
              <span>{t('findMore.section')}</span>
              <select
                className="st-input"
                value={sectionId}
                onChange={(e) => setSectionId(e.target.value)}
              >
                <option value="">{t('findMore.anySection')}</option>
                {sections.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="st-field st-field-grow">
            <span>{t('findMore.query')}</span>
            <input
              className="st-input"
              value={query}
              minLength={2}
              maxLength={256}
              placeholder={t('findMore.queryHint')}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <button
            type="submit"
            className="st-btn st-btn-primary"
            disabled={searching || query.trim().length < 2}
          >
            {searching ? t('findMore.searching') : t('findMore.search')}
          </button>
        </form>
      )}

      {message && (
        <p className="st-alert" role="alert">
          {message}
        </p>
      )}

      {claim === 'released' && (
        <p className="st-note" role="status">
          {t('findMore.released')} <Link to="/photos">{t('findMore.openPhotos')}</Link>
        </p>
      )}
      {claim === 'queued' && (
        <p className="st-note" role="status">
          {t('findMore.queued')}
        </p>
      )}

      {candidates?.length === 0 && <p className="st-note">{t('findMore.nothing')}</p>}

      {candidates && candidates.length > 0 && claim !== 'released' && claim !== 'queued' && (
        <>
          <p className="st-lead-small">{t('findMore.tapYourFace')}</p>
          <div className="st-candidates">
            {candidates.map((c) => (
              <CandidateCard
                key={c.photo_id}
                candidate={c}
                picked={picked}
                onPick={(faceId) => setPicked({ photoId: c.photo_id, faceId })}
              />
            ))}
          </div>
        </>
      )}

      {picked && (claim === 'idle' || claim === 'camera' || claim === 'sending') && (
        <div className="st-card st-claim">
          <h2>{t('findMore.confirmTitle')}</h2>
          <p className="st-lead-small">{t('findMore.confirmText')}</p>
          {claim !== 'idle' && (
            <div className="st-viewfinder st-claim-camera">
              <video ref={video} playsInline muted aria-label={t('scan.title')} />
              <div className={`st-oval ${claim === 'sending' ? 'is-live' : ''}`} />
              <div className="st-prompt" role="status" aria-live="assertive">
                {claim === 'sending' ? t('findMore.blinkNow') : t('findMore.lookHere')}
              </div>
            </div>
          )}
          {claim === 'idle' && (
            <button
              type="button"
              className="st-btn st-btn-primary"
              onClick={() => void openCamera()}
            >
              {t('findMore.openCamera')}
            </button>
          )}
          {claim === 'camera' && (
            <button
              type="button"
              className="st-btn st-btn-primary"
              onClick={() => void confirmClaim()}
            >
              {t('findMore.confirm')}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
