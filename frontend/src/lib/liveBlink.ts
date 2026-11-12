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

const EYES_OPEN_MAX = 0.3; // blink blendshape score below this: eyes open
const EYES_CLOSED_MIN = 0.5; // both eyes at or above this: eyes closed
const STEADY_MS = 600; // one open-eyed face for this long before the first frame
const FIND_FACE_TIMEOUT_MS = 12_000;
const BLINK_TIMEOUT_MS = 8_000;
const REOPEN_SETTLE_MS = 350;
const MAX_CLOSED_FRAMES = 2;
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

interface EyeReading {
  faces: number;
  left: number;
  right: number;
}

/**
 * Purpose: read face count and the two eye-blink scores for the current video frame.
 * Inputs: landmarker, playing video. Outputs: EyeReading (scores 0 open .. 1 closed).
 * Side effects: none.
 */
function readEyes(model: FaceLandmarker, video: HTMLVideoElement): EyeReading {
  const result = model.detectForVideo(video, performance.now());
  const faces = result.faceBlendshapes.length;
  const categories = result.faceBlendshapes[0]?.categories ?? [];
  const score = (name: string) => categories.find((c) => c.categoryName === name)?.score ?? 0;
  return { faces, left: score('eyeBlinkLeft'), right: score('eyeBlinkRight') };
}

/**
 * Purpose: copy the current video frame into a canvas (fast; JPEG encoding happens later).
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
 * Purpose: capture [open-eyed front, 1-2 closed-eye frames, open-eyed front] by watching the
 *   eyes live. Inputs: playing video, step callback, AbortSignal to stop when leaving the page.
 * Outputs: JPEG blobs (memory only).
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

  // 1. One face, eyes open, held steady -> front frame.
  onStep('findFace');
  let steadySince = 0;
  let front: HTMLCanvasElement | null = null;
  const findDeadline = performance.now() + FIND_FACE_TIMEOUT_MS;
  while (!front) {
    stopIfAborted();
    if (performance.now() > findDeadline) throw new BlinkTimeoutError('no_face');
    const now = await nextFrame();
    const eyes = readEyes(model, video);
    const ready = eyes.faces === 1 && eyes.left < EYES_OPEN_MAX && eyes.right < EYES_OPEN_MAX;
    if (!ready) {
      steadySince = 0;
      onStep('findFace');
      continue;
    }
    onStep('lookStraight');
    if (steadySince === 0) steadySince = now;
    if (now - steadySince >= STEADY_MS) front = grab(video);
  }

  // 2. Blink: take the frames where both eyes are closed.
  onStep('blink');
  const closed: HTMLCanvasElement[] = [];
  const blinkDeadline = performance.now() + BLINK_TIMEOUT_MS;
  let reopened = false;
  while (!reopened) {
    stopIfAborted();
    if (performance.now() > blinkDeadline) throw new BlinkTimeoutError('no_blink');
    await nextFrame();
    const eyes = readEyes(model, video);
    if (eyes.faces !== 1) continue;
    const isClosed = eyes.left >= EYES_CLOSED_MIN && eyes.right >= EYES_CLOSED_MIN;
    if (isClosed && closed.length < MAX_CLOSED_FRAMES) closed.push(grab(video));
    if (closed.length > 0 && eyes.left < EYES_OPEN_MAX && eyes.right < EYES_OPEN_MAX) {
      reopened = true;
    }
  }

  // 3. Eyes open again -> last front frame.
  onStep('eyesOpen');
  await new Promise((resolve) => setTimeout(resolve, REOPEN_SETTLE_MS));
  stopIfAborted();
  const back = grab(video);
  return Promise.all([front, ...closed, back].map(toJpeg));
}
