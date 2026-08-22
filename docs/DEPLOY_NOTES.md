# Why the deploy config looks the way it does

`vercel.json` is strict JSON with no comments — Vercel validates it against a
schema and rejects unknown keys, including a `"//"` used as a comment. The
reasoning lives here instead.

## `vercel.json`

**Root Directory must stay the repository root, not `apps/web`.** With Root
Directory set to `apps/web`, Vercel installs from inside that folder and cannot
resolve the `@ticket/shared` workspace dependency. `buildCommand` targets the
workspace instead.

**The rewrite matters more than it looks.**

```json
{ "source": "/((?!assets/).*)", "destination": "/index.html" }
```

Without it, `/verify/<token>` from a scanned QR and `/offers/<token>` from a
waitlist email both hit Vercel's static host directly and 404. Those are the
only two entry points that arrive as a **cold link** rather than a click from
inside the app, so they are exactly the ones a missing rewrite breaks — and the
ones nobody notices in local development, where Vite's dev server handles it.

The negative lookahead exempts `/assets/`, so a genuinely missing bundle 404s
honestly rather than returning HTML with a JavaScript content type.

**Caching.** Vite fingerprints asset filenames, so `/assets/*` is immutable for
a year. `index.html` is deliberately excluded and stays revalidated — cache it
and a deploy never reaches anyone.

**`X-Frame-Options: DENY`** because a booking QR should not be embeddable in
somebody else's page.

## `render.yaml`

YAML supports comments, so the reasoning is inline there. The two worth
repeating:

- The build runs `alembic upgrade head`, so deploying applies pending
  migrations. That is the non-interactive form and it uses `DIRECT_URL`, which
  is why both connection strings are required.
- `MAIL_REDIRECT_TO` is deliberately absent. It is ignored under
  `NODE_ENV=production` anyway, because silently redirecting a customer's ticket
  away from them is worse than not sending it.

## After both are deployed

1. **`WEB_URL` on Render must be the Vercel URL.** It is the CORS allowlist and
   the Socket.IO handshake origin check. Get it wrong and every request from the
   deployed frontend is blocked by the browser, while `curl` keeps working —
   which makes it look like a frontend bug.
2. **`VITE_API_URL` on Vercel must be the Render URL.** It is baked in at build
   time, so changing it needs a redeploy, not a restart. If it is missing the
   app calls its own origin, gets 404s, and the console carries a loud error
   saying exactly this.
3. **Set the `API_URL` repository variable** or the keep-alive workflow fails
   loudly on its next run — which is the intended behaviour, because a silent
   keep-alive is worse than none.
