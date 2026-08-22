import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth/AuthContext.js';
import { RequireAuth } from './auth/RequireAuth.js';
import { AppShell } from './components/AppShell.js';
import { EventsPage } from './pages/EventsPage.js';
import { EventDetailPage } from './pages/EventDetailPage.js';
import { ShowPage } from './pages/ShowPage.js';
import { LoginPage } from './pages/LoginPage.js';
import { RegisterPage } from './pages/RegisterPage.js';
import { AdminVenuesPage } from './pages/AdminVenuesPage.js';
import { OrganiserPage } from './pages/OrganiserPage.js';
import './styles/base.css';

const root = document.getElementById('root');
if (!root) throw new Error('#root missing from index.html');

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route element={<AppShell />}>
            {/* Public */}
            <Route path="/" element={<EventsPage />} />
            <Route path="/events/:id" element={<EventDetailPage />} />
            {/* Public: browsing the seat map needs no account. Holding one does. */}
            <Route path="/shows/:id" element={<ShowPage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />

            {/* Role-gated. The guard hides the page; the server's requireRole
                is what actually protects the data behind it. */}
            <Route
              path="/manage"
              element={
                <RequireAuth roles={['ORGANISER', 'ADMIN']}>
                  <OrganiserPage />
                </RequireAuth>
              }
            />
            <Route
              path="/admin/venues"
              element={
                <RequireAuth roles={['ADMIN']}>
                  <AdminVenuesPage />
                </RequireAuth>
              }
            />

            {/* Phase 4 fills this in. Wired now so the redirect is proven. */}
            <Route
              path="/bookings"
              element={
                <RequireAuth>
                  <p className="prose">Your bookings will appear here from Phase 4.</p>
                </RequireAuth>
              }
            />

            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
