import { useEffect, useState } from 'react';

/**
 * Counts a hold down to zero.
 *
 * Derives remaining time from the server's absolute `holdExpiresAt` on every
 * tick rather than decrementing a local number. A tab that was backgrounded,
 * or a machine that slept, would otherwise come back showing a hold that has
 * minutes left on a seat the server released long ago.
 */
export function HoldCountdown({
  expiresAt,
  onExpire,
}: {
  expiresAt: string;
  onExpire?: () => void;
}) {
  const remaining = () => Math.max(0, new Date(expiresAt).getTime() - Date.now());
  const [ms, setMs] = useState(remaining);

  useEffect(() => {
    setMs(remaining());
    const id = setInterval(() => {
      const left = remaining();
      setMs(left);
      if (left === 0) {
        clearInterval(id);
        onExpire?.();
      }
    }, 1000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expiresAt]);

  const total = Math.ceil(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  const urgent = ms > 0 && ms < 60_000;

  return (
    <span
      className={`countdown ${urgent ? 'countdown--urgent' : ''}`}
      // Announce at a minute and at zero, not every second — a live region
      // that fires 600 times is unusable with a screen reader on.
      role="timer"
      aria-live={urgent ? 'polite' : 'off'}
    >
      {ms === 0 ? 'expired' : `${minutes}:${String(seconds).padStart(2, '0')}`}
    </span>
  );
}
