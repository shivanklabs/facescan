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
import { grabCanvas, type FaceBox } from '@/lib/cameraFrames';

export type LiveStep =
  | 'findFace'
  | 'lookStraight'
  | 'blink'
  | 'eyesOpen'
  // quality hints while waiting for a good first frame
  | 'closer'
  | 'farther'
  | 'center'
  | 'straight'
  | 'light'
  | 'still';

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
const RELAX_GATE_MS = 8_000; // after this, take the best frame seen instead of waiting more
const BACK_CANDIDATES = 6; // frames looked at after the blink to pick the sharpest
const FIRST_LOOK_RESTING = [0.1, 0.1]; // resting eye scores when the gate was relaxed
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
  points?: { x: number; y: number }[]; // the face's landmarks (0..1), for the quality gate
}

export type FrameHint = 'closer' | 'farther' | 'center' | 'straight' | 'light' | 'still';

/** Face share of the picture, centring, turn, light and sharpness limits for a usable selfie. */
const QUALITY = {
  minShare: 0.2,
  maxShare: 0.75,
  maxTurn: 0.12,
  minLight: 60,
  maxLight: 225,
  minSharp: 30,
} as const;

/**
 * Purpose: judge one selfie frame before it is used (the biggest cause of wrong or missing
 *   matches is a poor selfie): face size, centring, looking straight (nose against the eye
 *   corners), light and sharpness (Laplacian variance of a small grey crop of the face).
 * Inputs: the frame canvas and the face landmarks (0..1).
 * Outputs: { hint: what to fix, or null when good; score: sharpness, to pick the best }.
 * Side effects: none (one small canvas in memory).
 */
export function frameQuality(
  frame: HTMLCanvasElement,
  points: { x: number; y: number }[],
): { hint: FrameHint | null; score: number } {
  let minX = 1;
  let minY = 1;
  let maxX = 0;
  let maxY = 0;
  for (const p of points) {
    minX = Math.min(minX, p.x);
    minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x);
    maxY = Math.max(maxY, p.y);
  }
  const share = maxX - minX;
  if (share < QUALITY.minShare) return { hint: 'closer', score: 0 };
  if (share > QUALITY.maxShare) return { hint: 'farther', score: 0 };
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  if (cx < 0.25 || cx > 0.75 || cy < 0.2 || cy > 0.8) return { hint: 'center', score: 0 };
  const nose = points[1];
  const left = points[33];
  const right = points[263];
  if (nose && left && right) {
    const eyes = Math.abs(right.x - left.x) || 1;
    if (Math.abs(nose.x - (left.x + right.x) / 2) / eyes > QUALITY.maxTurn) {
      return { hint: 'straight', score: 0 };
    }
  }
  const size = 64;
  const crop = document.createElement('canvas');
  crop.width = size;
  crop.height = size;
  const ctx = crop.getContext('2d', { willReadFrequently: true });
  if (!ctx) return { hint: null, score: 1 };
  ctx.drawImage(
    frame,
    minX * frame.width,
    minY * frame.height,
    share * frame.width,
    (maxY - minY) * frame.height,
    0,
    0,
    size,
    size,
  );
  const px = ctx.getImageData(0, 0, size, size).data;
  const grey = new Float32Array(size * size);
  let light = 0;
  for (let i = 0; i < grey.length; i++) {
    const g =
      0.299 * (px[i * 4] ?? 0) + 0.587 * (px[i * 4 + 1] ?? 0) + 0.114 * (px[i * 4 + 2] ?? 0);
    grey[i] = g;
    light += g;
  }
  light /= grey.length;
  if (light < QUALITY.minLight || light > QUALITY.maxLight) return { hint: 'light', score: 0 };
  let sum = 0;
  let sumSq = 0;
  let n = 0;
  for (let y = 1; y < size - 1; y++) {
    for (let x = 1; x < size - 1; x++) {
      const i = y * size + x;
      const lap =
        4 * (grey[i] ?? 0) -
        (grey[i - 1] ?? 0) -
        (grey[i + 1] ?? 0) -
        (grey[i - size] ?? 0) -
        (grey[i + size] ?? 0);
      sum += lap;
      sumSq += lap * lap;
      n++;
    }
  }
  const sharp = sumSq / n - (sum / n) ** 2;
  if (sharp < QUALITY.minSharp) return { hint: 'still', score: sharp };
  return { hint: null, score: sharp };
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
 * Purpose: read face count and the two eye-blink scores for one grabbed frame.
 * Inputs: landmarker, canvas from grabCanvas(). Outputs: EyeReading (scores 0 open .. 1 closed).
 * Side effects: none.
 */
function readEyes(model: FaceLandmarker, frame: HTMLCanvasElement): EyeReading {
  // The camera can report a 0 x 0 picture for a moment; the model breaks on that.
  if (frame.width === 0 || frame.height === 0) return { faces: 0, left: 0, right: 0 };
  const result = detect(model, frame);
  const faces = result.faceBlendshapes.length;
  const points = result.faceLandmarks[0];
  const categories = result.faceBlendshapes[0]?.categories ?? [];
  const score = (name: string) => categories.find((c) => c.categoryName === name)?.score ?? 0;
  return { faces, left: score('eyeBlinkLeft'), right: score('eyeBlinkRight'), points };
}

let lastTime = 0;

/**
 * Purpose: run the face model on one frame with a strictly increasing timestamp (the oval
 *   and the blink check share the model). If the model fails, it is thrown away so the next
 *   use loads a fresh one, and the caller gets LiveBlinkUnavailableError (the scan then uses
 *   the timed burst instead of stopping).
 * Inputs: model, a non-empty canvas. Outputs: the model's result.
 * Side effects: resets the cached model on failure.
 */
function detect(model: FaceLandmarker, frame: HTMLCanvasElement) {
  lastTime = Math.max(performance.now(), lastTime + 1);
  try {
    return model.detectForVideo(frame, lastTime);
  } catch (error) {
    landmarker = null;
    throw new LiveBlinkUnavailableError(error instanceof Error ? error.message : 'model failed');
  }
}

/**
 * Purpose: where the face is in the camera picture right now, for the live oval.
 * Inputs: landmarker, playing video. Outputs: centre and size as fractions of the video
 *   frame (0..1, not mirrored), or null when no face is seen.
 * Side effects: none (memory only; nothing is kept or sent).
 */
export function faceBox(model: FaceLandmarker, video: HTMLVideoElement): FaceBox | null {
  if (video.videoWidth === 0 || video.videoHeight === 0) return null;
  let points: ReturnType<FaceLandmarker['detectForVideo']>['faceLandmarks'][number] | undefined;
  try {
    points = detect(model, grabCanvas(video)).faceLandmarks[0];
  } catch {
    return null; // the oval simply stays where it is
  }
  if (!points || points.length === 0) return null;
  let minX = 1;
  let minY = 1;
  let maxX = 0;
  let maxY = 0;
  for (const p of points) {
    minX = Math.min(minX, p.x);
    minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x);
    maxY = Math.max(maxY, p.y);
  }
  return { cx: (minX + maxX) / 2, cy: (minY + maxY) / 2, w: maxX - minX, h: maxY - minY };
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
  let sharpest: { frame: HTMLCanvasElement; score: number } | null = null; // in the steady run
  let bestSeen: { frame: HTMLCanvasElement; score: number } | null = null; // any open-eyed face
  const findDeadline = performance.now() + FIND_FACE_TIMEOUT_MS;
  const relaxAt = performance.now() + RELAX_GATE_MS;
  while (!front) {
    stopIfAborted();
    if (performance.now() > findDeadline) throw new BlinkTimeoutError('no_face');
    const now = await nextFrame();
    const frame = grabCanvas(video);
    const eyes = readEyes(model, frame);
    if (eyes.faces !== 1 || !eyesOpen(eyes, FIRST_LOOK_LIMITS) || !eyes.points) {
      steadySince = 0;
      resting = [];
      sharpest = null;
      onStep('findFace');
      continue;
    }
    const quality = frameQuality(frame, eyes.points);
    if (!bestSeen || quality.score > bestSeen.score) bestSeen = { frame, score: quality.score };
    if (quality.hint) {
      // Never stuck: after a while the best frame seen so far is good enough.
      if (now > relaxAt && bestSeen) {
        front = bestSeen.frame;
        break;
      }
      steadySince = 0;
      sharpest = null;
      onStep(quality.hint);
      continue;
    }
    onStep('lookStraight');
    resting.push(eyes.left, eyes.right);
    if (!sharpest || quality.score > sharpest.score) sharpest = { frame, score: quality.score };
    if (steadySince === 0) steadySince = now;
    if (now - steadySince >= STEADY_MS) front = sharpest.frame;
  }
  if (resting.length === 0) resting = FIRST_LOOK_RESTING;
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
    const frame = grabCanvas(video);
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
  let back = grabCanvas(video);
  let backScore = -1;
  for (let i = 0; i < BACK_CANDIDATES; i++) {
    await nextFrame();
    const frame = grabCanvas(video);
    const eyes = readEyes(model, frame);
    if (eyes.faces !== 1 || !eyes.points || !eyesOpen(eyes, limits)) continue;
    const quality = frameQuality(frame, eyes.points);
    const score = quality.hint ? quality.score / 4 : quality.score;
    if (score > backScore) {
      backScore = score;
      back = frame;
    }
  }
  return Promise.all([front, beforeBlink, closedFrame, afterBlink, back].map(toJpeg));
}
