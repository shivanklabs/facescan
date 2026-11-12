// Live blink capture (milestone 7): MediaPipe Face Landmarker runs on the phone and watches the
// eyes, so the closed-eye frame is taken at the moment of the blink instead of on a timer.
// The model and the WebAssembly files are served by FaceScan itself (no CDN), and nothing
// leaves the browser except the final frames that are sent to the API like before.
import { FaceLandmarker, FilesetResolver } from '@mediapipe/tasks-vision';
import wasmLoaderSimd from '@mediapipe/tasks-vision/vision_wasm_internal.js?url';
import wasmBinarySimd from '@mediapipe/tasks-vision/vision_wasm_internal.wasm?url';
import wasmLoaderNoSimd from '@mediapipe/tasks-vision/vision_wasm_nosimd_internal.js?url';
import wasmBinaryNoSimd from '@mediapipe/tasks-vision/vision_wasm_nosimd_internal.wasm?url';
import modelUrl from '@/assets/models/face_landmarker.task?url';

export type LiveStep = 'findFace' | 'lookStraight' | 'blink' | 'eyesOpen';

/** The live detector could not start on this device (caller falls back to the timed burst). */
export class LiveBlinkUnavailableError extends Error {}

/** No blink was seen in time. */
export class BlinkTimeoutError extends Error {}

const EYES_OPEN_MAX = 0.3; // default "eyes open" limit (raised for people who rest higher)
const EYES_CLOSED_MIN = 0.5; // lowest "eyes closed" limit
const OPEN_MARGIN = 0.15; // open limit = this person's resting score + margin
const CLOSED_MARGIN = 0.3; // closed limit = this person's resting score + margin
const OPEN_LIMIT_CAP = 0.45; // never call eyes "open" above this
const CLOSED_LIMIT_CAP = 0.7; // never ask for more than this to call eyes "closed"
const STEADY_MS = 600; // one open-eyed face for this long before the first frame
const FIND_FACE_TIMEOUT_MS = 12_000;
const BLINK_TIMEOUT_MS = 8_000;
const REOPEN_SETTLE_MS = 350;
const LOAD_TIMEOUT_MS = 20_000;

let landmarker: Promise<FaceLandmarker> | null = null;

/**
 * Purpose: load the Face Landmarker once (SIMD build when supported), from local files.
 * Inputs: none. Outputs: the landmarker. Side effects: downloads ~4 MB model + wasm from
 *   this site; rejects with LiveBlinkUnavailableError on failure or timeout.
 */
export function loadLandmarker(): Promise<FaceLandmarker> {
  if (landmarker) return landmarker;
  const load = (async () => {
    const simd = await FilesetResolver.isSimdSupported();
    const fileset = simd
      ? { wasmLoaderPath: wasmLoaderSimd, wasmBinaryPath: wasmBinarySimd }
      : { wasmLoaderPath: wasmLoaderNoSimd, wasmBinaryPath: wasmBinaryNoSimd };
    return FaceLandmarker.createFromOptions(fileset, {
      baseOptions: { modelAssetPath: modelUrl, delegate: 'CPU' },
      runningMode: 'VIDEO',
      numFaces: 2,
      outputFaceBlendshapes: true,
    });
  })();
  const timeout = new Promise<never>((_, reject) =>
    setTimeout(() => reject(new Error('landmarker_timeout')), LOAD_TIMEOUT_MS),
  );
  landmarker = Promise.race([load, timeout]).catch((error: unknown) => {
    landmarker = null; // allow a later retry
    throw new LiveBlinkUnavailableError(error instanceof Error ? error.message : 'unavailable');
  });
  return landmarker;
}

export interface EyeReading {
  faces: number;
  left: number;
  right: number;
}

export interface EyeLimits {
  open: number;
  closed: number;
}

/** Lenient limits used before this person's resting scores are known. */
const FIRST_LOOK_LIMITS: EyeLimits = { open: OPEN_LIMIT_CAP, closed: EYES_CLOSED_MIN };

/**
 * Purpose: work out this person's "open" and "closed" eye limits from their resting scores, so
 *   people whose open eyes already score high (glasses, looking down at the phone) still pass.
 * Inputs: blink scores (0 open .. 1 closed) read while the person looked straight, eyes open.
 * Outputs: EyeLimits; the defaults when no scores are given.
 * Side effects: none.
 */
export function eyeLimits(restingScores: number[]): EyeLimits {
  if (restingScores.length === 0) return { open: EYES_OPEN_MAX, closed: EYES_CLOSED_MIN };
  const sorted = [...restingScores].sort((a, b) => a - b);
  const resting = sorted[Math.floor(sorted.length / 2)] ?? 0;
  return {
    open: Math.min(OPEN_LIMIT_CAP, Math.max(EYES_OPEN_MAX, resting + OPEN_MARGIN)),
    closed: Math.min(CLOSED_LIMIT_CAP, Math.max(EYES_CLOSED_MIN, resting + CLOSED_MARGIN)),
  };
}

/**
 * Purpose: say whether both eyes are open for one reading.
 * Inputs: EyeReading, EyeLimits. Outputs: boolean. Side effects: none.
 */
export function eyesOpen(eyes: EyeReading, limits: EyeLimits): boolean {
  return eyes.left < limits.open && eyes.right < limits.open;
}

/**
 * Purpose: say whether both eyes are closed for one reading.
 * Inputs: EyeReading, EyeLimits. Outputs: boolean. Side effects: none.
 */
export function eyesClosed(eyes: EyeReading, limits: EyeLimits): boolean {
  return eyes.left >= limits.closed && eyes.right >= limits.closed;
}

/**
 * Purpose: copy the current video frame into a canvas. The eyes are then read from this same
 *   canvas, so the frame sent to the server is exactly the frame that was judged. (Before, the
 *   eyes were read from the video and a slightly later frame was sent, often with the eyes
 *   already opening, so the server refused the blink.)
 * Inputs: playing video. Outputs: canvas. Side effects: none (memory only).
 */
function grab(video: HTMLVideoElement): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('canvas not available');
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas;
}

/**
 * Purpose: read face count and the two eye-blink scores for one grabbed frame.
 * Inputs: landmarker, canvas from grab(). Outputs: EyeReading (scores 0 open .. 1 closed).
 * Side effects: none.
 */
function readEyes(model: FaceLandmarker, frame: HTMLCanvasElement): EyeReading {
  const result = model.detectForVideo(frame, performance.now());
  const faces = result.faceBlendshapes.length;
  const categories = result.faceBlendshapes[0]?.categories ?? [];
  const score = (name: string) => categories.find((c) => c.categoryName === name)?.score ?? 0;
  return { faces, left: score('eyeBlinkLeft'), right: score('eyeBlinkRight') };
}

/**
 * Purpose: encode a canvas as JPEG in memory. Inputs: canvas. Outputs: JPEG blob.
 * Side effects: none.
 */
function toJpeg(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('could not capture frame'))),
      'image/jpeg',
      0.9,
    );
  });
}

const nextFrame = () => new Promise<number>((resolve) => requestAnimationFrame(resolve));

/**
 * Purpose: capture [front, open just before the blink, most-closed blink frame, open just
 *   after, front] by watching the eyes live. One closed frame in five keeps the same
 *   open/closed mix as the quick burst, which the server's anti-spoofing average accepts.
 * Inputs: playing video, step callback, AbortSignal to stop when leaving the page.
 * Outputs: 5 JPEG blobs (memory only).
 * Raises LiveBlinkUnavailableError (no model), BlinkTimeoutError (no face or no blink in time).
 * Side effects: calls onStep; runs the on-device model on video frames.
 */
export async function captureWithLiveBlink(
  video: HTMLVideoElement,
  onStep: (step: LiveStep) => void,
  signal?: AbortSignal,
): Promise<Blob[]> {
  const model = await loadLandmarker();
  const stopIfAborted = () => {
    if (signal?.aborted) throw new BlinkTimeoutError('aborted');
  };

  // 1. One face, eyes open, held steady -> front frame. The resting eye scores seen here set
  //    this person's limits for the blink.
  onStep('findFace');
  let steadySince = 0;
  let resting: number[] = [];
  let front: HTMLCanvasElement | null = null;
  const findDeadline = performance.now() + FIND_FACE_TIMEOUT_MS;
  while (!front) {
    stopIfAborted();
    if (performance.now() > findDeadline) throw new BlinkTimeoutError('no_face');
    const now = await nextFrame();
    const frame = grab(video);
    const eyes = readEyes(model, frame);
    if (eyes.faces !== 1 || !eyesOpen(eyes, FIRST_LOOK_LIMITS)) {
      steadySince = 0;
      resting = [];
      onStep('findFace');
      continue;
    }
    onStep('lookStraight');
    resting.push(eyes.left, eyes.right);
    if (steadySince === 0) steadySince = now;
    if (now - steadySince >= STEADY_MS) front = frame;
  }
  const limits = eyeLimits(resting);

  // 2. Blink: keep the last open frame before it, the most-closed frame during it, and the
  //    first open frame after it.
  onStep('blink');
  let lastOpen: HTMLCanvasElement = front;
  let beforeBlink: HTMLCanvasElement = front;
  let closedFrame: HTMLCanvasElement | null = null;
  let closedScore = 0;
  let afterBlink: HTMLCanvasElement | null = null;
  const blinkDeadline = performance.now() + BLINK_TIMEOUT_MS;
  while (!closedFrame || !afterBlink) {
    stopIfAborted();
    if (performance.now() > blinkDeadline) throw new BlinkTimeoutError('no_blink');
    await nextFrame();
    const frame = grab(video);
    const eyes = readEyes(model, frame);
    if (eyes.faces !== 1) continue;
    if (eyesClosed(eyes, limits)) {
      if (!closedFrame) beforeBlink = lastOpen;
      const score = Math.min(eyes.left, eyes.right);
      if (score > closedScore) {
        closedScore = score;
        closedFrame = frame;
      }
    } else if (eyesOpen(eyes, limits)) {
      if (closedFrame) afterBlink = frame;
      else lastOpen = frame;
    }
  }

  // 3. Eyes open again -> last front frame.
  onStep('eyesOpen');
  await new Promise((resolve) => setTimeout(resolve, REOPEN_SETTLE_MS));
  stopIfAborted();
  const back = grab(video);
  return Promise.all([front, beforeBlink, closedFrame, afterBlink, back].map(toJpeg));
}
