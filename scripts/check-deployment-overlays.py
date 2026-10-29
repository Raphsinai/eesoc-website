#!/usr/bin/env python3
"""Validate rendered production/staging manifests before publishing images."""
import sys, yaml

def load(path):
    return [d for d in yaml.safe_load_all(open(path)) if d]

def hosts(docs):
    return {
        r.get("host")
        for d in docs if d["kind"] == "Ingress"
        for r in (d["spec"].get("rules") or [])
    }

def namespaces(docs):
    return {
        d["metadata"]["name"] if d["kind"] == "Namespace"
        else d["metadata"].get("namespace")
        for d in docs
    } - {None}

prod, stage = load("production.yaml"), load("staging.yaml")
fail = []

# A host in both renders means both Ingresses claim the name and the
# controller picks one — a coin toss over which environment answers.
shared_hosts = hosts(prod) & hosts(stage)
if shared_hosts:
    fail.append(f"host served by both environments: {shared_hosts}")

shared_ns = namespaces(prod) & namespaces(stage)
if shared_ns:
    fail.append(f"namespace shared by both environments: {shared_ns}")

# Catches the overlay patch being dropped: staging would then inherit
# production's values and quietly send testers to eesoc.com.
for docs, name, expect in ((prod, "production", "https://eesoc.com"),
                           (stage, "staging", "https://eesoc.raphsinai.dev")):
    cfg = next(d for d in docs
               if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "eesoc-config")
    got = cfg["data"]["SITE_ORIGIN"]
    if got != expect:
        fail.append(f"{name} SITE_ORIGIN is {got}, expected {expect}")
    if cfg["data"]["DEV_LOGIN_ENABLED"] != "false":
        fail.append(f"{name} has the dev login bypass enabled")

# Empty rules in the base are the fail-safe; an overlay that renders
# with none of its own is an environment serving nothing.
for docs, name in ((prod, "production"), (stage, "staging")):
    if not hosts(docs):
        fail.append(f"{name} renders no Ingress hosts")

# Restoring a snapshot drops the entire staging schema (including
# sessions). It must NEVER run during a normal deploy, and must not
# even be renderable on production. Keep a suspended CronJob as a
# manual-only template; kubectl create job --from=cronjob is explicit.
def names(docs, kind):
    return {d["metadata"]["name"] for d in docs if d["kind"] == kind}

for kind in ("Job", "CronJob"):
    if "refresh-from-production" in names(prod, kind):
        fail.append(f"the staging refresh {kind} renders into PRODUCTION")

# Staging must not acquire a live PostSync/Job just because Argo
# reconciles a new build. Suspended means no schedule can run.
if "refresh-from-production" in names(stage, "Job"):
    fail.append("staging refresh is an automatic Job; logins would be lost")
manual = [d for d in stage if d["kind"] == "CronJob"
          and d["metadata"]["name"] == "refresh-from-production"]
if len(manual) != 1:
    fail.append("staging must have exactly one manual refresh CronJob")
else:
    refresh = manual[0]
    if refresh["spec"].get("suspend") is not True:
        fail.append("staging refresh must remain SUSPENDED")
    annotations = refresh["metadata"].get("annotations") or {}
    if annotations.get("argocd.argoproj.io/hook"):
        fail.append("staging refresh must not run as an ArgoCD hook")
    containers = refresh["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    if not any(c.get("name") == "restore" for c in containers.get("initContainers", [])):
        fail.append("manual refresh lost its guarded restore container")

# The production snapshot runs only in production, never staging.
if "snapshot" in names(stage, "CronJob"):
    fail.append("the production snapshot CronJob renders into staging")

for f in fail:
    print(f"::error::{f}")
sys.exit(1 if fail else 0)
