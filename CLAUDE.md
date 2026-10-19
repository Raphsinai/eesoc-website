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

### Staging's data is a scrubbed copy of production

Staging restores a snapshot of production after **every deploy**. From the
homelab it cannot reach production's Garage, so it restores the last snapshot
it kept (2026-10-18) until production gets a read path for it, which is a
production change (`deploy/homelab/README.md`, "Data"). Two things follow, and
both will bite you if you forget them:

- **Anything you change in the staging dashboard is discarded on the next
  push.** Do not use staging to stage real content — an event created there is
  gone, and was never on eesoc.com.
- **The people in it are fake.** Emails are `user<id>@staging.invalid`, names
  are `Test User <id>`, sessions and sign-in codes are deleted. Committee names
  are real, because they are already published on the live site. A bug report
  that depends on a specific member's data will not reproduce there from their
  real details.

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
