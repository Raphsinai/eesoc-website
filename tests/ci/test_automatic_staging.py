"""A passing push-to-main CI must publish all images before staging, never prod."""
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/deploy.yml'


def _jobs():
    return yaml.safe_load(WORKFLOW.read_text())['jobs']


def test_only_successful_main_push_can_automatically_stage():
    jobs = _jobs()
    assert jobs['images']['needs'] == 'checks'
    assert set(item['name'] for item in jobs['images']['strategy']['matrix']['include']) == {
        'api', 'database', 'union', 'frontend', 'garage-init', 'whatsapp',
    }
    release = jobs['release-staging']
    assert release['needs'] == ['checks', 'images']
    assert release['if'] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    assert release['environment'] == 'staging'
    assert release['permissions'] == {'contents': 'write'}
    assert release['concurrency']['cancel-in-progress'] is False


def test_staging_publisher_is_atomic_and_does_not_touch_production():
    release = _jobs()['release-staging']
    steps = release['steps']
    assert steps[1]['with']['ref'] == '${{ needs.checks.outputs.sha }}'
    publish = steps[-1]['run']
    assert 'git fetch -q origin main staging-deploy' in publish
    assert 'git merge-base --is-ancestor "$RELEASE_SHA" origin/main' in publish
    assert '[ "$tag_count" -eq 6 ]' in publish
    assert 'git show "origin/staging-deploy:$overlay/kustomization.yaml"' in publish
    assert 'git merge-base --is-ancestor "$RELEASE_SHA" "$pinned"' in publish
    assert 'git merge-base --is-ancestor "$pinned" "$RELEASE_SHA"' in publish
    assert 'git switch -C staging-deploy "$RELEASE_SHA"' in publish
    assert 'kustomize" build "$overlay" > /dev/null' in publish
    assert 'git push --force-with-lease=' in publish
    assert 'HEAD:refs/heads/staging-deploy' in publish
    assert 'deploy/k8s/overlays/production' not in publish
    assert 'refs/heads/main' not in publish
    assert subprocess.run(['bash', '-n'], input=publish, text=True, capture_output=True).returncode == 0
