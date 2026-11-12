import { cleanup, render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Providers } from '@/url/providers';
import { routes } from '@/url/router';

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
  cleanup();
  vi.unstubAllGlobals();
});

describe('router', () => {
  it('renders the simple welcome page when signed out', async () => {
    renderAt('/');
    expect(
      await screen.findByRole('heading', { name: 'Every photo of you, in one scan.', level: 1 }),
    ).toBeInTheDocument();
    expect(screen.getByText('Sign in with Google')).toBeInTheDocument();
  });

  it('sends signed-out visitors from the scan and photos pages to the welcome page', async () => {
    renderAt('/photos');
    expect(
      await screen.findByRole('heading', { name: 'Every photo of you, in one scan.', level: 1 }),
    ).toBeInTheDocument();
  });

  it('keeps the older staff sign-in page on /staff', async () => {
    renderAt('/staff');
    expect(await screen.findByRole('heading', { name: 'FaceScan', level: 1 })).toBeInTheDocument();
    expect(
      await screen.findByRole('button', { name: 'Email me a sign-in link' }),
    ).toBeInTheDocument();
  });

  it('renders the selfie page title', () => {
    renderAt('/events/demo/selfie');
    expect(screen.getByRole('heading', { name: 'Take a selfie' })).toBeInTheDocument();
  });

  it('renders the selfie page with no link to scene-search', () => {
    const eventId = '11111111-1111-1111-1111-111111111111';
    renderAt(`/events/${eventId}/selfie`);
    expect(document.querySelector(`a[href*="/events/${eventId}/scene-search"]`)).toBeNull();
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
