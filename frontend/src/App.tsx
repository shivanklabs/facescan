// App: wraps the router in global providers.
import { RouterProvider } from 'react-router-dom';
import { Providers } from './url/providers';
import { router } from './url/routes';

export default function App() {
  return (
    <Providers>
      <RouterProvider router={router} />
    </Providers>
  );
}
