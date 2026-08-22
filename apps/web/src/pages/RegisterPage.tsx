import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { messageFor, useAuth } from '../auth/AuthContext.js';
import { Alert, Button, Card, Field } from '../components/ui.js';
import './auth.css';

type Errors = Partial<Record<'name' | 'email' | 'password', string>>;

/** Mirrors the server's Zod schema. The server is the authority; this is
 *  only here so someone learns about a short password before a round trip. */
function validate(values: { name: string; email: string; password: string }): Errors {
  const errors: Errors = {};
  if (!values.name.trim()) errors.name = 'Please enter your name.';
  if (!/^\S+@\S+\.\S+$/.test(values.email)) errors.email = 'Enter a valid email address.';
  if (values.password.length < 8) errors.password = 'Use at least 8 characters.';
  return errors;
}

export function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();

  const [values, setValues] = useState({ name: '', email: '', password: '' });
  const [errors, setErrors] = useState<Errors>({});
  // Only fields the person has actually left show an error. Shouting about an
  // empty password box before they have typed in it is just noise.
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const set = (key: keyof typeof values) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setValues((v) => ({ ...v, [key]: e.target.value }));

  const blur = (key: string) => () => setTouched((t) => ({ ...t, [key]: true }));

  const shown = (key: keyof Errors) => (touched[key] ? validate(values)[key] : undefined);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setFormError(null);

    const found = validate(values);
    if (Object.keys(found).length > 0) {
      // Reveal every problem at once on submit, rather than one per attempt.
      setTouched({ name: true, email: true, password: true });
      setErrors(found);
      return;
    }

    setSubmitting(true);
    try {
      await register(values);
      navigate('/', { replace: true });
    } catch (err) {
      setFormError(messageFor(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="auth">
      <Card className="auth__card">
        <div className="auth__head">
          <h1 className="auth__title">Create your account</h1>
          <p className="auth__sub">
            Book seats, hold them while you decide, and keep your tickets in one place.
          </p>
        </div>

        <form className="auth__form" onSubmit={onSubmit} noValidate>
          {formError && <Alert>{formError}</Alert>}

          <Field
            label="Name"
            name="name"
            autoComplete="name"
            required
            value={values.name}
            onChange={set('name')}
            onBlur={blur('name')}
            error={shown('name') ?? errors.name}
            placeholder="Alex Kumar"
          />

          <Field
            label="Email"
            type="email"
            name="email"
            autoComplete="email"
            required
            value={values.email}
            onChange={set('email')}
            onBlur={blur('email')}
            error={shown('email') ?? errors.email}
            placeholder="you@example.com"
          />

          <Field
            label="Password"
            type="password"
            name="password"
            autoComplete="new-password"
            required
            value={values.password}
            onChange={set('password')}
            onBlur={blur('password')}
            error={shown('password') ?? errors.password}
            hint="At least 8 characters."
          />

          <Button type="submit" loading={submitting} full>
            {submitting ? 'Creating account' : 'Create account'}
          </Button>
        </form>

        <p className="auth__alt">
          Already have an account? <Link to="/login">Log in</Link>
        </p>
      </Card>
    </div>
  );
}
