#!/usr/bin/env python3
"""Generic private-source launcher. Never prints source paths or process output."""
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
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-fA-F]{40}', sha):
        raise ValueError('Invalid source revision')
    if env.get('RELEASE_TARGET') not in ('validate', 'linux', 'windows', 'android', 'ios', 'publish'):
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
    except (OSError, ValueError, TypeError):
        pass


def main():
    # Public Actions are observable. These checks are in addition to, not a
    # replacement for, environment reviewers and default-branch protections.
    env = os.environ.copy()
    # Storage is optional; private implementation decides which targets need it.
    env['BUILD_CONFIG'] = env.get('BUILD_CONFIG') or '{}'
    env['STORAGE_CONFIG'] = env.get('STORAGE_CONFIG') or '{}'
    if (env.get('GITHUB_ACTIONS') != 'true'
            or env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or env.get('GITHUB_REF') != 'refs/heads/main'):
        print('This launcher requires a reviewed manual workflow on main.')
        return 1
    try:
        repo, branch, entry, sha = validate(env)
    except Exception:
        print('Release configuration is incomplete or invalid. Contact the maintainer.')
        return 1
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='private-task-', dir=required(env, 'RUNNER_TEMP')) as temp:
        root = Path(temp)
        key, hosts = root / 'identity', root / 'known_hosts'
        key.write_text(env.pop('SOURCE_DEPLOY_KEY'), encoding='utf-8')
        hosts.write_text(env.pop('SOURCE_KNOWN_HOSTS'), encoding='utf-8')
        key.chmod(0o600)
        hosts.chmod(0o600)
        source = root / 'source'
        source.mkdir()
        env.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        for name in ('GH_DEBUG', 'GIT_TRACE', 'GIT_TRACE_PACKET', 'GIT_TRACE_CURL', 'GIT_CURL_VERBOSE',
                     'GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS', 'GIT_ALTERNATE_OBJECT_DIRECTORIES'):
            env.pop(name, None)
        ssh = 'ssh'
        if os.name == 'nt':
            git = Path(shutil.which('git') or '')
            bundled_ssh = git.parent.parent / 'usr/bin/ssh.exe'
            if not bundled_ssh.is_file():
                raise RuntimeError('Git SSH is unavailable')
            ssh = bundled_ssh.as_posix()
        env['GIT_SSH_COMMAND'] = (shlex.quote(ssh) + ' -F /dev/null -i ' + shlex.quote(key.as_posix())
            + ' -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes'
            + ' -o UserKnownHostsFile=' + shlex.quote(hosts.as_posix()))
        env['PRIVATE_BOOTSTRAP_LOG'] = str(root / 'bootstrap.log')
        env['PUBLIC_BUILDER_SHA'] = required(env, 'GITHUB_SHA')
        env['RELEASE_STATUS_FILE'] = str(root / 'status.json')
        try:
            with (root / 'bootstrap.log').open('wb') as log:
                invoke(['git', 'init', '-q'], source, env, log)
                invoke(['git', 'remote', 'add', 'origin', f'ssh://git@ssh.github.com:443/{repo}.git'], source, env, log)
                # Fetch ancestry, without materializing the application working tree.
                invoke(['git', 'fetch', '--quiet', '--no-tags', '--filter=blob:none', 'origin',
                        f'+refs/heads/{branch}:refs/remotes/origin/reviewed'], source, env, log)
                invoke(['git', 'merge-base', '--is-ancestor', sha, 'refs/remotes/origin/reviewed'], source, env, log)
                invoke(['git', 'sparse-checkout', 'init', '--cone'], source, env, log)
                invoke(['git', 'sparse-checkout', 'set', entry.parent.as_posix()], source, env, log)
                invoke(['git', 'checkout', '--quiet', '--detach', sha], source, env, log)
                script = source.joinpath(*entry.parts)
                if script.is_symlink() or not script.is_file() or not script.resolve().is_relative_to(source):
                    raise ValueError('Unsafe entrypoint')
                # The private entrypoint expands its checkout, installs pinned tools,
                # performs release tasks and retains diagnostics only in private storage.
                invoke([sys.executable, '-I', str(script)], source, env, log, timeout=10000)
        except Exception:
            print('Release task failed. Inspect private diagnostics and any completed stages before retrying.')
            report_phase(root / 'status.json')
            return 1
        finally:
            # TemporaryDirectory removes the sparse/full source, checkout key and logs.
            # No Actions caches or public artifacts are used for private material.
            key.unlink(missing_ok=True)
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
