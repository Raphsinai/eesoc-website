#!/usr/bin/env python3
"""Require a successful CI run with all checked image builds for one commit."""
import json
import os
import subprocess
import sys

sha = sys.argv[1]
repo = os.environ["GITHUB_REPOSITORY"]
def api(path):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))
runs = api(f"repos/{repo}/actions/workflows/deploy.yml/runs?head_sha={sha}&per_page=100")["workflow_runs"]
for run in runs:
    if run["head_sha"] != sha or run["conclusion"] != "success" or run["event"] not in ("push", "workflow_dispatch"):
        continue
    jobs = api(f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100")["jobs"]
    checks = [j for j in jobs if j["name"] == "checks" and j["conclusion"] == "success"]
    images = [j for j in jobs if j["name"].startswith("images (") and j["conclusion"] == "success"]
    names = ("api", "database", "union", "frontend", "garage-init", "whatsapp")
    if checks and len(images) == 6 and all(any(f"images ({name}," in j["name"] for j in images) for name in names):
        print(f"Verified CI checks and six image builds: {run['html_url']}")
        sys.exit(0)
print(f"::error::No successful CI checks + six image builds for {sha}. Run CI manually on that SHA first.")
sys.exit(1)
