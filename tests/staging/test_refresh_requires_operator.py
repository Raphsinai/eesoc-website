"""Regression guard: code releases must not clear staging login sessions.

Staging's session cookie is valid for seven days and the token hash lives in
Postgres. The old Argo PostSync refresh dropped the whole schema on every push,
so the cookie stopped working immediately. Keeping a manual-only suspended
CronJob protects sessions and ongoing test submissions through redeploys.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "deploy/k8s/overlays/staging"
PROD = ROOT / "deploy/k8s/overlays/production"


def test_staging_refresh_is_not_a_deployment_hook():
    template = yaml.safe_load((STAGE / "refresh-cronjob.yaml").read_text())
    assert template["kind"] == "CronJob"
    assert template["metadata"]["name"] == "refresh-from-production"
    assert template["spec"]["suspend"] is True
    assert "argocd.argoproj.io/hook" not in template["metadata"].get("annotations", {})
    assert template["spec"]["concurrencyPolicy"] == "Forbid"

    pod = template["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert {c["name"] for c in pod["initContainers"]} == {"fetch", "restore"}
    assert pod["containers"][0]["name"] == "migrate"
    restore = next(c for c in pod["initContainers"] if c["name"] == "restore")
    env = {item["name"]: item for item in restore["env"]}
    assert env["POD_NAMESPACE"]["valueFrom"]["fieldRef"]["fieldPath"] == "metadata.namespace"
    assert env["ENVIRONMENT"]["valueFrom"]["configMapKeyRef"]["key"] == "ENVIRONMENT"
    assert restore["command"] == ["bash", "/snapshot/restore.sh"]


def test_staging_and_production_overlays_keep_refresh_manual_and_separate():
    stage = yaml.safe_load((STAGE / "kustomization.yaml").read_text())
    prod = yaml.safe_load((PROD / "kustomization.yaml").read_text())
    assert "refresh-cronjob.yaml" in stage["resources"]
    assert "refresh-job.yaml" not in stage["resources"]
    assert "refresh-cronjob.yaml" not in prod["resources"]
    assert "refresh-job.yaml" not in prod["resources"]


def test_staging_restore_guards_are_preserved_for_manual_resets():
    source = (ROOT / "deploy/snapshot/restore.sh").read_text()
    assert 'ENVIRONMENT:-' in source
    assert 'POD_NAMESPACE:-' in source
    assert 'PGHOST:-' in source
    assert "DROP SCHEMA public CASCADE;" in source
    # No normal release or database start-up invokes the destructive script.
    from_deploy = (ROOT / "deploy/argocd/application-staging.yaml").read_text()
    assert "restore.sh" not in from_deploy
