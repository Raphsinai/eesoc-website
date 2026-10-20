#!/usr/bin/env python3
"""Preflight guard for an isolated frontend-only production release.

A candidate commit must descend from the *currently deployed frontend source*,
be reachable on a named release branch, and differ in exactly the explicitly
approved frontend files. Do not allow a general staging SHA through this path.
"""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml


class ReleaseError(Exception):
    pass


def git(*args):
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if proc.returncode:
        raise ReleaseError(f"git {' '.join(args)}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def pinned_frontend(manifest):
    doc = yaml.safe_load(Path(manifest).read_text())
    images = [x for x in doc.get("images", []) if x.get("name") == "ghcr.io/OWNER/eesoc-frontend"]
    if len(images) != 1:
        raise ReleaseError("Production must have exactly one frontend image pin")
    tag = images[0].get("newTag", "")
    if not re.fullmatch(r"[0-9a-f]{40}", tag):
        raise ReleaseError(f"Production frontend tag is not a source commit SHA: {tag!r}")
    return tag


def validate(source_sha, approved_paths, manifest, test_spec):
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ReleaseError("Source SHA must be a full 40-character commit hash")

    base_sha = pinned_frontend(manifest)
    if source_sha == base_sha:
        raise ReleaseError("Candidate is already the production frontend source")

    git("cat-file", "-e", f"{source_sha}^{{commit}}")
    git("cat-file", "-e", f"{base_sha}^{{commit}}")
    git("merge-base", "--is-ancestor", base_sha, source_sha)

    # A release source must remain addressable: unreferenced detached SHAs can
    # disappear after garbage collection and cannot be reproduced or audited.
    refs = git("branch", "-r", "--contains", source_sha).splitlines()
    if not any(ref.strip().startswith("origin/release/") for ref in refs):
        raise ReleaseError("Candidate must be pushed to an origin/release/* branch")

    files = [part.strip() for part in approved_paths.split(",")]
    if not files or any(not f for f in files) or len(files) != len(set(files)):
        raise ReleaseError("Provide explicit, unique comma-separated approved file paths")
    if any(not f.startswith("frontend/") or ".." in Path(f).parts or f == "frontend/" for f in files):
        raise ReleaseError("Only individual files inside frontend/ are allowed")

    changed = set(git("diff", "--name-only", "--no-renames", base_sha, source_sha).splitlines())
    if not changed or changed != set(files):
        raise ReleaseError(
            "Candidate differs from the production frontend source in files "
            f"{sorted(changed)}, but approved files are {sorted(files)}"
        )
    git("diff", "--check", base_sha, source_sha)

    if not re.fullmatch(r"tests/e2e/[A-Za-z0-9_./-]+\.spec\.js", test_spec) or ".." in Path(test_spec).parts:
        raise ReleaseError("Test must be a Playwright .spec.js inside tests/e2e/")
    if not Path(test_spec).is_file():
        raise ReleaseError(f"Test not found in main: {test_spec}")

    return base_sha, sorted(changed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--approved-paths", required=True)
    parser.add_argument("--test-spec", required=True)
    parser.add_argument("--manifest", default="deploy/k8s/overlays/production/kustomization.yaml")
    args = parser.parse_args()
    try:
        base_sha, changed = validate(args.source, args.approved_paths, args.manifest, args.test_spec)
    except ReleaseError as exc:
        sys.exit(f"::error::{exc}")

    print(f"Production frontend source: {base_sha}")
    print(f"Selective candidate: {args.source}")
    print(f"Approved changes ONLY: {changed}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write(f"source_sha={args.source}\nbase_sha={base_sha}\n")


if __name__ == "__main__":
    main()
