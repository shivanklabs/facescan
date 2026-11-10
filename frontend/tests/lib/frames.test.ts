import { describe, expect, it } from 'vitest';
import {
  FRONT_ONLY_STEPS,
  framesBody,
  FRAMES_CONTENT_TYPE,
  GUIDED_STEPS,
} from '@/lib/frames';

describe('framesBody', () => {
  it('prefixes every frame with its 4-byte big-endian length', async () => {
    const a = new Blob([new Uint8Array([1, 2, 3])]);
    const b = new Blob([new Uint8Array([9])]);
    const body = await framesBody([a, b]);
    expect(Array.from(body)).toEqual([0, 0, 0, 3, 1, 2, 3, 0, 0, 0, 1, 9]);
  });

  it('uses the content type the API expects', () => {
    expect(FRAMES_CONTENT_TYPE).toBe('application/x-facescan-frames');
  });

  it('defines 4-step sequence for guided scan and 3-step for front-only', () => {
    expect(GUIDED_STEPS.map((s) => s.key)).toEqual([
      'lookStraight',
      'blink',
      'turnLeft',
      'turnRight',
    ]);
    expect(FRONT_ONLY_STEPS.map((s) => s.key)).toEqual([
      'lookStraight',
      'blink',
      'lookStraight',
    ]);
  });
});
