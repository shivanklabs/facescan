// Route table.
// Simple site (milestone 7): / (Google sign-in) -> /scan -> /photos; /studio for the owner.
// Older pages stay reachable on their own paths (not linked from the simple site).
import { createBrowserRouter } from 'react-router-dom';
import { AppLayout } from '@/components/ui/AppLayout';
import { StudioLayout } from '@/components/simple/StudioLayout';
import WelcomePage from '@/pages/simple/WelcomePage';
import ScanPage from '@/pages/simple/ScanPage';
import MyPhotosPage from '@/pages/simple/MyPhotosPage';
import StudioPage from '@/pages/simple/StudioPage';
import HomePage from '@/pages/HomePage';
import AuthCallbackPage from '@/pages/AuthCallbackPage';
import EventLandingPage from '@/pages/guest/EventLandingPage';
import ConsentPage from '@/pages/guest/ConsentPage';
import SelfieCapturePage from '@/pages/guest/SelfieCapturePage';
import GalleryPage from '@/pages/guest/GalleryPage';
import ClaimPhotoPage from '@/pages/guest/ClaimPhotoPage';
import PrivacySettingsPage from '@/pages/guest/PrivacySettingsPage';
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
    element: <StudioLayout />,
    children: [
      { index: true, element: <WelcomePage /> },
      { path: 'scan', element: <ScanPage /> },
      { path: 'photos', element: <MyPhotosPage /> },
      { path: 'studio', element: <StudioPage /> },
    ],
  },
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { path: 'staff', element: <HomePage /> },
      { path: 'auth/callback', element: <AuthCallbackPage /> },
      { path: 'e/:eventId', element: <EventLandingPage /> },
      { path: 'events/:eventId/consent', element: <ConsentPage /> },
      { path: 'events/:eventId/selfie', element: <SelfieCapturePage /> },
      { path: 'events/:eventId/gallery', element: <GalleryPage /> },
      { path: 'events/:eventId/claim/:photoId', element: <ClaimPhotoPage /> },
      { path: 'privacy', element: <PrivacySettingsPage /> },
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
