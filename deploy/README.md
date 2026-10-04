# Deployment

GitOps: GitHub Actions builds and publishes images, then commits the new tag to
`deploy/k8s/base/kustomization.yaml`. ArgoCD watches
`deploy/k8s/overlays/production` and reconciles. **CI never runs `kubectl`** —
the repository is the only statement of what is deployed, and a rollback is
`git revert`.

```
push to main ──> test ──> build & push images ──> commit image tag
                                                        │
                                              ArgoCD sync ──> cluster
```

## First-time setup

**One line to change:** `repoURL` in `deploy/argocd/application.yaml`.

The `ghcr.io/OWNER/...` entries in `deploy/k8s/base/kustomization.yaml` are
*match keys*, not real image names — CI rewrites them to
`ghcr.io/<your-user>/eesoc-<svc>:<sha>` using `${{ github.repository_owner }}`,
so they can be left exactly as they are. Verified by running the rewrite:
`kustomize edit set image` fills in `newName`/`newTag` and the render comes out
as `ghcr.io/<user>/eesoc-api:<sha>`.

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
kubectl -n eesoc create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io \
  --docker-username=<github-user> \
  --docker-password=<token with read:packages>
```

Without it every pod sits in `ImagePullBackOff` with `denied`, which reads like
a missing image rather than a missing credential.

`ICU_API_KEY` comes from eActivities. Check which key belongs to EESoc before
using it — the one currently in `backend.env` matches neither key seen in the
HTTPie screenshots, and repeated auth failures get the cluster's IP **banned for
an hour**.

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

### 2a. Temporary test domain

`eesoc.raphsinai.dev` is served while eesoc.com still points at the old site.
Three things had to move together, and all are marked TEMPORARY:

- both Ingresses list the host (without a matching rule the controller answers
  404 from its default backend, whatever the proxy sends);
- `SITE_ORIGIN`, which is what goes into sign-in links in email;
- `S3_PUBLIC_BASE_URL`, which is the absolute URL browsers fetch uploads from.

The vhost on the proxy VM is `deploy/nginx/eesoc.raphsinai.dev.conf`, installed
at `/etc/nginx/sites-available/eesoc.raphsinai.dev`.

**A ConfigMap change does not restart anything.** The pods read their
environment once at startup and there is no checksum annotation on the pod
template, so ArgoCD will show Synced while the api still holds the old values.
`kubectl -n eesoc rollout restart deploy/api` after changing it.

To cut over to the real domain: point DNS at the proxy, drop the extra host
from both Ingresses, reset the two ConfigMap values, and restart the api.

### 2b. TLS and the edge proxy

**TLS terminates on the VM in front of the cluster**, not in Kubernetes. The
Ingress carries no certificate and no cert-manager annotation; the hop from the
VM into the cluster is plain HTTP.

`deploy/nginx/eesoc.conf` is the vhost for that VM. Point `upstream
eesoc_cluster` at the cluster's ingress address, then:

```bash
cp deploy/nginx/eesoc.conf /etc/nginx/sites-available/eesoc.conf
ln -s /etc/nginx/sites-available/eesoc.conf /etc/nginx/sites-enabled/
certbot --nginx -d eesoc.com -d www.eesoc.com
nginx -t && systemctl reload nginx
```

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
kubectl apply -f deploy/argocd/application.yaml
```

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

**`api` is pinned to one replica.** The daily ICU sync runs inside the process
and takes an in-process lock, which does not span pods. Before scaling up, move
the sync to a CronJob that calls `POST /admin/sync` and drop the in-process
scheduler.

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
