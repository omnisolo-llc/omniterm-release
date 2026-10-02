"""Public workflow contracts; require no private source, signing keys or network."""
import json
from pathlib import Path
import re
import unittest

import test_bootstrap as bootstrap_tests

ROOT = Path(__file__).resolve().parent.parent
TARGETS = {'linux', 'windows', 'macos', 'android', 'web', 'ios'}


def workflow_jobs(workflow):
    jobs_section = workflow.split('\njobs:\n', 1)[1]
    starts = list(re.finditer(r'^  ([a-z_]+):\n', jobs_section, re.MULTILINE))
    jobs = {}
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(jobs_section)
        jobs[match.group(1)] = jobs_section[match.end():end]
    return jobs


def job_needs(job):
    match = re.search(r'^    needs: (.+)$', job, re.MULTILINE)
    if match is None:
        return set()
    value = match.group(1).strip()
    if value.startswith('[') and value.endswith(']'):
        return {item.strip() for item in value[1:-1].split(',') if item.strip()}
    return {value}


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
            'validate', 'ios-deliver', 'ios-submit', 'publication-prepare', 'vpn-container', 'external-tests',
            'managed-rtc-provider', 'external-windows-signing', 'publish',
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

    def test_full_release_jobs_transport_evidence_through_private_storage(self):
        workflow = (ROOT / '.github/workflows/release.yml').read_text()
        jobs = ('integration', 'validate', 'downloads', 'windows_download', 'ios',
                'package_signatures', 'apple_testflight', 'installation', 'ios_delivery',
                'vpn_container', 'managed_rtc_provider', 'publication_prepare', 'external_tests', 'external_windows_signing',
                'apple_submission', 'publish')
        for name in jobs:
            job = re.split(r'\n  [a-z_]+:\n', workflow.split(f'\n  {name}:\n', 1)[1], maxsplit=1)[0]
            with self.subTest(job=name):
                self.assertIn('STORAGE_CONFIG: ${{ secrets.STORAGE_CONFIG }}', job)

        for name in ('resolve', 'verify'):
            job = re.split(r'\n  [a-z_]+:\n', workflow.split(f'\n  {name}:\n', 1)[1], maxsplit=1)[0]
            with self.subTest(job=name):
                self.assertNotIn('STORAGE_CONFIG:', job)

    def test_source_release_stage_contract_has_a_complete_same_run_job_graph(self):
        workflow = (ROOT / '.github/workflows/release.yml').read_text()
        jobs = workflow_jobs(workflow)
        target_jobs = {
            'resolve': 'resolve',
            'integration': 'integration',
            'validate': 'validate',
            'downloads': '${{ matrix.target }}',
            'windows_download': 'windows',
            'ios': 'ios',
            'package_signatures': 'package-signatures',
            'apple_testflight': 'apple-testflight',
            'installation': 'installation',
            'vpn_container': 'vpn-container',
            'managed_rtc_provider': 'managed-rtc-provider',
            'ios_delivery': 'ios-deliver',
            'publication_prepare': 'publication-prepare',
            'external_tests': 'external-tests',
            'external_windows_signing': 'external-windows-signing',
            'apple_submission': 'ios-submit',
            'publish': 'publish',
        }
        for name, target in target_jobs.items():
            with self.subTest(job=name):
                self.assertIn(name, jobs)
                self.assertIn('RELEASE_TARGET: ' + target, jobs[name])
                self.assertIn('RELEASE_REQUEST: ${{ toJSON(inputs) }}', jobs[name])
                if name != 'resolve':
                    for output in ('source_sha', 'version', 'build_number'):
                        self.assertIn(
                            f'RESOLVED_{output.upper()}: ${{{{ needs.resolve.outputs.{output} }}}}',
                            jobs[name],
                        )

        self.assertEqual(
            set(re.findall(r'^[ \t]*- target: ([a-z]+)$', jobs['downloads'], re.MULTILINE)),
            {'linux', 'macos', 'android', 'web'},
        )
        expected_platforms = {'linux', 'macos', 'windows', 'android', 'ios', 'web'}
        for name in ('integration', 'installation'):
            with self.subTest(platform_matrix=name):
                self.assertEqual(
                    set(re.findall(r'^[ \t]*- platform: ([a-z]+)$', jobs[name], re.MULTILINE)),
                    expected_platforms,
                )

        workflow_stage_targets = (
            set(target_jobs.values()) - {'resolve', '${{ matrix.target }}'} - TARGETS
        )
        self.assertEqual(
            workflow_stage_targets,
            set(bootstrap_tests.b.RELEASE_TARGETS)
            - set(bootstrap_tests.b.BUILD_TARGETS) - {'resolve'},
        )

        required_edges = {
            'validate': {'resolve', 'integration'},
            'downloads': {'resolve', 'validate'},
            'windows_download': {'resolve', 'validate'},
            'ios': {'resolve', 'validate'},
            'package_signatures': {'resolve', 'validate', 'downloads', 'windows_download'},
            'apple_testflight': {'resolve', 'validate', 'ios'},
            'installation': {'resolve', 'validate', 'downloads', 'windows_download', 'ios',
                             'package_signatures', 'apple_testflight'},
            'vpn_container': {'resolve', 'validate', 'downloads', 'windows_download', 'ios',
                              'package_signatures', 'apple_testflight', 'installation'},
            'managed_rtc_provider': {'resolve', 'publication_prepare'},
            'ios_delivery': {'resolve', 'validate', 'ios', 'apple_testflight', 'installation',
                             'vpn_container'},
            'publication_prepare': {
                'resolve', 'validate', 'downloads', 'windows_download', 'ios', 'package_signatures',
                'apple_testflight', 'installation', 'vpn_container', 'ios_delivery',
            },
            'external_tests': {'resolve', 'publication_prepare', 'managed_rtc_provider'},
            'external_windows_signing': {'resolve', 'publication_prepare', 'external_tests'},
            'apple_submission': {'resolve', 'validate', 'ios_delivery', 'publication_prepare',
                                'vpn_container', 'external_tests', 'external_windows_signing'},
            'publish': {'resolve', 'validate', 'downloads', 'windows_download', 'ios',
                        'package_signatures', 'apple_testflight', 'installation', 'ios_delivery',
                        'vpn_container', 'publication_prepare', 'managed_rtc_provider',
                        'external_tests', 'external_windows_signing',
                        'apple_submission'},
        }
        for name, required in required_edges.items():
            with self.subTest(dependencies=name):
                self.assertTrue(required <= job_needs(jobs[name]))

        stages = {'integration', 'installation', 'vpn_container', 'managed_rtc_provider', 'package_signatures',
                  'apple_testflight', 'ios_delivery'}
        for name in stages:
            with self.subTest(private_evidence=name):
                self.assertIn('STORAGE_CONFIG: ${{ secrets.STORAGE_CONFIG }}', jobs[name])
                diagnostic_uploads = re.findall(
                    r'^[ \t]+path: (.+)$', jobs[name], re.MULTILINE)
                self.assertEqual(diagnostic_uploads,
                                 ['${{ runner.temp }}/encrypted-diagnostics/diagnostics.sealed'])

        publish = jobs['publish']
        for dependency in required_edges['publish'] - {'apple_submission'}:
            with self.subTest(publication_gate=dependency):
                self.assertIn(f"needs.{dependency}.result == 'success'", publish)
        apple_submission = jobs['apple_submission']
        self.assertIn("needs.ios_delivery.result == 'success'", apple_submission)
        self.assertIn('RELEASE_IOS_DELIVERY_RESULT: ${{ needs.ios_delivery.result }}',
                      apple_submission)
        self.assertIn('needs.vpn_container.result == \'success\'', publish)
        self.assertIn('needs.managed_rtc_provider.result == \'success\'', publish)
        self.assertIn("inputs.ios_action == 'upload' || needs.apple_submission.result == 'success'", publish)

    def test_container_vpn_evidence_is_two_run_bound_kernel_jobs_required_before_delivery(self):
        workflow = (ROOT / '.github/workflows/release.yml').read_text()
        jobs = workflow_jobs(workflow)
        job = jobs['vpn_container']
        rows = dict(re.findall(
            r'^          - kernel_state: (present|absent)\n            runner: ([a-z0-9-]+)$',
            job, re.MULTILINE))
        self.assertEqual(rows, {
            'present': 'omniterm-release-vpn-kmod-present',
            'absent': 'omniterm-release-vpn-kmod-absent',
        })
        self.assertIn("environment: vpn-container", job)
        self.assertIn("permissions:\n      contents: read\n      id-token: write", job)
        self.assertIn('OMNI_VPN_E2E_KERNEL_MODULE_STATE: ${{ matrix.kernel_state }}', job)
        self.assertIn('OMNITERM_VPN_E2E_PROFILE_FILE: ${{ vars.OMNITERM_VPN_E2E_PROFILE_FILE }}', job)
        for role in ('CLIENT_BASE', 'GATEWAY', 'RELAY', 'RECEIVER', 'DNS'):
            with self.subTest(image=role):
                name = 'OMNITERM_VPN_E2E_' + role + '_IMAGE'
                self.assertIn(name + ': ${{ vars.' + name + ' }}', job)
        self.assertIn('docker info >/dev/null 2>&1', job)
        self.assertIn('present) test -d /sys/module/wireguard', job)
        self.assertIn('absent) test ! -d /sys/module/wireguard', job)
        self.assertNotIn('OMNITERM_VPN_E2E_PROFILE_FILE', jobs['external_tests'])
        for name in ('ios_delivery', 'publication_prepare', 'apple_submission', 'publish'):
            with self.subTest(gate=name):
                self.assertIn('vpn_container', jobs[name])
        self.assertIn("needs.vpn_container.result == 'success'", jobs['publish'])

    def test_managed_rtc_provider_evidence_is_protected_and_private_before_external_tests(self):
        workflow = (ROOT / '.github/workflows/release.yml').read_text()
        jobs = workflow_jobs(workflow)
        producer = jobs['managed_rtc_provider']
        self.assertEqual(job_needs(producer), {'resolve', 'publication_prepare'})
        self.assertIn("environment: managed-rtc-provider", producer)
        self.assertIn("runs-on: [self-hosted, 'omniterm-release-managed-rtc']", producer)
        self.assertIn("permissions:\n      contents: read\n      id-token: write", producer)
        self.assertIn('RELEASE_TARGET: managed-rtc-provider', producer)
        self.assertIn('MANAGED_RTC_PROVIDER_ENVIRONMENT_FILE: ${{ vars.MANAGED_RTC_PROVIDER_ENVIRONMENT_FILE }}',
                      producer)
        self.assertIn('MANAGED_RTC_IDENTITY_FIXTURE_FILE: ${{ vars.MANAGED_RTC_IDENTITY_FIXTURE_FILE }}',
                      producer)
        self.assertIn('RESOLVED_SOURCE_SHA: ${{ needs.resolve.outputs.source_sha }}', producer)
        launcher = Path(bootstrap_tests.b.__file__).read_text()
        self.assertIn("env['PUBLIC_BUILDER_SHA'] = required(env, 'GITHUB_SHA')", launcher)
        self.assertIn('task_env = private_task_environment(env, submodule_token)', launcher)
        task_env = bootstrap_tests.b.private_task_environment({'PUBLIC_BUILDER_SHA': 'a' * 40}, None)
        self.assertEqual(task_env['PUBLIC_BUILDER_SHA'], 'a' * 40)
        self.assertIn('STORAGE_CONFIG: ${{ secrets.STORAGE_CONFIG }}', producer)
        self.assertNotIn('GH_TOKEN:', producer)
        self.assertNotIn('SIGNING_CONFIG:', producer)
        uploads = re.findall(r'^[ \t]+path: (.+)$', producer, re.MULTILINE)
        self.assertEqual(uploads, ['${{ runner.temp }}/encrypted-diagnostics/diagnostics.sealed'])
        self.assertTrue({'managed_rtc_provider'} <= job_needs(jobs['external_tests']))
        self.assertTrue({'managed_rtc_provider'} <= job_needs(jobs['publish']))
        self.assertNotIn('MANAGED_RTC_ACCEPTANCE_EVIDENCE:', jobs['external_tests'])

    def test_installed_vpn_attestations_use_platform_scoped_protected_environments(self):
        text = (ROOT / '.github/workflows/release.yml').read_text()
        jobs = workflow_jobs(text)
        installation = jobs['installation']
        self.assertIn('environment: ${{ matrix.attestation_environment }}', installation)
        platform_environments = {}
        rows = re.findall(
            r'(?ms)^          - platform: ([a-z]+)\n(.*?)(?=^          - platform: |\Z)',
            installation,
        )
        for platform, row in rows:
            match = re.search(r'^            attestation_environment: ([a-z-]+)$',
                              row, re.MULTILINE)
            self.assertIsNotNone(match, platform)
            platform_environments[platform] = match.group(1)
        self.assertEqual(platform_environments, {
            'linux': 'vpn-installation-linux',
            'macos': 'vpn-installation-macos',
            'windows': 'vpn-installation-windows',
            'android': 'vpn-installation-android',
            'ios': 'vpn-installation-ios',
            'web': 'downloads',
        })
        self.assertIn('id-token: write', installation)
        self.assertIn('environment: downloads', jobs['windows_download'])

    def test_release_tasks_receive_the_pinned_vpn_provider_public_key(self):
        workflow = (ROOT / '.github/workflows/release.yml').read_text()
        jobs = workflow_jobs(workflow)
        key = 'OMNITERM_VPN_PROVIDER_PUBLIC_KEY'
        expected = '${{ vars.' + key + ' }}'
        self.assertNotIn(key, workflow.split('\njobs:\n', 1)[0])
        self.assertNotIn(key, jobs['resolve'])
        for name, job in jobs.items():
            if name == 'resolve':
                continue
            with self.subTest(job=name):
                targets = re.findall(r'^\s+RELEASE_TARGET: .+$', job, re.MULTILINE)
                values = re.findall(r'^\s+' + key + r': (.+)$', job, re.MULTILINE)
                self.assertEqual(len(targets), 1)
                self.assertEqual(values, [expected])
                if name == 'verify':
                    self.assertNotIn('BUILD_CONFIG:', job)
                else:
                    self.assertIn('BUILD_CONFIG: ${{ secrets.BUILD_CONFIG }}', job)

    def test_release_job_permissions_are_exact_and_minimal(self):
        workflow = (ROOT / '.github/workflows/release.yml').read_text()
        jobs = workflow_jobs(workflow)
        expected = {
            'resolve': {'contents': 'read'},
            'integration': {'contents': 'read'},
            'verify': {'contents': 'read'},
            'validate': {'contents': 'write'},
            'downloads': {'contents': 'write'},
            'windows_download': {'contents': 'write', 'id-token': 'write'},
            'ios': {'contents': 'write'},
            'package_signatures': {'contents': 'read'},
            'apple_testflight': {'contents': 'write'},
            'installation': {'contents': 'read', 'id-token': 'write'},
            'vpn_container': {'contents': 'read', 'id-token': 'write'},
            'managed_rtc_provider': {'contents': 'read', 'id-token': 'write'},
            'ios_delivery': {'contents': 'write'},
            'publication_prepare': {'contents': 'read'},
            'external_tests': {'contents': 'read', 'id-token': 'write'},
            'external_windows_signing': {'contents': 'read', 'id-token': 'write'},
            'apple_submission': {'contents': 'read'},
            'publish': {'contents': 'write'},
        }
        self.assertEqual(set(jobs), set(expected))
        for name, job in jobs.items():
            with self.subTest(job=name):
                block = re.search(
                    r'^    permissions:\n((?:^      [a-z-]+: [a-z]+\n)+)',
                    job, re.MULTILINE)
                self.assertIsNotNone(block)
                actual = dict(re.findall(r'^      ([a-z-]+): ([a-z]+)$',
                                         block.group(1), re.MULTILINE))
                self.assertEqual(actual, expected[name])

    def test_private_source_jobs_are_limited_to_the_canonical_builder(self):
        text = (ROOT / '.github/workflows/release.yml').read_text()
        names = ('resolve', 'integration', 'verify', 'validate', 'downloads', 'ios',
                 'package_signatures', 'apple_testflight', 'installation', 'ios_delivery',
                 'vpn_container', 'managed_rtc_provider', 'windows_download', 'publication_prepare',
                 'external_tests', 'external_windows_signing',
                 'apple_submission', 'publish')
        for name in names:
            job = re.split(r'\n  [a-z_]+:\n', text.split(f'\n  {name}:\n', 1)[1], maxsplit=1)[0]
            with self.subTest(job=name):
                self.assertIn("github.repository == 'omnisolo-llc/omniterm-release'", job)

    def test_only_private_source_tasks_receive_the_optional_submodule_credential(self):
        text = (ROOT / '.github/workflows/release.yml').read_text()
        resolve = re.split(r'\n  [a-z_]+:\n', text.split('\n  resolve:\n', 1)[1], maxsplit=1)[0]
        self.assertNotIn('SOURCE_SUBMODULE_TOKEN:', resolve)
        names = ('integration', 'verify', 'validate', 'downloads', 'ios',
                 'package_signatures', 'apple_testflight', 'installation', 'ios_delivery',
                 'vpn_container', 'managed_rtc_provider', 'windows_download', 'publication_prepare',
                 'external_tests', 'external_windows_signing',
                 'apple_submission', 'publish')
        for name in names:
            job = re.split(r'\n  [a-z_]+:\n', text.split(f'\n  {name}:\n', 1)[1], maxsplit=1)[0]
            with self.subTest(job=name):
                self.assertIn('SOURCE_SUBMODULE_TOKEN: ${{ secrets.SOURCE_SUBMODULE_TOKEN }}', job)

    def test_external_acceptance_targets_have_exact_protected_provider_routes(self):
        text = (ROOT / '.github/workflows/release.yml').read_text()
        external = text.split('\n  external_tests:\n', 1)[1].split('\n  external_windows_signing:\n', 1)[0]
        for platform in ('linux', 'macos', 'windows'):
            with self.subTest(platform=platform):
                self.assertIn('- platform: ' + platform + '\n', external)
                self.assertIn('config_variable: OMNI_EXTERNAL_CONFIG_' + platform.upper(), external)
                self.assertIn('candidate_guard_variable: OMNI_EXTERNAL_CANDIDATE_GUARD_CONFIG_' + platform.upper(), external)
                self.assertIn('runner: omniterm-release-' + platform, external)
        self.assertIn('environment: external-tests', external)
        self.assertIn('managed_rtc_provider', job_needs(workflow_jobs(text)['external_tests']))
        self.assertIn('permissions:\n      contents: read\n      id-token: write', external)
        self.assertIn('OMNI_EXTERNAL_CONFIG_FILE: ${{ vars[matrix.config_variable] }}', external)
        self.assertIn('OMNI_EXTERNAL_CANDIDATE_GUARD_CONFIG_FILE: ${{ vars[matrix.candidate_guard_variable] }}', external)
        self.assertIn('RELEASE_METADATA_READ_TOKEN', external)
        self.assertNotIn('SIGNING_CONFIG:', external)
        self.assertIn("grep -Eq '^ID=\"?ubuntu\"?$' /etc/os-release", external)
        self.assertNotIn('OMNITERM_VPN_E2E_PROFILE_FILE', external)

        signing = text.split('\n  external_windows_signing:\n', 1)[1].split('\n  apple_submission:\n', 1)[0]
        self.assertIn('RELEASE_TARGET: external-windows-signing', signing)
        self.assertIn('OMNI_EXTERNAL_PLATFORM: windows-signing', signing)
        self.assertIn('OMNI_EXTERNAL_CANDIDATE_GUARD_CONFIG_FILE: ${{ vars.OMNI_EXTERNAL_CANDIDATE_GUARD_CONFIG_FILE }}', signing)
        self.assertIn('SIGNING_CONFIG: ${{ secrets.WINDOWS_SIGNING_CONFIG }}', signing)
        self.assertNotIn('OMNI_EXTERNAL_CONFIG_FILE:', signing)
        self.assertIn('id-token: write', signing)
        self.assertIn('environment: external-windows-signing', signing)
        self.assertIn('Microsoft\\.NETCore\\.App 8\\.', signing)

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
        self.assertEqual(text.count('needs: [resolve, validate]'), 3)
        publish = text.split('\n  publish:\n', 1)[1]
        self.assertIn('needs: [resolve, validate, downloads, windows_download, ios, package_signatures, apple_testflight, installation, vpn_container, ios_delivery, publication_prepare, managed_rtc_provider, external_tests, external_windows_signing, apple_submission]', publish)
        self.assertIn("needs.vpn_container.result == 'success'", publish)
        self.assertIn("needs.external_tests.result == 'success'", publish)
        self.assertIn("needs.external_windows_signing.result == 'success'", publish)
        self.assertIn('needs.resolve.result == \'success\'', publish)

    def test_download_jobs_cover_every_public_distribution_group(self):
        workflow = (ROOT / '.github/workflows/release.yml').read_text()
        text = workflow.split('\n  downloads:\n')[1].split('\n  windows_download:\n')[0]
        self.assertEqual(set(re.findall(r'- target: ([a-z]+)', text)), {'linux', 'macos', 'android', 'web'})
        self.assertNotIn('id-token: write', text)
        self.assertIn("matrix.target == 'macos' && secrets.MACOS_SIGNING_CONFIG", text)
        self.assertIn("matrix.target == 'android' && secrets.ANDROID_SIGNING_CONFIG", text)
        self.assertNotIn('WINDOWS_SIGNING_CONFIG', text)

        windows = workflow.split('\n  windows_download:\n')[1].split('\n  ios:\n')[0]
        self.assertIn('RELEASE_TARGET: windows', windows)
        self.assertIn('contents: write', windows)
        self.assertIn('id-token: write', windows)
        self.assertIn('SIGNING_CONFIG: ${{ secrets.WINDOWS_SIGNING_CONFIG }}', windows)
        self.assertIn('74bd7d27e6ce1051409c38d9b46bc8df0400ecd643d51ffbf2ac00869061e40b', windows)
        self.assertIn('OMNI_WINDOWS_ARTIFACT_SIGNING_DLIB=$dlib', windows)

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
        for name in ('integration', 'verify', 'validate', 'downloads', 'windows_download', 'ios', 'package_signatures', 'apple_testflight', 'installation', 'vpn_container', 'managed_rtc_provider', 'ios_delivery', 'publication_prepare', 'external_tests', 'external_windows_signing', 'apple_submission', 'publish'):
            job = re.split(r'\n  [a-z_]+:\n', text.split(f'\n  {name}:\n')[1], maxsplit=1)[0]
            with self.subTest(job=name):
                self.assertIn('DIAGNOSTICS_PUBLIC_KEY:', job)
                self.assertIn('path: ${{ runner.temp }}/encrypted-diagnostics/diagnostics.sealed', job)
                self.assertIn('retention-days: 1', job)
                self.assertNotIn('path: ${{ github.workspace }}', job)

    def test_signing_configuration_is_target_scoped_and_denied_to_unrelated_jobs(self):
        text = (ROOT / '.github/workflows/release.yml').read_text()
        jobs = workflow_jobs(text)
        self.assertNotIn('secrets.SIGNING_CONFIG', text)
        downloads = text.split('\n  downloads:\n')[1].split('\n  windows_download:\n')[0]
        self.assertNotIn('id-token: write', downloads)
        self.assertNotIn('WINDOWS_SIGNING_CONFIG', downloads)
        self.assertIn("matrix.target == 'macos' && secrets.MACOS_SIGNING_CONFIG", downloads)
        self.assertIn("matrix.target == 'android' && secrets.ANDROID_SIGNING_CONFIG", downloads)
        # Unsigned download targets (linux, web) evaluate to an empty signing config.
        self.assertTrue(downloads.strip().endswith("|| ''") or "|| ''" in downloads)

        windows = text.split('\n  windows_download:\n')[1].split('\n  ios:\n')[0]
        self.assertIn('id-token: write', windows)
        self.assertIn('SIGNING_CONFIG: ${{ secrets.WINDOWS_SIGNING_CONFIG }}', windows)

        # Build-only and publication jobs do not need OIDC; evidence producers do.
        for job_name in ('integration', 'installation', 'verify', 'validate', 'publish',
                         'vpn_container', 'managed_rtc_provider', 'external_tests'):
            job_text = re.split(r'\n  [a-z_]+:\n', text.split(f'\n  {job_name}:\n')[1], maxsplit=1)[0]
            self.assertNotIn('SIGNING_CONFIG:', job_text, f'{job_name} must not expose signing secrets')
            if job_name not in ('installation', 'vpn_container', 'managed_rtc_provider',
                                'external_tests'):
                self.assertNotIn('id-token: write', job_text, f'{job_name} must not grant OIDC token permissions')

        installation = text.split('\n  installation:\n')[1].split('\n  ios_delivery:\n')[0]
        self.assertIn('permissions:\n      contents: read\n      id-token: write', installation)
        self.assertIn('OMNI_VPN_INSTALLATION_PROFILE_FILE: ${{ vars[matrix.vpn_profile_variable] || \'\' }}', installation)
        self.assertIn('OMNI_VPN_RECEIVER_TRUSTED_JWKS_JSON: ${{ vars.OMNI_VPN_RECEIVER_TRUSTED_JWKS_JSON }}', installation)
        self.assertIn('OMNI_VPN_TRUSTED_GATEWAY_POLICIES_JSON: ${{ vars.OMNI_VPN_TRUSTED_GATEWAY_POLICIES_JSON }}', installation)
        for platform in ('linux', 'macos', 'windows', 'android', 'ios'):
            self.assertIn('vpn_profile_variable: OMNI_VPN_INSTALLATION_PROFILE_' + platform.upper(), installation)
        for name in ('integration', 'downloads', 'windows_download', 'ios', 'external_tests', 'publish'):
            with self.subTest(vpn_trust_scope=name):
                self.assertNotIn('OMNI_VPN_RECEIVER_TRUSTED_JWKS_JSON:', jobs[name])
                self.assertNotIn('OMNI_VPN_TRUSTED_GATEWAY_POLICIES_JSON:', jobs[name])

        external_signing = text.split('\n  external_windows_signing:\n')[1].split('\n  apple_submission:\n')[0]
        self.assertIn('id-token: write', external_signing)
        self.assertIn('SIGNING_CONFIG: ${{ secrets.WINDOWS_SIGNING_CONFIG }}', external_signing)
        self.assertNotIn('WINDOWS_SIGNING_CONFIG ||', external_signing)

        # iOS delivery receives target-scoped iOS signing configuration in app-store environment
        ios_text = text.split('\n  ios:\n')[1].split('\n  publish:\n')[0]
        self.assertIn('SIGNING_CONFIG: ${{ secrets.IOS_SIGNING_CONFIG }}', ios_text)

    def test_release_tools_are_pinned_and_publication_preparation_is_read_only(self):
        text = (ROOT / '.github/workflows/release.yml').read_text()
        actions = re.findall(r'^\s*(?:-\s*)?uses:\s*([^\s#]+)', text, re.MULTILINE)
        self.assertTrue(actions)
        for action in actions:
            with self.subTest(action=action):
                self.assertRegex(action, r'^[^@]+@[0-9a-f]{40}$')

        self.assertEqual(set(re.findall(r"node-version: '([^']+)'", text)), {'24.21.0'})
        self.assertEqual(set(re.findall(r"java-version: '([^']+)'", text)), {'17.0.20+101'})
        preparation = text.split('\n  publication_prepare:\n', 1)[1].split('\n  external_tests:\n', 1)[0]
        self.assertIn('permissions:\n      contents: read', preparation)
        self.assertNotIn('contents: write', preparation)

    def test_linux_provider_and_oidc_fixtures_are_separate_and_required(self):
        documentation = (ROOT / 'README.md').read_text()
        for value in (
                'OMNI_EXTERNAL_CONFIG_LINUX.fixtures.live_share_provider_environment_file',
                'OMNI_EXTERNAL_CONFIG_LINUX.fixtures.production_oidc_environment_file',
                'production_oidc_environment_file',
                'live_share_provider_environment_file', 'mode `0600`',
                'LIVE_SHARE_SFU_ENABLED', 'CLOUDFLARE_SFU_APP_ID',
                'CLOUDFLARE_SFU_APP_SECRET', 'LIVE_SHARE_MOQ_ENABLED',
                'CLOUDFLARE_MOQ_ACCOUNT_ID', 'CLOUDFLARE_MOQ_API_TOKEN',
                'exactly these six string fields', 'No extra fields are accepted',
                'isolated Playwright owner environment', '`require_all`',
                'Missing or invalid provider configuration prevents the Linux evidence',
                'Apple submission and public promotion remain blocked'):
            with self.subTest(value=value):
                self.assertIn(value, documentation)
        for value in (
                'OMNI_E2E_EDGE_URL', 'OMNI_E2E_IDENTITY_URL',
                'OMNI_E2E_IDENTITY_WORKLOAD_TOKEN', 'OMNI_E2E_OIDC_ISSUER_URL',
                'OMNI_E2E_OIDC_AUTHORIZATION_ENDPOINT', 'OMNI_E2E_OIDC_REDIRECT_URI',
                'OMNI_E2E_OIDC_CLIENT_ID', 'OMNI_E2E_OIDC_EXPECTED_SUBJECT_ID',
                'OMNI_E2E_OIDC_TENANT_ID', 'OMNI_E2E_OIDC_STORAGE_STATE',
                'exactly these ten string fields',
                'separate owner-only JSON settings file',
                'owner-only Playwright storage-state JSON file',
                'state object may contain only `cookies` and `origins`',
                'separate from the SFU/MoQ provider file',
                'distinct guarded OIDC owner within the', 'existing Linux `external_tests` job',
                '181 exact case receipts'):
            with self.subTest(value=value):
                self.assertIn(value, documentation)
        linux_fixtures = documentation.split('Linux requires\n', 1)[1].split(
            'Set `OMNI_EXTERNAL_CONFIG_LINUX.fixtures.live_share_provider_environment_file`', 1)[0]
        self.assertIn('`live_share_provider_environment_file`', linux_fixtures)
        self.assertIn('`production_oidc_environment_file`', linux_fixtures)

        workflow = (ROOT / '.github/workflows/release.yml').read_text()
        self.assertIn('\n  external_tests:\n', workflow)
        self.assertNotIn('\n  production_oidc:\n', workflow)
        external = workflow.split('\n  external_tests:\n', 1)[1].split('\n  external_windows_signing:\n', 1)[0]
        linux = external.split('          - platform: linux\n', 1)[1].split('          - platform: macos\n', 1)[0]
        self.assertIn('config_variable: OMNI_EXTERNAL_CONFIG_LINUX', linux)
        self.assertIn('OMNI_EXTERNAL_CONFIG_FILE: ${{ vars[matrix.config_variable] }}', external)
        for name in ('LIVE_SHARE_SFU_ENABLED', 'CLOUDFLARE_SFU_APP_ID',
                     'CLOUDFLARE_SFU_APP_SECRET', 'LIVE_SHARE_MOQ_ENABLED',
                     'CLOUDFLARE_MOQ_ACCOUNT_ID', 'CLOUDFLARE_MOQ_API_TOKEN',
                     'OMNI_E2E_EDGE_URL', 'OMNI_E2E_IDENTITY_URL',
                     'OMNI_E2E_IDENTITY_WORKLOAD_TOKEN', 'OMNI_E2E_OIDC_ISSUER_URL',
                     'OMNI_E2E_OIDC_AUTHORIZATION_ENDPOINT', 'OMNI_E2E_OIDC_REDIRECT_URI',
                     'OMNI_E2E_OIDC_CLIENT_ID', 'OMNI_E2E_OIDC_EXPECTED_SUBJECT_ID',
                     'OMNI_E2E_OIDC_TENANT_ID', 'OMNI_E2E_OIDC_STORAGE_STATE'):
            self.assertNotIn(name + ':', external)

        self.assertIn('needs: [resolve, publication_prepare, managed_rtc_provider]', external)

    def test_full_release_requires_the_self_hosted_relay_kit(self):
        base = {'build_only': False, 'ios_action': 'upload', 'source_sha': 'a' * 40,
                'version': '0.1.0', 'build_number': '42'}
        with bootstrap_tests.approved_launcher() as reviewed:
            with self.assertRaisesRegex(ValueError, 'Full releases require the self-hosted kit'):
                reviewed.task_request(json.dumps({**base, 'include_selfhost': False}))
            request = json.loads(reviewed.task_request(
                json.dumps({**base, 'include_selfhost': True})))
            self.assertIs(request['include_selfhost'], True)

        text = (ROOT / '.github/workflows/release.yml').read_text()
        option = text.split('      include_selfhost:\n', 1)[1].split('\n      ', 1)[0]
        self.assertIn('only build-only validation may omit it', option)

    def test_public_relay_locked_suites_run_in_contract_and_release_gates(self):
        contracts = (ROOT / '.github/workflows/contracts.yml').read_text()
        moq = contracts.split('\n  first-party-moq:\n', 1)[1].split('\n  native-relay-contract:\n', 1)[0]
        self.assertIn('npm ci --prefix relay/moq', moq)
        self.assertIn('npm test --prefix relay/moq', moq)
        native = contracts.split('\n  native-relay-contract:\n', 1)[1]
        self.assertIn('npm ci --prefix relay/native', native)
        self.assertIn('node --test --test-concurrency=1 ci/test_native_relay_contract.mjs', native)

        release = (ROOT / '.github/workflows/release.yml').read_text()
        validate = release.split('\n  validate:\n', 1)[1].split('\n  downloads:\n', 1)[0]
        self.assertIn("node-version: '24.21.0'", validate)
        for command in ('npm ci --prefix relay/native',
                        'node --test --test-concurrency=1 ci/test_native_relay_contract.mjs',
                        'npm ci --prefix relay/moq',
                        'npm test --prefix relay/moq'):
            with self.subTest(command=command):
                self.assertIn(command, validate)

    def test_moq_native_cutoff_patch_is_pinned_and_rebuilt_on_install(self):
        moq_root = ROOT / 'relay/moq'
        package = json.loads((moq_root / 'package.json').read_text())
        self.assertEqual(package['scripts']['postinstall'],
                         'node scripts/build-patched-webtransport.mjs')

        builder = (moq_root / 'scripts/build-patched-webtransport.mjs').read_text()
        self.assertIn('212ef743f0cf52adb234d60d5b41c48257e967b4', builder)
        self.assertIn('80bf9559d3a4c08dde4b85abc46d190a88ffef64', builder)
        self.assertIn('npm_tarball_integrity', builder)
        self.assertIn('patch_sha256', builder)
        self.assertIn("'--unidiff-zero'", builder)
        self.assertIn('package-lock.json', builder)
        self.assertIn("join(packageRoot, 'node_modules', '.bin')", builder)
        self.assertIn('binary_sha256', builder)
        self.assertIn("npm_config_build_from_source: 'true'", builder)
        self.assertNotIn("'build.js', 'install'", builder)
        self.assertIn('cmake-js/bin/cmake-js', builder)
        self.assertIn('nativeBuildPath(adapterRoot)', builder)
        self.assertIn('requireLoadedAddon', builder)
        self.assertIn('webtransport-client-close.patch', builder)
        self.assertIn('client_sha256=', builder)
        patch = (moq_root / 'patches/quiche-server-close-ack.patch').read_text()
        self.assertEqual(patch.count('+    MaybeNotifyClose();'), 2)
        native_patch = (moq_root / 'patches/webtransport-server-connection-close.patch').read_text()
        self.assertIn('Http3ServerSession::OnConnectionClosed', native_patch)
        self.assertIn('Http3ServerSession::~Http3ServerSession()', native_patch)
        self.assertIn('session_closed_ = false', native_patch)

        contracts = (ROOT / '.github/workflows/contracts.yml').read_text()
        moq_job = contracts.split('\n  first-party-moq:\n', 1)[1].split(
            '\n  native-relay-contract:\n', 1)[0]
        self.assertIn('build-essential cmake libicu-dev', moq_job)
        release = (ROOT / '.github/workflows/release.yml').read_text()
        validate = release.split('\n  validate:\n', 1)[1].split('\n  downloads:\n', 1)[0]
        self.assertIn('build-essential cmake libicu-dev', validate)
