import { describe, expect, it } from 'vitest';
import { API_BASE, apiUrl } from '@/lib/apiClient';

/**
 * Purpose: verify apiClient uses relative same-origin URLs in public/production mode
 * so phones on tunnel URLs never attempt to call localhost.
 * Inputs: none.
 * Outputs: none.
 * Side effects: none.
 */
describe('apiClient URL resolution', () => {
  it('uses relative /api base path and never points to localhost', () => {
    expect(API_BASE).toMatch(/^\/api\b/);
    expect(API_BASE).not.toContain('localhost');
    expect(API_BASE).not.toContain('127.0.0.1');
    expect(API_BASE).not.toContain('http://');
    expect(API_BASE).not.toContain('https://');
  });

  it('constructs relative endpoint URLs via apiUrl', () => {
    const url = apiUrl('/auth/me');
    expect(url).toBe('/api/auth/me');
    expect(url.startsWith('/')).toBe(true);
    expect(url).not.toContain('localhost');
  });
});
