# CLAUDE.md — eesoc-website

The EESoc website. Vue SPA + FastAPI, deployed to Kubernetes by ArgoCD.
Full deployment documentation: [`deploy/README.md`](deploy/README.md).

---

## Deploying: staging first, production only when Raph says so

There are two environments. **Staging is yours — deploy to it freely and
without asking. Production is Raph's.**

| | Staging | Production |
|---|---|---|
| | <https://eesoc.raphsinai.dev> | <https://eesoc.com> |
| Namespace | `eesoc-staging` | `eesoc` |
| Cluster | its own, one VM on the homelab (`ssh eesoc-staging`; [`deploy/homelab`](deploy/homelab/README.md)) | TensorHost (`ssh rke2-tensorhost`) |
| Deploys when | a push to `main` lands — push freely, no need to ask | somebody runs the promote workflow — needs Raph's approval |

**Never put a change on eesoc.com that Raph has not seen on
eesoc.raphsinai.dev and explicitly approved.** It is a live site used by real
students — booking events, holding sessions, reading resources.

### The sequence

1. **Commit and push to `main` without asking.** This is a standing
   instruction from Raph and it overrides the usual "only commit or push when
   asked" default — for this repository, pushing to `main` *is* deploying to
   staging, and staging is meant to be deployed to freely. Finish the change,
   then push it; do not stop to request permission for this step.

   CI tests, builds the images, and pins them in
   `deploy/k8s/overlays/staging` — eesoc.raphsinai.dev updates itself within a
   couple of minutes. A failing test stops the build, so broken code does not
   reach staging. If CI fails, fix it and push again.

   The only judgement left here is *what* goes in the commit: push work that is
   finished and coherent, not a half-done experiment, and keep unrelated
   changes out of it. That is ordinary care, not a reason to ask.
2. **Tell Raph it is on staging and stop.** Say what to look at, and name the
   URL. Do not promote, and do not treat silence, a thumbs-up on the code, or
   "looks good" about the diff as approval to go to production.
3. Only after Raph approves *this specific change for production*:

   ```bash
   gh workflow run promote.yml        # or: Actions -> promote to production
   ```

   With no arguments it promotes exactly what staging is running, which is what
   you want. The workflow refuses mixed tags, a no-op promote, and a SHA that
   is not on `main`.

Approval is per-change. Being told to promote once is not standing permission
to promote the next thing.

### Releasing just one approved feature

If Raph approves **only a feature**, not everything presently on staging, do
**not** run the standard whole-staging promotion. Other changes on staging may
not be approved yet. Use the *frontend-only* mode of `.github/workflows/promote.yml`
for a standalone UI change:

1. Read the actual frontend source SHA pinned in production's kustomization.
2. Branch from that SHA on `release/<feature>`, then apply **only** the approved
   frontend file changes from staging. Verify `git diff --name-only BASE HEAD`
   contains exactly those files, and push the release branch.
3. Trigger `promote.yml` with `mode=frontend-only`, the full release-branch
   `source_sha`, the **exact** `approved_paths` comma-separated list, and a
   `test_spec` from `tests/e2e`. The workflow validates ancestry and the exact
   changed-file allowlist, builds the candidate from the production baseline,
   tests it, and changes **only** the production frontend image pin. It
   preserves the prior production assets for users with old tabs open.
4. Verify ArgoCD has synced the new frontend and the public page serves the
   expected link. Do not update any other production service for that approval.

This intentionally makes production image tags mixed. The normal full promote
still works for a later, separately approved whole-staging release. For a
feature involving API/database/schema changes, create an appropriate isolated
multi-service plan; never blindly cherry-pick just the frontend of such a
feature. Full instructions and invocation are in `deploy/README.md`.

### Do not go around it

- **Do not hand-edit `deploy/k8s/overlays/production/kustomization.yaml`.** That
  file is written by the promote workflow. Editing the image tags there deploys
  to eesoc.com just as surely as running the workflow, without the guard rails.
- **Do not `kubectl apply`, `set image`, `scale`, `rollout` or `patch` against
  the `eesoc` namespace.** The repository is the only statement of what is
  running; ArgoCD has `selfHeal: true` and will revert manual changes, so a
  direct edit is both an unapproved deploy *and* one that silently disappears.
  `kubectl` against `eesoc-staging` for debugging is fine; it lives on the
  staging VM, not on TensorHost.
- **Do not add hosts, image tags, or a `namespace:` to `deploy/k8s/base/`.**
  The base is shared by both overlays, so anything environment-specific there
  reaches production on the next sync. Hosts and image tags are deliberately
  absent from the base; the `manifests` CI job fails if that stops being true.

### If production is broken

Rolling *back* does not need the same caution as rolling forward — restoring the
last known-good state is the safe direction. Still say what you are doing and
why, then `git revert` the `promote:` commit, or run promote with an older SHA.

### Staging data and sessions persist across deployments

A staging release changes code/images only. **Do not restore its database after
an ordinary push to `main`.** Staging user sessions, opportunities, submissions,
and other test data must survive redeploys. Sessions are stored in Postgres, so
API pod replacements do not require anybody to sign in again; normal 7-day
session expiration and explicit logout still apply.

Staging was initially seeded from a **scrubbed production snapshot**. Since the
homelab cannot reach production's Garage, its cached snapshot (2026-10-18) is
stale until the production read path is configured. The manual-only suspended
`refresh-from-production` CronJob in `deploy/k8s/overlays/staging` can still
be used for a deliberate reset:

```bash
kubectl -n eesoc-staging create job --from=cronjob/refresh-from-production refresh-manual-$(date +%s)
```

**WARNING:** A manual refresh drops staging's database schema, discarding all
staging-only edits, accounts and sessions. Never run it merely to deploy code,
and never run it against production. Production snapshots delete sessions,
sign-in codes and passkey credentials before leaving production; people copied
from production have anonymised identities (`user<id>@staging.invalid`). Real
staging test accounts can be created by signing in independently afterward.

If you add a migration with a new column holding an email, CID, name or phone
number, add it to `deploy/snapshot/scrub.sql`. You do not have to remember:
`verify.sql` scans every text column of every table and **fails the snapshot
job** if real-looking data survives, so the consequence of forgetting is staging
stops refreshing — not production data leaking. Fix it by adding the column to
the scrub, never by loosening the check.

### Reading what is deployed

Both environments state their version in git — no cluster access needed:

```bash
grep -A2 'eesoc-api' deploy/k8s/overlays/staging/kustomization.yaml     # staging
grep -A2 'eesoc-api' deploy/k8s/overlays/production/kustomization.yaml  # production
```

If those two SHAs differ, something is sitting on staging waiting to be looked
at. Mention it rather than promoting it.

---

## Three standing hazards

**eActivities bans the whole society.** The Union's API rate-limits per source
IP and a breach bans EESoc, not just the caller. The throttle and cache live
in-process in the `union` service, which is why it is pinned to one replica with
`strategy: Recreate`, and why **staging has no `ICU_API_KEY`** — a second
client spends the same society's allowance, and the ban it can trigger takes
production down too, whichever IP it came from. Do not add that key to the
staging secret, and do not scale `union` up.

**Outbound email is one shared Imperial mailbox** (`eesocweb@ic.ac.uk`) whose
password Imperial force-resets annually. When it lapses, sign-in codes stop and
members are locked out. The site handles this by taking sign-in and booking
into maintenance mode rather than half-working. Anything that sends mail from
staging sends it for real, from that same address.

**WhatsApp bans the society's number.** The `whatsapp` service is the society's
WhatsApp Business number linked as a device (the WhatsApp Web protocol, not the
Business API), which WhatsApp's terms do not allow for automation; if it is
flagged as a bot, the number that runs the community is banned. Keep it to
groups, a few posts an hour, one replica — do not scale it, loosen its pacing or
hourly cap, or add direct messages. It is on in production (Raph, 2026-10-17);
turning it off there is removing one `components:` line from its overlay.
Staging may be linked to the real number: its Send says so before posting, and
the snapshot scrub strips WhatsApp groups from emails copied from production.
