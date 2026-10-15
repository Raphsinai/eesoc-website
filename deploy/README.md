# Deployment

Two environments, one cluster, one branch.

| | Staging | Production |
|---|---|---|
| URL | <https://eesoc.raphsinai.dev> | <https://eesoc.com> |
| Namespace | `eesoc-staging` | `eesoc` |
| Overlay | `deploy/k8s/overlays/staging` | `deploy/k8s/overlays/production` |
| ArgoCD app | `eesoc-website-staging` | `eesoc-website` |
| Edge vhost | `deploy/nginx/eesoc.raphsinai.dev.conf` | `deploy/nginx/eesoc.com.conf` |
| Moves when | every push to `main` | somebody runs **promote** |
| Database | its own, disposable | the real one |
| Indexed by Google | no (`X-Robots-Tag` at the edge) | yes |

GitOps: GitHub Actions builds and publishes images, then commits the new tag to
the **staging** overlay. ArgoCD watches each overlay and reconciles. **CI never
runs `kubectl`** — the repository is the only statement of what is deployed, and
a rollback is `git revert`.

```
push to main ─> test ─> build & push images ─> pin tag in overlays/staging
                                                        │
                                          ArgoCD ─> eesoc-staging ns
                                                        │
                                                   ( you look at it )
                                                        │
                        run `promote` workflow ─> copy that same tag
                                                   into overlays/production
                                                        │
                                          ArgoCD ─> eesoc ns ─> eesoc.com
```

## Releasing

1. Merge to `main`. CI tests, builds five images tagged with the commit SHA,
   and pins them in `overlays/staging`. Within a minute or two
   <https://eesoc.raphsinai.dev> is running the new code.
2. Look at it. This is the only step a machine cannot do.
3. Actions → **promote to production** → *Run workflow*. Leave the SHA blank to
   promote whatever staging is running.

**Promotion moves the image, not the source.** The tag staging has been running
is copied across verbatim; nothing is rebuilt. A rebuild of the same commit can
still differ — a base image moves, a transitive dependency publishes a patch —
and then what is on eesoc.com is not what anyone looked at.

The workflow refuses to run when it would be unsafe:

| It stops if | Because |
|---|---|
| staging's five services are on different SHAs | a half-finished deploy; that combination has never run anywhere |
| production is already on the requested SHA | nothing to do, and the empty commit is noise |
| the SHA is not an ancestor of `main` | a typo'd SHA commits an image reference that does not exist, and production sits in `ImagePullBackOff` |
| either overlay fails to render | ArgoCD would otherwise find out mid-sync, with production part-applied |

To **roll back**, `git revert` the promote commit, or run promote again with an
older SHA typed into the input — any commit on `main` whose images are still in
GHCR.

To require a second pair of eyes before production, add required reviewers to
the `production` environment in the repository settings. The workflow already
declares it; with no reviewers configured it simply runs.

### Why one branch and not a `staging` branch

Promotion is a copy of an image tag, not a merge. A `staging` branch would make
promotion a merge, and a merge can carry commits that were never on staging —
which is the exact thing this is supposed to prevent. The two environments are
separated by directory.

## First-time setup

**One line to change, twice:** `repoURL` in
`deploy/argocd/application-production.yaml` and
`deploy/argocd/application-staging.yaml`.

The `ghcr.io/OWNER/...` entries in each overlay's `kustomization.yaml` are
*match keys*, not real image names — CI rewrites them to
`ghcr.io/<your-user>/eesoc-<svc>:<sha>` using `${{ github.repository_owner }}`,
so they can be left exactly as they are. Verified by running the rewrite:
`kustomize edit set image` fills in `newName`/`newTag` and the render comes out
as `ghcr.io/<user>/eesoc-api:<sha>`.

The base carries **no** `images:` block and **no** Ingress hosts: both are
per-environment, and a shared value for either is how staging ends up on
eesoc.com. The base also carries no `namespace:` — the overlay sets it, which is
what lets the same manifests run twice in one cluster.

Everything below is per-environment. Do it once for `eesoc` and once for
`eesoc-staging`.

### 1. Secrets

Database credentials are **not** in this list: CloudNativePG generates them and
publishes them as `eesoc-db-app`, which the adapter reads by key. There is no
Postgres password to set, rotate or leak.

Everything else non-secret is in the ConfigMap. These are not, and are **not in
git**:

```bash
kubectl create namespace eesoc
kubectl -n eesoc create secret generic eesoc-secrets \
  --from-literal=ICU_API_KEY='...' \
  --from-literal=SMTP_USER='...' \
  --from-literal=SMTP_PASS='...' \
  --from-literal=S3_ACCESS_KEY_ID='...' \
  --from-literal=S3_SECRET_ACCESS_KEY='...'
```

To change **only** the SMTP password later, use the script rather than the line
above:

```bash
./scripts/set-smtp-secret.sh          # reads SMTP_PASS from backend.env
./scripts/set-smtp-secret.sh --ask    # or type it, not echoed
```

`kubectl create` fails once the secret exists, and the reflex fix — delete and
recreate — quietly takes `ICU_API_KEY` and the S3 keys with it. The script
patches the one key, keeps the password out of argv and shell history, and
restarts the api (which reads SMTP_PASS at startup, so the running pod would
otherwise keep the old one).

#### Email will break once a year, on purpose

The webmaster mailbox password is force-reset annually by Imperial. When that
happens every outbound email stops — login codes included, which locks members
out of the site. Re-run the script with the new password.

Verify before trusting it:

```bash
python3 scripts/check-smtp.py you@ic.ac.uk
```

That exists because a wrong password, a blocked port, a `From` the mailbox may
not send as, and a tenant with SMTP AUTH switched off all fail identically from
the application's side and need completely different fixes. The likeliest of
those is the last: **Exchange Online disables SMTP AUTH per-mailbox by default**,
and no password change fixes it — Imperial IT has to enable Authenticated SMTP
on `eesocweb@ic.ac.uk`. The checker names that case specifically.

A **private repository publishes private images**, so the cluster also needs a
registry credential. The four deployments reference `ghcr-pull`:

```bash
for ns in eesoc eesoc-staging; do
  kubectl -n "$ns" create secret docker-registry ghcr-pull \
    --docker-server=ghcr.io \
    --docker-username=<github-user> \
    --docker-password=<token with read:packages>
done
```

Without it every pod sits in `ImagePullBackOff` with `denied`, which reads like
a missing image rather than a missing credential.

`ICU_API_KEY` comes from eActivities. Check which key belongs to EESoc before
using it — the one currently in `backend.env` matches neither key seen in the
HTTPie screenshots, and repeated auth failures get the cluster's IP **banned for
an hour**.

#### The staging secret is deliberately smaller

```bash
kubectl create namespace eesoc-staging
kubectl -n eesoc-staging create secret generic eesoc-secrets \
  --from-literal=S3_ACCESS_KEY_ID="GK$(openssl rand -hex 12)" \
  --from-literal=S3_SECRET_ACCESS_KEY="$(openssl rand -hex 32)"
```

Note what is **not** there.

**`ICU_API_KEY` must stay out.** eActivities rate-limits per source IP and bans
the whole society on breach — and both environments leave the cluster from the
same address. A staging `union` pod with a working key doubles the request rate
against that limit, and the ban that follows takes production's membership sync
down with it. With no key the bridge answers `503 ICU bridge is not configured`
and makes no upstream calls at all, which is exactly the behaviour staging
wants. Its own S3 keys, generated fresh, so staging cannot write into
production's bucket.

**`SMTP_PASS` is optional, and leaving it out is the safe default.** Without it
sign-in and event booking put themselves under maintenance (see *Maintenance
mode*), and sign-in codes are written to the api log instead —
`LOG_CODES_WHEN_UNCONFIGURED` is `true` in the staging overlay, so:

```bash
kubectl -n eesoc-staging logs deploy/api | grep -i code
```

is how you sign in to staging. That is a working credential in a log, which is
why production has it off.

Add the password only when testing the mail path itself, and remember it sends
as `eesocweb@ic.ac.uk` for real. Staging's database is separate and starts
empty, so the only addresses it can reach are ones typed in by hand — but a
waitlist promotion on test data will still email whoever is on it.

### 2. Uploads (self-hosted Garage)

Committee photos, event images, sponsor logos and PDFs go to a
[Garage](https://garagehq.deuxfleurs.fr/) instance in the cluster
(`deploy/k8s/base/garage.yaml`) — no external provider.

**This was MinIO until MinIO stopped publishing community images.** Docker Hub
now reports the `minio/minio` *repository* as `object not found`, and quay.io
answers `401 Unauthorized` on an anonymous pull. Anything still running MinIO is
running on a layer cached on its node and will not survive a reschedule — worth
checking if you have other MinIO deployments. Garage is S3-compatible and
actively published, and nothing in the application changed: `storage.py` already
spoke S3.

Browsers read objects through the site's own domain, so there is no second
hostname and no second certificate:

```
GET https://eesoc.com/media/ab/cd.webp
      │
      ├─ edge nginx (VM, terminates TLS)
      ├─ cluster ingress  ── strips /media, sets Host: eesoc-media.web.garage
      └─ garage :3902     ── serves the object anonymously
```

**Two ports, deliberately.** Garage has no public-read ACL on its S3 API the way
MinIO did. Unauthenticated reads are served by a separate *web* endpoint (3902),
which cannot write; the S3 API (3900) always requires credentials and is
reachable only from the api pod. That split is enforced twice — by Garage, and
by the NetworkPolicy. Verified: an anonymous S3-API read is refused while the
same object fetches fine over the web endpoint.

The bucket is chosen from the `Host` header against `root_domain`, which is why
the Ingress sets `upstream-vhost: eesoc-media.web.garage` rather than putting the
bucket in the path.

**Initialisation runs as a sidecar, not a Job.** The Garage CLI authenticates
with a node key stored in the metadata directory, so it must share the pod's
volume; a Job would have to fight over a ReadWriteOnce volume. It also costs no
extra pod, which matters on a node at its pod limit. The script is idempotent —
verified by running it twice against an initialised cluster, where every step
reports "already" and changes nothing.

Credentials are **imported**, not generated: `S3_ACCESS_KEY_ID` and
`S3_SECRET_ACCESS_KEY` in `eesoc-secrets` are the source of truth, so a rebuilt
cluster picks the same key up instead of minting one that then has to be copied
back into Kubernetes by hand. Garage requires a specific shape:

```
S3_ACCESS_KEY_ID      GK + 24 hex characters   (openssl rand -hex 12)
S3_SECRET_ACCESS_KEY  64 hex characters        (openssl rand -hex 32)
GARAGE_RPC_SECRET     64 hex characters        (openssl rand -hex 32)
```

`garage key import` rejects anything else. The import step's output is sent to
`/dev/null` because it echoes the secret back, and that would otherwise sit in
the pod log for anyone with read access.

Public read is deliberate — these are event posters and sponsor logos, meant for
any browser. **Nothing private is ever written here.** Objects use random keys
that are never reused, which is what makes the one-year immutable cache header
safe.

> Garage runs as a single replica with `strategy: Recreate` — two pods cannot
> hold the same ReadWriteOnce volume, and a rolling update would deadlock.
> Uploads are briefly unavailable during a restart.

### 2a. Keeping the two environments apart

Three values differ per environment and all three are in the overlays, not the
base:

| Value | Where | Why it must differ |
|---|---|---|
| Ingress `spec.rules[].host` | `overlays/*/ingress-hosts.yaml` | the only thing routing a request to one namespace rather than the other |
| `SITE_ORIGIN` | `overlays/staging/configmap.yaml` | goes into sign-in links in email; pointing at eesoc.com sends a staging tester to production with a code that does not exist there |
| `ENVIRONMENT` | same | `staging`, which the api validates as a real value |

`S3_PUBLIC_BASE_URL` is deliberately **not** on that list. It is `/media` —
relative, so it follows whatever host served the page. The URL it produces is
stored in the database on upload (`committee_profiles.photo_url`,
`events.image_url`), so an absolute one would bake a hostname into every row and
the rows would outlive it. That mistake has already been made once here, with
the old test domain.

**A ConfigMap change does not restart anything.** The pods read their
environment once at startup and there is no checksum annotation on the pod
template, so ArgoCD will show Synced while the api still holds the old values:

```bash
kubectl -n eesoc-staging rollout restart deploy/api
```

**Staging is not indexed.** `X-Robots-Tag: noindex, nofollow, noarchive` is set
by the edge vhost rather than by the app, so it covers HTML, `sitemap.xml` and
uploaded media alike and cannot be forgotten in a new route. Without it staging
competes with eesoc.com in search results for the society's own name. If you
want it closed off entirely rather than merely unindexed, add HTTP basic auth to
`deploy/nginx/eesoc.raphsinai.dev.conf` — nothing in the app depends on being
publicly reachable.

### 2c. Staging's data: a scrubbed copy of production

Staging restores a snapshot of production after **every deploy**, so it looks
like the real site — the same events, sponsors, resources, committee and
lockers — while anything edited in the staging dashboard is discarded on the
next push. A staging edit can never become a production change.

```
PRODUCTION (ns eesoc)                         STAGING (ns eesoc-staging)
  CronJob snapshot, every 6h
    1 pg_dump the live database
    2 restore into a throwaway
      postgres INSIDE the job pod
    3 scrub.sql   — anonymise
    4 verify.sql  — prove it
    5 upload ─────────────────> eesoc-snapshots/latest.sql.gz
                                         │  read-only key
                                         v
                                 PostSync hook, every deploy
                                   1 fetch snapshot + media
                                   2 DROP SCHEMA, restore
```

**The copy is anonymised before it leaves production.** The database holds real
Imperial student data — CIDs, names, college shortcodes, emails, phone numbers,
purchase records. Staging is a second internet-facing host; putting a live copy
of that there would be a data-protection problem, not a convenience. So:

| Kept | Removed |
|---|---|
| Events, sponsors, resources, lockers, site content | Every member and user email, name and shortcode |
| Committee **names and roles** — already published on eesoc.com | Committee CIDs, logins, emails, phone numbers |
| Row counts and shapes, so bookings and waitlists behave realistically | Session tokens and sign-in codes (**deleted**, not rewritten) |
| `order_number`, so reservation matching still works | Purchaser names, CIDs and emails on Union sales |

Addresses become `user<id>@staging.invalid`. `.invalid` is reserved by RFC 2606
and never resolves, so even a staging instance given a working SMTP password
cannot reach a real person — mail fails at DNS. That is the backstop behind
leaving `SMTP_PASS` out of the staging secret, not a replacement for it.

**Sessions are deleted rather than anonymised.** A session row is a working
credential whatever name is attached to it; copied to staging, a leak there
would be a leak of live production logins.

#### Why it cannot run backwards

The failure that matters is not "staging has stale data", it is "the refresh ran
the wrong way and production was overwritten". Four independent things prevent
it, and no single mistake defeats them:

1. **Production never connects to staging.** The snapshot job writes one object
   to a bucket and stops. It holds no staging credential.
2. **Staging's production credential is read-only** — a Garage key with `read`
   on two buckets and nothing else, enforced by Garage.
3. **The NetworkPolicy** only lets staging reach production's S3 port at all.
4. **`restore.sh` refuses to run** unless `ENVIRONMENT=staging`, the pod's
   namespace (from the Downward API, not a manifest string) is `eesoc-staging`,
   and `PGHOST` resolves inside that namespace. Verified against all four
   refusal cases.

And `verify.sql` walks **every text column of every table** in
`information_schema` rather than a list someone maintains — so a future
migration that adds an email column is caught even though nobody remembered to
add it to `scrub.sql`, and the job fails instead of shipping it. Tested: adding
an unscrubbed column to `events` fails the snapshot and publishes nothing.

#### Extra secrets this needs

One key pair, generated once, stored in both namespaces — written in production,
read in staging:

```bash
RO_ID="GK$(openssl rand -hex 12)"
RO_SECRET="$(openssl rand -hex 32)"

kubectl -n eesoc patch secret eesoc-secrets --type merge -p "{\"stringData\":{
  \"STAGING_RO_ACCESS_KEY_ID\":\"$RO_ID\",
  \"STAGING_RO_SECRET_ACCESS_KEY\":\"$RO_SECRET\"}}"

kubectl -n eesoc-staging patch secret eesoc-secrets --type merge -p "{\"stringData\":{
  \"PROD_RO_ACCESS_KEY_ID\":\"$RO_ID\",
  \"PROD_RO_SECRET_ACCESS_KEY\":\"$RO_SECRET\"}}"

kubectl -n eesoc rollout restart deploy/garage   # imports the key
```

Garage requires the `GK` + 24 hex / 64 hex shape; `key import` rejects anything
else.

#### Running one now

A deploy restores the newest snapshot, which may be up to six hours old. To take
a fresh one first:

```bash
kubectl -n eesoc create job --from=cronjob/snapshot snapshot-now
kubectl -n eesoc logs -f job/snapshot-now -c dump
```

Then re-sync staging in ArgoCD, or delete the refresh job to make the hook run
again. Change `schedule:` in `overlays/production/snapshot-cronjob.yaml` if
staging needs to track production more closely; the only cost is reading the
database more often.

#### Media

Uploaded images are copied with `rclone copy`, never `sync` — sync would delete
anything uploaded through the staging dashboard, which is the exact workflow
staging exists to test. The copy is incremental, so after the first run only
genuinely new objects transfer. These objects are already public: they are
served anonymously from eesoc.com.

### 2b. TLS and the edge proxy

**TLS terminates on the VM in front of the cluster**, not in Kubernetes. The
Ingress carries no certificate and no cert-manager annotation; the hop from the
VM into the cluster is plain HTTP.

`deploy/nginx/` holds one vhost per environment, both pointing at the same
cluster ingress and separated only by `server_name`. Point each `upstream` at
the cluster's ingress address, then:

```bash
cp deploy/nginx/eesoc.com.conf /etc/nginx/sites-available/eesoc.com
ln -s /etc/nginx/sites-available/eesoc.com /etc/nginx/sites-enabled/
certbot --nginx -d eesoc.com -d www.eesoc.com

cp deploy/nginx/eesoc.raphsinai.dev.conf \
   /etc/nginx/sites-available/eesoc.raphsinai.dev
ln -s /etc/nginx/sites-available/eesoc.raphsinai.dev /etc/nginx/sites-enabled/
certbot --nginx -d eesoc.raphsinai.dev

nginx -t && systemctl reload nginx
```

**Neither vhost may rewrite `Host`.** The cluster matches on it, and that match
is the entire separation between the two environments at this hop — a rewritten
Host sends staging traffic to production's Ingress.

Two things that are easy to get wrong:

- **`COOKIE_SECURE` stays `true`.** The browser is still on HTTPS; only the
  internal hop is not. Setting it false because the app "sees HTTP" would ship
  session cookies that any network observer could lift.
- **The cluster must not be reachable from the internet.** The Ingress serves
  plain HTTP for any host that resolves to it, so restrict it to the proxy VM at
  the firewall or with a LoadBalancer source range. Otherwise the site is
  available unencrypted and the Secure flag protects nothing.

The proxy sets `X-Forwarded-Proto: https` and `X-Forwarded-For`; the latter is
what login-code rate limiting keys off, so without it every request looks like it
comes from the same address.

### 3. Postgres (CloudNativePG)

`deploy/k8s/base/postgres.yaml` declares a CNPG `Cluster` — two instances (one
primary, one replica), the operator handling failover and minor upgrades. It
assumes the CNPG operator is already installed in `cnpg-system`; the CRDs come
with the operator, not with this app.

CNPG creates `eesoc-db-rw` (primary), `eesoc-db-ro` (replicas) and the
`eesoc-db-app` secret. The adapter uses `eesoc-db-app`, so it always follows the
current primary.

**Backups are deliberately not configured yet.** The `ScheduledBackup` and
`barmanObjectStore` blocks are written out and commented in
`deploy/k8s/base/postgres.yaml`; they need a bucket and credentials. The two-node
cluster survives a node failure but not a bad migration or a `DELETE` without a
`WHERE`, so this is worth returning to before the database holds bookings people
care about.

### 4. ArgoCD

```bash
kubectl apply -f deploy/argocd/application-production.yaml
kubectl apply -f deploy/argocd/application-staging.yaml
```

### 5. WhatsApp (optional, per environment)

The `whatsapp` service links the society's WhatsApp Business number to the site
as a device — the way WhatsApp Web is — and posts announcements into the groups
chosen on the Email tab. It is **not** the WhatsApp Business API, which cannot
post into an existing group or community; it is the WhatsApp Web protocol,
which WhatsApp's terms do not allow for automation. **If WhatsApp decides it is
a bot, it bans the number that runs the community, with no appeal.**
`backend/whatsapp/src/link.js` and API.md's WhatsApp section say how it keeps
that risk small.

It is a kustomize **component**, not part of the base, so an environment has it
only if its overlay says so:

```yaml
components:
- ../../components/whatsapp
```

Staging has it. **Production does not until somebody adds that line to
`overlays/production/kustomization.yaml`** — a change to review and approve
like a promote, because it is the moment the site can start posting into the
real community. The component brings the deployment (one replica,
`Recreate`), a 256Mi volume for the linked session, a NetworkPolicy letting only
the api reach it, and `WHATSAPP_API_URL` for the api. Its image is built and
pinned with the others; production gets its pin on the next promote whether or
not it runs it.

To link: Email tab → WhatsApp card → **Link a number**, then on the society's
phone, WhatsApp Business → Settings → Linked devices → Link a device, and scan
the QR code (or **Link with a code instead**, and type the code shown). Then
enable the groups announcements may go to; each post picks among them. The session lives on the volume, so
deploys do not need a re-link; unlinking from the phone, or **Unlink** on the
card, ends it.

**On staging, enabling a group asks first**: staging can be linked to the real
number too — a second linked device — and then its groups are the real
community. Enable only a group made for testing. The snapshot scrub empties
enabled groups and every email's picked groups after each deploy, so nothing
copied from production can post from staging. `WHATSAPP_GROUP_ALLOWLIST`
(comma-separated group ids, shown on the card) is an optional hard fence in
any environment.

## Maintenance mode

Features whose dependencies are missing take themselves offline and say so,
rather than half-working. The set is **derived from what the process can
actually do**, so restoring a dependency restores the feature on the next
restart — no code change, no redeploy.

Today that means email. With no `SMTP_PASS` in `eesoc-secrets`:

| Feature | State | Why |
|---|---|---|
| Signing in | **under maintenance** | sign-in codes are email |
| Event booking | **under maintenance** | confirmations, reminders and the whole waitlist are email |
| Locker booking | available | self-service; only the yearly clear-out notice is email |
| Sponsor portal | available | needs sign-in, but sends nothing itself |
| Admin dashboard | available | see below |
| Everything public | available | events, resources, committee, sponsors, search |

So a first deploy with no SMTP password comes up as a working public site that
is honest about the parts it cannot do. Add the password, restart the api, and
sign-in and booking return by themselves.

`GET /status` is what the SPA renders its banners from, and it is public: the
whole point is that somebody who cannot sign in is told why.

**Admin is never gated automatically.** The webmaster password lapses every
year, and the person who has to fix it is an admin — taking the dashboard away
at that exact moment would turn a one-command fix into an outage. Sign-in going
down already keeps new people out; an existing admin session keeps working.

**Booking is gated even though guests could technically still book.** A
reservation nobody can be sent a confirmation for is not a booking, it is a
support ticket.

To force something off by hand (breakage the process cannot detect), set
`MAINTENANCE_FEATURES` in the ConfigMap and restart the api:

```yaml
MAINTENANCE_FEATURES: "lockers"
MAINTENANCE_MESSAGE: "Locker booking reopens on 1 October."
```

Keys: `login`, `bookings`, `lockers`, `sponsor_portal`, `admin`. An unknown key
is logged as a warning rather than ignored — believing a feature is off when it
is not is the worse failure. Cancelling and releasing are never gated: removing
the only way to give back a held seat or a locker would strand it.

## Things worth knowing

**Migrations run themselves.** The `database` service applies pending migrations
on startup under a Postgres advisory lock, so replicas queue rather than race.
There is no migration Job to remember. Sync waves order this: the CNPG cluster
comes up first (-1), then the adapter migrates (1), then the api starts (2). An edited migration that has already been
applied is logged as an error rather than silently re-run — migrations are
immutable once shipped.

**Migrations expand, deploy, then contract — never in one step.** A migration
is immutable once it has shipped, so the only lever over a rollout is what the
*next* file may contain, and the rule keeps a rolling update survivable:

1. **Expand** — an additive migration only: a new table, a new index, a new
   column that is nullable or carries a `DEFAULT`.
2. **Deploy** — the old code keeps working, because nothing it reads was
   removed or narrowed.
3. **Old and new pods run side by side** through the RollingUpdate
   (`maxUnavailable: 0`, `maxSurge: 1`) — this is the exact window a drop or a
   rename would break, and the window this rule exists to protect.
4. **Contract** — `DROP COLUMN`, `DROP TABLE`, `RENAME`, or narrowing a column
   to `NOT NULL` land **only in a later deploy**, once no running code depends
   on the old shape. The file must carry `-- contract-step: <reason>` saying
   why *now* is safe.

`scripts/check-migrations.py` (CI job `migrations`) fails the build on any of
those without the marker, and prints the marker's reason when one is present —
so a contract step is a reviewed, named decision, not housekeeping that
happened to look safe. The rule is prospective: it gates migrations from 035
onward, because 000–034 shipped before the rule existed and an applied
migration is immutable now — their drops and renames are exactly why the rule
was written, and editing history to make it look compliant would be the worse
contract.

**`api` runs one replica.** The daily ICU sync guard is a Postgres-level lease
claimed through the database adapter (`job_leases`, migration 035), so it
spans pods — two api replicas could not double-run the sync, and the old
"move the sync to a CronJob before scaling up" warning is done. It stays at
one for simplicity, and because the event-announce loop still runs
in-process.

**`union` is pinned to one replica, with `strategy: Recreate`.** eActivities
rate-limits per source IP and bans the whole society on breach. The throttle and
cache that keep us under the limit are in-process, so two instances would double
the request rate — including briefly during a rolling update, which is what
`Recreate` prevents.

**Probes are asymmetric on purpose.** `/health` (liveness) does not touch the
database: a transient Postgres blip would otherwise restart every pod at once
and turn a hiccup into an outage. `/ready` does check dependencies, so a pod
that cannot reach the adapter leaves the load balancer instead of serving
errors.

**`DEV_LOGIN_ENABLED` must stay false.** It mints a session for any email with
no code. The api refuses to start if it is true while `ENVIRONMENT` is not
`development`, and CI asserts the default in `config.py` has not been flipped.

**Only the frontend is exposed.** `api`, `database`, `union` and `postgres` have
ClusterIP services and NetworkPolicies restricting who may reach them — the SQL
adapter has no authentication of its own, so nothing outside the api should be
able to talk to it.

## Local development

```bash
docker compose up --build     # http://localhost:8080
```

Local defaults to `ENVIRONMENT=development` and `DEV_LOGIN_ENABLED=true`, so you
can sign in as anyone without a mail server:

```bash
curl -X POST localhost:8080/api/auth/dev-login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@imperial.ac.uk","make_admin":true}'
```

The real code flow also works — with no `SMTP_HOST` set, codes are written to
`docker compose logs api` instead of being emailed.
