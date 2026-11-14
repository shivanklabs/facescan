// Route table. Every page uses the one site frame (SiteLayout) and its role-based menu:
// guests: / (sign in) -> /scan -> /photos (events) -> /photos/:eventId (3D gallery), /find,
// /privacy, /e/:eventId (QR / invite link);
// event owners and photographers: /studio and /studio/events/:eventId/*; admins: /admin.
import { createBrowserRouter } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { SiteLayout } from '@/components/site/SiteLayout';
import type { ComponentType } from 'react';
import LoginPage from '@/pages/guest/LoginPage';
import EventLinkPage from '@/pages/guest/EventLinkPage';

/**
 * Purpose: load a page's code only when its route is opened, so a guest's first visit does
 *   not download Studio, admin or gallery code (the two entry pages above load at once).
 * Inputs: a dynamic import of a page module. Outputs: a react-router `lazy` function.
 * Side effects: fetches the page's JS chunk on first navigation.
 */
function page(load: () => Promise<{ default: ComponentType }>) {
  return async () => ({ Component: (await load()).default });
}

/**
 * Purpose: the page for unknown addresses, in the site design.
 * Inputs: none. Outputs: page element. Side effects: none.
 */
function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <section className="st-page">
      <h1>{t('notFound.title')}</h1>
      <p className="st-lead-small">{t('notFound.text')}</p>
    </section>
  );
}

export const routes = [
  {
    path: '/',
    element: <SiteLayout />,
    // Shown for a moment while a page's code loads (no empty flash, no router warning).
    hydrateFallbackElement: <div className="st-searching" aria-busy="true" />,
    children: [
      { index: true, element: <LoginPage /> },
      { path: 'scan', lazy: page(() => import('@/pages/guest/FaceScanPage')) },
      { path: 'photos', lazy: page(() => import('@/pages/guest/MyPhotosPage')) },
      { path: 'photos/:eventId', lazy: page(() => import('@/pages/guest/EventGalleryPage')) },
      { path: 'find', lazy: page(() => import('@/pages/guest/FindMorePhotosPage')) },
      { path: 'privacy', lazy: page(() => import('@/pages/guest/PrivacyPage')) },
      { path: 'e/:eventId', element: <EventLinkPage /> },
      { path: 'studio', lazy: page(() => import('@/pages/owner/OwnerUploadPage')) },
      {
        path: 'studio/events/:eventId/settings',
        lazy: page(() => import('@/pages/owner/EventSettingsPage')),
      },
      {
        path: 'studio/events/:eventId/status',
        lazy: page(() => import('@/pages/owner/UploadStatusPage')),
      },
      {
        path: 'studio/events/:eventId/claims',
        lazy: page(() => import('@/pages/owner/ClaimReviewPage')),
      },
      { path: 'admin', lazy: page(() => import('@/pages/admin/AdminHealthPage')) },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];

export const router = createBrowserRouter(routes);
