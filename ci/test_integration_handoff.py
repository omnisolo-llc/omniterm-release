"""Authorization/workflow boundaries only; no substitute hardware execution."""
import contextlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest import mock

import bootstrap
import test_bootstrap


class IntegrationHandoffTests(unittest.TestCase):
    def environment(self):
        builder = 'omnisolo-llc/omniterm-release'
        source = 'ql-owo-lp/omniterm'
        return {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                'GITHUB_REPOSITORY': builder, 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_WORKFLOW_REF': builder + '/.github/workflows/release.yml@refs/heads/main',
                'GITHUB_SHA': 'a' * 40, 'GITHUB_RUN_ID': '1', 'GITHUB_RUN_ATTEMPT': '1',
                'SOURCE_REPOSITORY': source,
                'SOURCE_ENTRYPOINT': 'scripts/release/entrypoint.py'}

    def test_launcher_refuses_pr_fork_and_other_source_repository(self):
        env = self.environment()
        source = 'ql-owo-lp/omniterm'
        for candidate, repository, branch in [
            ({**env, 'GITHUB_EVENT_NAME': 'pull_request'}, source, 'main'),
            ({**env, 'GITHUB_REPOSITORY': 'fork/builder'}, source, 'main'),
            ({**env, 'GITHUB_WORKFLOW_REF': 'another/workflow'}, source, 'main'),
            ({**env, 'GITHUB_RUN_ATTEMPT': '0'}, source, 'main'),
            ({**env, 'SOURCE_ENTRYPOINT': 'scripts/alternate.py'}, source, 'main'),
            (env, 'other/source', 'main'), (env, source, 'topic')]:
            with self.assertRaises(ValueError):
                bootstrap.validate_integration_authority(candidate, repository, branch)

    def test_trusted_predicate_does_not_execute_a_hardware_job(self):
        self.assertIsNone(bootstrap.validate_integration_authority(
            self.environment(), 'ql-owo-lp/omniterm', 'main'))

    def test_launcher_checks_canonical_authority_for_resolve_and_build_targets(self):
        request = ('{"build_only":true,"verify_target":"linux","ios_action":"skip",'
                   '"source_sha":"' + 'a' * 40 + '","build_number":"42"}')
        for target in ('resolve', 'linux'):
            with self.subTest(target=target):
                env = {
                    **self.environment(),
                    'GITHUB_WORKFLOW_REF': 'attacker/other.yml@refs/heads/main',
                    'RELEASE_TARGET': target,
                    'RELEASE_REQUEST': request,
                    'SOURCE_BRANCH': 'main',
                    'SOURCE_ENTRYPOINT': 'scripts/release/entrypoint.py',
                    'SOURCE_DEPLOY_KEY': 'synthetic',
                    'SOURCE_KNOWN_HOSTS': 'synthetic',
                    'RUNNER_TEMP': '/tmp',
                }
                with contextlib.redirect_stdout(io.StringIO()), \
                        mock.patch.dict(os.environ, env, clear=True), \
                        mock.patch.object(bootstrap.tempfile, 'TemporaryDirectory',
                                          side_effect=AssertionError('checkout boundary was reached')):
                    self.assertEqual(bootstrap.main(), 1)

    def test_all_platform_jobs_precede_validation_and_only_encrypted_logs_are_public(self):
        workflow = (Path(__file__).resolve().parent.parent / '.github/workflows/release.yml').read_text()
        integration = workflow.split('\n  integration:\n', 1)[1].split('\n  verify:\n', 1)[0]
        validation = workflow.split('\n  validate:\n', 1)[1].split('\n  downloads:\n', 1)[0]
        self.assertIn('needs: [resolve, integration]', validation)
        self.assertIn('environment: downloads', integration)
        self.assertIn("github.repository == 'omnisolo-llc/omniterm-release'", integration)
        for platform in ('linux', 'macos', 'windows', 'android', 'ios', 'web'):
            self.assertIn('- platform: ' + platform + '\n', integration)
        self.assertIn('STORAGE_CONFIG:', integration)
        paths = [line.strip() for line in integration.splitlines() if line.strip().startswith('path:')]
        self.assertEqual(paths, ['path: ${{ runner.temp }}/encrypted-diagnostics/diagnostics.sealed'])

    def test_exact_candidate_installation_precedes_apple_delivery_and_publication(self):
        workflow = (Path(__file__).resolve().parent.parent / '.github/workflows/release.yml').read_text()
        installation = workflow.split('\n  installation:\n', 1)[1].split('\n  ios_delivery:\n', 1)[0]
        apple = workflow.split('\n  ios_delivery:\n', 1)[1].split('\n  publish:\n', 1)[0]
        publish = workflow.split('\n  publish:\n', 1)[1]
        self.assertIn('needs: [resolve, validate, downloads, ios, package_signatures, apple_testflight]', installation)
        self.assertIn('needs: [resolve, validate, ios, apple_testflight, installation]', apple)
        self.assertIn('RELEASE_TARGET: ios-deliver', apple)
        self.assertIn("needs.installation.result == 'success'", publish)
        self.assertIn("needs.ios_delivery.result == 'success'", publish)
        self.assertIn('environment: downloads', installation)
        self.assertIn("github.repository == 'omnisolo-llc/omniterm-release'", installation)
        self.assertIn('runs-on: [self-hosted,', installation)
        for platform in ('linux', 'macos', 'windows', 'android', 'ios', 'web'):
            self.assertIn('- platform: ' + platform + '\n', installation)
            self.assertIn('OMNI_INSTALL_CONFIG_' + platform.upper(), installation)
        self.assertIn('GH_TOKEN:', installation)
        self.assertIn('STORAGE_CONFIG:', installation)
        self.assertNotIn('SIGNING_CONFIG:', installation)
        self.assertIn('SIGNING_CONFIG:', apple)
        paths = [line.strip() for line in installation.splitlines() if line.strip().startswith('path:')]
        self.assertEqual(paths, ['path: ${{ runner.temp }}/encrypted-diagnostics/diagnostics.sealed'])

    def test_launcher_accepts_reviewed_installation_and_delivery_targets(self):
        for target in ('integration', 'installation', 'package-signatures', 'apple-testflight',
                       'validate', 'ios-deliver', 'ios-submit', 'publication-prepare',
                       'external-tests', 'publish'):
            with self.subTest(target=target):
                env = {**test_bootstrap.BootstrapTests().env(), **self.environment()}
                request = json.dumps({'build_only': False, 'ios_action': 'submit',
                                      'source_sha': 'a' * 40, 'version': '0.1.0',
                                      'build_number': '42'})
                env.update(RELEASE_TARGET=target, SOURCE_BRANCH='main',
                           SOURCE_ENTRYPOINT='scripts/release/entrypoint.py',
                           RELEASE_REQUEST=request)
                bootstrap.validate_target_request(target, json.loads(request))
                bootstrap.validate(env)
                bootstrap.validate_integration_authority(env, 'ql-owo-lp/omniterm', 'main')

    def test_new_evidence_and_publication_targets_reject_foreign_workflow_before_checkout(self):
        request = ('{"build_only":true,"ios_action":"skip",'
                   '"source_sha":"' + 'a' * 40 + '","build_number":"42"}')
        for target in ('external-tests', 'ios-submit', 'publication-prepare', 'publish'):
            with self.subTest(target=target):
                env = {
                    **self.environment(),
                    'GITHUB_WORKFLOW_REF': 'attacker/other.yml@refs/heads/main',
                    'RELEASE_TARGET': target,
                    'RELEASE_REQUEST': request,
                    'SOURCE_BRANCH': 'main',
                    'SOURCE_ENTRYPOINT': 'scripts/release/entrypoint.py',
                    'SOURCE_DEPLOY_KEY': 'synthetic',
                    'SOURCE_KNOWN_HOSTS': 'synthetic',
                    'RUNNER_TEMP': '/tmp',
                }
                output = io.StringIO()
                with mock.patch.dict(os.environ, env, clear=True), \
                        mock.patch.object(bootstrap.tempfile, 'TemporaryDirectory',
                                          side_effect=AssertionError('checkout boundary was reached')), \
                        contextlib.redirect_stdout(output):
                    self.assertEqual(bootstrap.main(), 1)
                self.assertIn('configuration is incomplete or invalid', output.getvalue())


if __name__ == '__main__':
    unittest.main()
