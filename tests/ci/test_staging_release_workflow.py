"""The gated staging workflow must deploy only verified images to GitOps."""
from pathlib import Path
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/stage-one-feature.yml'


def _jobs():
    return yaml.safe_load(WORKFLOW.read_text())['jobs']


def test_checked_release_gate_precedes_the_only_staging_write():
    steps = _jobs()['publish-checked-staging']['steps']
    gate = next(i for i, step in enumerate(steps)
                if 'check-ci-release.py' in step.get('run', ''))
    publish = next(i for i, step in enumerate(steps)
                   if 'git push --force-with-lease' in step.get('run', ''))
    assert gate < publish
    assert steps[publish]['name'] == 'Update staging GitOps branch with checked images'


def test_current_staging_pin_is_read_from_the_branch_argocd_tracks():
    jobs = _jobs()
    scripts = [step['run'] for job in jobs.values()
               for step in job['steps'] if 'run' in step]
    source = 'git show origin/staging-deploy:deploy/k8s/overlays/staging/kustomization.yaml'
    assert sum(source in script for script in scripts) == 2
    publish = scripts[-1]
    assert 'git switch -C staging-deploy "$RELEASE_SHA"' in publish
    assert 'HEAD:refs/heads/staging-deploy' in publish
    assert '--force-with-lease=' in publish
    assert 'gh pr create' not in publish
    assert 'git push origin main' not in publish
