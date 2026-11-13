// Route table. Every page uses the one site frame (SiteLayout) and its role-based menu:
// guests: / (sign in) -> /scan -> /photos (events) -> /photos/:eventId (3D gallery), /find,
// /privacy, /e/:eventId (QR / invite link);
// event owners and photographers: /studio and /studio/events/:eventId/*; admins: /admin.
import { createBrowserRouter } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { SiteLayout } from '@/components/site/SiteLayout';
import LoginPage from '@/pages/guest/LoginPage';
import FaceScanPage from '@/pages/guest/FaceScanPage';
import MyPhotosPage from '@/pages/guest/MyPhotosPage';
import EventGalleryPage from '@/pages/guest/EventGalleryPage';
import PrivacyPage from '@/pages/guest/PrivacyPage';
import FindMorePhotosPage from '@/pages/guest/FindMorePhotosPage';
import EventLinkPage from '@/pages/guest/EventLinkPage';
import OwnerUploadPage from '@/pages/owner/OwnerUploadPage';
import EventSettingsPage from '@/pages/owner/EventSettingsPage';
import UploadStatusPage from '@/pages/owner/UploadStatusPage';
import ClaimReviewPage from '@/pages/owner/ClaimReviewPage';
import AdminHealthPage from '@/pages/admin/AdminHealthPage';

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
    children: [
      { index: true, element: <LoginPage /> },
      { path: 'scan', element: <FaceScanPage /> },
      { path: 'photos', element: <MyPhotosPage /> },
      { path: 'photos/:eventId', element: <EventGalleryPage /> },
      { path: 'find', element: <FindMorePhotosPage /> },
      { path: 'privacy', element: <PrivacyPage /> },
      { path: 'e/:eventId', element: <EventLinkPage /> },
      { path: 'studio', element: <OwnerUploadPage /> },
      { path: 'studio/events/:eventId/settings', element: <EventSettingsPage /> },
      { path: 'studio/events/:eventId/status', element: <UploadStatusPage /> },
      { path: 'studio/events/:eventId/claims', element: <ClaimReviewPage /> },
      { path: 'admin', element: <AdminHealthPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];

export const router = createBrowserRouter(routes);
