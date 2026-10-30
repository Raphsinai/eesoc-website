#!/usr/bin/env python3
"""Validate a source SHA has a successful CI check AND all six published images.

A manual workflow_dispatch run is attached to main HEAD even when it checked
an older target_sha. Its validated run-name carries that target SHA; looking
only at workflow_run.head_sha silently rejected these legitimate builds.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

SERVICES = ('api', 'database', 'union', 'frontend', 'garage-init', 'whatsapp')


def gh_api(path: str) -> dict:
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def has_release(repo: str, sha: str, api=gh_api) -> bool:
    base = f'repos/{repo}/actions/workflows/deploy.yml/runs'
    # Push builds carry the source SHA in head_sha. Manual builds instead carry
    # that source in the run-name (set by the workflow from target_sha).
    push_runs = api(f'{base}?head_sha={sha}&per_page=100')['workflow_runs']
    manual_runs = api(f'{base}?event=workflow_dispatch&per_page=100')['workflow_runs']
    seen: set[int] = set()
    for run in (*push_runs, *manual_runs):
        if run['id'] in seen:
            continue
        seen.add(run['id'])
        if run.get('conclusion') != 'success':
            continue
        event = run.get('event')
        if event == 'push':
            if run.get('head_sha') != sha:
                continue
        elif event == 'workflow_dispatch':
            if run.get('display_title') != f'CI {sha}':
                continue
        else:
            continue  # A successful PR never publishes release images.
        jobs = api(f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100")['jobs']
        if not any(j['name'] == 'checks' and j['conclusion'] == 'success' for j in jobs):
            continue
        completed = {
            j['name'].split(' (', 1)[1].split(',', 1)[0]
            for j in jobs
            if j['name'].startswith('images (') and j['conclusion'] == 'success'
        }
        if set(SERVICES).issubset(completed):
            print(f"Verified CI checks and six images: {run['html_url']}")
            return True
    return False


def main() -> int:
    sha = sys.argv[1]
    repo = os.environ['GITHUB_REPOSITORY']
    if has_release(repo, sha):
        return 0
    print(f'::error::No successful CI checks and six image builds for {sha}. Run CI on that SHA first.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
