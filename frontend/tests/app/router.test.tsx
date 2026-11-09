import { render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Providers } from '@/app/providers';
import { routes } from '@/app/router';

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <Providers>
      <RouterProvider router={router} />
    </Providers>,
  );
}

beforeEach(() => {
  // Signed out: every API call answers 401.
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ detail: 'sign_in_required' }), { status: 401 }),
      ),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('router', () => {
  it('renders the home page with sign-in when signed out', async () => {
    renderAt('/');
    expect(await screen.findByRole('heading', { name: 'FaceScan', level: 1 })).toBeInTheDocument();
    expect(
      await screen.findByRole('button', { name: 'Email me a sign-in link' }),
    ).toBeInTheDocument();
  });

  it('renders the selfie page title', () => {
    renderAt('/events/demo/selfie');
    expect(screen.getByRole('heading', { name: 'Take a selfie' })).toBeInTheDocument();
  });

  it('shows an invalid-invite message for a bad link', async () => {
    renderAt('/e/00000000-0000-0000-0000-000000000000?k=bad');
    expect(await screen.findByRole('alert')).toHaveTextContent('invalid or has expired');
  });

  it('renders not found for unknown routes', () => {
    renderAt('/nope');
    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
  });
});
