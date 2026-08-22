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
