import { describe, expect, it, vi } from 'vitest';

// The MediaPipe runtime is not needed for the pure eye-limit helpers.
vi.mock('@mediapipe/tasks-vision', () => ({ FaceLandmarker: {}, FilesetResolver: {} }));

const { eyeLimits, eyesClosed, eyesOpen } = await import('@/lib/liveBlink');

describe('eyeLimits', () => {
  it('uses the default limits when no resting scores are known', () => {
    expect(eyeLimits([])).toEqual({ open: 0.3, closed: 0.5 });
  });

  it('keeps the defaults for people whose open eyes score low', () => {
    expect(eyeLimits([0.02, 0.05, 0.04, 0.03])).toEqual({ open: 0.3, closed: 0.5 });
  });

  it('raises the limits for people whose open eyes already score high', () => {
    const limits = eyeLimits([0.25, 0.25, 0.25, 0.25]);
    expect(limits.open).toBeCloseTo(0.4);
    expect(limits.closed).toBeCloseTo(0.55);
  });

  it('never raises the limits past the caps', () => {
    expect(eyeLimits([0.9, 0.9])).toEqual({ open: 0.45, closed: 0.7 });
  });
});

describe('eyesOpen / eyesClosed', () => {
  const limits = { open: 0.3, closed: 0.5 };

  it('needs both eyes for open and for closed', () => {
    expect(eyesOpen({ faces: 1, left: 0.1, right: 0.1 }, limits)).toBe(true);
    expect(eyesOpen({ faces: 1, left: 0.1, right: 0.4 }, limits)).toBe(false);
    expect(eyesClosed({ faces: 1, left: 0.8, right: 0.9 }, limits)).toBe(true);
    expect(eyesClosed({ faces: 1, left: 0.8, right: 0.4 }, limits)).toBe(false);
  });
});
