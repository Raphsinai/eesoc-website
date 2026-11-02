"""Cache hits reuse only identical, previously published Docker image inputs."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/deploy.yml'
SCRIPT = ROOT / 'scripts/ci-image-plan.sh'
SERVICES = {'api', 'database', 'frontend', 'garage-init', 'union', 'whatsapp'}


def git(cwd, *args):
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()


class ImagePlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        git(self.repo, 'init', '-q', '-b', 'main')
        git(self.repo, 'config', 'user.name', 'CI test')
        git(self.repo, 'config', 'user.email', 'ci@example.com')
        git(self.repo, 'config', 'commit.gpgsign', 'false')
        for name in ('frontend', 'backend/api', 'backend/whatsapp', '.github/workflows'):
            (self.repo / name).mkdir(parents=True, exist_ok=True)
        (self.repo / 'frontend/Dockerfile').write_text('FROM nginx:alpine\n')
        (self.repo / 'frontend/app.js').write_text('console.log(1)\n')
        (self.repo / 'backend/api/main.py').write_text('print(1)\n')
        (self.repo / 'backend/whatsapp/main.js').write_text('console.log(1)\n')
        (self.repo / '.github/workflows/deploy.yml').write_text('build-args: original\n')
        self.commit('base')
        self.base = git(self.repo, 'rev-parse', 'HEAD')
        (self.repo / 'docs.txt').write_text('unrelated docs\n')
        self.commit('docs only')
        self.target = git(self.repo, 'rev-parse', 'HEAD')
        self.registry = 'ghcr.io/test/eesoc-frontend'
        self.source_image = f'{self.registry}:{self.base}'
        self.target_image = f'{self.registry}:{self.target}'

        # Mock ONLY the registry inspection. The actual plan uses real git
        # history/diffs; tests never require Docker or a running registry.
        bin_dir = self.repo / 'fake-bin'
        bin_dir.mkdir()
        docker = bin_dir / 'docker'
        docker.write_text('''#!/usr/bin/env python3
import os, sys
if sys.argv[1:4] != ['buildx', 'imagetools', 'inspect']:
    sys.exit(2)
sys.exit(0 if sys.argv[4] in os.environ.get('PRESENT_IMAGES', '').split(',') else 1)
''')
        docker.chmod(0o755)
        self.mock_path = f'{bin_dir}:{os.environ["PATH"]}'

    def commit(self, message):
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-qm', message)

    def plan(self, available=(), context='frontend', service='frontend', base=None, target=None):
        output = self.repo / 'outputs.txt'
        output.write_text('')
        env = {
            **os.environ,
            'PATH': self.mock_path,
            'GITHUB_OUTPUT': str(output),
            'RELEASE_SHA': target or self.target,
            'PREVIOUS_SHA': base or self.base,
            'BUILD_CONTEXT': context,
            'IMAGE_NAME': service,
            'OWNER_LC': 'test',
            'PRESENT_IMAGES': ','.join(available),
        }
        proc = subprocess.run(['bash', str(SCRIPT)], cwd=self.repo, env=env,
                              capture_output=True, text=True)
        result = dict(line.split('=', 1) for line in output.read_text().splitlines())
        return proc, result

    def test_reuses_published_image_when_only_unrelated_files_changed(self):
        proc, result = self.plan(available=[self.source_image])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result['mode'], 'reuse')
        self.assertEqual(result['source'], self.source_image)
        self.assertEqual(result['target'], self.target_image)

    def test_builds_if_any_context_file_changed(self):
        (self.repo / 'frontend/app.js').write_text('console.log(2)\n')
        self.commit('frontend change')
        self.target = git(self.repo, 'rev-parse', 'HEAD')
        proc, result = self.plan(available=[self.source_image])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result['mode'], 'build')

    def test_builds_if_dockerfile_or_ci_build_settings_changed(self):
        for path in ('frontend/Dockerfile', '.github/workflows/deploy.yml'):
            with self.subTest(path=path):
                (self.repo / path).write_text((self.repo / path).read_text() + '#changed\n')
                self.commit('change ' + path)
                self.target = git(self.repo, 'rev-parse', 'HEAD')
                proc, result = self.plan(available=[self.source_image])
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(result['mode'], 'build')

    def test_falls_back_to_build_if_previous_image_not_available(self):
        proc, result = self.plan()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result['mode'], 'build')

    def test_exact_commit_image_short_circuits_build(self):
        (self.repo / 'frontend/app.js').write_text('console.log(2)\n')
        self.commit('frontend change')
        self.target = git(self.repo, 'rev-parse', 'HEAD')
        self.target_image = f'{self.registry}:{self.target}'
        proc, result = self.plan(available=[self.target_image])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result['mode'], 'exists')
        self.assertEqual(result['source'], self.target_image)

    def test_can_reuse_unchanged_api_even_if_frontend_changed(self):
        (self.repo / 'frontend/app.js').write_text('console.log(2)\n')
        self.commit('frontend change')
        self.target = git(self.repo, 'rev-parse', 'HEAD')
        source = f'ghcr.io/test/eesoc-api:{self.base}'
        proc, result = self.plan(available=[source], context='backend/api', service='api')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result['mode'], 'reuse')

    def test_does_not_reuse_unrelated_staging_history(self):
        git(self.repo, 'checkout', '-q', '--detach', self.base)
        (self.repo / 'other.txt').write_text('other side\n')
        self.commit('diverged')
        other = git(self.repo, 'rev-parse', 'HEAD')
        proc, result = self.plan(available=[self.source_image], base=other)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn('mode', result)


class WorkflowCacheTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load(WORKFLOW.read_text())

    def test_only_latest_branch_push_runs_for_each_branch(self):
        c = self.workflow['concurrency']
        self.assertIn('github.ref', c['group'])
        self.assertIn('github.run_id', c['group'])
        self.assertEqual(c['cancel-in-progress'], "${{ github.event_name != 'workflow_dispatch' }}")

    def test_dependency_and_browser_download_caches_enabled(self):
        steps = self.workflow['jobs']['checks']['steps']
        python = next(s for s in steps if s.get('uses', '').startswith('actions/setup-python'))
        node = next(s for s in steps if s.get('uses', '').startswith('actions/setup-node'))
        browsers = next(s for s in steps if s.get('name') == 'Cache Playwright Chromium download')
        self.assertEqual(python['with']['cache'], 'pip')
        self.assertIn('backend/api/requirements-dev.txt', python['with']['cache-dependency-path'])
        self.assertEqual(node['with']['cache'], 'npm')
        self.assertIn('package-lock.json', browsers['with']['key'])

    def test_api_image_built_once_and_tests_preserved(self):
        steps = self.workflow['jobs']['checks']['steps']
        stack = next(s['run'] for s in steps if s.get('name', '').startswith('Start isolated'))
        self.assertIn('build database union-stub api frontend', stack)
        self.assertIn('up -d --no-build postgres database union-stub mailpit api api-nologin frontend', stack)
        self.assertNotIn('build api-nologin', stack)
        self.assertTrue(any('python3 -m pytest tests -q' in s.get('run', '') for s in steps))
        self.assertTrue(any('npx playwright test --config' in s.get('run', '') for s in steps))

    def test_registry_reuse_only_after_checks_and_staging_after_all_six(self):
        jobs = self.workflow['jobs']
        self.assertEqual(jobs['images']['needs'], 'checks')
        self.assertEqual(jobs['release-staging']['needs'], ['checks', 'images'])
        self.assertEqual({m['name'] for m in jobs['images']['strategy']['matrix']['include']}, SERVICES)
        steps = jobs['images']['steps']
        decide = next(s for s in steps if s.get('id') == 'image-plan')
        self.assertEqual(decide['run'], 'bash scripts/ci-image-plan.sh')
        reuse = next(s for s in steps if s.get('name', '').startswith('Reuse existing'))
        build = next(s for s in steps if s.get('uses', '').startswith('docker/build-push-action'))
        self.assertIn('imagetools create', reuse['run'])
        self.assertEqual(build['if'], "steps.image-plan.outputs.mode == 'build'")
        self.assertIn('type=gha,scope=', build['with']['cache-from'])
        self.assertEqual(jobs['release-staging']['environment'], 'staging')


if __name__ == '__main__':
    unittest.main()
