import { Link } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext.js';
import { Card } from '../components/ui.js';
import './home.css';

/**
 * Placeholder until Phase 2 brings real events. It states honestly what works
 * and what does not, rather than pretending to be a finished home page.
 */
export function HomePage() {
  const { user } = useAuth();

  const phases = [
    {
      n: 'Phase 1',
      title: 'Accounts and roles',
      state: 'done' as const,
      note: 'Sign up, log in, customer / organiser / admin.',
    },
    {
      n: 'Phase 2',
      title: 'Events and shows',
      state: 'next' as const,
      note: 'Browse and filter events, pick a show.',
    },
    {
      n: 'Phase 3',
      title: 'Seat map and holds',
      state: 'todo' as const,
      note: 'Pick seats, held on a timer while you check out.',
    },
    {
      n: 'Phase 4',
      title: 'Booking and tickets',
      state: 'todo' as const,
      note: 'Confirm a booking, QR ticket by email.',
    },
    {
      n: 'Phase 5',
      title: 'Waitlist',
      state: 'todo' as const,
      note: 'Join a queue when a show sells out.',
    },
  ];

  return (
    <div className="home">
      <section className="hero">
        <p className="hero__eyebrow">Movies and concerts</p>
        <h1 className="hero__title">
          {user ? `Welcome back, ${user.name.split(' ')[0]}.` : 'Book the seat you actually want.'}
        </h1>
        <p className="hero__sub prose">
          Pick from a live seat map, hold your seats while you check out, and get a QR ticket by
          email. Sold out? Join the waitlist and the next cancelled seat is offered to you
          automatically.
        </p>
        {!user && (
          <div className="hero__actions">
            <Link to="/register" className="btn btn--cta">
              Create an account
            </Link>
            <Link to="/login" className="btn btn--ghost">
              Log in
            </Link>
          </div>
        )}
      </section>

      <Card className="roadmap">
        <h2 className="roadmap__title">What works so far</h2>
        <ol className="roadmap__list">
          {phases.map((phase) => (
            <li key={phase.n} className={`roadmap__row roadmap__row--${phase.state}`}>
              <span className="roadmap__n">{phase.n}</span>
              <span className="roadmap__body">
                <span className="roadmap__name">{phase.title}</span>
                <span className="roadmap__note">{phase.note}</span>
              </span>
              {/* Text label, not colour alone — the state has to survive
                  greyscale and colour-blindness. */}
              <span className="roadmap__state">
                {phase.state === 'done'
                  ? 'Ready'
                  : phase.state === 'next'
                    ? 'In progress'
                    : 'Planned'}
              </span>
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}
