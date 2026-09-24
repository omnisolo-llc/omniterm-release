#!/usr/bin/env python3
"""Generic private-source launcher. Never prints source paths or process output."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


def required(env, name):
    value = env.get(name, '')
    if not value.strip():
        raise ValueError('Required configuration is missing')
    return value


def validate(env):
    repo = required(env, 'SOURCE_REPOSITORY')
    branch = required(env, 'SOURCE_BRANCH')
    entry = required(env, 'SOURCE_ENTRYPOINT')
    request = json.loads(required(env, 'RELEASE_REQUEST'))
    if not re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9._-]+', repo):
        raise ValueError('Invalid repository')
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', branch)
            or '..' in branch or '//' in branch or branch.endswith(('/', '.'))
            or any(p.startswith('.') or p.endswith('.lock') for p in branch.split('/'))):
        raise ValueError('Invalid branch')
    path = PurePosixPath(entry)
    if (path.is_absolute() or '..' in path.parts or len(path.parts) < 2
            or not re.fullmatch(r'[A-Za-z0-9_./-]+\.py', entry)
            or any(part.startswith('.') for part in path.parts)):
        raise ValueError('Invalid entrypoint')
    sha = request.get('source_sha', '')
    if (not isinstance(sha, str)
            or (sha and not re.fullmatch(r'[0-9a-fA-F]{40}', sha))
            or (not sha and env.get('RELEASE_TARGET') != 'resolve')):
        raise ValueError('Invalid source revision')
    if env.get('RELEASE_TARGET') not in ('resolve', 'validate', 'linux', 'windows', 'macos', 'android', 'web', 'ios', 'publish'):
        raise ValueError('Invalid target')
    for name in ('SOURCE_DEPLOY_KEY', 'SOURCE_KNOWN_HOSTS'):
        required(env, name)
    return repo, branch, path, sha.lower()


def invoke(args, cwd, env, log, timeout=600):
    # Never use a shell or let compiler/Git output reach the Actions log.
    subprocess.run(args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                   check=True, timeout=timeout)


def report_phase(path):
    # Only fixed stage names leave the private subprocess. Never echo its errors.
    stages = {'source-check', 'python-dependencies', 'rust-toolchain', 'flutter-sdk',
              'system-dependencies', 'android-sdk', 'package-resolution',
              'application-build', 'lockfile-check', 'complete'}
    try:
        if path.is_symlink() or path.stat().st_size > 512:
            return
        value = json.loads(path.read_text())
        if isinstance(value, dict) and isinstance(value.get('stage'), str) and value['stage'] in stages:
            print('Last completed/attempted phase: ' + value['stage'])
            allowed = {'android-kotlin-plugin', 'android-sdk-level', 'android-java-version',
                       'android-namespace', 'android-apk-output', 'dependency-resolution',
                       'windows-visual-studio', 'cmake-minimum-version', 'native-build-hook',
                       'rust-compilation', 'native-linker', 'dart-compilation', 'windows-symlinks',
                       'missing-native-library', 'network-download', 'android-ndk'}
            diagnostics = value.get('diagnostics', [])
            if isinstance(diagnostics, list):
                for category in diagnostics[:6]:
                    if isinstance(category, str) and category in allowed:
                        print('Build diagnostic category: ' + category)
    except (OSError, ValueError, TypeError):
        pass


def ssh_executable(windows=None):
    if not (os.name == 'nt' if windows is None else windows):
        return 'ssh'
    git = shutil.which('git')
    if git:
        # Git for Windows can be resolved through cmd/, bin/, or mingw64/bin/.
        for parent in Path(git).parents:
            candidate = parent / 'usr/bin/ssh.exe'
            if candidate.is_file():
                return candidate.as_posix()
    raise RuntimeError('Git SSH is unavailable')


def checkout_failure(path):
    # Classify known infrastructure errors without emitting any private log text.
    patterns = {
        'host-key-verification': (b'Host key verification failed', b'host key is known'),
        'checkout-key-format': (b'invalid format', b'error in libcrypto'),
        'checkout-authentication': (b'Permission denied (publickey)', b'Repository not found'),
        'checkout-network': (b'Could not resolve hostname', b'Connection timed out', b'Connection refused'),
        'source-reference': (b"couldn\'t find remote ref", b'Not a valid object name'),
    }
    try:
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 65536))
            tail = stream.read(65536)
        return next((name for name, values in patterns.items() if any(value in tail for value in values)), 'unclassified')
    except OSError:
        return 'unclassified'


def select_source_sha(requested, branch_tip):
    requested = requested.strip().lower()
    branch_tip = branch_tip.strip().lower()
    if not re.fullmatch(r'[0-9a-f]{40}', branch_tip):
        raise ValueError('Invalid source branch tip')
    if requested and not re.fullmatch(r'[0-9a-f]{40}', requested):
        raise ValueError('Invalid source revision')
    return requested or branch_tip


def approved_release_sha(path=None):
    approval = Path(path) if path is not None else Path(__file__).with_name('approved_release_source.json')
    if approval.is_symlink() or not approval.is_file() or approval.stat().st_size > 128:
        raise ValueError('A reviewed release source is required')
    value = json.loads(approval.read_text(encoding='ascii'))
    if (not isinstance(value, dict) or set(value) != {'source_sha'}
            or not isinstance(value['source_sha'], str)
            or not re.fullmatch(r'[0-9a-f]{40}', value['source_sha'])):
        raise ValueError('A reviewed release source is required')
    return value['source_sha']


def workflow_identifier(now=None):
    timestamp = now or datetime.now(timezone.utc)
    return timestamp.astimezone(timezone.utc).strftime('%Y%m%d%H%M')


def task_request(raw, *, resolved=None, allow_missing_source_sha=False):
    request = json.loads(raw)
    if not isinstance(request, dict):
        raise ValueError('Expected request object')
    selected = request.pop('verify_target', 'all')
    if selected not in ('all', 'linux', 'windows', 'macos', 'android', 'web', 'ios'):
        raise ValueError('Invalid verification target')
    windows_preview = request.pop('preview_windows_self_sign', False)
    if not isinstance(windows_preview, bool):
        raise ValueError('Windows preview selection must be a boolean')
    if windows_preview and (selected != 'windows' or request.get('build_only') is not True):
        raise ValueError('Self-signed Windows preview requires Windows build-only verification')
    if selected != 'all' and request.get('build_only') is not True:
        raise ValueError('Actual releases must build all targets')
    build_only = request.get('build_only', False)
    if not isinstance(build_only, bool):
        raise ValueError('build_only must be a boolean')
    if not build_only and request.get('ios_action') not in ('upload', 'submit'):
        raise ValueError('A full release requires Apple upload or submission')
    if build_only and request.get('ios_action', 'skip') != 'skip':
        raise ValueError('Build verification cannot distribute to Apple')

    requested_sha = request.get('source_sha', '')
    approved_sha = None
    if not build_only:
        approved_sha = approved_release_sha()
        if not isinstance(requested_sha, str) or requested_sha.lower() != approved_sha:
            raise ValueError('The full release source has not been reviewed')

    if resolved:
        for key in ('source_sha', 'version', 'build_number'):
            if resolved.get(key):
                request[key] = resolved[key]

    sha = request.get('source_sha', '')
    if not isinstance(sha, str) or (sha and not re.fullmatch(r'[0-9a-fA-F]{40}', sha)):
        raise ValueError('Invalid source revision')
    if not sha and not allow_missing_source_sha:
        raise ValueError('A source revision must be resolved before building')
    request['source_sha'] = sha.lower()
    if approved_sha is not None and request['source_sha'] != approved_sha:
        raise ValueError('The resolved release source differs from the reviewed source')

    version = request.get('version') or '0.1.0'
    if not isinstance(version, str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version):
        raise ValueError('Version must use major.minor.patch')
    request['version'] = version

    build_number = request.get('build_number', '')
    if not isinstance(build_number, str):
        raise ValueError('Invalid build number')
    if not re.fullmatch(r'[1-9][0-9]{0,3}', build_number):
        raise ValueError('App builds require a build number from 1-9999')
    request['build_number'] = build_number
    return json.dumps(request)


def capture(args, cwd, env, timeout=600):
    result = subprocess.run(args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, check=True, timeout=timeout)
    return result.stdout.decode('ascii').strip()


def write_resolved_outputs(path, request, source_sha, run_id):
    values = {'source_sha': source_sha, 'version': request['version'],
              'build_number': request['build_number'], 'workflow_id': run_id}
    with Path(path).open('a', encoding='utf-8', newline='\n') as output:
        for key, value in values.items():
            output.write(f'{key}={value}\n')


def seal_diagnostics(root, recipient, runner_temp):
    if not recipient:
        return
    output = Path(runner_temp).resolve() / 'encrypted-diagnostics' / 'diagnostics.sealed'
    # No ambient Node options, GitHub command files, tokens or signing settings.
    env = {k: v for k, v in os.environ.items() if k.upper() in {'PATH', 'SYSTEMROOT', 'SYSTEMDRIVE', 'TEMP', 'TMP'}}
    env['DIAGNOSTICS_PUBLIC_KEY'] = recipient
    try:
        result = subprocess.run(['node', str(Path(__file__).with_name('seal_diagnostics.cjs')), str(root), str(output)],
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                check=False, timeout=30)
        if result.returncode:
            output.unlink(missing_ok=True)
            print('Encrypted diagnostics could not be retained.')
    except (OSError, subprocess.TimeoutExpired):
        output.unlink(missing_ok=True)
        print('Encrypted diagnostics could not be retained.')


def main():
    # Public Actions are observable. These checks are in addition to, not a
    # replacement for, environment reviewers and default-branch protections.
    env = os.environ.copy()
    recipient = env.pop('DIAGNOSTICS_PUBLIC_KEY', '')
    # Storage is optional; private implementation decides which targets need it.
    env['BUILD_CONFIG'] = env.get('BUILD_CONFIG') or '{}'
    env['STORAGE_CONFIG'] = env.get('STORAGE_CONFIG') or '{}'
    if (env.get('GITHUB_ACTIONS') != 'true'
            or env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or env.get('GITHUB_REF') != 'refs/heads/main'):
        print('This launcher requires a reviewed manual workflow on main.')
        return 1
    try:
        target = env.get('RELEASE_TARGET')
        resolved = None
        if target != 'resolve':
            resolved = {'source_sha': env.get('RESOLVED_SOURCE_SHA', ''),
                        'version': env.get('RESOLVED_VERSION', ''),
                        'build_number': env.get('RESOLVED_BUILD_NUMBER', '')}
        env['RELEASE_REQUEST'] = task_request(
            required(env, 'RELEASE_REQUEST'), resolved=resolved,
            allow_missing_source_sha=(target == 'resolve'))
        repo, branch, entry, sha = validate(env)
    except Exception:
        print('Release configuration is incomplete or invalid. Contact the maintainer.')
        return 1
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='private-task-', dir=required(env, 'RUNNER_TEMP')) as temp:
        # Resolve Windows short-name aliases before checking containment.
        root = Path(temp).resolve()
        key, hosts = root / 'identity', root / 'known_hosts'
        key.write_text(env.pop('SOURCE_DEPLOY_KEY').replace('\r\n', '\n').rstrip() + '\n', encoding='utf-8', newline='\n')
        hosts.write_text(env.pop('SOURCE_KNOWN_HOSTS').replace('\r\n', '\n').rstrip() + '\n', encoding='utf-8', newline='\n')
        key.chmod(0o600)
        hosts.chmod(0o600)
        source = root / 'source'
        source.mkdir()
        env.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        for name in ('GH_DEBUG', 'GIT_TRACE', 'GIT_TRACE_PACKET', 'GIT_TRACE_CURL', 'GIT_CURL_VERBOSE',
                     'GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS', 'GIT_ALTERNATE_OBJECT_DIRECTORIES'):
            env.pop(name, None)
        ssh = ssh_executable()
        env['GIT_SSH_COMMAND'] = (shlex.quote(ssh) + ' -F /dev/null -i ' + shlex.quote(key.as_posix())
            + ' -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes'
            + ' -o UserKnownHostsFile=' + shlex.quote(hosts.as_posix()))
        env['PRIVATE_BOOTSTRAP_LOG'] = str(root / 'bootstrap.log')
        env['PRIVATE_DIAGNOSTIC_LOG'] = str(root / 'task.log')
        env['PUBLIC_BUILDER_SHA'] = required(env, 'GITHUB_SHA')
        env['RELEASE_STATUS_FILE'] = str(root / 'status.json')
        stage = 'checkout-initialization'
        try:
            with (root / 'bootstrap.log').open('wb') as log:
                invoke(['git', 'init', '-q'], source, env, log)
                invoke(['git', 'remote', 'add', 'origin', f'ssh://git@ssh.github.com:443/{repo}.git'], source, env, log)
                # Fetch ancestry, without materializing the application working tree.
                stage = 'private-checkout'
                invoke(['git', 'fetch', '--quiet', '--no-tags', '--filter=blob:none', 'origin',
                        f'+refs/heads/{branch}:refs/remotes/origin/reviewed'], source, env, log)
                if target == 'resolve':
                    stage = 'source-revision'
                    branch_tip = capture(['git', 'rev-parse', 'refs/remotes/origin/reviewed'], source, env)
                    sha = select_source_sha(sha, branch_tip)
                stage = 'source-ancestry'
                invoke(['git', 'merge-base', '--is-ancestor', sha, 'refs/remotes/origin/reviewed'], source, env, log)
                if target == 'resolve':
                    request = json.loads(env['RELEASE_REQUEST'])
                    write_resolved_outputs(required(env, 'GITHUB_OUTPUT'), request, sha,
                                           workflow_identifier())
                    print('Release inputs resolved.')
                    return 0
                invoke(['git', 'sparse-checkout', 'init', '--cone'], source, env, log)
                invoke(['git', 'sparse-checkout', 'set', entry.parent.as_posix()], source, env, log)
                invoke(['git', 'checkout', '--quiet', '--detach', sha], source, env, log)
                stage = 'entrypoint-check'
                script = source.joinpath(*entry.parts)
                if script.is_symlink() or not script.is_file() or not script.resolve().is_relative_to(source):
                    raise ValueError('Unsafe entrypoint')
                # The private entrypoint expands its checkout, installs pinned tools,
                # performs release tasks and retains diagnostics only in private storage.
                stage = 'private-task'
                invoke([sys.executable, '-I', str(script)], source, env, log, timeout=10000)
        except Exception:
            print('Release task failed. Inspect private diagnostics and any completed stages before retrying.')
            print('Launcher phase: ' + stage + '; category: ' + checkout_failure(root / 'bootstrap.log'))
            report_phase(root / 'status.json')
            return 1
        finally:
            # TemporaryDirectory removes the sparse/full source, checkout key and logs.
            # No Actions caches or public artifacts are used for private material.
            key.unlink(missing_ok=True)
            if target != 'resolve':
                seal_diagnostics(root, recipient, env['RUNNER_TEMP'])
        print('Release task completed.')
        return 0


if __name__ == '__main__':
    try:
        status = main()
    except Exception:
        # Includes setup/cleanup errors outside the task subprocess. Do not leak
        # a private path or exception message from those failures either.
        print('Release setup or cleanup failed. Contact the maintainer.')
        status = 1
    sys.exit(status)
