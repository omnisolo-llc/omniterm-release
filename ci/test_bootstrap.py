import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('bootstrap', Path(__file__).with_name('bootstrap.py'))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class BootstrapTests(unittest.TestCase):
    def env(self):
        return {'SOURCE_REPOSITORY': 'example/source', 'SOURCE_BRANCH': 'main',
                'SOURCE_ENTRYPOINT': 'scripts/task.py', 'SOURCE_DEPLOY_KEY': 'synthetic',
                'SOURCE_KNOWN_HOSTS': 'synthetic', 'BUILD_CONFIG': '{}', 'STORAGE_CONFIG': '{}',
                'RELEASE_REQUEST': json.dumps({'source_sha': 'a' * 40}), 'RELEASE_TARGET': 'linux'}

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
        output = io.StringIO()
        with patch.dict(b.os.environ, {}, clear=True), patch.object(b.subprocess, 'run') as run:
            with contextlib.redirect_stdout(output): self.assertEqual(b.main(), 1)
            run.assert_not_called()

    def test_invalid_config_does_not_leak(self):
        env = self.env(); env.update(GITHUB_ACTIONS='true', GITHUB_EVENT_NAME='workflow_dispatch',
                                     GITHUB_REF='refs/heads/main', SOURCE_REPOSITORY='confidential!')
        output = io.StringIO()
        with patch.dict(b.os.environ, env, clear=True), contextlib.redirect_stdout(output):
            self.assertEqual(b.main(), 1)
        self.assertNotIn('confidential', output.getvalue())


class WorkflowOrderingTests(unittest.TestCase):
    def test_publication_waits_for_optional_receipt_consumer_and_successful_downloads(self):
        workflow = (Path(__file__).resolve().parent.parent / '.github/workflows/release.yml').read_text()
        publish = workflow.split('\n  publish:\n', 1)[1]
        self.assertIn('needs: [validate, downloads, ios]', publish)
        # A status function prevents skipped/failed optional Apple work from
        # implicitly skipping publication; cancellation still prevents writes.
        expected = ("if: ${{ !cancelled() && github.ref == 'refs/heads/main' "
                    "&& !inputs.build_only && needs.validate.result == 'success' && needs.downloads.result == 'success' }}")
        self.assertIn(expected, publish)
        self.assertNotIn("needs.ios.result == 'success'", publish)
        self.assertIn('environment: public-release', publish)


class VerificationTests(unittest.TestCase):
    def test_build_verification_has_no_signing_or_storage_credentials(self):
        workflow = (Path(__file__).resolve().parent.parent / '.github/workflows/release.yml').read_text()
        verify = workflow.split('\n  verify:\n', 1)[1].split('\n  validate:\n', 1)[0]
        self.assertIn('contents: read', verify)
        for forbidden in ('SIGNING_CONFIG', 'STORAGE_CONFIG', 'GH_TOKEN', 'upload-artifact'):
            self.assertNotIn(forbidden, verify)
        self.assertIn('inputs.build_only', verify)

    def test_phase_reporting_cannot_echo_private_details(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'status.json'
            for value in ({'stage': 'confidential/path'}, {'stage': 'application-build', 'error': 'private output'}):
                path.write_text(json.dumps(value))
                output = io.StringIO()
                with contextlib.redirect_stdout(output): b.report_phase(path)
                self.assertNotIn('confidential', output.getvalue())
                self.assertNotIn('private output', output.getvalue())


class SSHLauncherTests(unittest.TestCase):
    def test_windows_git_layouts_are_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'Git'
            ssh = root / 'usr/bin/ssh.exe'
            ssh.parent.mkdir(parents=True)
            ssh.touch()
            for layout in ('cmd/git.exe', 'bin/git.exe', 'mingw64/bin/git.exe'):
                with patch.object(b.shutil, 'which', return_value=str(root / layout)):
                    self.assertEqual(b.ssh_executable(windows=True), ssh.as_posix())

    def test_checkout_error_classification_never_echoes_private_log(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'log'
            path.write_text('secret/path: Host key verification failed; private-token')
            self.assertEqual(b.checkout_failure(path), 'host-key-verification')
            path.write_text('private exception details')
            self.assertEqual(b.checkout_failure(path), 'unclassified')


class BootstrapExecutionTests(unittest.TestCase):
    def test_task_output_is_never_printed_and_checkout_is_removed(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temp:
                env = BootstrapTests().env()
                env.update(GITHUB_ACTIONS='true', GITHUB_EVENT_NAME='workflow_dispatch',
                           GITHUB_REF='refs/heads/main', GITHUB_SHA='b' * 40, RUNNER_TEMP=temp)
                output = io.StringIO()
                original_invoke = b.invoke
                calls = []
                def invoke(args, cwd, child_env, log, timeout=600):
                    calls.append(args)
                    self.assertNotIn('SOURCE_DEPLOY_KEY', child_env)
                    if args[:2] == ['git', 'checkout']:
                        task = Path(cwd) / 'scripts/task.py'
                        task.parent.mkdir(parents=True)
                        task.write_text("import sys; print('private fixture output'); sys.exit(" + str(int(fail)) + ")")
                    elif args[0] == sys.executable:
                        original_invoke(args, cwd, child_env, log, timeout)
                with patch.dict(b.os.environ, env, clear=True), patch.object(b, 'ssh_executable', return_value='ssh'), patch.object(b, 'invoke', side_effect=invoke), contextlib.redirect_stdout(output):
                    self.assertEqual(b.main(), 1 if fail else 0)
                self.assertNotIn('private fixture output', output.getvalue())
                self.assertNotIn('example/source', output.getvalue())
                self.assertEqual(list(Path(temp).iterdir()), [])
                ancestor_index = next(i for i, call in enumerate(calls) if call[1:2] == ['merge-base'])
                run_index = next(i for i, call in enumerate(calls) if call[0] == sys.executable)
                self.assertLess(ancestor_index, run_index)

    def test_nonancestor_revision_never_executes_private_task(self):
        with tempfile.TemporaryDirectory() as temp:
            env = BootstrapTests().env()
            env.update(GITHUB_ACTIONS='true', GITHUB_EVENT_NAME='workflow_dispatch',
                       GITHUB_REF='refs/heads/main', GITHUB_SHA='b' * 40, RUNNER_TEMP=temp)
            calls = []
            def invoke(args, cwd, child_env, log, timeout=600):
                calls.append(args)
                if args[:2] == ['git', 'merge-base']:
                    raise subprocess.CalledProcessError(1, args, stderr='private failure detail')
            with patch.dict(b.os.environ, env, clear=True), patch.object(b, 'ssh_executable', return_value='ssh'), patch.object(b, 'invoke', side_effect=invoke), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(b.main(), 1)
            self.assertFalse(any(call[0] == sys.executable for call in calls))
            self.assertEqual(list(Path(temp).iterdir()), [])


if __name__ == '__main__': unittest.main()
