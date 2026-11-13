import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
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
  it('offers passkey sign-up and sign-in when the browser supports passkeys', async () => {
    vi.stubGlobal('PublicKeyCredential', function PublicKeyCredential() {});
    renderAt('/');
    expect(
      await screen.findByRole('heading', { name: 'Every photo of you, in one scan.', level: 1 }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start with a passkey' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: "I've been here before" })).toBeInTheDocument();
    expect(screen.queryByText(/Google/)).toBeNull();
  });

  it('asks in-app browsers without passkeys to open the page in a real browser', async () => {
    renderAt('/');
    expect(await screen.findByRole('link', { name: 'Open in browser' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start with a passkey' })).toBeNull();
  });

  it('sends signed-out visitors from the scan and photos pages to the welcome page', async () => {
    renderAt('/photos');
    expect(
      await screen.findByRole('heading', { name: 'Every photo of you, in one scan.', level: 1 }),
    ).toBeInTheDocument();
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
  flags: { can_create_events: boolean; is_admin: boolean; is_staff?: boolean },
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

describe('owner pages', () => {
  it('retries network and server errors, but not a file that is too large', async () => {
    const { withRetry } = await import('@/pages/owner/OwnerUploadPage');
    const { ApiError } = await import('@/lib/serverApi');
    const noWait = () => Promise.resolve();
    let calls = 0;
    const flaky = () => {
      calls += 1;
      return calls < 3 ? Promise.reject(new ApiError(503, 'busy')) : Promise.resolve('ok');
    };
    await expect(withRetry(flaky, noWait)).resolves.toBe('ok');
    expect(calls).toBe(3);

    let tooLargeCalls = 0;
    const tooLarge = () => {
      tooLargeCalls += 1;
      return Promise.reject(new ApiError(413, 'too large'));
    };
    await expect(withRetry(tooLarge, noWait)).rejects.toThrow();
    expect(tooLargeCalls).toBe(1);
  });

  it('places the claimed face box in percent of the original photo', async () => {
    const { boxStyle } = await import('@/pages/owner/ClaimReviewPage');
    const style = boxStyle({
      claim_id: 'c',
      photo_id: 'p',
      photo_url: '',
      face_bbox: { x: 100, y: 50, width: 200, height: 100 },
      photo_width: 1000,
      photo_height: 500,
      claimant_email: 'Guest #1',
      reason: 'face_check_failed',
      created_at: '2026-11-12T10:00:00Z',
    });
    expect(style).toEqual({ left: '10%', top: '10%', width: '20%', height: '20%' });
  });

  it('shows every settings section to an event owner', async () => {
    signedInAs(
      { can_create_events: true, is_admin: false },
      {
        '/events/ev1': { event_id: 'ev1', name: 'Sharma Wedding', search_window_ends_at: null },
        '/invites': { invites: [] },
        '/sections': { sections: [] },
        '/cameras': { cameras: [] },
        '/staff-keys': { keys: [] },
      },
    );
    renderAt('/studio/events/ev1/settings');
    expect(await screen.findByRole('heading', { name: 'Sharma Wedding' })).toBeInTheDocument();
    for (const title of [
      'Invite link and QR code',
      'Parts of the event',
      'Search window',
      'Staff keys',
      'Camera clocks',
    ]) {
      expect(screen.getByRole('heading', { name: title })).toBeInTheDocument();
    }
    // Photographers are added with a one-time key, never by email.
    expect(screen.queryByRole('textbox', { name: 'Email' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Create photographer key' })).toBeInTheDocument();
  });

  it('lists failed photos with a retry button on the upload status page', async () => {
    signedInAs(
      { can_create_events: true, is_admin: false },
      {
        '/processing-status': {
          quarantined: 0,
          approved: 1,
          indexed: 8,
          failed: 1,
          near_duplicates: 2,
          failed_photos: [
            { id: 'p9', filename: 'IMG_0009.JPG', created_at: '2026-11-12T10:00:00Z' },
          ],
        },
      },
    );
    renderAt('/studio/events/ev1/status');
    expect(await screen.findByText('IMG_0009.JPG')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.getByText(/2 photos look almost the same/)).toBeInTheDocument();
  });
});

describe('admin health page', () => {
  it('shows disk, backup warning and per-event counts in the site layout', async () => {
    signedInAs(
      { can_create_events: false, is_admin: true },
      {
        '/admin/health': {
          system: {
            disk_free_bytes: 50 * 1024 ** 3,
            disk_total_bytes: 100 * 1024 ** 3,
            disk_used_percent: 50,
            last_backup_at: null,
          },
          events: [
            {
              event_id: 'e1',
              name: 'Sharma Wedding',
              status: 'searchable',
              photos_quarantined: 0,
              photos_approved: 0,
              photos_indexed: 9,
              photos_failed: 1,
              photos_total: 10,
              indexing_progress_pct: 90,
              job_failures: 0,
              pending_claims: 2,
              not_me_count: 1,
            },
          ],
        },
      },
    );
    renderAt('/admin');
    expect(await screen.findByRole('heading', { name: 'Sharma Wedding' })).toBeInTheDocument();
    expect(screen.getByText('50.0 GB')).toBeInTheDocument();
    expect(screen.getByText(/older than a day/)).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Main menu' })).toHaveTextContent('Health');
    expect(screen.queryByText(/Organisers and photographers must use a passkey/)).toBeNull();
  });

  it('flags a missing or day-old backup', async () => {
    const { backupIsStale } = await import('@/pages/admin/AdminHealthPage');
    const now = Date.parse('2026-11-12T12:00:00Z');
    expect(backupIsStale(null, now)).toBe(true);
    expect(backupIsStale('2026-11-12T06:00:00Z', now)).toBe(false);
    expect(backupIsStale('2026-11-11T06:00:00Z', now)).toBe(true);
  });
});

describe('photographers in Studio', () => {
  it('shows Studio to a photographer with upload and status only', async () => {
    signedInAs(
      { can_create_events: false, is_admin: false, is_staff: true },
      {
        '/events': {
          events: [
            {
              event_id: 'ev1',
              name: 'Sharma Wedding',
              status: 'searchable',
              created_at: '2026-11-12T10:00:00Z',
              roles: ['photographer'],
            },
          ],
        },
        '/processing-status': {
          quarantined: 0,
          approved: 0,
          indexed: 0,
          failed: 0,
          near_duplicates: 0,
          failed_photos: [],
        },
      },
    );
    renderAt('/studio');
    const menu = await screen.findByRole('navigation', { name: 'Main menu' });
    expect(menu).toHaveTextContent('Studio');
    expect(await screen.findByRole('heading', { name: 'Sharma Wedding' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Upload status' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Settings' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Claims' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Create event' })).toBeNull();
  });

  it("lists photographers' uploads waiting for the organiser's approval", async () => {
    signedInAs(
      { can_create_events: true, is_admin: false },
      {
        '/collections': {
          collections: [
            { collection_id: 'c1', status: 'open', photos: 12, previews: [] },
            { collection_id: 'c2', status: 'approved', photos: 5, previews: [] },
          ],
        },
        '/processing-status': {
          quarantined: 12,
          approved: 0,
          indexed: 5,
          failed: 0,
          near_duplicates: 0,
          failed_photos: [],
        },
      },
    );
    renderAt('/studio/events/ev1/status');
    expect(
      await screen.findByRole('heading', { name: 'Uploads waiting for your approval' }),
    ).toBeInTheDocument();
    expect(screen.getByText('12 photos')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Approve' })).toHaveLength(1);
  });
});

const photosResponse = {
  events: [
    {
      event_id: 'ev1',
      event_name: 'Sharma Wedding',
      photos: [
        { photo_id: 'p1', url: '/x/1.jpg', expires_at: '', download_path: '/d/1' },
        { photo_id: 'p2', url: '/x/2.jpg', expires_at: '', download_path: '/d/2' },
      ],
    },
    { event_id: 'ev2', event_name: 'Empty Event', photos: [] },
  ],
};

describe('events dashboard and 3D gallery', () => {
  it('shows one card per event I was found in, linking to its gallery', async () => {
    signedInAs(
      { can_create_events: false, is_admin: false },
      { '/discover/photos': photosResponse },
    );
    renderAt('/photos');
    const card = await screen.findByRole('link', { name: /Sharma Wedding/ });
    expect(card.getAttribute('href')).toBe('/photos/ev1');
    expect(card).toHaveTextContent('2');
    expect(screen.queryByText('Empty Event')).toBeNull();
  });

  it('tells the guest when the organiser rejected a claim, without showing that photo', async () => {
    signedInAs(
      { can_create_events: false, is_admin: false },
      {
        '/discover/photos': {
          events: [
            ...photosResponse.events,
            { event_id: 'ev3', event_name: 'Mehndi', photos: [], rejected_claims: 2 },
          ],
        },
      },
    );
    renderAt('/photos');
    expect(
      await screen.findByText('The organiser of Mehndi did not approve 2 photos you claimed.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Mehndi/ })).toBeNull(); // no card, no photo
  });

  it('falls back to a light grid when the device cannot do 3D', async () => {
    signedInAs(
      { can_create_events: false, is_admin: false },
      { '/discover/photos': photosResponse },
    );
    renderAt('/photos/ev1');
    expect(await screen.findByRole('heading', { name: 'Sharma Wedding' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /^Open photo/ })).toHaveLength(2);
    expect(screen.queryByRole('button', { name: '3D view' })).toBeNull(); // no WebGL here
  });

  it('sends older ?event= links to the event gallery', async () => {
    signedInAs(
      { can_create_events: false, is_admin: false },
      { '/discover/photos': photosResponse },
    );
    renderAt('/photos?event=ev1');
    expect(await screen.findByRole('heading', { name: 'Sharma Wedding' })).toBeInTheDocument();
  });

  it('places coverflow photos: front flat, neighbours turned and pushed back', async () => {
    const { coverflowPose } = await import('@/pages/guest/EventGalleryPage');
    expect(coverflowPose(0)).toEqual({ x: 0, z: -0, rotY: -0, scale: 1.15 });
    const right = coverflowPose(1);
    expect(right.x).toBeCloseTo(1.45);
    expect(right.rotY).toBeCloseTo(-0.95);
    expect(right.z).toBeCloseTo(-0.9);
    const left = coverflowPose(-2);
    expect(left.x).toBeCloseTo(-(1.45 + 0.62));
    expect(left.rotY).toBeCloseTo(0.95);
  });
});

describe('help bot', () => {
  it('matches typed questions in English, Hindi and Hinglish without any network call', async () => {
    const { matchTopic } = await import('@/components/site/HelpChat');
    expect(matchTopic('how do I delete my account?')).toBe('delete');
    expect(matchTopic('meri photo nahi mil rahi')).toBe('none');
    expect(matchTopic('यह फ़ोटो मैं नहीं हूँ, गलत है')).toBe('notMe');
    expect(matchTopic('QR link expired')).toBe('badLink');
    expect(matchTopic('what is the weather')).toBeNull();
  });

  it('opens from the bubble and answers a picked question with a link to the right page', async () => {
    const fetchMock = signedInAs({ can_create_events: false, is_admin: false });
    renderAt('/photos');
    await screen.findByRole('navigation', { name: 'Main menu' });
    const callsBefore = fetchMock.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: 'Open help' }));
    const panel = screen.getByRole('dialog', { name: 'FaceScan help' });
    fireEvent.click(within(panel).getByRole('button', { name: 'How do I delete my data?' }));
    expect(within(panel).getByText(/type DELETE to erase your account/)).toBeInTheDocument();
    expect(within(panel).getByRole('link', { name: 'Open Privacy' })).toHaveAttribute(
      'href',
      '/privacy',
    );
    fireEvent.change(within(panel).getByRole('textbox'), { target: { value: 'hello?' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Ask' }));
    expect(within(panel).getByText(/I don't know that one yet/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.length).toBe(callsBefore); // nothing typed is sent anywhere
  });
});

describe('log in as', () => {
  it('offers guest, organiser, photographer and admin on the front page', async () => {
    vi.stubGlobal('PublicKeyCredential', function PublicKeyCredential() {});
    renderAt('/?as=photographer');
    const group = await screen.findByRole('radiogroup', { name: 'Log in as' });
    for (const name of ['Guest', 'Organiser', 'Photographer', 'Admin']) {
      expect(within(group).getByRole('radio', { name })).toBeInTheDocument();
    }
    expect(within(group).getByRole('radio', { name: 'Photographer' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(screen.getByText(/Event settings > Staff keys/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Staff key/)).toBeInTheDocument();
    fireEvent.click(within(group).getByRole('radio', { name: 'Guest' }));
    expect(screen.getByRole('button', { name: 'Start with a passkey' })).toBeInTheDocument();
  });

  it('falls back to guest for an unknown ?as= value', async () => {
    const { loginRoleFrom } = await import('@/pages/guest/LoginPage');
    expect(loginRoleFrom('admin')).toBe('admin');
    expect(loginRoleFrom('root')).toBe('guest');
    expect(loginRoleFrom(null)).toBe('guest');
  });
});

describe('live scan oval', () => {
  it('sits on the mirrored face, grows with it and asks to come closer or move back', async () => {
    const { ovalFor } = await import('@/lib/cameraFrames');
    const video = { width: 1280, height: 960 }; // landscape camera
    const box = { width: 300, height: 400 }; // portrait viewfinder (video is cropped at the sides)
    const centred = ovalFor({ cx: 0.5, cy: 0.5, w: 0.25, h: 0.45 }, video, box);
    expect(centred.left).toBeCloseTo(50);
    expect(centred.top).toBeCloseTo(50);
    expect(centred.distance).toBe('ok');
    // A face on the camera's left shows on the person's right (mirror view).
    expect(ovalFor({ cx: 0.4, cy: 0.5, w: 0.25, h: 0.45 }, video, box).left).toBeGreaterThan(50);
    const far = ovalFor({ cx: 0.5, cy: 0.5, w: 0.08, h: 0.15 }, video, box);
    const near = ovalFor({ cx: 0.5, cy: 0.5, w: 0.4, h: 0.7 }, video, box);
    expect(far.distance).toBe('closer');
    expect(near.distance).toBe('farther');
    expect(near.width).toBeGreaterThan(far.width);
  });
});
