/**
 * Icons, hand-rolled on one 24×24 grid.
 *
 * ponytail: a handful of paths instead of an icon dependency. Add lucide-react
 * the moment this list gets past ~15 icons or someone wants tree-shaking.
 * Never emoji — they render differently per platform and carry no aria meaning.
 *
 * Every icon is aria-hidden: an icon is decoration unless a button has no text,
 * and a button with no text carries its own aria-label.
 */
type IconProps = { size?: number; className?: string };

const base = (size: number) => ({
  width: size,
  height: size,
  viewBox: '0 0 24 24',
  fill: 'none' as const,
  stroke: 'currentColor',
  strokeWidth: 1.75,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
  focusable: false as const,
});

export const TicketIcon = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M3 8.5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v1a2.5 2.5 0 0 0 0 5v1a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-1a2.5 2.5 0 0 0 0-5v-1Z" />
    <path d="M14 6.5v11" strokeDasharray="2 2.5" />
  </svg>
);

export const EyeIcon = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);

export const EyeOffIcon = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M10.6 6.1A8.6 8.6 0 0 1 12 6c6 0 9.5 6 9.5 6a17 17 0 0 1-2.4 3.2M6.2 7.9A17 17 0 0 0 2.5 12S6 18 12 18a8.9 8.9 0 0 0 3.5-.7" />
    <path d="M10 10a2.8 2.8 0 0 0 4 4" />
    <path d="m3 3 18 18" />
  </svg>
);

export const AlertIcon = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7.5v5M12 16h.01" />
  </svg>
);

export const CheckIcon = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <circle cx="12" cy="12" r="9" />
    <path d="m8.5 12 2.5 2.5 4.5-5" />
  </svg>
);

export const LogOutIcon = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M9.5 21H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.5" />
    <path d="m16 16 4-4-4-4M20 12H9.5" />
  </svg>
);

export const SunIcon = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.2 5.2l1.4 1.4M17.4 17.4l1.4 1.4M18.8 5.2l-1.4 1.4M6.6 17.4l-1.4 1.4" />
  </svg>
);

export const MoonIcon = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M20 13.5A8.5 8.5 0 0 1 10.5 4a8.5 8.5 0 1 0 9.5 9.5Z" />
  </svg>
);

/** Spinner. The motion is suppressed by the reduced-motion rule in base.css. */
export const SpinnerIcon = ({ size = 18, className }: IconProps) => (
  <svg {...base(size)} className={className} style={{ animation: 'spin 800ms linear infinite' }}>
    <path d="M12 3a9 9 0 1 0 9 9" />
  </svg>
);
