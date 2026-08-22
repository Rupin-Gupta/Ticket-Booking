import { useEffect, useState } from 'react';
import { MoonIcon, SunIcon } from './icons.js';

type Choice = 'light' | 'dark' | 'system';

const read = (): Choice => (localStorage.getItem('theme') as Choice) ?? 'system';

/**
 * Three states, not two. "System" is the default and stamps nothing on the
 * root, letting the prefers-color-scheme block in tokens.css decide; an
 * explicit choice stamps data-theme and overrides the OS in both directions.
 */
export function ThemeToggle() {
  const [choice, setChoice] = useState<Choice>(read);

  useEffect(() => {
    const root = document.documentElement;
    if (choice === 'system') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', choice);
    localStorage.setItem('theme', choice);
  }, [choice]);

  const prefersDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
  const isDark = choice === 'dark' || (choice === 'system' && prefersDark);

  return (
    <button
      type="button"
      className="btn btn--quiet icon-btn"
      onClick={() => setChoice(isDark ? 'light' : 'dark')}
      aria-label={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
    >
      {isDark ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}
