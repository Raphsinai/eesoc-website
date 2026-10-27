# EESoc GitHub Actions runner and staged releases

EESoc CI uses a repository-scoped **self-hosted runner** named
`eesoc-ci-pve-114` (labels: `self-hosted`, `Linux`, `X64`, `eesoc-ci`). It is
installed on Proxmox `pve` as VM 114 (`eesoc-actions`), Ubuntu 24.04 LTS,
4 vCPUs, 6 GiB RAM, a 96 GiB virtual disk and 4 GiB swap. The runner runs
as a systemd service on the VM and builds/tests with its own Docker daemon.

## Deployment policy

* Pushes to `main` run `.github/workflows/deploy.yml`: tests and image builds
  only. They do **not** move either staging or production.
* To review a feature on staging, open GitHub Actions > **stage one EESoc
  feature** > **Run workflow** and provide the complete 40-character SHA of
  the **next** feature commit. The workflow checks the sequence, runs focused
  regressions, builds all six image tags from that historical commit, and
  commits only the selected image tags to the staging overlay with `[skip ci]`.
  ArgoCD reconciles that staging overlay. It refuses to skip a feature.
* Review the feature at `https://eesoc.raphsinai.dev` before staging the next.
* The `promote.yml` workflow remains **manual**. Do not run it without
  explicit production approval. It promotes staging's pinned artifacts.

At introduction of this policy, staging was pinned to `23bb17e`, and the
pending feature commits are, in order: `f7795dd`, `845ed8d`, `7a0f0e0`,
`2c1c859`, `3060d45`, `d0b55e6`. Use **full** SHAs when dispatching.

## Operations

On homelab-work, connect with `ssh runneradmin@192.168.1.132` (DHCP address;
confirm address in Proxmox if it changes). On the VM:

```bash
sudo systemctl status actions.runner.Raphsinai-eesoc-website.eesoc-ci-pve-114.service
sudo journalctl -u actions.runner.Raphsinai-eesoc-website.eesoc-ci-pve-114.service -n 100 --no-pager
docker ps
docker system df
```

The runner is a **single persistent executor**: jobs queue rather than running
concurrently. Each Docker Compose test must tear down containers and volumes
on success or failure. CI credentials and third-party action code execute with
access to the VM's Docker socket; do not run untrusted fork PRs here. Push
reviewed code only and use least-privilege job permissions. The VM is separate
from Kubernetes but shares network connectivity with the homelab and is not
an untrusted-code sandbox.

If GitHub starts reporting `billing` or `spending-limit` restrictions again,
verify whether the blocked job uses a GitHub-hosted runner; the self-hosted
smoke test in `runner-smoke.yml` validates the on-premises execution path.
