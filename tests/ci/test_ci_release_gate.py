"""Release gating must accept checked, published images only; never a failed CI."""
import importlib.util
from pathlib import Path

FILE = Path(__file__).resolve().parents[2] / 'scripts' / 'check-ci-release.py'
spec = importlib.util.spec_from_file_location('eesoc_ci_release_gate', FILE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
SHA = 'a' * 40
OTHER_SHA = 'b' * 40
REPO = 'Raphsinai/eesoc-website'


def fake_api(runs, *, images=None, checks=True):
    jobs = ([{'name': 'checks', 'conclusion': 'success'}] if checks else [])
    jobs += [{'name': f'images ({svc}, {svc})', 'conclusion': 'success'}
             for svc in (images if images is not None else gate.SERVICES)]

    def response(path):
        if path.endswith('/jobs?per_page=100'):
            return {'jobs': jobs}
        if '?head_sha=' in path:
            return {'workflow_runs': runs}
        if '?event=workflow_dispatch' in path:
            return {'workflow_runs': runs}
        raise AssertionError(path)
    return response


def example(event='push', *, sha=SHA, title=None, conclusion='success'):
    return {'id': 123, 'event': event, 'head_sha': sha,
            'display_title': title if title is not None else f'CI {sha}',
            'conclusion': conclusion, 'html_url': 'https://github.com/example/run/123'}


def test_push_requires_all_checks_and_images():
    assert gate.has_release(REPO, SHA, fake_api([example()]))
    assert not gate.has_release(REPO, SHA, fake_api([example()], checks=False))
    assert not gate.has_release(REPO, SHA, fake_api([example()], images=gate.SERVICES[:-1]))
    assert not gate.has_release(REPO, SHA, fake_api([example(conclusion='failure')]))


def test_manual_build_of_older_sha_uses_checked_target_not_workflow_head():
    dispatched = example('workflow_dispatch', sha=OTHER_SHA, title=f'CI {SHA}')
    assert gate.has_release(REPO, SHA, fake_api([dispatched]))
    assert not gate.has_release(REPO, OTHER_SHA, fake_api([dispatched]))
    assert not gate.has_release(REPO, SHA, fake_api([example('workflow_dispatch', sha=OTHER_SHA, title='CI ' + OTHER_SHA)]))


def test_pr_runs_never_authorize_a_release():
    assert not gate.has_release(REPO, SHA, fake_api([example('pull_request')]))
