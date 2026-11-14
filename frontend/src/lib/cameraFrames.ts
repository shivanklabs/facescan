// Live selfie frames: captured from the camera into memory and sent as one raw body
// (application/x-facescan-frames: 4-byte big-endian length + JPEG bytes, per frame).
// Nothing is ever saved to disk or browser storage.

export const FRAMES_CONTENT_TYPE = 'application/x-facescan-frames';

/**
 * Purpose: open the front camera and show it in the video element once it has mounted.
 * Inputs: ref of the <video> (rendered after this resolves). Outputs: the camera stream.
 * Side effects: asks for camera permission; throws when it is refused or missing.
 */
export async function startSelfieCamera(video: {
  current: HTMLVideoElement | null;
}): Promise<MediaStream> {
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 960 } },
    audio: false,
  });
  // Wait one frame for the <video> to mount, then attach the stream.
  requestAnimationFrame(() => {
    if (video.current) {
      video.current.srcObject = stream;
      void video.current.play();
    }
  });
  return stream;
}

/**
 * Purpose: turn the camera off. Inputs: stream or null. Outputs: none.
 * Side effects: stops every track (the camera light goes off).
 */
export function stopStream(stream: MediaStream | null): void {
  stream?.getTracks().forEach((track) => track.stop());
}

/** A face in the camera picture: centre and size as fractions (0..1) of the video frame. */
export interface FaceBox {
  cx: number;
  cy: number;
  w: number;
  h: number;
}

/** The oval drawn over the camera, in percent of the viewfinder, plus a distance hint. */
export interface OvalBox {
  left: number;
  top: number;
  width: number;
  height: number;
  distance: 'closer' | 'farther' | 'ok';
}

const CLOSER_BELOW = 0.28; // face narrower than this share of the viewfinder: come closer
const FARTHER_ABOVE = 0.62; // wider than this: move back

/**
 * Purpose: place the oval around the face as the person sees it: the video fills the
 *   viewfinder with object-fit: cover and is mirrored, so the face box is scaled, cropped and
 *   flipped the same way, then given a little room around the face.
 * Inputs: face box (video fractions), video size, viewfinder size (px).
 * Outputs: OvalBox in percent of the viewfinder (clamped) with a distance hint.
 * Side effects: none.
 */
export function ovalFor(
  face: FaceBox,
  video: { width: number; height: number },
  box: { width: number; height: number },
): OvalBox {
  const scale = Math.max(box.width / video.width, box.height / video.height);
  const shownW = video.width * scale;
  const shownH = video.height * scale;
  const offsetX = (box.width - shownW) / 2;
  const offsetY = (box.height - shownH) / 2;
  const centreX = box.width - (face.cx * shownW + offsetX); // mirrored like the video
  const centreY = face.cy * shownH + offsetY;
  const faceShare = (face.w * shownW) / box.width;
  const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));
  return {
    left: clamp((centreX / box.width) * 100, 15, 85),
    top: clamp((centreY / box.height) * 100, 15, 85),
    width: clamp(faceShare * 100 * 1.5, 30, 95),
    height: clamp(((face.h * shownH) / box.height) * 100 * 1.4, 30, 95),
    distance: faceShare < CLOSER_BELOW ? 'closer' : faceShare > FARTHER_ABOVE ? 'farther' : 'ok',
  };
}

export interface ScanStep {
  key: 'lookStraight' | 'blink';
}

export async function framesBody(frames: Blob[]): Promise<Uint8Array<ArrayBuffer>> {
  const parts = await Promise.all(frames.map(async (f) => new Uint8Array(await f.arrayBuffer())));
  const total = parts.reduce((sum, p) => sum + 4 + p.length, 0);
  const body = new Uint8Array(new ArrayBuffer(total));
  const view = new DataView(body.buffer);
  let offset = 0;
  for (const part of parts) {
    view.setUint32(offset, part.length, false);
    body.set(part, offset + 4);
    offset += 4 + part.length;
  }
  return body;
}

export const BLINK_BURST_FRAMES = 5;
export const BLINK_BURST_GAP_MS = 140;
const BLINK_REACTION_MS = 250;
const SETTLE_MS = 900;

/**
 * Purpose: draw the current video frame into a new canvas (fast; no encoding yet), so a burst
 *   can be taken at an even pace while the person blinks.
 * Inputs: playing video element. Outputs: canvas holding the frame.
 * Side effects: none (memory only).
 */
export function grabCanvas(video: HTMLVideoElement): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('canvas not available');
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas;
}

/**
 * Purpose: encode a grabbed canvas as JPEG in memory.
 * Inputs: canvas. Outputs: JPEG blob. Side effects: none.
 */
function canvasToJpeg(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('could not capture frame'))),
      'image/jpeg',
      0.9,
    );
  });
}

/**
 * Purpose: simple-site scan (milestone 7): front frame, a burst of frames while the person
 *   blinks (so one of them catches the eyes closed), then a front frame again.
 * Inputs: playing video, callback for the current instruction.
 * Outputs: JPEG blobs [front, burst..., front] (memory only).
 * Side effects: calls onStep; nothing is stored.
 */
export async function captureBlinkScan(
  video: HTMLVideoElement,
  onStep: (step: ScanStep['key']) => void,
): Promise<Blob[]> {
  const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
  onStep('lookStraight');
  await wait(SETTLE_MS);
  const canvases = [grabCanvas(video)];
  onStep('blink');
  await wait(BLINK_REACTION_MS);
  for (let i = 0; i < BLINK_BURST_FRAMES; i += 1) {
    canvases.push(grabCanvas(video));
    await wait(BLINK_BURST_GAP_MS);
  }
  onStep('lookStraight');
  await wait(SETTLE_MS);
  canvases.push(grabCanvas(video));
  return Promise.all(canvases.map(canvasToJpeg));
}
