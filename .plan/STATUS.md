status: in_progress

# Plan: zero-downtime deploys, richer events, email approval queue

First plan for this codebase. The brief is saved in `BRIEF.md`; the intake survey is in
`CODEBASE.md`. Nine sections, each spec'd in `.plan/<NN>-<slug>.md` with acceptance
criteria phrased as tests. **Staging only throughout; nothing is promoted.**

## Sections

| # | Section | Depends on | Can run in parallel with |
|---|---|---|---|
| 01 | Baseline: pytest + Playwright + CI wiring | — | — |
| 02 | Zero-downtime rollout (k8s, drains, resumable jobs, migration guard) | 01 | 03, 05 |
| 03 | SPA asset continuity (old chunks, caching, chunk-error reload) | 01 | 02, 05 |
| 04 | Staging refresh without downtime | 02 | 05 |
| 05 | Events: calendar, links, target year groups | 01 | 02, 03, 04 |
| 06 | Events: sign-up forms, CSV export, QR check-in | 02, 05 | 07 (disjoint-ish; merge order 06→07) |
| 07 | Events: rich pages, gallery, archive | 05 | 06 |
| 08 | Events: admin workflow (duplicate/series/schedule/cancel/feedback) | 05, 07 | — |
| 09 | Email approval queue + all triggers | 02, 05, 06, 08 | — |

Migration numbers are pre-assigned per section (02: 035–039, 05: 040–042, 06: 043–046,
07: 047–049, 08: 050–052, 09: 053–057) so parallel sections never collide; a section
needing more takes the next free number and records it in `DECISIONS.md`.

Test ownership: each section owns `tests/<slug>/` (pytest) and its prefixed Playwright
specs; CI discovers the whole `tests/` tree, so only 01 and 02 ever edit the workflow.

## Key plan decisions (rationale in the specs)

- **api rollout:** the api stays multi-replica-safe by claiming the daily sync with a
  **database advisory lock** (RollingUpdate `maxUnavailable: 0`) rather than moving the
  sync out of the api — keeps Union call rate unchanged. If that proves impossible it
  comes back as a plan change, per the brief.
- **Resumable bulk sends** live in section 02 (DB-level per-recipient claims), and every
  later send path (06 confirmations, 09 queue) builds on that machinery.
- **Old asset chunks** are kept in the frontend image (union of last N builds) — no
  cluster-side Garage keys needed. The Garage option is recorded as a blocked human step
  in the spec if the in-image approach is rejected.
- **Cancel/postpone/feedback states** are built in 08; their **notice drafts** are wired
  in 09, which owns every queue trigger. This breaks the circular dependency by putting
  all triggers in one place.
- **On-sale topic email** (the only auto-send being removed) is removed in 09, last, so
  the queue exists to replace it the moment it goes.

## Human steps (stop as `blocked` when reached)

1. **02:** live staging rollout verification — push to `main` (if the build environment
   can't) and run `scripts/watch-rollout.sh https://eesoc.raphsinai.dev` during the deploy.
   Exact commands will be in the spec's report.
2. **03/04:** any cluster-side setup outside git (Garage bucket/key for old chunks if
   that route is taken; CNPG permissions for the staging database switchover).
3. **Final:** Raph verifies on staging: queue end-to-end with real allowlist mail, QR
   check-in on a phone, and a push during which the site stays up.

## Nothing built yet

This round is PLAN ONLY: no code, no delegation. Awaiting approval of the section split
above; the next round starts at section 01.
