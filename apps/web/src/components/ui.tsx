import { forwardRef, useId, useState, type ReactNode } from 'react';
import { AlertIcon, CheckIcon, EyeIcon, EyeOffIcon, SpinnerIcon } from './icons.js';
import './ui.css';

/* ------------------------------------------------------------------ Button */

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'cta' | 'ghost' | 'quiet';
  loading?: boolean;
  full?: boolean;
};

export function Button({
  variant = 'primary',
  loading = false,
  full = false,
  disabled,
  children,
  className = '',
  ...rest
}: ButtonProps) {
  return (
    <button
      // Disabled while loading, or a double-click submits the form twice —
      // which for a booking means two holds on the same seat.
      disabled={disabled || loading}
      // aria-busy tells a screen reader the control is working; the spinner
      // alone communicates nothing to one.
      aria-busy={loading || undefined}
      className={`btn btn--${variant} ${full ? 'btn--full' : ''} ${className}`}
      {...rest}
    >
      {loading && <SpinnerIcon />}
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------- Field */

type FieldProps = React.InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  hint?: string;
  error?: string | undefined;
};

/**
 * Label, input, hint and error wired together by id.
 *
 * A placeholder is not a label: it disappears the moment someone types, and
 * screen readers treat it inconsistently. Every input here gets a real
 * <label for>.
 */
export const Field = forwardRef<HTMLInputElement, FieldProps>(function Field(
  { label, hint, error, type = 'text', className = '', ...rest },
  ref,
) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const [revealed, setRevealed] = useState(false);

  const isPassword = type === 'password';
  const inputType = isPassword && revealed ? 'text' : type;

  return (
    <div className={`field ${className}`}>
      <label className="field__label" htmlFor={id}>
        {label}
      </label>

      <div className="field__control">
        <input
          {...rest}
          ref={ref}
          id={id}
          type={inputType}
          aria-invalid={error ? true : undefined}
          aria-describedby={
            [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ') || undefined
          }
          className={`field__input ${error ? 'field__input--error' : ''} ${isPassword ? 'field__input--padded' : ''}`}
        />

        {isPassword && (
          <button
            type="button"
            className="field__reveal"
            onClick={() => setRevealed((v) => !v)}
            // Icon-only button, so it needs its own accessible name.
            aria-label={revealed ? 'Hide password' : 'Show password'}
            aria-pressed={revealed}
            // Never a tab stop before the field it belongs to.
            tabIndex={0}
          >
            {revealed ? <EyeOffIcon /> : <EyeIcon />}
          </button>
        )}
      </div>

      {hint && !error && (
        <p className="field__hint" id={hintId}>
          {hint}
        </p>
      )}

      {error && (
        // role="alert" so the message is announced, not just coloured red.
        <p className="field__error" id={errorId} role="alert">
          <AlertIcon size={15} />
          {error}
        </p>
      )}
    </div>
  );
});

/* ------------------------------------------------------------------- Alert */

export function Alert({
  tone = 'danger',
  children,
}: {
  tone?: 'danger' | 'success';
  children: ReactNode;
}) {
  return (
    <div className={`alert alert--${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
      {tone === 'danger' ? <AlertIcon size={18} /> : <CheckIcon size={18} />}
      <span>{children}</span>
    </div>
  );
}

/* -------------------------------------------------------------------- Card */

export const Card = ({ children, className = '' }: { children: ReactNode; className?: string }) => (
  <div className={`card ${className}`}>{children}</div>
);

/* ------------------------------------------------------------------ Select */

type SelectProps = React.SelectHTMLAttributes<HTMLSelectElement> & {
  label: string;
  /** Renders the label for the eye as well as the screen reader. */
  showLabel?: boolean;
};

export function Select({
  label,
  showLabel = true,
  className = '',
  children,
  ...rest
}: SelectProps) {
  const id = useId();
  return (
    <div className={`field ${className}`}>
      <label className={showLabel ? 'field__label' : 'sr-only'} htmlFor={id}>
        {label}
      </label>
      <select id={id} className="field__input field__select" {...rest}>
        {children}
      </select>
    </div>
  );
}

/* ---------------------------------------------------------------- Textarea */

type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string };

export function Textarea({ label, className = '', ...rest }: TextareaProps) {
  const id = useId();
  return (
    <div className={`field ${className}`}>
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      <textarea id={id} className="field__input field__textarea" rows={3} {...rest} />
    </div>
  );
}

/* -------------------------------------------------------------- Empty/Load */

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      {children && <p className="empty__body">{children}</p>}
    </div>
  );
}

/**
 * Reserves the space the real content will occupy, so nothing jumps when it
 * lands. `aria-hidden` because a skeleton means nothing read aloud — the
 * surrounding region carries aria-busy instead.
 */
export const Skeleton = ({ height = 76, count = 3 }: { height?: number; count?: number }) => (
  <div className="skeletons" aria-hidden="true">
    {Array.from({ length: count }, (_, i) => (
      <div key={i} className="skeleton" style={{ height }} />
    ))}
  </div>
);
