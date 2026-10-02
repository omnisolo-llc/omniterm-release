"""The native agent workflow has narrower authority than an application release."""
import json
from pathlib import Path
import unittest

import bootstrap as b

ROOT = Path(__file__).resolve().parents[1]


class NativeBuildAuthorityTests(unittest.TestCase):
    def env(self, platform='linux-x86_64'):
        pin = json.loads((ROOT / 'ci/approved_agent_source.json').read_text())
        target = {'linux': 'linux', 'darwin': 'macos', 'windows': 'windows'}[platform.split('-')[0]]
        repo = 'omnisolo-llc/omniterm-release'
        return {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                'GITHUB_REPOSITORY': repo, 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_WORKFLOW_REF': repo + '/.github/workflows/omni-agent.yml@refs/heads/main',
                'GITHUB_SHA': 'a' * 40, 'GITHUB_RUN_ID': '120', 'GITHUB_RUN_ATTEMPT': '1',
                'SOURCE_REPOSITORY': 'ql-owo-lp/omniterm', 'SOURCE_BRANCH': pin['source_branch'],
                'SOURCE_ENTRYPOINT': 'scripts/release/agent_entrypoint.py',
                'RESOLVED_SOURCE_SHA': pin['source_sha'], 'RELEASE_TARGET': target,
                'OMNI_AGENT_PLATFORM': platform,
                'RELEASE_REQUEST': b.task_request(json.dumps({
                    'source_sha': pin['source_sha'], 'version': pin['version'],
                    'build_number': '1', 'build_only': True, 'ios_action': 'skip'}))}

    def verify(self, env):
        b.validate_integration_authority(env, env['SOURCE_REPOSITORY'], env['SOURCE_BRANCH'])

    def test_exact_approved_agent_build_is_admitted_on_every_native_platform(self):
        for family in ('linux', 'darwin', 'windows'):
            for arch in ('x86_64', 'aarch64'):
                with self.subTest(platform=family + '-' + arch):
                    self.verify(self.env(family + '-' + arch))

    def test_native_build_cannot_borrow_release_or_another_workflow_authority(self):
        env = self.env()
        changes = {'GITHUB_ACTIONS': 'false', 'GITHUB_EVENT_NAME': 'push',
                   'GITHUB_REPOSITORY': 'other/builder', 'GITHUB_REF': 'refs/heads/other',
                   'GITHUB_WORKFLOW_REF': 'omnisolo-llc/omniterm-release/.github/workflows/release.yml@refs/heads/main',
                   'GITHUB_SHA': 'unknown', 'GITHUB_RUN_ID': '0', 'GITHUB_RUN_ATTEMPT': '0',
                   'SOURCE_REPOSITORY': 'other/source', 'SOURCE_BRANCH': 'main',
                   'SOURCE_ENTRYPOINT': 'scripts/release/entrypoint.py',
                   'RESOLVED_SOURCE_SHA': 'b' * 40, 'RELEASE_TARGET': 'publish',
                   'OMNI_AGENT_PLATFORM': 'windows-x86_64'}
        for key, value in changes.items():
            with self.subTest(field=key), self.assertRaises(ValueError):
                self.verify({**env, key: value})

    def test_agent_build_refuses_effectful_unpinned_or_malformed_requests(self):
        env = self.env()
        request = json.loads(env['RELEASE_REQUEST'])
        for key, value in {'source_sha': 'b' * 40, 'version': '9.9.9',
                           'build_number': '2', 'build_only': False,
                           'ios_action': 'upload', 'automatic_release': True,
                           'include_selfhost': False, 'unexpected': 'value'}.items():
            with self.subTest(field=key), self.assertRaises(ValueError):
                self.verify({**env, 'RELEASE_REQUEST': json.dumps({**request, key: value})})
        for raw in ('[]', 'null', 'not json', '{}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.verify({**env, 'RELEASE_REQUEST': raw})

    def test_source_argument_cannot_diverge_from_approved_environment(self):
        env = self.env()
        for repo, branch in [('other/source', env['SOURCE_BRANCH']),
                             (env['SOURCE_REPOSITORY'], 'release/omni-agent-forged')]:
            with self.assertRaises(ValueError):
                b.validate_integration_authority(env, repo, branch)


if __name__ == '__main__':
    unittest.main()
