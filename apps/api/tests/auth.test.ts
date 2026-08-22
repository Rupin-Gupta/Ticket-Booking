import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { after, before, describe, test } from 'node:test';
import type { Server } from 'node:http';
import express from 'express';
import { createApp } from '../src/app.js';
import { prisma } from '../src/lib/prisma.js';
import { requireAuth, requireRole } from '../src/middleware/auth.js';
import { errorHandler } from '../src/middleware/error.js';

/**
 * Runs against the real database. Every account it creates carries this suffix
 * so a failed run cannot collide with the next one, and cleanup deletes by it.
 *
 * ponytail: no test framework, no supertest, no fixtures — node:test is stdlib
 * and the app is already an HTTP server. Add tooling when this stops fitting.
 */
const RUN = randomBytes(5).toString('hex');
const emailFor = (who: string) => `test-${who}-${RUN}@example.test`;
const PASSWORD = 'correct horse battery';

let server: Server;
let base: string;

/**
 * requireRole gets its own tiny app rather than a route bolted onto the real
 * one. createApp() ends with notFound + errorHandler, so anything appended
 * afterwards is shadowed by the 404 handler and never runs — which is the
 * correct behaviour, and exactly why the middleware needs its own harness.
 */
let roleServer: Server;
let roleBase: string;

const post = (path: string, body: unknown, token?: string) =>
  fetch(base + path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });

const get = (path: string, token?: string) =>
  fetch(base + path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });

// fetch types the body as `unknown`; asserting on a response shape is the whole
// job here, so read it loosely rather than restating every DTO.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const json = async (res: Response): Promise<any> => res.json();

const listen = async (app: express.Express): Promise<[Server, string]> => {
  const s = await new Promise<Server>((resolve) => {
    const created = app.listen(0, () => resolve(created));
  });
  const addr = s.address();
  if (typeof addr === 'string' || addr === null) throw new Error('no port');
  return [s, `http://127.0.0.1:${addr.port}`];
};

before(async () => {
  [server, base] = await listen(createApp());

  // Phase 1 ships no organiser-only route yet, and requireRole is worth
  // proving before anything depends on it.
  const roleApp = express();
  roleApp.get('/organiser-only', requireAuth, requireRole(['ORGANISER']), (_req, res) => {
    res.json({ ok: true });
  });
  // Sentinel: if a request ever lands on the wrong server, this makes it
  // obvious instead of surfacing as a mystery 404 from the real app.
  roleApp.use((_req, res) => res.status(418).json({ wrongServer: true }));
  roleApp.use(errorHandler as express.ErrorRequestHandler);
  [roleServer, roleBase] = await listen(roleApp);

  assert.notEqual(roleBase, base, 'the two test servers collided on one port');
});

after(async () => {
  await prisma.user.deleteMany({ where: { email: { endsWith: `-${RUN}@example.test` } } });
  await prisma.$disconnect();
  server.close();
  roleServer.close();
});

describe('registration', () => {
  test('creates a CUSTOMER and returns a token', async () => {
    const res = await post('/api/v1/auth/register', {
      email: emailFor('basic'),
      password: PASSWORD,
      name: 'Basic Person',
    });
    assert.equal(res.status, 201);

    const body = await json(res);
    assert.equal(body.user.role, 'CUSTOMER');
    assert.ok(body.accessToken, 'expected an access token');
    assert.equal(body.user.passwordHash, undefined, 'password hash must never be returned');
  });

  test('IGNORES a client-supplied role — the privilege-escalation case', async () => {
    const email = emailFor('escalate');
    const res = await post('/api/v1/auth/register', {
      email,
      password: PASSWORD,
      name: 'Sneaky Person',
      role: 'ADMIN',
    });
    assert.equal(res.status, 201);
    assert.equal((await json(res)).user.role, 'CUSTOMER');

    // Check the database directly, not just the response: the response could
    // be filtering a role that actually got written.
    const stored = await prisma.user.findUnique({ where: { email }, select: { role: true } });
    assert.equal(stored?.role, 'CUSTOMER', 'an ADMIN row was written to the database');
  });

  test('rejects a duplicate email with 409', async () => {
    const email = emailFor('dupe');
    const body = { email, password: PASSWORD, name: 'First' };
    assert.equal((await post('/api/v1/auth/register', body)).status, 201);

    const second = await post('/api/v1/auth/register', body);
    assert.equal(second.status, 409);
    assert.equal((await json(second)).error.code, 'EMAIL_TAKEN');
  });

  test('rejects a short password with 400', async () => {
    const res = await post('/api/v1/auth/register', {
      email: emailFor('short'),
      password: 'abc',
      name: 'Short',
    });
    assert.equal(res.status, 400);
    assert.equal((await json(res)).error.code, 'VALIDATION_FAILED');
  });
});

describe('login', () => {
  const email = emailFor('login');

  before(async () => {
    await post('/api/v1/auth/register', { email, password: PASSWORD, name: 'Login Person' });
  });

  test('succeeds with the right password', async () => {
    const res = await post('/api/v1/auth/login', { email, password: PASSWORD });
    assert.equal(res.status, 200);
    assert.ok((await json(res)).accessToken);
  });

  test('gives the same answer for a wrong password and an unknown email', async () => {
    const wrongPassword = await post('/api/v1/auth/login', { email, password: 'not it at all' });
    const unknownEmail = await post('/api/v1/auth/login', {
      email: emailFor('ghost'),
      password: PASSWORD,
    });

    assert.equal(wrongPassword.status, 401);
    assert.equal(unknownEmail.status, 401);

    // Identical code and message, or the endpoint is an account-enumeration
    // oracle: an attacker learns which addresses have accounts.
    const a = (await json(wrongPassword)).error;
    const b = (await json(unknownEmail)).error;
    assert.deepEqual(a, b);
  });
});

describe('token and roles', () => {
  test('/me returns the caller, and 401s without a token', async () => {
    const email = emailFor('me');
    const { accessToken } = await json(
      await post('/api/v1/auth/register', { email, password: PASSWORD, name: 'Me Person' }),
    );

    const authed = await get('/api/v1/auth/me', accessToken);
    assert.equal(authed.status, 200);
    assert.equal((await json(authed)).user.email, email);

    assert.equal((await get('/api/v1/auth/me')).status, 401);
    assert.equal((await get('/api/v1/auth/me', 'not-a-real-token')).status, 401);
  });

  test('a CUSTOMER cannot reach an ORGANISER route', async () => {
    const { accessToken } = await json(
      await post('/api/v1/auth/register', {
        email: emailFor('role'),
        password: PASSWORD,
        name: 'Role Person',
      }),
    );

    const res = await fetch(`${roleBase}/organiser-only`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    assert.notEqual(res.status, 418, 'the request reached the wrong test server');
    assert.equal(res.status, 403);
    assert.equal((await json(res)).error.code, 'FORBIDDEN');
  });

  test('rejects a token signed with the wrong secret', async () => {
    const jwt = (await import('jsonwebtoken')).default;
    const forged = jwt.sign({ sub: 'whoever', role: 'ADMIN' }, 'a different secret entirely', {
      algorithm: 'HS256',
      expiresIn: '15m',
    });
    assert.equal((await get('/api/v1/auth/me', forged)).status, 401);
  });

  test('rejects an alg:none token', async () => {
    // The classic JWT bypass: strip the signature and claim the token needs
    // none. verifyAccessToken pins algorithms: ['HS256'], which is what stops
    // this — a verifier that reads `alg` from the header accepts it.
    const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url');
    const payload = Buffer.from(
      JSON.stringify({ sub: 'whoever', role: 'ADMIN', exp: Math.floor(Date.now() / 1000) + 900 }),
    ).toString('base64url');

    const res = await get('/api/v1/auth/me', `${header}.${payload}.`);
    assert.equal(res.status, 401);
  });
});
