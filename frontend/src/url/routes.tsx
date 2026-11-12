// Route table.
// Simple site (milestone 7): / (Google sign-in) -> /scan -> /photos; /studio for the owner.
// Older pages stay reachable on their own paths (not linked from the simple site).
import { createBrowserRouter } from 'react-router-dom';
import { OldLayout } from '@/components/old/OldLayout';
import { SiteLayout } from '@/components/site/SiteLayout';
import LoginPage from '@/pages/guest/LoginPage';
import FaceScanPage from '@/pages/guest/FaceScanPage';
import MyPhotosPage from '@/pages/guest/MyPhotosPage';
import OwnerUploadPage from '@/pages/owner/OwnerUploadPage';
import HomePage from '@/pages/HomePage';
import AuthCallbackPage from '@/pages/AuthCallbackPage';
import ConsentPage from '@/pages/guest-old/ConsentPage';
import SelfieCapturePage from '@/pages/guest-old/SelfieCapturePage';
import GalleryPage from '@/pages/guest-old/GalleryPage';
import ClaimPhotoPage from '@/pages/guest-old/ClaimPhotoPage';
import PrivacyPage from '@/pages/guest/PrivacyPage';
import FindMorePhotosPage from '@/pages/guest/FindMorePhotosPage';
import EventLinkPage from '@/pages/guest/EventLinkPage';
import UploadPage from '@/pages/photographer/UploadPage';
import ProcessingStatusPage from '@/pages/photographer/ProcessingStatusPage';
import EventSetupPage from '@/pages/organiser/EventSetupPage';
import CollectionApprovalPage from '@/pages/organiser/CollectionApprovalPage';
import ClaimQueuePage from '@/pages/organiser/ClaimQueuePage';
import AdminDashboardPage from '@/pages/admin/AdminDashboardPage';

function NotFoundPage() {
  return <h1>Page not found</h1>;
}

export const routes = [
  {
    path: '/',
    element: <SiteLayout />,
    children: [
      { index: true, element: <LoginPage /> },
      { path: 'scan', element: <FaceScanPage /> },
      { path: 'photos', element: <MyPhotosPage /> },
      { path: 'studio', element: <OwnerUploadPage /> },
      { path: 'privacy', element: <PrivacyPage /> },
      { path: 'find', element: <FindMorePhotosPage /> },
      { path: 'e/:eventId', element: <EventLinkPage /> },
    ],
  },
  {
    path: '/',
    element: <OldLayout />,
    children: [
      { path: 'staff', element: <HomePage /> },
      { path: 'auth/callback', element: <AuthCallbackPage /> },
      { path: 'events/:eventId/consent', element: <ConsentPage /> },
      { path: 'events/:eventId/selfie', element: <SelfieCapturePage /> },
      { path: 'events/:eventId/gallery', element: <GalleryPage /> },
      { path: 'events/:eventId/claim/:photoId', element: <ClaimPhotoPage /> },
      { path: 'photographer/events/:eventId/upload', element: <UploadPage /> },
      { path: 'photographer/events/:eventId/status', element: <ProcessingStatusPage /> },
      { path: 'organiser', element: <EventSetupPage /> },
      { path: 'organiser/events/:eventId/approval', element: <CollectionApprovalPage /> },
      { path: 'organiser/events/:eventId/claims', element: <ClaimQueuePage /> },
      { path: 'admin', element: <AdminDashboardPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];

export const router = createBrowserRouter(routes);
