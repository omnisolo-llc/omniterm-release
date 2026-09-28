import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
import unittest

spec = importlib.util.spec_from_file_location('bootstrap', Path(__file__).with_name('bootstrap.py'))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


@contextlib.contextmanager
def approved_launcher():
    # Load unchanged production code beside an actual parser-input approval file.
    # This exercises file validation without replacing any function or process.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'bootstrap.py'
        shutil.copyfile(Path(b.__file__), path)
        path.with_name('approved_release_source.json').write_text(json.dumps({'source_sha': 'a' * 40}))
        spec = importlib.util.spec_from_file_location('reviewed_launcher_contract', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module


class BootstrapTests(unittest.TestCase):
    def env(self):
        return {'SOURCE_REPOSITORY': 'example/source', 'SOURCE_BRANCH': 'main',
                'SOURCE_ENTRYPOINT': 'scripts/task.py', 'SOURCE_DEPLOY_KEY': 'synthetic',
                'SOURCE_KNOWN_HOSTS': 'synthetic', 'BUILD_CONFIG': '{}', 'STORAGE_CONFIG': '{}',
                'RELEASE_REQUEST': json.dumps({'source_sha': 'a' * 40, 'version': '0.1.0',
                                                'build_number': '42', 'build_only': True,
                                                'ios_action': 'skip'}), 'RELEASE_TARGET': 'linux'}

    def test_valid_config(self):
        self.assertEqual(b.validate(self.env())[-1], 'a' * 40)

    def test_validation_target_is_supported_without_signing_credentials(self):
        env = self.env()
        env['RELEASE_TARGET'] = 'validate'
        self.assertNotIn('SIGNING_CONFIG', env)
        self.assertEqual(b.validate(env)[-1], 'a' * 40)

    def test_reject_paths_refs_and_repositories(self):
        cases = {'SOURCE_REPOSITORY': ['https://example/repo', 'a/b/c', 'a/b\n'],
                 'SOURCE_BRANCH': ['-main', 'main\n', '../x', 'x/.bad', 'x.lock'],
                 'SOURCE_ENTRYPOINT': ['../task.py', '/task.py', 'task.py', 'scripts/.hidden.py'],
                 'RELEASE_TARGET': ['other', 'linux; echo unsafe']}
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    env = self.env(); env[key] = value
                    with self.assertRaises(ValueError): b.validate(env)

    def test_missing_secrets_rejected(self):
        for key in set(self.env()) - {'BUILD_CONFIG', 'STORAGE_CONFIG'}:
            env = self.env(); env.pop(key)
            with self.subTest(key=key), self.assertRaises((ValueError, KeyError)): b.validate(env)

    def test_optional_task_configuration_does_not_block_checkout(self):
        env = self.env()
        del env['BUILD_CONFIG']; del env['STORAGE_CONFIG']
        self.assertEqual(b.validate(env)[-1], 'a' * 40)

    def test_no_non_manual_execution(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith(('GITHUB_', 'SOURCE_', 'RELEASE_'))}
        result = subprocess.run([sys.executable, str(Path(b.__file__))], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn('reviewed manual workflow', result.stdout)

    def test_invalid_config_does_not_leak(self):
        env = self.env(); env.update(GITHUB_ACTIONS='true', GITHUB_EVENT_NAME='workflow_dispatch',
                                     GITHUB_REF='refs/heads/main', SOURCE_REPOSITORY='confidential!')
        result = subprocess.run([sys.executable, str(Path(b.__file__))], env={**os.environ, **env},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('confidential', result.stdout + result.stderr)


class WorkflowOrderingTests(unittest.TestCase):
    def test_publication_requires_every_download_and_successful_apple_delivery(self):
        workflow = (Path(__file__).resolve().parent.parent / '.github/workflows/release.yml').read_text()
        publish = workflow.split('\n  publish:\n', 1)[1]
        self.assertIn('needs: [resolve, validate, downloads, ios, package_signatures, apple_testflight, installation, ios_delivery, publication_prepare, external_tests, apple_submission]', publish)
        # No partial release can be published after any platform fails or is skipped.
        expected = ("if: ${{ !cancelled() && github.repository == 'omnisolo-llc/omniterm-release' "
                    "&& github.ref == 'refs/heads/main' "
                    "&& !inputs.build_only && needs.resolve.result == 'success' && needs.validate.result == 'success' && needs.downloads.result == 'success' && needs.ios.result == 'success' && needs.package_signatures.result == 'success' && needs.apple_testflight.result == 'success' && needs.installation.result == 'success' && needs.ios_delivery.result == 'success' && needs.publication_prepare.result == 'success' && needs.external_tests.result == 'success' && (inputs.ios_action == 'upload' || needs.apple_submission.result == 'success') }}")
        self.assertIn(expected, publish)
        self.assertIn("needs.ios.result == 'success'", publish)
        self.assertIn('environment: public-release', publish)

    def test_public_submission_waits_for_accepted_candidate(self):
        workflow = (Path(__file__).resolve().parent.parent / '.github/workflows/release.yml').read_text()
        apple = workflow.split('\n  apple_submission:\n', 1)[1].split('\n  publish:\n', 1)[0]
        self.assertIn('needs: [resolve, validate, ios_delivery, publication_prepare, external_tests]', apple)
        self.assertIn("inputs.ios_action == 'submit'", apple)
        self.assertIn("needs.external_tests.result == 'success'", apple)
        self.assertIn('RELEASE_TARGET: ios-submit', apple)
        self.assertIn('environment: app-store', apple)


class VerificationTests(unittest.TestCase):
    def test_target_selection_is_generic_and_cannot_make_partial_release(self):
        raw = b.task_request(json.dumps({'build_only': True, 'verify_target': 'windows',
                                         'source_sha': 'a' * 40, 'build_number': '42'}))
        self.assertNotIn('verify_target', json.loads(raw))
        for value in ({'build_only': False, 'verify_target': 'windows'}, {'build_only': True, 'verify_target': '../x'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                b.task_request(json.dumps(value))

    def test_build_verification_has_no_signing_or_storage_credentials(self):
        workflow = (Path(__file__).resolve().parent.parent / '.github/workflows/release.yml').read_text()
        verify = workflow.split('\n  verify:\n', 1)[1].split('\n  validate:\n', 1)[0]
        self.assertIn('contents: read', verify)
        for forbidden in ('SIGNING_CONFIG', 'STORAGE_CONFIG', 'GH_TOKEN'):
            self.assertNotIn(forbidden, verify)
        self.assertIn('inputs.build_only', verify)
        self.assertIn('encrypted-diagnostics/diagnostics.sealed', verify)
        self.assertIn('retention-days: 1', verify)
        self.assertNotIn('path: ${{ github.workspace }}', verify)

    def test_phase_reporting_cannot_echo_private_details(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'status.json'
            for value in ({'stage': 'confidential/path'}, {'stage': 'application-build', 'error': 'private output'}):
                path.write_text(json.dumps(value))
                output = io.StringIO()
                with contextlib.redirect_stdout(output): b.report_phase(path)
                self.assertNotIn('confidential', output.getvalue())
                self.assertNotIn('private output', output.getvalue())


class ReleaseInputTests(unittest.TestCase):
    def test_empty_source_sha_uses_resolved_revision_and_version_default(self):
        now = datetime(2026, 9, 23, 14, 7, tzinfo=timezone.utc)
        raw = json.dumps({'build_only': True, 'ios_action': 'skip', 'build_number': '42'})
        resolved = {'source_sha': 'a' * 40}

        request = json.loads(b.task_request(raw, resolved=resolved))

        self.assertEqual(request['source_sha'], 'a' * 40)
        self.assertEqual(request['version'], '0.1.0')
        self.assertEqual(request['build_number'], '42')
        self.assertEqual(b.workflow_identifier(now), '202609231407')

    def test_resolver_may_leave_source_sha_empty_until_branch_tip_is_fetched(self):
        raw = json.dumps({'build_only': True, 'ios_action': 'skip', 'build_number': '9'})

        request = json.loads(b.task_request(raw, allow_missing_source_sha=True))

        self.assertEqual(request['source_sha'], '')
        self.assertEqual(request['version'], '0.1.0')
        self.assertEqual(request['build_number'], '9')

    def test_explicit_source_sha_wins_over_branch_tip(self):
        requested = 'A' * 40
        tip = 'b' * 40

        self.assertEqual(b.select_source_sha(requested, tip), requested.lower())
        self.assertEqual(b.select_source_sha('', tip), tip)

    def test_every_app_build_requires_a_numeric_build_number_from_1_to_9999(self):
        for build_only, ios_action in ((True, 'skip'), (False, 'upload')):
            base = {'build_only': build_only, 'ios_action': ios_action,
                    'source_sha': 'a' * 40, 'version': '0.1.0'}
            with approved_launcher() as reviewed:
                for value in ('', '202609231407', '10000', '0'):
                    with self.subTest(build_only=build_only, value=value), self.assertRaises(ValueError):
                        reviewed.task_request(json.dumps({**base, 'build_number': value}))

                request = json.loads(reviewed.task_request(json.dumps({**base, 'build_number': '9999'})))
                self.assertEqual(request['build_number'], '9999')

    def test_full_release_requires_explicit_reviewed_source_in_every_job(self):
        base = {'build_only': False, 'ios_action': 'upload', 'build_number': '42'}
        with approved_launcher() as reviewed:
            for source in ('', 'b' * 40):
                with self.subTest(source=source), self.assertRaises(ValueError):
                    reviewed.task_request(json.dumps({**base, 'source_sha': source}),
                                   allow_missing_source_sha=True)
            approved = {**base, 'source_sha': 'A' * 40}
            self.assertEqual(json.loads(reviewed.task_request(json.dumps(approved)))['source_sha'],
                             'a' * 40)
            with self.assertRaises(ValueError):
                reviewed.task_request(json.dumps(approved), resolved={'source_sha': 'b' * 40})

    def test_reviewed_source_file_is_exact_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            approval = Path(temp) / 'approval.json'
            for value in ({'source_sha': ''}, {'source_sha': 'A' * 40},
                          {'source_sha': 'a' * 40, 'other': True}):
                approval.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    b.approved_release_sha(approval)
            approval.write_text(json.dumps({'source_sha': 'a' * 40}))
            self.assertEqual(b.approved_release_sha(approval), 'a' * 40)
            linked = Path(temp) / 'linked.json'
            linked.symlink_to(approval)
            with self.assertRaises(ValueError):
                b.approved_release_sha(linked)


class SSHLauncherTests(unittest.TestCase):
    def test_windows_git_layout_calculation_and_actual_host_ssh(self):
        root = Path(tempfile.gettempdir()) / 'Git'
        expected = root / 'usr/bin/ssh.exe'
        for layout in ('cmd/git.exe', 'bin/git.exe', 'mingw64/bin/git.exe'):
            self.assertIn(expected, b.git_ssh_candidates(root / layout))
        actual = b.ssh_executable()
        version = subprocess.run([actual, '-V'], capture_output=True, text=True, timeout=30, check=True)
        self.assertIn('OpenSSH', version.stdout + version.stderr)

    def test_checkout_error_classification_never_echoes_private_log(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'log'
            path.write_text('secret/path: Host key verification failed; private-token')
            self.assertEqual(b.checkout_failure(path), 'host-key-verification')
            path.write_text('private exception details')
            self.assertEqual(b.checkout_failure(path), 'unclassified')


class BootstrapExecutionTests(unittest.TestCase):
    def repository(self, root):
        source = root / 'source'
        source.mkdir()
        env = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
               'GIT_AUTHOR_NAME': 'Launcher contract', 'GIT_AUTHOR_EMAIL': 'launcher@example.invalid',
               'GIT_COMMITTER_NAME': 'Launcher contract', 'GIT_COMMITTER_EMAIL': 'launcher@example.invalid'}
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=source, env=env, stderr=subprocess.PIPE, text=True).strip()
        git('init', '-q')
        task = source / 'scripts/task.py'
        task.parent.mkdir()
        task.write_text("import os,sys; print('private actual child output'); sys.exit(int(os.environ.get('CHILD_EXIT_CODE','0')))\n")
        git('add', '.')
        git('commit', '-qm', 'Actual reviewed source')
        sha = git('rev-parse', 'HEAD')
        git('update-ref', 'refs/remotes/origin/reviewed', sha)
        return source, env, git, sha

    def test_resolved_outputs_come_from_actual_git_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, env, git, sha = self.repository(root)
            tip = b.capture(['git', 'rev-parse', 'refs/remotes/origin/reviewed'], source, env)
            selected = b.select_source_sha('', tip)
            request = json.loads(b.task_request(json.dumps({'build_only': True, 'ios_action': 'skip',
                                                           'build_number': '42'}), allow_missing_source_sha=True))
            output = root / 'outputs'
            b.write_resolved_outputs(output, request, selected, b.workflow_identifier())
            values = dict(line.split('=', 1) for line in output.read_text().splitlines())
            self.assertEqual(values['source_sha'], sha)
            self.assertEqual(values['version'], '0.1.0')
            self.assertEqual(values['build_number'], '42')
            self.assertRegex(values['workflow_id'], r'^\d{12}$')

    def test_actual_reviewed_checkout_executes_and_confines_child_output(self):
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, env, git, sha = self.repository(root)
                output = io.StringIO()
                with (root / 'private.log').open('wb') as log, contextlib.redirect_stdout(output):
                    script = b.checkout_reviewed_entrypoint(source, sha, PurePosixPath('scripts/task.py'), env, log)
                    if exit_code:
                        with self.assertRaises(subprocess.CalledProcessError):
                            b.invoke([sys.executable, '-I', str(script)], source, {**env, 'CHILD_EXIT_CODE': str(exit_code)}, log)
                    else:
                        b.invoke([sys.executable, '-I', str(script)], source, env, log)
                self.assertNotIn('private actual child output', output.getvalue())
                self.assertIn('private actual child output', (root / 'private.log').read_text())
                self.assertEqual(git('rev-parse', 'HEAD'), sha)
            self.assertFalse(root.exists())

    def test_submodule_credential_is_added_only_to_private_task_environment(self):
        env = {'PATH': '/trusted/bin', 'SOURCE_SUBMODULE_TOKEN': 'private-read-token'}
        token = env.pop('SOURCE_SUBMODULE_TOKEN')

        self.assertNotIn('SOURCE_SUBMODULE_TOKEN', env)
        task_env = b.private_task_environment(env, token)
        self.assertEqual(task_env['SOURCE_SUBMODULE_TOKEN'], token)
        self.assertNotIn('SOURCE_SUBMODULE_TOKEN', env)
        self.assertEqual(task_env['PATH'], env['PATH'])

    def test_reviewed_checkout_accepts_a_real_directory_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, env, git, sha = self.repository(root)
            alias = root / 'source-alias'
            alias.symlink_to(source, target_is_directory=True)
            with (root / 'private.log').open('wb') as log:
                script = b.checkout_reviewed_entrypoint(
                    alias, sha, PurePosixPath('scripts/task.py'), env, log)
            self.assertEqual(script.resolve(), (source / 'scripts/task.py').resolve())
            self.assertEqual(git('rev-parse', 'HEAD'), sha)

    def test_reviewed_checkout_still_rejects_an_escaping_entrypoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, env, git, _ = self.repository(root)
            outside = root / 'outside.py'
            outside.write_text("raise RuntimeError('must never execute')\n")
            entry = source / 'scripts/escape.py'
            entry.symlink_to(outside)
            git('config', 'core.symlinks', 'true')
            git('add', 'scripts/escape.py')
            git('commit', '-qm', 'Reviewed tree with forbidden external entrypoint')
            sha = git('rev-parse', 'HEAD')
            git('update-ref', 'refs/remotes/origin/reviewed', sha)
            self.assertTrue(git('ls-tree', sha, '--', 'scripts/escape.py').startswith('120000 '))
            for flattened in (False, True):
                with self.subTest(flattened_symlink=flattened):
                    if flattened:
                        git('config', 'core.symlinks', 'false')
                        entry.unlink()  # Only the symlink created by this fixture.
                        git('checkout-index', '--force', '--', 'scripts/escape.py')
                        self.assertFalse(entry.is_symlink())
                    with (root / 'private.log').open('wb') as log:
                        with self.assertRaisesRegex(ValueError, 'Unsafe entrypoint'):
                            b.checkout_reviewed_entrypoint(
                                source, sha, PurePosixPath('scripts/escape.py'), env, log)

    def test_actual_nonancestor_revision_cannot_materialize_entrypoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, env, git, reviewed = self.repository(root)
            # A real later commit is not an ancestor of the pinned reviewed ref.
            (source / 'unreviewed').write_text('Unreviewed source change')
            git('add', '.')
            git('commit', '-qm', 'Actual unreviewed child revision')
            candidate = git('rev-parse', 'HEAD')
            git('checkout', '--quiet', '--detach', reviewed)
            with (root / 'private.log').open('wb') as log, self.assertRaises(subprocess.CalledProcessError):
                b.checkout_reviewed_entrypoint(source, candidate, PurePosixPath('scripts/task.py'), env, log)
            self.assertEqual(git('rev-parse', 'HEAD'), reviewed)
            self.assertFalse((source / 'unreviewed').exists())


if __name__ == '__main__': unittest.main()
