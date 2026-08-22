import { Navigate, useLocation } from 'react-router-dom';
import type { Role } from '@ticket/shared';
import { useAuth } from './AuthContext.js';
import { SpinnerIcon } from '../components/icons.js';

/**
 * Route guard. Convenience only — it hides a page, it does not protect data.
 * Every one of these has a matching requireRole() on the server, and that is
 * the one doing the actual work.
 */
export function RequireAuth({
  roles,
  children,
}: {
  roles?: readonly Role[];
  children: React.ReactNode;
}) {
  const { user, loading } = useAuth();
  const location = useLocation();

  // Redirecting before the stored token has been checked bounces a signed-in
  // person to the login screen on every refresh.
  if (loading) {
    return (
      <div className="route-pending" role="status" aria-live="polite">
        <SpinnerIcon size={22} />
        <span className="sr-only">Checking your session</span>
      </div>
    );
  }

  // `state` carries where they were headed, so login can send them back
  // instead of dumping them on the home page.
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />;

  if (roles && !roles.includes(user.role)) return <Navigate to="/" replace />;

  return <>{children}</>;
}
