"""Guardrails for promoting ONLY an approved frontend feature, not all staging."""

import importlib.util
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'scripts/validate-selective-frontend.py'
spec = importlib.util.spec_from_file_location('selective_release_guard', SOURCE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


@pytest.fixture()
def isolated_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git('init', '-q')
    git('config', 'user.name', 'Release Test')
    git('config', 'user.email', 'release-test@example.invalid')
    frontend = tmp_path / 'frontend/src/views'
    frontend.mkdir(parents=True)
    (frontend / 'LockersView.vue').write_text('original locker page\n')
    (frontend / 'AdminView.vue').write_text('original admin page\n')
    specfile = tmp_path / 'tests/e2e/lockers/locker.spec.js'
    specfile.parent.mkdir(parents=True)
    specfile.write_text('// acceptance test\n')
    manifest = tmp_path / 'production.yaml'
    git('add', '.')
    git('commit', '-qm', 'production source')
    base = git('rev-parse', 'HEAD')
    manifest.write_text('images:\n- name: ghcr.io/OWNER/eesoc-frontend\n  newTag: ' + base + '\n')
    (frontend / 'LockersView.vue').write_text('original locker page\nUnion Shop link\n')
    git('add', 'frontend')
    git('commit', '-qm', 'feature candidate')
    source = git('rev-parse', 'HEAD')
    git('update-ref', 'refs/remotes/origin/release/test-locker-link', source)
    return tmp_path, manifest, source


def test_exactly_approved_frontend_file_allowed(isolated_repo):
    _, manifest, source = isolated_repo
    base, changed = guard.validate(
        source, 'frontend/src/views/LockersView.vue', manifest,
        'tests/e2e/lockers/locker.spec.js',
    )
    assert len(base) == 40
    assert changed == ['frontend/src/views/LockersView.vue']


def test_cannot_smuggle_another_frontend_change(isolated_repo):
    root, manifest, _ = isolated_repo
    (root / 'frontend/src/views/AdminView.vue').write_text('unapproved admin change\n')
    git('add', 'frontend')
    git('commit', '-qm', 'unapproved admin addition')
    source = git('rev-parse', 'HEAD')
    git('update-ref', 'refs/remotes/origin/release/test-locker-link', source)
    with pytest.raises(guard.ReleaseError, match='approved files'):
        guard.validate(source, 'frontend/src/views/LockersView.vue', manifest,
                       'tests/e2e/lockers/locker.spec.js')


def test_reject_non_frontend_approval(isolated_repo):
    _, manifest, source = isolated_repo
    with pytest.raises(guard.ReleaseError, match='Only individual files inside frontend/'):
        guard.validate(source, 'backend/api/routes/events.py', manifest,
                       'tests/e2e/lockers/locker.spec.js')


def test_reject_unpushed_candidate(isolated_repo):
    root, manifest, _ = isolated_repo
    (root / 'frontend/src/views/LockersView.vue').write_text('modified again\n')
    git('add', 'frontend')
    git('commit', '-qm', 'unpublished change')
    source = git('rev-parse', 'HEAD')
    with pytest.raises(guard.ReleaseError, match='origin/release/'):
        guard.validate(source, 'frontend/src/views/LockersView.vue', manifest,
                       'tests/e2e/lockers/locker.spec.js')


def test_reject_missing_browser_test(isolated_repo):
    _, manifest, source = isolated_repo
    with pytest.raises(guard.ReleaseError, match='not found'):
        guard.validate(source, 'frontend/src/views/LockersView.vue', manifest,
                       'tests/e2e/lockers/nonexistent.spec.js')
