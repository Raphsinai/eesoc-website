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
2. **03/04:** any cluster-side setup outside git (Garage bucket/key for old chunks if that
   route is taken; CNPG permissions for the staging database switchover).
3. **Final:** Raph verifies on staging: queue end-to-end with real allowlist mail, QR
   check-in on a phone, and a push during which the site stays up.

## Build rounds

The plan is approved; build rounds run from this file. Round 1 (2026-10-07) merged section
01. Round 2 triaged the user's notes into proposed sections 10/11 (awaiting approval, not
built) and salvaged an interrupted round's WIP onto clearly-marked `wip:` commits on
`worktree-agent-*` branches. Round 3 merged 03+05, resumed 02 to its merge, spawned 07.
This is round 4: it reconciled `origin/main` (a parallel line that had already landed QR
ticketing, check-in, committee-added tickets and the sponsor agreement — see DECISIONS)
into `main` as merge commit 2582c63.

## Build progress

| # | Section | Status |
|---|---|---|
| 01 | Baseline tests | **merged** (branch `worktree-agent-a6a87117693b0238c`, PASSED — static validation; see blockers) |
| 02 | Zero-downtime rollout | in progress (lead hit its turn limit mid-flight with uncommitted work; resumed to finish, validate, commit) |
| 03 | SPA asset continuity | **merged** (branch `plan-03-spa-assets`, PASSED static: route-level lazy chunks, in-image union of previous builds, no-cache index + immutable assets, reload-loop-safe recovery; e2e execution CI-gated) |
| 05 | Events: calendar, links | **merged** (branch `plan-05-events-calendar`, PASSED static: .ics download, subscribable all/category feeds with pagination, booking-gated meeting links, external sign-up link, target year groups; stack runs CI-gated) |
| 07 | Rich event pages | in progress (spawned after 05 merged) |
| 04, 06, 08, 09 | rest | awaiting dependencies |

Post-merge static gates green on `main`: pytest collects 49 tests, `compileall` over
backend+scripts, `node --check` on every e2e spec, `npm run build` clean. Nothing is
pushed — push is blocked, so staging has not moved; the first CI run (pytest + e2e) is
the true execution gate.

Round-3 notes:
- 03's `deploy.yml` change is purely additive (`PREVIOUS_FRONTEND_IMAGE` build-arg for the
  frontend image union); 02 also edits `deploy.yml` — expect a small conflict at 02's merge.
- 05's validator flagged a pre-existing cap in `backend/api/routes/admin.py`
  `list_all_events` (`limit=500`) — same defect class 05 fixed on its own feed (pagination
  + tie-break). Handed to 08's prompt when 08 is spawned (08 owns the admin routes).
- Old `refs/stash` (autostash + pre-staging-backup) predates the plan; left untouched.
- Proposed sections 10 (committee-added tickets) and 11 (form builder), from round-2 notes,
  remain **awaiting approval** — not built, not part of this round.

## Round 4 (2026-10-08): reconciliation

Raph asked to "finish the event QR code ticketing system" — and it turned out to be
**partly built already** on `origin/main` by a parallel line: QR tickets, the door
check-in screen and ticket pages (34f0ac3, on staging), committee-added tickets +
confirmation email (a43c4b5, **promoted to production**), the sponsor agreement, admin
account creation. That line reserved migrations 058–060 and coordinates with this plan;
it landed with **zero tests** (origin has no `tests/` — the whole pytest+Playwright infra
is this line's sections 01–03+05, which origin lacks).

Done this round: merged origin/main into local main (2582c63; both conflicts were
additive wiring — see DECISIONS). Static gates green (compileall; 61 tests collect; all
e2e specs node --check; `npm run build` clean, jsQR its own on-demand chunk). Plan docs
updated. **Not pushed — the permission layer blocks `git push` here.**

Next: gap analysis of the landed ticketing vs spec 06 is running; from it, rescope
section 06 to only what's missing (expected: per-event sign-up questions + answers,
answers CSV export, answers retention purge, and test suites for everything landed), then
delegate the remainder and merge. Section 06 must not re-create the landed checkins table
(migration 060 on origin) — its own numbering (043–046) predates and skips that now.

1. **`git push` is denied in the build environment.** All merges land on local `main`;
   nothing has reached CI or staging. Human must run `git push origin main` and watch
   the **first CI run of the `pytest` and `e2e` jobs** — the suites were validated
   statically (49 pytest tests collect, every Playwright spec lists, all code compiles,
   both compose YAMLs parse) but never executed: the Docker daemon socket is also
   blocked here. If the first run is red, fix before promoting anything.
2. Real staging rollout verification (section 02, `scripts/watch-rollout.sh`) after CI is
   green — see spec 02.
