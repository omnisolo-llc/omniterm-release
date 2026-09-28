"""Public workflow contracts; require no private source, signing keys or network."""
import json
from pathlib import Path
import re
import unittest

import test_bootstrap as bootstrap_tests

ROOT = Path(__file__).resolve().parent.parent
TARGETS = {'linux', 'windows', 'macos', 'android', 'web', 'ios'}


class ReleaseMatrixTests(unittest.TestCase):
    def test_launcher_accepts_every_platform_and_refuses_docker(self):
        for target in TARGETS:
            with self.subTest(target=target):
                env = bootstrap_tests.BootstrapTests().env()
                env['RELEASE_TARGET'] = target
                bootstrap_tests.b.validate(env)
                request = {'build_only': True, 'verify_target': target, 'ios_action': 'skip',
                           'source_sha': 'a' * 40, 'build_number': '42'}
                bootstrap_tests.b.validate_target_request(target, request)
                self.assertNotIn('verify_target', json.loads(bootstrap_tests.b.task_request(json.dumps(request))))
        env['RELEASE_TARGET'] = 'docker'
        with self.assertRaises(ValueError):
            bootstrap_tests.b.validate(env)

    def test_stage_allowlist_and_request_modes_match_the_public_protocol(self):
        self.assertEqual(set(bootstrap_tests.b.BUILD_TARGETS), TARGETS)
        self.assertEqual(set(bootstrap_tests.b.RELEASE_TARGETS), TARGETS | {
            'resolve', 'integration', 'installation', 'package-signatures', 'apple-testflight',
            'validate', 'ios-deliver', 'ios-submit', 'publication-prepare', 'external-tests',
            'publish',
        })
        release = {'build_only': False, 'ios_action': 'submit'}
        for target in bootstrap_tests.b.RELEASE_TARGETS - bootstrap_tests.b.BUILD_TARGETS - {'resolve'}:
            with self.subTest(target=target), self.assertRaises(ValueError):
                bootstrap_tests.b.validate_target_request(target, {'build_only': True, 'ios_action': 'skip'})
        bootstrap_tests.b.validate_target_request('ios-submit', release)
        with self.assertRaises(ValueError):
            bootstrap_tests.b.validate_target_request('ios-submit', {**release, 'ios_action': 'upload'})
        with self.assertRaises(ValueError):
            bootstrap_tests.b.validate_target_request('windows', {
                'build_only': True, 'verify_target': 'linux', 'ios_action': 'skip',
            })

    def test_private_source_jobs_are_limited_to_the_canonical_builder(self):
        text = (ROOT / '.github/workflows/release.yml').read_text()
        names = ('resolve', 'integration', 'verify', 'validate', 'downloads', 'ios',
                 'package_signatures', 'apple_testflight', 'installation', 'ios_delivery',
                 'publication_prepare', 'external_tests', 'apple_submission', 'publish')
        for name in names:
            job = re.split(r'\n  [a-z_]+:\n', text.split(f'\n  {name}:\n', 1)[1], maxsplit=1)[0]
            with self.subTest(job=name):
                self.assertIn("github.repository == 'omnisolo-llc/omniterm-release'", job)

    def test_source_jobs_forward_the_optional_pinned_submodule_credential(self):
        text = (ROOT / '.github/workflows/release.yml').read_text()
        resolve = re.split(r'\n  [a-z_]+:\n', text.split('\n  resolve:\n', 1)[1], maxsplit=1)[0]
        self.assertIn('SOURCE_SUBMODULE_TOKEN: ${{ secrets.SOURCE_SUBMODULE_TOKEN }}', resolve)
        names = ('integration', 'verify', 'validate', 'downloads', 'ios',
                 'package_signatures', 'apple_testflight', 'installation', 'ios_delivery',
                 'publication_prepare', 'external_tests', 'apple_submission', 'publish')
        for name in names:
            job = re.split(r'\n  [a-z_]+:\n', text.split(f'\n  {name}:\n', 1)[1], maxsplit=1)[0]
            with self.subTest(job=name):
                self.assertIn('SOURCE_SUBMODULE_TOKEN: ${{ secrets.SOURCE_SUBMODULE_TOKEN }}', job)

    def test_external_acceptance_targets_have_exact_protected_provider_routes(self):
        text = (ROOT / '.github/workflows/release.yml').read_text()
        external = text.split('\n  external_tests:\n', 1)[1].split('\n  apple_submission:\n', 1)[0]
        for platform in ('linux', 'macos', 'windows'):
            with self.subTest(platform=platform):
                self.assertIn('- platform: ' + platform + '\n', external)
                self.assertIn('config_variable: OMNI_EXTERNAL_CONFIG_' + platform.upper(), external)
                self.assertIn('runner: omniterm-release-' + platform, external)
        self.assertIn('environment: external-tests', external)
        self.assertIn('id-token: write', external)
        self.assertIn('OMNI_EXTERNAL_CONFIG_FILE: ${{ vars[matrix.config_variable] }}', external)
        self.assertIn('RELEASE_METADATA_READ_TOKEN', external)
        self.assertNotIn('SIGNING_CONFIG:', external)
        self.assertIn("grep -Eq '^ID=\"?ubuntu\"?$' /etc/os-release", external)
        self.assertIn('Microsoft\\.NETCore\\.App 8\\.', external)

    def test_build_only_matrix_contains_all_six_platforms(self):
        text = (ROOT / '.github/workflows/release.yml').read_text()
        matrix = json.loads(re.search(r"fromJSON\('(\[[^']+\])'\)", text).group(1))
        self.assertEqual(set(matrix), TARGETS)
        self.assertIn("'macos-26'", text)
        self.assertNotIn('macos-15', text)
        self.assertNotIn('macos-latest', text)
        self.assertIn("'windows-2022'", text)
        self.assertNotIn('windows-2025', text)
        self.assertIn('max-parallel: 6', text)
        self.assertNotIn('uses: docker/', text)
        self.assertNotIn('publish_docker', text)

    def test_self_signed_preview_is_windows_build_only(self):
        request = {'build_only': True, 'verify_target': 'windows',
                   'preview_windows_self_sign': True,
                   'source_sha': 'a' * 40, 'build_number': '42'}
        forwarded = json.loads(bootstrap_tests.b.task_request(json.dumps(request)))
        self.assertNotIn('preview_windows_self_sign', forwarded)
        for change in ({'build_only': False}, {'verify_target': 'all'},
                       {'verify_target': 'linux'}, {'preview_windows_self_sign': 'true'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                bootstrap_tests.b.task_request(json.dumps({**request, **change}))

        workflow = (ROOT / '.github/workflows/release.yml').read_text()
        verify = workflow.split('\n  verify:\n', 1)[1].split('\n  validate:\n', 1)[0]
        self.assertIn("inputs.preview_windows_self_sign && matrix.target == 'windows'", verify)
        self.assertIn('WINDOWS_PREVIEW_OUTPUT_DIR:', verify)
        self.assertIn('name: windows-self-signed-preview-', verify)
        self.assertIn('retention-days: 7', verify)
        self.assertIn('if-no-files-found: error', verify)
        publish = workflow.split('\n  publish:\n', 1)[1]
        self.assertNotIn('preview_windows_self_sign', publish)

    def test_release_defaults_are_resolved_once_for_all_jobs(self):
        text = (ROOT / '.github/workflows/release.yml').read_text()
        source_input = text.split('      source_sha:\n', 1)[1].split('      version:\n', 1)[0]
        self.assertIn('required: false', source_input)
        self.assertIn("default: ''", source_input)
        self.assertIn("default: '0.1.0'", text)
        build_input = text.split('      build_number:\n', 1)[1].split('      ios_action:\n', 1)[0]
        self.assertIn('required: true', build_input)
        self.assertIn('1-9999', build_input)
        self.assertNotIn('default:', build_input)
        self.assertIn('needs.resolve.outputs.source_sha', text)
        self.assertIn('needs.resolve.outputs.version', text)
        self.assertIn('needs.resolve.outputs.build_number', text)
        self.assertIn('needs.resolve.outputs.workflow_id', text)
        self.assertEqual(text.count('needs: resolve'), 2)
        self.assertEqual(text.count('needs: [resolve, validate]'), 2)
        publish = text.split('\n  publish:\n', 1)[1]
        self.assertIn('needs: [resolve, validate, downloads, ios, package_signatures, apple_testflight, installation, ios_delivery, publication_prepare, external_tests, apple_submission]', publish)
        self.assertIn("needs.external_tests.result == 'success'", publish)
        self.assertIn('needs.resolve.result == \'success\'', publish)

    def test_download_jobs_cover_every_public_distribution_group(self):
        text = (ROOT / '.github/workflows/release.yml').read_text().split('\n  downloads:\n')[1].split('\n  ios:\n')[0]
        self.assertEqual(set(re.findall(r'- target: ([a-z]+)', text)), TARGETS - {'ios'})
        self.assertIn("matrix.target == 'windows' && (secrets.WINDOWS_SIGNING_CONFIG || secrets.SIGNING_CONFIG)", text)
        self.assertIn("matrix.target == 'macos' && (secrets.MACOS_SIGNING_CONFIG || secrets.SIGNING_CONFIG)", text)
        self.assertIn("matrix.target == 'android' && (secrets.ANDROID_SIGNING_CONFIG || secrets.SIGNING_CONFIG)", text)
        self.assertIn('id-token: write', text)
        self.assertIn('74bd7d27e6ce1051409c38d9b46bc8df0400ecd643d51ffbf2ac00869061e40b', text)
        self.assertIn('OMNI_WINDOWS_ARTIFACT_SIGNING_DLIB=$dlib', text)

    def test_full_release_must_explicitly_request_apple_delivery(self):
        for action in ('skip', '', None):
            with self.subTest(action=action), self.assertRaises(ValueError):
                bootstrap_tests.b.task_request(json.dumps({'build_only': False, 'ios_action': action,
                                                            'source_sha': 'a' * 40,
                                                            'version': '0.1.0', 'build_number': '1'}))
        for action in ('upload', 'submit'):
            with bootstrap_tests.approved_launcher() as reviewed:
                reviewed.task_request(json.dumps({'build_only': False, 'ios_action': action,
                                                            'source_sha': 'a' * 40,
                                                            'version': '0.1.0', 'build_number': '1'}))

    def test_automatic_public_release_is_submit_only_and_boolean(self):
        with self.assertRaises(ValueError):
            bootstrap_tests.b.task_request(json.dumps({
                'build_only': True, 'ios_action': 'skip', 'automatic_release': True,
                'source_sha': 'a' * 40, 'build_number': '42',
            }))
        with self.assertRaises(ValueError):
            bootstrap_tests.b.task_request(json.dumps({
                'build_only': False, 'ios_action': 'upload', 'automatic_release': True,
                'source_sha': 'a' * 40, 'version': '0.1.0', 'build_number': '42',
            }))
        with self.assertRaises(ValueError):
            bootstrap_tests.b.task_request(json.dumps({
                'build_only': False, 'ios_action': 'submit', 'automatic_release': 'true',
                'source_sha': 'a' * 40, 'version': '0.1.0', 'build_number': '42',
            }))
        with bootstrap_tests.approved_launcher() as reviewed:
            request = reviewed.task_request(json.dumps({
                'build_only': False, 'ios_action': 'submit', 'automatic_release': True,
                'source_sha': 'a' * 40, 'version': '0.1.0', 'build_number': '42',
            }))
            self.assertIs(json.loads(request)['automatic_release'], True)

    def test_optional_self_hosted_relay_kit_selection_is_boolean(self):
        base = {'build_only': True, 'ios_action': 'skip',
                'source_sha': 'a' * 40, 'build_number': '42'}
        for value in ('false', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bootstrap_tests.b.task_request(json.dumps({**base, 'include_selfhost': value}))
        request = json.loads(bootstrap_tests.b.task_request(
            json.dumps({**base, 'include_selfhost': False})))
        self.assertIs(request['include_selfhost'], False)

    def test_apple_job_can_write_its_delivery_receipt(self):
        text = (ROOT / '.github/workflows/release.yml').read_text().split('\n  ios:\n')[1].split('\n  publish:\n')[0]
        self.assertIn('contents: write', text)
        self.assertIn('environment: app-store', text)

    def test_every_job_retains_only_encrypted_diagnostics(self):
        text = (ROOT / '.github/workflows/release.yml').read_text()
        for name in ('integration', 'verify', 'validate', 'downloads', 'ios', 'package_signatures', 'apple_testflight', 'installation', 'ios_delivery', 'publication_prepare', 'external_tests', 'apple_submission', 'publish'):
            job = re.split(r'\n  [a-z_]+:\n', text.split(f'\n  {name}:\n')[1], maxsplit=1)[0]
            with self.subTest(job=name):
                self.assertIn('DIAGNOSTICS_PUBLIC_KEY:', job)
                self.assertIn('path: ${{ runner.temp }}/encrypted-diagnostics/diagnostics.sealed', job)
                self.assertIn('retention-days: 1', job)
                self.assertNotIn('path: ${{ github.workspace }}', job)

    def test_signing_configuration_is_target_scoped_and_denied_to_unrelated_jobs(self):
        text = (ROOT / '.github/workflows/release.yml').read_text()
        downloads = text.split('\n  downloads:\n')[1].split('\n  ios:\n')[0]
        self.assertIn('id-token: write', downloads)
        self.assertIn("matrix.target == 'windows' && (secrets.WINDOWS_SIGNING_CONFIG || secrets.SIGNING_CONFIG)", downloads)
        self.assertIn("matrix.target == 'macos' && (secrets.MACOS_SIGNING_CONFIG || secrets.SIGNING_CONFIG)", downloads)
        self.assertIn("matrix.target == 'android' && (secrets.ANDROID_SIGNING_CONFIG || secrets.SIGNING_CONFIG)", downloads)
        # Unrelated download targets (linux, web) evaluate to empty string
        self.assertTrue(downloads.strip().endswith("|| ''") or "|| ''" in downloads)

        # Build-only verification, validate, and publish jobs must not receive SIGNING_CONFIG
        for job_name in ('integration', 'installation', 'verify', 'validate', 'publish'):
            job_text = re.split(r'\n  [a-z_]+:\n', text.split(f'\n  {job_name}:\n')[1], maxsplit=1)[0]
            self.assertNotIn('SIGNING_CONFIG:', job_text, f'{job_name} must not expose signing secrets')
            self.assertNotIn('id-token: write', job_text, f'{job_name} must not grant OIDC token permissions')

        # iOS delivery receives target-scoped iOS signing configuration in app-store environment
        ios_text = text.split('\n  ios:\n')[1].split('\n  publish:\n')[0]
        self.assertIn('SIGNING_CONFIG: ${{ secrets.IOS_SIGNING_CONFIG || secrets.SIGNING_CONFIG }}', ios_text)
