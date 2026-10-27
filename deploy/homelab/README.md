# Staging on the homelab

Staging (<https://eesoc.raphsinai.dev>, namespace `eesoc-staging`) runs on its
own single-node RKE2 cluster in one VM on the homelab Proxmox cluster. It left
the TensorHost cluster on 2026-10-19: that node is shared with paying
customers, its root disk filled up and paused every VM on it (2026-10-15/16),
and staging's per-push image pulls were part of the churn.

Production is unchanged. It still runs on TensorHost and still moves only when
somebody runs **promote**.

```
visitor ─https─> Cloudflare ─tunnel─> cloudflared (in the cluster, dials out)
                                           │
                       rke2-ingress-nginx (ClusterIP, kube-system)
                                           │
                                  eesoc-staging namespace
```

| | |
|---|---|
| VM | **110 `eesoc-staging`** on **pve2** (Proxmox cluster `hypervisor`, nodes pve + pve2) |
| OS | Debian 13 (trixie) genericcloud, cloud-init (`cloud-init.yaml`) |
| Kubernetes | RKE2 v1.34 channel, Calico (iptables), bundled ingress-nginx, as production |
| Storage | `local-path` (directories on the VM disk), default class |
| GitOps | ArgoCD v3.4.6 in the VM, one Application: `deploy/argocd/application-staging.yaml` |
| Public access | Cloudflare Tunnel `eesoc-staging` (`ee536f2b-…`), connector `rke2/manifests/cloudflared.yaml` |
| SSH | `ssh eesoc-staging` (user `raph`; jumps through pve2 over the tailnet) |

The deploy flow uses a separate manual **stage one EESoc feature** GitHub
Actions workflow. Pushes to `main` test and publish images but do not update
staging. The feature workflow builds the selected next commit and pins its
images in `deploy/k8s/overlays/staging`; ArgoCD in this VM picks the pin up
within about three minutes. CI does not need direct access to this cluster.

## Getting in

```bash
ssh eesoc-staging                      # Host entry: HostName 192.168.1.223, ProxyJump root@pve2
sudo -i
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml PATH=$PATH:/var/lib/rancher/rke2/bin
kubectl -n eesoc-staging get pods
kubectl -n eesoc-staging logs deploy/api -c api | grep -A12 'Not sending'   # sign-in codes
```

The Mac's `~/.ssh/config` entry:

```
Host eesoc-staging
	HostName 192.168.1.223
	User raph
	ProxyJump root@100.88.220.66
```

ArgoCD's UI is not exposed. Port-forward it and sign in as `admin`:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
kubectl -n argocd port-forward svc/argocd-server 8080:80   # then ssh -L 8080:localhost:8080 eesoc-staging
```

The VM has no Tailscale of its own and needs none: pve2 is on the tailnet,
and the VM is one hop behind it on the LAN. Its address comes from the
router's DHCP; if it ever changes, `qm guest cmd 110 network-get-interfaces`
on pve2 shows the new one.

## Not interfering with the other VMs

The two rules for this VM were: stay inside Proxmox, and leave every other
guest alone. Each of these is a limit on staging, not on anything else:

| Resource | Limit | Why |
|---|---|---|
| Host | pve2, never in HA | pve's 15 GiB are committed to the NAS (12.5 GiB, no balloon). If HA moved staging there after a pve2 failure, the NAS would lose. |
| Memory | 6 GiB, balloon down to 4 GiB | pve2 had 21 GiB free. Proxmox inflates balloons above ~80% host RAM, so staging hands back 2 GiB first. |
| CPU | 4 vCPUs, `cpulimit 2`, `cpuunits 50` | Never more than half the host, and half the default scheduling weight, so every other VM wins under contention. Four vCPUs, not two, because RKE2's control plane requests more than two cores of scheduling room and CoreDNS could not be placed. |
| Disk | 32 GiB thin volume on `local-lvm`, `discard`, 200/100 MB/s and 4000/2000 IOPS caps | The pool is shared and already overcommitted. A full thin pool pauses every VM on the host, as tensormain's full disk did. Daily `fstrim` and early image GC (70/50%) return space; `discard` passes it through to the pool. |
| Network | 25 MB/s cap on the NIC | Image pulls cannot saturate the home uplink. |
| LAN | nftables fence in the guest (`cloud-init.yaml`) | Staging serves the internet from inside the home LAN. Nothing in the VM, host or pod, may open a connection to a private address except its own and the cluster's; nothing on the LAN may open one to it except SSH. IPv6 is off, since the LAN's v6 prefix is not fenceable by address. DNS goes to 1.1.1.1/9.9.9.9 rather than the Pi-hole. |
| Boot | `onboot`, startup order 99, a minute after the others | |

Measured on 2026-10-19 with staging deployed: about 3.2 GiB used in the
guest, 12 GiB of disk (36% of the thin volume), and pve2's thin pool at 69.5%.
It was 61% before the VM existed. **Watch the thin pool:**

```bash
ssh root@pve2 'lvs pve/data -o data_percent'
```

The pool is overcommitted by allocation independently of staging. VM 107
`openclaw` alone has 103 GiB allocated, and its 28 GiB of RAM cannot fit
alongside the running VMs whether staging exists or not. If the pool climbs,
run `fstrim -av` in the guest first.

## Public access: Cloudflare Tunnel

`eesoc.raphsinai.dev` is a proxied CNAME to the tunnel. Cloudflare terminates
TLS; cloudflared carries the request to the ingress Service with `Host`
untouched, and the overlay's Ingress matches on it, as production's edge nginx
does for eesoc.com. Nothing listens on the VM's network: there is no port
forward and the home IP is not published.

What production's edge vhost adds is done by the ingress controller instead
(`rke2/manifests/rke2-ingress-nginx-config.yaml`): `X-Robots-Tag: noindex`
(staging must not compete with eesoc.com in search), HSTS, nosniff,
Referrer-Policy and X-Frame-Options. `overlays/staging/ingress-https.yaml`
redirects plain HTTP.

The tunnel is **locally managed**: its routes are the `ingress:` list in
`cloudflared.yaml`, in git. Its credential is the Secret `cloudflared/tunnel-token`,
never committed. Managing the tunnel needs `~/.cloudflared/cert.pem` on Raph's
Mac, created by `cloudflared tunnel login` for the raphsinai.dev zone. The same
account has an unrelated `homeassistant` tunnel; leave it alone.

```bash
cloudflared tunnel info eesoc-staging            # connectors and edges
cloudflared tunnel token eesoc-staging           # re-create the Secret after a rebuild
```

To roll the hostname back to somewhere else, replace the CNAME in the
Cloudflare dashboard. `cloudflared tunnel route dns` only adds records.

## Data: production's snapshot, kept

The suspended, manual-only refresh template
(`overlays/staging/refresh-cronjob.yaml`) can restore an anonymised production
snapshot **only when explicitly invoked**. It does not run on commits, ArgoCD
syncs or Kubernetes scheduling. Ordinary staging deployments preserve login
sessions and staging-only edits. A manual refresh:

1. tries production (`RCLONE_CONFIG_PROD_ENDPOINT`, currently empty because
   there is no route);
2. otherwise restores the copy staging keeps in its own private
   `eesoc-snapshots` bucket (`overlays/staging/garage-snapshot-cache.yaml`).
   This is never `eesoc-media`, which is public;
3. fetches media referenced by the snapshot from `https://eesoc.com/media/`,
   skipping existing files, then replaces staging's database and migrates it.

**Only a manual refresh loses your login and test submissions.** To run one:

```bash
JOB=refresh-manual-$(date +%s)
kubectl -n eesoc-staging create job --from=cronjob/refresh-from-production "$JOB"
kubectl -n eesoc-staging get job "$JOB"
```

This replaces the staging schema, deleting all staging-only accounts, sessions,
and edits. The cached copy is still production's snapshot of 2026-10-18 12:17
UTC, seeded before the move; normal deployments do not update that seed.

To give staging fresh data again, production needs a read path for staging,
and that is a production change, so it needs Raph's approval. The smallest
version: an Ingress in the production overlay on a dedicated hostname for
Garage's S3 port, fronted by an edge vhost that allows only `GET`/`HEAD`.
Staging's credential there is the existing read-only key
(`PROD_RO_*`), which Garage limits to the two buckets. Then set
`RCLONE_CONFIG_PROD_ENDPOINT` to that URL in the refresh job.

To refresh by hand in the meantime, copy production's `latest.sql.gz` into
staging's `eesoc-snapshots/latest.sql.gz` using that read-only key from
anywhere that can reach production's Garage, then manually invoke `refresh-from-production` as above.

## Secrets (not in git)

| Namespace | Secret | Source |
|---|---|---|
| `eesoc-staging` | `eesoc-secrets` | copied from the old staging namespace: S3 + Garage RPC keys, `EMAIL_TOKEN_SECRET`, `SMTP_PASS`, `PUB_SMTP_PASS`, `PROD_RO_*`. **Never `ICU_API_KEY`** (CLAUDE.md, "eActivities bans the whole society"). |
| `eesoc-staging` | `ghcr-pull` | copied from the old staging namespace |
| `argocd` | `eesoc-website-repo` | its own read-only deploy key, "argocd homelab staging" in the repo's deploy keys. Revoke that key to cut this cluster off. |
| `cloudflared` | `tunnel-token` | `cloudflared tunnel token eesoc-staging` |

Staging still sends real mail from the shared mailboxes, restricted by
`MAIL_ALLOWLIST` to ras124@ic.ac.uk.

## Rebuilding from nothing

Everything here is disposable; the only state worth keeping is the kept
snapshot, and that can be re-seeded.

```bash
# 1. VM (on pve2). Refuses an existing VMID; destroy 110 first if rebuilding.
scp deploy/homelab/{create-vm.sh,cloud-init.yaml} root@pve2:/root/
ssh root@pve2 'bash /root/create-vm.sh'

# 2. RKE2 + cluster add-ons. Everything in rke2/manifests/ is applied by RKE2
#    itself from its manifests directory, now and on every start.
scp -r deploy/homelab/rke2 eesoc-staging:/tmp/
ssh eesoc-staging 'sudo install -D -m600 /tmp/rke2/config.yaml /etc/rancher/rke2/config.yaml &&
  sudo install -d /var/lib/rancher/rke2/server/manifests &&
  sudo install -m600 /tmp/rke2/manifests/*.yaml /var/lib/rancher/rke2/server/manifests/ &&
  curl -sfL https://get.rke2.io | sudo INSTALL_RKE2_CHANNEL=v1.34 sh - &&
  sudo systemctl enable --now rke2-server'

# 3. Secrets (table above), then the Application.
kubectl apply -f deploy/argocd/application-staging.yaml

# 4. Seed eesoc-snapshots/latest.sql.gz (see "Data"), then re-sync.
```

Three things that went wrong the first time, so they do not again:

- **CPU model.** `qm create` defaults to `kvm64`, and current images need
  x86-64-v2 or later ("Fatal glibc error: CPU does not support x86-64-v2").
  `create-vm.sh` sets `x86-64-v3`; both pve and pve2 support it.
- **CNI.** RKE2's default, Canal, segfaulted programming nftables on this
  kernel. Calico, which production runs, does not.
- **Volume binding.** With `local-path`'s default `WaitForFirstConsumer`, the
  app's PVCs (sync wave -1) stay Pending until a pod in a later wave mounts
  them, and ArgoCD never leaves wave -1. The class here binds `Immediate`,
  which needs the provisioner's `sharedFileSystemPath` mode.

## Left over on TensorHost

The old staging (ArgoCD app and namespace) was deleted from the TensorHost
cluster on 2026-10-19. Two things still mention it there:

- The **edge nginx vhost** for `eesoc.raphsinai.dev` on VM 110 (`tensorhost`),
  and its Let's Encrypt certificate. It gets no traffic now, and its renewal
  will fail because the name no longer points there. To remove it:
  `rm /etc/nginx/sites-enabled/eesoc.raphsinai.dev && nginx -t && systemctl reload nginx && certbot delete --cert-name eesoc.raphsinai.dev`.
- **Production's** `garage-ingress` NetworkPolicy
  (`overlays/production/snapshot-access.yaml`) still admits the
  `eesoc-staging` namespace to Garage's S3 port. Nothing lives there now, but
  a namespace of that name created on TensorHost later would inherit the
  access. Removing the rule is a production change: a file in the production
  overlay deploys on ArgoCD's next sync of `main`, not on promote. So it needs
  Raph's approval like any other.
