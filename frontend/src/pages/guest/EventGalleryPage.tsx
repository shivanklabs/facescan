// EventGalleryPage: one event's photos of me as a 3D coverflow (three.js, Photo lab look:
// polaroid frames on a dark stage), with a light grid for slow phones, no WebGL or "reduce
// motion" (they can still switch to 3D). Tap the front photo to view it full screen; download originals; "Not me".
import { useEventTheme } from '@/components/site/SiteLayout';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useCurrentUser } from '@/features/auth/useCurrentUser';
import { api, apiUrl } from '@/lib/serverApi';

type ThreeModule = typeof import('three');

interface Photo {
  photo_id: string;
  url: string;
  download_path: string;
}

interface EventPhotos {
  event_id: string;
  event_name: string;
  photos: Photo[];
}

type ViewMode = '3d' | 'grid';

const DOWNLOAD_GAP_MS = 400;
const VIEW_KEY = 'fs-gallery-view';
const TEXTURE_RANGE = 6; // photos each side of the front one that keep a texture in memory
const VISIBLE_RANGE = 5; // photos each side that are drawn

/**
 * Purpose: start a browser download of one original photo (session cookie authorises it).
 * Inputs: API download path. Outputs: none. Side effects: clicks a temporary link.
 */
function downloadOriginal(path: string): void {
  const link = document.createElement('a');
  link.href = apiUrl(path);
  link.download = '';
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
}

/**
 * Purpose: whether this device can run the 3D coverflow at all: WebGL works and the phone is
 *   not very low-end (few cores or little memory). Reduced motion does not block it (see
 *   prefersReducedMotion): those people start in the grid and may switch to 3D themselves.
 * Inputs: none. Outputs: boolean. Side effects: creates and drops a test WebGL context.
 */
export function canUse3d(): boolean {
  if (typeof window === 'undefined') return false;
  const nav = navigator as Navigator & { deviceMemory?: number };
  if ((nav.hardwareConcurrency ?? 8) < 4) return false;
  if (nav.deviceMemory !== undefined && nav.deviceMemory < 3) return false;
  try {
    const canvas = document.createElement('canvas');
    return Boolean(canvas.getContext('webgl2') ?? canvas.getContext('webgl'));
  } catch {
    return false;
  }
}

/**
 * Purpose: whether the person asked their device for less motion (start in the grid then).
 * Inputs: none. Outputs: boolean. Side effects: none.
 */
export function prefersReducedMotion(): boolean {
  return Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
}

/**
 * Purpose: the coverflow pose of a photo `d` places away from the front (d may be fractional
 *   while sliding): x offset, depth, turn and scale.
 * Inputs: d. Outputs: { x, z, rotY, scale }. Side effects: none.
 */
export function coverflowPose(d: number): { x: number; z: number; rotY: number; scale: number } {
  const side = Math.sign(d);
  const near = Math.min(Math.abs(d), 1); // 0 at the front, 1 from the first neighbour on
  const far = Math.max(Math.abs(d) - 1, 0);
  return {
    x: side * (near * 1.45 + far * 0.62),
    z: -(near * 0.9 + far * 0.55),
    rotY: -side * near * 0.95,
    scale: 1.15 - near * 0.15,
  };
}

interface CoverflowProps {
  photos: Photo[];
  index: number;
  onIndex: (index: number) => void;
  onOpen: () => void;
}

/**
 * Purpose: the three.js coverflow stage. Photos are polaroid-framed planes; drag or use the
 *   arrows to move, tap the front photo to open it, tap a side photo to bring it forward.
 * Inputs: photos, current index, callbacks. Outputs: canvas element.
 * Side effects: loads three.js on demand; WebGL rendering loop; loads and disposes textures
 *   near the front photo only (keeps memory low on phones); cleans everything up on unmount.
 */
function Coverflow({ photos, index, onIndex, onOpen }: CoverflowProps) {
  const host = useRef<HTMLDivElement | null>(null);
  const target = useRef(index);
  const callbacks = useRef({ onIndex, onOpen });
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    target.current = index;
  }, [index]);

  useEffect(() => {
    callbacks.current = { onIndex, onOpen };
  }, [onIndex, onOpen]);

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    let disposed = false;
    let cleanup: () => void = () => {};

    void import('three')
      .then((THREE: ThreeModule) => {
        if (disposed) return;
        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.outputColorSpace = THREE.SRGBColorSpace;
        element.appendChild(renderer.domElement);
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 50);
        camera.position.set(0, 0, 6.2);

        const loader = new THREE.TextureLoader();
        const frameMaterial = new THREE.MeshBasicMaterial({ color: 0xf4ede1 });
        // A soft dark print shadow so the cream frame stands out on the light theme too.
        const shadowMaterial = new THREE.MeshBasicMaterial({
          color: 0x000000,
          transparent: true,
          opacity: 0.22,
          depthWrite: false,
        });
        const placeholder = new THREE.MeshBasicMaterial({ color: 0x2a251c });
        const height = 2.3;
        const items = photos.map((photo) => {
          const group = new THREE.Group();
          const shadow = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), shadowMaterial);
          shadow.position.z = -0.02;
          const frame = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), frameMaterial);
          frame.position.z = -0.01;
          const picture = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), placeholder);
          group.add(shadow, frame, picture);
          scene.add(group);
          return {
            photo,
            group,
            frame,
            shadow,
            picture,
            texture: null as import('three').Texture | null,
            loading: false,
            aspect: 0.75,
          };
        });

        /** Size a polaroid to the photo's shape (wide bottom border, like a print). */
        const shape = (item: (typeof items)[number]) => {
          const w = height * item.aspect;
          item.picture.scale.set(w, height, 1);
          item.frame.scale.set(w + 0.16, height + 0.42, 1);
          item.frame.position.y = -0.12;
          item.shadow.scale.set(w + 0.34, height + 0.62, 1);
          item.shadow.position.set(0.06, -0.2, -0.02);
        };
        items.forEach(shape);

        /** Load textures near the front photo; free the far ones. */
        const manageTextures = (front: number) => {
          items.forEach((item, i) => {
            const near = Math.abs(i - front) <= TEXTURE_RANGE;
            if (near && !item.texture && !item.loading) {
              item.loading = true;
              loader.load(
                item.photo.url,
                (texture) => {
                  item.loading = false;
                  if (disposed) {
                    texture.dispose();
                    return;
                  }
                  texture.colorSpace = THREE.SRGBColorSpace;
                  const image = texture.image as { width?: number; height?: number };
                  if (image.width && image.height) item.aspect = image.width / image.height;
                  item.texture = texture;
                  item.picture.material = new THREE.MeshBasicMaterial({ map: texture });
                  shape(item);
                },
                undefined,
                () => {
                  item.loading = false;
                },
              );
            } else if (!near && item.texture) {
              item.texture.dispose();
              if (item.picture.material !== placeholder) {
                (item.picture.material as import('three').Material).dispose();
              }
              item.picture.material = placeholder;
              item.texture = null;
            }
          });
        };

        const resize = () => {
          const width = element.clientWidth;
          const h = element.clientHeight;
          renderer.setSize(width, h, false);
          camera.aspect = width / Math.max(h, 1);
          // Step back until about 5.2 units fit across: the front photo (even a wide one)
          // plus a glimpse of each neighbour, so narrow phones still see the coverflow.
          const halfFov = THREE.MathUtils.degToRad(camera.fov / 2);
          camera.position.z = Math.max(6.2, 5.2 / (2 * Math.tan(halfFov) * camera.aspect));
          camera.updateProjectionMatrix();
        };
        const observer = new ResizeObserver(resize);
        observer.observe(element);
        resize();

        // Dragging: pixels -> photo positions.
        let position = target.current;
        let dragStart: { x: number; pos: number } | null = null;
        let moved = false;
        const raycaster = new THREE.Raycaster();
        const pointer = new THREE.Vector2();

        const onDown = (event: PointerEvent) => {
          dragStart = { x: event.clientX, pos: position };
          moved = false;
          renderer.domElement.setPointerCapture(event.pointerId);
        };
        const onMove = (event: PointerEvent) => {
          if (!dragStart) return;
          const dx = event.clientX - dragStart.x;
          if (Math.abs(dx) > 6) moved = true;
          const next = dragStart.pos - dx / Math.max(element.clientWidth / 3, 80);
          position = Math.min(Math.max(next, 0), photos.length - 1);
        };
        const onUp = (event: PointerEvent) => {
          if (!dragStart) return;
          dragStart = null;
          if (moved) {
            callbacks.current.onIndex(Math.round(position));
            return;
          }
          // A tap: which photo was hit?
          const rect = renderer.domElement.getBoundingClientRect();
          pointer.set(
            ((event.clientX - rect.left) / rect.width) * 2 - 1,
            -((event.clientY - rect.top) / rect.height) * 2 + 1,
          );
          raycaster.setFromCamera(pointer, camera);
          const hit = raycaster.intersectObjects(
            items.map((it) => it.group),
            true,
          )[0];
          const tapped = hit ? items.findIndex((it) => it.group === hit.object.parent) : -1;
          if (tapped === -1) return;
          if (tapped === target.current) callbacks.current.onOpen();
          else callbacks.current.onIndex(tapped);
        };
        renderer.domElement.addEventListener('pointerdown', onDown);
        renderer.domElement.addEventListener('pointermove', onMove);
        renderer.domElement.addEventListener('pointerup', onUp);

        let frame = 0;
        let lastFront = -1;
        let lastTime = performance.now();
        const tick = (now: number) => {
          // Time-based easing: the photos settle in about 0.3 s whatever the frame rate
          // (slow phones and background tabs draw fewer frames).
          const dt = Math.min((now - lastTime) / 1000, 0.25);
          lastTime = now;
          if (!dragStart) position += (target.current - position) * (1 - Math.exp(-dt * 12));
          const front = Math.round(position);
          if (front !== lastFront) {
            lastFront = front;
            manageTextures(front);
          }
          items.forEach((item, i) => {
            const d = i - position;
            const visible = Math.abs(d) <= VISIBLE_RANGE;
            item.group.visible = visible;
            if (!visible) return;
            const pose = coverflowPose(d);
            item.group.position.set(pose.x, 0.1, pose.z);
            item.group.rotation.y = pose.rotY;
            item.group.scale.setScalar(pose.scale);
            item.group.renderOrder = -Math.round(Math.abs(d) * 10);
          });
          renderer.render(scene, camera);
          frame = requestAnimationFrame(tick);
        };
        frame = requestAnimationFrame(tick);

        cleanup = () => {
          cancelAnimationFrame(frame);
          observer.disconnect();
          renderer.domElement.removeEventListener('pointerdown', onDown);
          renderer.domElement.removeEventListener('pointermove', onMove);
          renderer.domElement.removeEventListener('pointerup', onUp);
          items.forEach((item) => {
            item.texture?.dispose();
            if (item.picture.material !== placeholder) {
              (item.picture.material as import('three').Material).dispose();
            }
            item.picture.geometry.dispose();
            item.frame.geometry.dispose();
            item.shadow.geometry.dispose();
          });
          frameMaterial.dispose();
          shadowMaterial.dispose();
          placeholder.dispose();
          renderer.dispose();
          renderer.domElement.remove();
        };
      })
      .catch(() => {
        if (!disposed) setFailed(true);
      });

    return () => {
      disposed = true;
      cleanup();
    };
  }, [photos]);

  if (failed) return null;
  return <div ref={host} className="st-coverflow" aria-hidden="true" />;
}

/**
 * Purpose: one event's photo gallery (3D coverflow or grid), viewer, downloads, "Not me".
 * Inputs: eventId (route). Outputs: page element.
 * Side effects: GET /discover/photos; downloads; POST not-me; localStorage view choice.
 */
export default function EventGalleryPage() {
  const { t } = useTranslation();
  const { eventId = '' } = useParams();
  const { me, loading } = useCurrentUser();
  const [group, setGroup] = useState<EventPhotos | null | undefined>(undefined);
  useEventTheme(group?.event_name);
  const [error, setError] = useState('');
  const [index, setIndex] = useState(0);
  const [viewing, setViewing] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [capable] = useState(canUse3d);
  const [mode, setMode] = useState<ViewMode>(() => {
    try {
      const saved = window.localStorage.getItem(VIEW_KEY);
      if (saved === 'grid' || saved === '3d') return saved;
    } catch {
      // storage blocked: use the default
    }
    return prefersReducedMotion() ? 'grid' : '3d';
  });
  const view: ViewMode = capable ? mode : 'grid';

  const load = useCallback(() => {
    api<{ events: EventPhotos[] }>('/discover/photos')
      .then((value) => setGroup(value.events.find((e) => e.event_id === eventId) ?? null))
      .catch(() => setError(t('photos.loadFailed')));
  }, [eventId, t]);

  useEffect(() => {
    if (me) load();
  }, [me, load]);

  const count = group?.photos.length ?? 0;
  const step = useCallback(
    (delta: number) => {
      if (count === 0) return;
      setMoreOpen(false);
      setIndex((i) => Math.min(Math.max(i + delta, 0), count - 1));
    },
    [count],
  );

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setViewing(false);
      if (event.key === 'ArrowRight') step(1);
      if (event.key === 'ArrowLeft') step(-1);
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [step]);

  function chooseMode(next: ViewMode) {
    setMode(next);
    try {
      window.localStorage.setItem(VIEW_KEY, next);
    } catch {
      // storage blocked: the choice lasts for this visit only
    }
  }

  async function downloadAll() {
    for (const photo of group?.photos ?? []) {
      downloadOriginal(photo.download_path);
      await new Promise((resolve) => setTimeout(resolve, DOWNLOAD_GAP_MS));
    }
  }

  async function notMe(photoId: string) {
    try {
      await api(`/events/${eventId}/gallery/${photoId}/not-me`, { method: 'POST' });
      setViewing(false);
      setMoreOpen(false);
      setGroup((current) =>
        current
          ? { ...current, photos: current.photos.filter((p) => p.photo_id !== photoId) }
          : current,
      );
      setIndex((i) => Math.max(0, Math.min(i, count - 2)));
    } catch {
      setError(t('photos.notMeFailed'));
    }
  }

  if (!loading && !me) return <Navigate to="/" replace />;

  if (error && group === undefined) {
    return (
      <section className="st-empty">
        <div>
          <p className="st-alert" role="alert">
            {error}
          </p>
          <button type="button" className="st-btn st-btn-ghost" onClick={load}>
            {t('photos.retry')}
          </button>
        </div>
      </section>
    );
  }

  if (group === undefined) {
    return (
      <section className="st-searching" aria-busy="true">
        <div className="st-aperture" aria-hidden="true" />
      </section>
    );
  }

  if (group === null || group.photos.length === 0) {
    return (
      <section className="st-empty">
        <div>
          <h1>{t('gallery3d.noneTitle')}</h1>
          <p>{t('gallery3d.noneText')}</p>
          <Link to="/photos" className="st-btn st-btn-primary">
            {t('photos.allEvents')}
          </Link>
        </div>
      </section>
    );
  }

  const current = group.photos[Math.min(index, group.photos.length - 1)];

  return (
    <>
      <div className="st-gallery-head">
        <div>
          <Link to="/photos" className="st-back">
            ← {t('photos.allEvents')}
          </Link>
          <h1>{group.event_name}</h1>
          <p className="st-count">{t('photos.inEvent', { count: group.photos.length })}</p>
        </div>
        <div className="st-bar-actions">
          {capable && (
            <button
              type="button"
              className="st-btn st-btn-ghost st-btn-small"
              onClick={() => chooseMode(view === '3d' ? 'grid' : '3d')}
            >
              {view === '3d' ? t('gallery3d.showGrid') : t('gallery3d.show3d')}
            </button>
          )}
          <button
            type="button"
            className="st-btn st-btn-ghost st-btn-small"
            onClick={() => void downloadAll()}
          >
            ↓ {t('photos.downloadAll')}
          </button>
        </div>
      </div>
      {error && (
        <p className="st-alert" role="alert">
          {error}
        </p>
      )}

      {view === '3d' ? (
        <section className="st-stage" aria-label={t('gallery3d.stage')}>
          <Coverflow
            photos={group.photos}
            index={index}
            onIndex={setIndex}
            onOpen={() => setViewing(true)}
          />
          <div className="st-stage-controls">
            <button
              type="button"
              className="st-icon-btn"
              onClick={() => step(-1)}
              disabled={index === 0}
              aria-label={t('photos.previous')}
            >
              ‹
            </button>
            <span className="st-count" aria-live="polite">
              {index + 1} / {group.photos.length}
            </span>
            <button
              type="button"
              className="st-icon-btn"
              onClick={() => step(1)}
              disabled={index >= group.photos.length - 1}
              aria-label={t('photos.next')}
            >
              ›
            </button>
            <button
              type="button"
              className="st-btn st-btn-primary st-btn-small"
              onClick={() => setViewing(true)}
            >
              {t('gallery3d.open')}
            </button>
          </div>
          <p className="st-fine">{t('gallery3d.hint')}</p>
        </section>
      ) : (
        <div className="st-grid">
          {group.photos.map((photo, photoIndex) => (
            <button
              type="button"
              className="st-tile"
              key={photo.photo_id}
              style={{ animationDelay: `${Math.min(photoIndex, 12) * 40}ms` }}
              onClick={() => {
                setIndex(photoIndex);
                setViewing(true);
              }}
              aria-label={t('photos.open', { n: photoIndex + 1 })}
            >
              <img src={photo.url} alt="" loading="lazy" />
            </button>
          ))}
        </div>
      )}

      {viewing && current && (
        <div className="st-lightbox" role="dialog" aria-modal="true" aria-label={group.event_name}>
          <div className="st-lightbox-top">
            <span>
              {group.event_name} · {index + 1}/{group.photos.length}
            </span>
            <button
              type="button"
              className="st-icon-btn"
              onClick={() => setViewing(false)}
              aria-label={t('photos.close')}
            >
              ✕
            </button>
          </div>
          <div
            className="st-lightbox-stage"
            onTouchStart={(event) => {
              event.currentTarget.dataset.startX = String(event.touches[0]?.clientX ?? 0);
            }}
            onTouchEnd={(event) => {
              const start = Number(event.currentTarget.dataset.startX ?? '0');
              const end = event.changedTouches[0]?.clientX ?? start;
              if (Math.abs(end - start) > 50) step(end < start ? 1 : -1);
            }}
          >
            <img src={current.url} alt="" />
            {group.photos.length > 1 && (
              <>
                <button
                  type="button"
                  className="st-icon-btn st-nav st-nav-prev"
                  onClick={() => step(-1)}
                  aria-label={t('photos.previous')}
                >
                  ‹
                </button>
                <button
                  type="button"
                  className="st-icon-btn st-nav st-nav-next"
                  onClick={() => step(1)}
                  aria-label={t('photos.next')}
                >
                  ›
                </button>
              </>
            )}
          </div>
          <div className="st-lightbox-bottom">
            <div className="st-more">
              <button
                type="button"
                className="st-icon-btn"
                onClick={() => setMoreOpen((open) => !open)}
                aria-expanded={moreOpen}
                aria-label={t('photos.more')}
              >
                ⋯
              </button>
              {moreOpen && (
                <div className="st-more-list">
                  <button type="button" onClick={() => void notMe(current.photo_id)}>
                    {t('photos.notMe')}
                  </button>
                </div>
              )}
            </div>
            <button
              type="button"
              className="st-btn st-btn-primary"
              onClick={() => downloadOriginal(current.download_path)}
            >
              ↓ {t('photos.download')}
            </button>
          </div>
        </div>
      )}
    </>
  );
}
