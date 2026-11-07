// Route table for the app shell. Pages render titles only for now.
import { createBrowserRouter, Link, Outlet } from 'react-router-dom';
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

function Layout() {
  return (
    <main>
      <Outlet />
    </main>
  );
}

function HomePage() {
  return (
    <>
      <h1>FaceScan</h1>
      <p>Open your event link to find your photos.</p>
      <Link to="/privacy">Privacy settings</Link>
    </>
  );
}

function NotFoundPage() {
  return <h1>Page not found</h1>;
}

export const routes = [
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'events/:eventId', element: <EventLandingPage /> },
      { path: 'events/:eventId/consent', element: <ConsentPage /> },
      { path: 'events/:eventId/selfie', element: <SelfieCapturePage /> },
      { path: 'events/:eventId/gallery', element: <GalleryPage /> },
      { path: 'events/:eventId/claim/:photoId', element: <ClaimPhotoPage /> },
      { path: 'privacy', element: <PrivacySettingsPage /> },
      { path: 'photographer/events/:eventId/upload', element: <UploadPage /> },
      { path: 'photographer/events/:eventId/status', element: <ProcessingStatusPage /> },
      { path: 'organiser/events/new', element: <EventSetupPage /> },
      { path: 'organiser/events/:eventId/approval', element: <CollectionApprovalPage /> },
      { path: 'organiser/events/:eventId/claims', element: <ClaimQueuePage /> },
      { path: 'admin', element: <AdminDashboardPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];

export const router = createBrowserRouter(routes);
