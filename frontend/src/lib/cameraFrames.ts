// Live selfie frames: captured from the camera into memory and sent as one raw body
// (application/x-facescan-frames: 4-byte big-endian length + JPEG bytes, per frame).
// Nothing is ever saved to disk or browser storage.

export const FRAMES_CONTENT_TYPE = 'application/x-facescan-frames';
export const FRAME_COUNT = 4;
export const FRAME_INTERVAL_MS = 500;

export interface ScanStep {
  key: 'lookStraight' | 'blink';
}

export function frameToJpeg(video: HTMLVideoElement): Promise<Blob> {
  const canvas = document.createElement('canvas');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const context = canvas.getContext('2d');
  if (!context) return Promise.reject(new Error('canvas not available'));
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('could not capture frame'))),
      'image/jpeg',
      0.92,
    );
  });
}

export async function captureFrames(
  video: HTMLVideoElement,
  count = FRAME_COUNT,
  intervalMs = FRAME_INTERVAL_MS,
): Promise<Blob[]> {
  const frames: Blob[] = [];
  for (let i = 0; i < count; i += 1) {
    if (i > 0) await new Promise((resolve) => setTimeout(resolve, intervalMs));
    frames.push(await frameToJpeg(video));
  }
  return frames;
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
function grabCanvas(video: HTMLVideoElement): HTMLCanvasElement {
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
