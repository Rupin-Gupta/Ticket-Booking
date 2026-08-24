import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth/AuthContext.js';
import { RequireAuth } from './auth/RequireAuth.js';
import { AppShell } from './components/AppShell.js';
import { EventsPage } from './pages/EventsPage.js';
import { EventDetailPage } from './pages/EventDetailPage.js';
import { ShowPage } from './pages/ShowPage.js';
import { CheckoutPage } from './pages/CheckoutPage.js';
import { LoginPage } from './pages/LoginPage.js';
import { RegisterPage } from './pages/RegisterPage.js';
import { AdminVenuesPage } from './pages/AdminVenuesPage.js';
import { OrganiserPage } from './pages/OrganiserPage.js';
import { DashboardPage } from './pages/DashboardPage.js';
import { BookingsPage } from './pages/BookingsPage.js';
import { TicketPage } from './pages/TicketPage.js';
import { VerifyPage } from './pages/VerifyPage.js';
import { OfferPage } from './pages/OfferPage.js';
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
            <Route
              path="/shows/:id/checkout"
              element={
                <RequireAuth>
                  <CheckoutPage />
                </RequireAuth>
              }
            />
            {/* Where a scanned QR lands. Public by necessity — the person on
                the door is not logged in. */}
            <Route path="/verify/:token" element={<VerifyPage />} />
            {/* Where the waitlist offer email lands. Reading is public — it is
                often opened on a phone that is not signed in yet — but claiming
                requires the account the offer was made to. */}
            <Route path="/offers/:token" element={<OfferPage />} />
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
              path="/manage/:id"
              element={
                <RequireAuth roles={['ORGANISER', 'ADMIN']}>
                  <DashboardPage />
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

            <Route
              path="/bookings"
              element={
                <RequireAuth>
                  <BookingsPage />
                </RequireAuth>
              }
            />
            <Route
              path="/bookings/:id"
              element={
                <RequireAuth>
                  <TicketPage />
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
