# Working Rules

Rules the user has set for how this project runs. Append here the moment a new
rule is given — never rely on conversation memory for these.

## Git

1. **Claude commits. Claude never pushes.** When work is ready to go to
   `https://github.com/Rupin-Gupta/Ticket-Booking.git`, Claude stops and asks
   the user to push. No `git push`, no `gh pr create`, no remote writes.
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
6. `CLAUDE.md` is the condensed load-bearing memory. Detail goes in `docs/`.
   Never let the two contradict each other.

## Working style

7. **Caveman mode** — Claude's prose is terse. Technical substance stays; filler
   goes. Code, commit messages, and security warnings are written normally.
8. **Ponytail mode** — laziest solution that actually works. No speculative
   abstractions, no scaffolding "for later". Deliberate simplifications get a
   `ponytail:` comment naming the ceiling and the upgrade path.
9. **`ui-ux-pro-max` skill drives all frontend work.** Do not hand-roll UI
   decisions on the web app without it.
10. **`code-reviewer` skill runs at the end** before the project is called done.

## Engineering

11. Never weaken the concurrency guarantee to make a test pass. The parallel-hold
    test in `apps/api/tests/concurrency/` is the project's headline feature.
12. Secrets never enter the repo. `.env` is gitignored; `.env.example` carries
    every key with a dummy or empty value.
