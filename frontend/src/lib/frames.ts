// Live selfie frames: captured from the camera into memory and sent as one raw body
// (application/x-facescan-frames: 4-byte big-endian length + JPEG bytes, per frame).
// Nothing is ever saved to disk or browser storage.

export const FRAMES_CONTENT_TYPE = 'application/x-facescan-frames';
export const FRAME_COUNT = 4;
export const FRAME_INTERVAL_MS = 500; // 4 frames over about two seconds

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
