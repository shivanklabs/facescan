import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Providers } from '@/url/providers';
import { routes } from '@/url/routes';

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

/**
 * Purpose: answer /auth/me with the given flags and every other call with an empty list.
 * Inputs: user flags. Outputs: the fetch mock (to count calls). Side effects: stubs fetch.
 */
function signedInAs(
  flags: { can_create_events: boolean; is_admin: boolean },
  extra: Record<string, unknown> = {},
) {
  const me = {
    user_id: 'u1',
    auth_method: 'google',
    has_passkey: false,
    age_answered: true,
    is_minor: false,
    ...flags,
  };
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString();
    const path = Object.keys(extra).find((p) => url.endsWith(p));
    const body = url.endsWith('/auth/me') ? me : path ? extra[path] : { events: [] };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('site menu', () => {
  it('shows a guest only their own pages', async () => {
    signedInAs({ can_create_events: false, is_admin: false });
    renderAt('/photos');
    const menu = await screen.findByRole('navigation', { name: 'Main menu' });
    expect(menu).toHaveTextContent('My photos');
    expect(menu).toHaveTextContent('Scan');
    expect(menu).toHaveTextContent('Privacy');
    expect(menu).not.toHaveTextContent('Studio');
    expect(menu).not.toHaveTextContent('Health');
  });

  it('adds Studio for event owners and Health for admins', async () => {
    signedInAs({ can_create_events: true, is_admin: true });
    renderAt('/photos');
    const menu = await screen.findByRole('navigation', { name: 'Main menu' });
    expect(menu).toHaveTextContent('Studio');
    expect(menu).toHaveTextContent('Health');
  });

  it('asks the server who is signed in only once per page view', async () => {
    const fetchMock = signedInAs({ can_create_events: false, is_admin: false });
    renderAt('/photos');
    await screen.findByRole('navigation', { name: 'Main menu' });
    const meCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/auth/me'));
    expect(meCalls).toHaveLength(1);
  });
});

describe('privacy page', () => {
  it('shows one line per event, on or off, ignoring rows closed by a new notice', async () => {
    signedInAs(
      { can_create_events: false, is_admin: false },
      {
        '/me/consents': {
          consents: [
            // Event A: old notice row closed when the new one was given -> still On.
            {
              event_id: 'a',
              event_name: 'Event A',
              purpose: 'face_search',
              granted_at: '2026-11-10T10:00:00Z',
              withdrawn_at: '2026-11-12T10:00:00Z',
            },
            {
              event_id: 'a',
              event_name: 'Event A',
              purpose: 'face_search',
              granted_at: '2026-11-12T10:00:00Z',
              withdrawn_at: null,
            },
            // Event B: withdrawn by the person -> Off.
            {
              event_id: 'b',
              event_name: 'Event B',
              purpose: 'face_search',
              granted_at: '2026-11-11T10:00:00Z',
              withdrawn_at: '2026-11-12T11:00:00Z',
            },
          ],
        },
      },
    );
    renderAt('/privacy');
    const rows = await screen.findAllByRole('listitem');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('Event A');
    expect(rows[0]).toHaveTextContent('On');
    expect(rows[0]).toHaveTextContent('Turn off');
    expect(rows[1]).toHaveTextContent('Event B');
    expect(rows[1]).toHaveTextContent('Off');
    expect(rows[1]).toHaveTextContent('Turn back on');
  });
});

describe('find more photos page', () => {
  it('searches an event and puts a tappable box on each face of the preview', async () => {
    signedInAs(
      { can_create_events: false, is_admin: false },
      {
        '/me/consents': {
          consents: [
            {
              event_id: 'ev1',
              event_name: 'Wedding',
              purpose: 'face_search',
              granted_at: '2026-11-12T10:00:00Z',
              withdrawn_at: null,
            },
          ],
        },
        '/sections': { sections: [] },
        '/scene-search': {
          query_rewritten: false,
          candidates: [
            {
              photo_id: 'p1',
              preview_url: '/events/ev1/scene-search/photos/p1/preview?expires=1&signature=x',
              faces: [{ face_id: 'f1', bbox: [100, 50, 200, 100] }],
              photo_width: 1000,
              photo_height: 500,
            },
          ],
        },
      },
    );
    renderAt('/find');
    expect(await screen.findByRole('option', { name: 'Wedding' })).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(/dancing on stage/), {
      target: { value: 'cake cutting' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    const face = await screen.findByRole('button', { name: 'This is me (face 1)' });
    expect(face.style.left).toBe('10%');
    expect(face.style.top).toBe('10%');
    expect(face.style.width).toBe('20%');
    expect(face.style.height).toBe('20%');
    fireEvent.click(face);
    expect(face).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Open camera' })).toBeInTheDocument();
  });
});

describe('invite link page', () => {
  it('says the link does not work when the key is missing (no endless loading)', async () => {
    renderAt('/e/00000000-0000-0000-0000-000000000000');
    expect(await screen.findByRole('alert')).toHaveTextContent('invalid or has expired');
  });

  it('asks signed-out people to sign in and come back to the same link', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith('/resolve')) {
          return Promise.resolve(new Response(JSON.stringify({ name: 'Sharma Wedding' })));
        }
        return Promise.resolve(new Response('{"detail":"sign_in_required"}', { status: 401 }));
      }),
    );
    const eventId = '11111111-1111-1111-1111-111111111111';
    renderAt(`/e/${eventId}?k=abcdefghijklmnopqrstuv`);
    expect(await screen.findByRole('heading', { name: 'Sharma Wedding' })).toBeInTheDocument();
    const signIn = screen.getByRole('link', { name: 'Sign in' });
    expect(signIn.getAttribute('href')).toBe(
      `/?next=${encodeURIComponent(`/e/${eventId}?k=abcdefghijklmnopqrstuv`)}`,
    );
  });
});

describe('return address after sign-in', () => {
  it('allows only invite links and falls back to My photos', async () => {
    const { afterSignInPath } = await import('@/pages/guest/LoginPage');
    const invite = '/e/11111111-1111-1111-1111-111111111111?k=abcdefghijklmnopqrstuv';
    expect(afterSignInPath(invite)).toBe(invite);
    expect(afterSignInPath(null)).toBe('/photos');
    expect(afterSignInPath('https://evil.example/e/x')).toBe('/photos');
    expect(afterSignInPath('//evil.example')).toBe('/photos');
    expect(afterSignInPath('/admin')).toBe('/photos');
  });
});
