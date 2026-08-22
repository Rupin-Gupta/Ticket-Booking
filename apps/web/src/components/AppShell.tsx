import { Link, NavLink, Outlet } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext.js';
import { LogOutIcon, TicketIcon } from './icons.js';
import { ThemeToggle } from './ThemeToggle.js';
import './AppShell.css';

export function AppShell() {
  const { user, logout } = useAuth();

  return (
    <>
      {/* First tab stop on the page — lets a keyboard user skip the nav */}
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <header className="shell__header">
        <div className="shell__bar">
          <Link to="/" className="brand" aria-label="Ticket Booking, home">
            <TicketIcon size={22} />
            <span className="brand__word">Ticket</span>
          </Link>

          <nav className="shell__nav" aria-label="Main">
            <NavLink to="/" end className="navlink">
              Events
            </NavLink>
            {user && (
              <NavLink to="/bookings" className="navlink">
                My bookings
              </NavLink>
            )}
          </nav>

          <div className="shell__actions">
            <ThemeToggle />
            {user ? (
              <>
                <span className="whoami">
                  <span className="whoami__name">{user.name}</span>
                  {/* Role is meaningful to organisers and admins; a customer
                      does not need reminding what they are. */}
                  {user.role !== 'CUSTOMER' && (
                    <span className="pill">{user.role.toLowerCase()}</span>
                  )}
                </span>
                <button
                  type="button"
                  className="btn btn--quiet icon-btn"
                  onClick={logout}
                  aria-label="Log out"
                >
                  <LogOutIcon />
                </button>
              </>
            ) : (
              <>
                <Link to="/login" className="btn btn--quiet">
                  Log in
                </Link>
                <Link to="/register" className="btn btn--primary">
                  Sign up
                </Link>
              </>
            )}
          </div>
        </div>
      </header>

      <main id="main" className="shell__main">
        <Outlet />
      </main>
    </>
  );
}
