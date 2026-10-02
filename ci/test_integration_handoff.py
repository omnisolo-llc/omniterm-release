"""Authorization/workflow boundaries only; no substitute hardware execution."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import unittest
from unittest import mock

import bootstrap
import test_bootstrap


class IntegrationHandoffTests(unittest.TestCase):
    def job(self, workflow, name):
        return re.split(r'\n  [a-z_]+:\n', workflow.split(f'\n  {name}:\n', 1)[1], maxsplit=1)[0]

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
                    'BUILD_CONFIG': '{"OMNI_ENABLE_VPN":"true"}',
                    'STORAGE_CONFIG': '{}',
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
        installation = workflow.split('\n  installation:\n', 1)[1].split('\n  vpn_container:\n', 1)[0]
        apple = workflow.split('\n  ios_delivery:\n', 1)[1].split('\n  publish:\n', 1)[0]
        publish = workflow.split('\n  publish:\n', 1)[1]
        self.assertIn('needs: [resolve, validate, downloads, windows_download, ios, package_signatures, apple_testflight]', installation)
        self.assertIn('needs: [resolve, validate, ios, apple_testflight, installation, vpn_container]', apple)
        self.assertIn('RELEASE_TARGET: ios-deliver', apple)
        self.assertIn("needs.installation.result == 'success'", publish)
        self.assertIn("needs.ios_delivery.result == 'success'", publish)
        self.assertIn("needs.vpn_container.result == 'success'", publish)
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

    def test_protected_source_stages_bind_attempt_and_transport_private_evidence(self):
        workflow = (Path(__file__).resolve().parent.parent / '.github/workflows/release.yml').read_text()
        stages = {
            'integration': ('integration', 'downloads', 'needs: resolve'),
            'installation': ('installation', 'downloads',
                             'needs: [resolve, validate, downloads, windows_download, ios, package_signatures, apple_testflight]'),
            'vpn_container': ('vpn-container', 'vpn-container',
                              'needs: [resolve, validate, downloads, windows_download, ios, package_signatures, apple_testflight, installation]'),
            'managed_rtc_provider': ('managed-rtc-provider', 'managed-rtc-provider',
                                     'needs: [resolve, publication_prepare]'),
            'package_signatures': ('package-signatures', 'package-signing',
                                   'needs: [resolve, validate, downloads, windows_download]'),
            'apple_testflight': ('apple-testflight', 'app-store',
                                 'needs: [resolve, validate, ios]'),
            'ios_delivery': ('ios-deliver', 'app-store',
                             'needs: [resolve, validate, ios, apple_testflight, installation, vpn_container]'),
        }
        identity = (
            'RELEASE_REQUEST: ${{ toJSON(inputs) }}',
            'RESOLVED_SOURCE_SHA: ${{ needs.resolve.outputs.source_sha }}',
            'RESOLVED_VERSION: ${{ needs.resolve.outputs.version }}',
            'RESOLVED_BUILD_NUMBER: ${{ needs.resolve.outputs.build_number }}',
            'SOURCE_REPOSITORY: ${{ secrets.SOURCE_REPOSITORY }}',
            'SOURCE_BRANCH: ${{ secrets.SOURCE_BRANCH }}',
            'SOURCE_ENTRYPOINT: ${{ secrets.SOURCE_ENTRYPOINT }}',
            'SOURCE_DEPLOY_KEY: ${{ secrets.SOURCE_DEPLOY_KEY }}',
            'SOURCE_KNOWN_HOSTS: ${{ secrets.SOURCE_KNOWN_HOSTS }}',
            'SOURCE_SUBMODULE_TOKEN: ${{ secrets.SOURCE_SUBMODULE_TOKEN }}',
            'STORAGE_CONFIG: ${{ secrets.STORAGE_CONFIG }}',
            'DIAGNOSTICS_PUBLIC_KEY: ${{ secrets.DIAGNOSTICS_PUBLIC_KEY }}',
        )
        for name, (target, environment, needs) in stages.items():
            with self.subTest(stage=name):
                job = self.job(workflow, name)
                self.assertIn(needs, job)
                self.assertIn("github.repository == 'omnisolo-llc/omniterm-release'", job)
                self.assertIn('environment: ' + environment, job)
                self.assertIn('RELEASE_TARGET: ' + target, job)
                target_step = job.split('RELEASE_TARGET: ' + target, 1)[1].split('\n      - name:', 1)[0]
                for field in identity:
                    self.assertIn(field, target_step)

        integration = self.job(workflow, 'integration')
        installation = self.job(workflow, 'installation')
        self.assertIn('OMNI_INTEGRATION_PLATFORM: ${{ matrix.platform }}', integration)
        self.assertIn('OMNI_INTEGRATION_DEVICE: ${{ vars[matrix.device_variable] }}', integration)
        self.assertNotIn('GH_TOKEN:', integration)
        self.assertIn('OMNI_INSTALL_CONFIG: ${{ vars[matrix.config_variable] }}', installation)
        self.assertIn('GH_TOKEN: ${{ github.token }}', installation)
        for name in ('integration', 'installation'):
            job = self.job(workflow, name)
            self.assertNotIn('SIGNING_CONFIG:', job)
            if name == 'integration':
                self.assertNotIn('id-token: write', job)
            else:
                self.assertIn('id-token: write', job)
                self.assertIn('OMNI_VPN_INSTALLATION_PROFILE_FILE:', job)
        package_signatures = self.job(workflow, 'package_signatures')
        self.assertIn('contents: read', package_signatures)
        self.assertIn('GH_TOKEN: ${{ github.token }}', package_signatures)
        self.assertIn('SIGNING_CONFIG: ${{ secrets.PACKAGE_SIGNING_CONFIG }}', package_signatures)
        for name in ('apple_testflight', 'ios_delivery'):
            job = self.job(workflow, name)
            self.assertIn('GH_TOKEN: ${{ github.token }}', job)
            self.assertIn('SIGNING_CONFIG: ${{ secrets.IOS_SIGNING_CONFIG }}', job)
        managed_rtc = self.job(workflow, 'managed_rtc_provider')
        self.assertIn('MANAGED_RTC_PROVIDER_ENVIRONMENT_FILE:', managed_rtc)
        self.assertIn('MANAGED_RTC_IDENTITY_FIXTURE_FILE:', managed_rtc)
        self.assertNotIn('GH_TOKEN:', managed_rtc)
        self.assertNotIn('SIGNING_CONFIG:', managed_rtc)

    def test_linux_external_tests_refresh_and_verify_the_archive_fixture_before_capture(self):
        workflow = (Path(__file__).resolve().parent.parent / '.github/workflows/release.yml').read_text()
        linux_external = self.job(workflow, 'external_tests')
        metadata = linux_external.split(
            '      - name: Verify genuine Ubuntu archive metadata for Linux external tests\n', 1)[1]
        metadata = metadata.split(
            '      - name: Execute exact external cases against real candidate and providers\n', 1)[0]
        self.assertIn("if: matrix.platform == 'linux'", linux_external)
        self.assertIn('sudo apt-get update', metadata)
        self.assertIn('ubuntu-archive-keyring', metadata)
        self.assertIn('dpkg-query -S "$keyring"', metadata)
        self.assertIn('dpkg --verify ubuntu-keyring', metadata)
        self.assertIn('gpgv --status-fd 1 --keyring "$keyring" "$metadata"', metadata)
        self.assertIn('F6ECB3762474EDA9D21B7022871920D1991BC93C', metadata)

    def test_validation_uses_the_protocol_fixture_python_runtime(self):
        workflow = (Path(__file__).resolve().parent.parent / '.github/workflows/release.yml').read_text()
        validate = self.job(workflow, 'validate')
        setup = validate.index('actions/setup-python@e797f83bcb11b83ae66e0230d6156d7c80228e7c')
        setup_step = validate[setup:].split('\n      - ', 1)[0]
        self.assertIn("python-version: '3.14'", setup_step)
        validation = validate.index('RELEASE_TARGET: validate')
        self.assertLess(setup, validation)

    def test_launcher_accepts_reviewed_installation_and_delivery_targets(self):
        for target in ('integration', 'installation', 'vpn-container', 'managed-rtc-provider', 'package-signatures', 'apple-testflight',
                       'validate', 'ios-deliver', 'ios-submit', 'publication-prepare',
                       'external-tests', 'external-windows-signing', 'publish'):
            with self.subTest(target=target):
                env = {**test_bootstrap.BootstrapTests().env(), **self.environment()}
                request = json.dumps({'build_only': False, 'ios_action': 'submit',
                                      'source_sha': 'a' * 40, 'version': '0.1.0',
                                      'build_number': '42'})
                env.update(RELEASE_TARGET=target, SOURCE_BRANCH='main',
                           SOURCE_ENTRYPOINT='scripts/release/entrypoint.py',
                           RELEASE_REQUEST=request,
                           BUILD_CONFIG='{"OMNI_ENABLE_VPN":"true"}')
                bootstrap.validate_target_request(target, json.loads(request))
                bootstrap.validate(env)
                bootstrap.validate_integration_authority(env, 'ql-owo-lp/omniterm', 'main')

    def test_new_evidence_and_publication_targets_reject_foreign_workflow_before_checkout(self):
        source_sha = 'a' * 40
        for target in ('external-tests', 'external-windows-signing', 'vpn-container',
                       'managed-rtc-provider', 'ios-submit',
                       'publication-prepare', 'publish'):
            with self.subTest(target=target):
                request = json.dumps({'build_only': False,
                                      'ios_action': 'submit' if target == 'ios-submit' else 'upload',
                                      'source_sha': source_sha, 'version': '0.1.0',
                                      'build_number': '42'})
                env = {
                    **self.environment(),
                    'GITHUB_WORKFLOW_REF': 'attacker/other.yml@refs/heads/main',
                    'RELEASE_TARGET': target,
                    'RELEASE_REQUEST': request,
                    'SOURCE_BRANCH': 'main',
                    'SOURCE_ENTRYPOINT': 'scripts/release/entrypoint.py',
                    'SOURCE_DEPLOY_KEY': 'synthetic',
                    'SOURCE_KNOWN_HOSTS': 'synthetic',
                    'BUILD_CONFIG': '{"OMNI_ENABLE_VPN":"true"}',
                    'STORAGE_CONFIG': '{}',
                    'RESOLVED_SOURCE_SHA': source_sha,
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
