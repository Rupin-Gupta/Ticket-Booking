# Working Rules

Rules the user has set for how this project runs. Append here the moment a new
rule is given — never rely on conversation memory for these.

## Git

1. **Commit locally; the repo owner pushes.** Work destined for
   `https://github.com/Rupin-Gupta/Ticket-Booking.git` stops at the commit. No
   unattended `git push`, no `gh pr create`, no remote writes.
2. Commit in small, working increments — one commit per completed unit of work,
   not one giant commit per phase.
3. Commit messages: imperative mood, subject line under 72 chars, body explains
   _why_ when the _what_ is not obvious.

## Documentation

4. **Docs are updated in the same commit as the code they describe.** A phase is
   not done until `docs/TODO.md`, `docs/CONTEXT.md`, and the relevant doc reflect
   reality.
5. **Every new rule the user states gets appended to this file**, numbered,
   in the same turn it is given.
6. `docs/CONVENTIONS.md` is the condensed load-bearing summary. Detail goes in
   the rest of `docs/`. Never let the two contradict each other.

## Working style

7. **Prose stays terse.** Technical substance stays; filler goes. Code, commit
   messages, and security warnings are written normally.
8. **The laziest solution that actually works.** No speculative abstractions, no
   scaffolding "for later". Deliberate simplifications get a `ponytail:` comment
   naming the ceiling and the upgrade path.
9. **Frontend work follows the existing design system** in
   `apps/web/src/styles/` — no one-off UI decisions hand-rolled per page.
10. **A full code review runs at the end**, before the project is called done.

## Engineering

11. Never weaken the concurrency guarantee to make a test pass. The parallel-hold
    test in `apps/api/tests/concurrency/` is the project's headline feature.
12. Secrets never enter the repo. `.env` is gitignored; `.env.example` carries
    every key with a dummy or empty value.
