#!/usr/bin/env python3
"""Generic private-source launcher. Never prints source paths or process output."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import threading


BUILD_TARGETS = frozenset({'linux', 'windows', 'macos', 'android', 'web', 'ios'})
APPLICATION_SOURCE_REPOSITORY = 'ql-owo-lp/omniterm'
APPLICATION_SOURCE_ENTRYPOINT = 'scripts/release/entrypoint.py'
MAX_REVIEWED_FILE_COUNT = 512
MAX_REVIEWED_DIRECTORY_COUNT = 2048
MAX_REVIEWED_PATH_BYTES = 4096
MAX_REVIEWED_PATH_DEPTH = 64
MAX_REVIEWED_FILE_BYTES = 4 * 1024 * 1024
MAX_REVIEWED_TOTAL_BYTES = 16 * 1024 * 1024
MAX_REVIEWED_TREE_BYTES = 1024 * 1024
REVIEWED_TREE_TIMEOUT_SECONDS = 30
RELEASE_TARGETS = frozenset({
    'resolve', 'integration', 'installation', 'package-signatures', 'apple-testflight',
    'validate', 'ios-deliver', 'ios-submit', 'publication-prepare', 'vpn-container',
    'managed-rtc-provider', 'external-tests',
    'external-windows-signing',
    'publish', *BUILD_TARGETS,
})


def required(env, name):
    value = env.get(name, '')
    if not value.strip():
        raise ValueError('Required configuration is missing')
    return value


def validate_managed_vpn_build_config(env, target, request):
    if target == 'resolve' or request.get('build_only') is True:
        return
    try:
        config = json.loads(env.get('BUILD_CONFIG') or '{}')
    except (TypeError, json.JSONDecodeError):
        raise ValueError('Full releases require OMNI_ENABLE_VPN=true') from None
    if not isinstance(config, dict) or config.get('OMNI_ENABLE_VPN') != 'true':
        raise ValueError('Full releases require OMNI_ENABLE_VPN=true')


def validate(env):
    repo = required(env, 'SOURCE_REPOSITORY')
    branch = required(env, 'SOURCE_BRANCH')
    entry = required(env, 'SOURCE_ENTRYPOINT')
    request = json.loads(required(env, 'RELEASE_REQUEST'))
    if not isinstance(request, dict):
        raise ValueError('Invalid release request')
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
    if sha and int(sha, 16) == 0:
        raise ValueError('Invalid source revision')
    if env.get('RELEASE_TARGET') not in RELEASE_TARGETS:
        raise ValueError('Invalid target')
    target = env['RELEASE_TARGET']
    validate_managed_vpn_build_config(env, target, request)
    if (target != 'resolve'
            and not (target in BUILD_TARGETS and request.get('build_only') is True)):
        required(env, 'STORAGE_CONFIG')
    for name in ('SOURCE_DEPLOY_KEY', 'SOURCE_KNOWN_HOSTS'):
        required(env, name)
    return repo, branch, path, sha.lower()


def validate_target_request(target, request):
    if target not in RELEASE_TARGETS or not isinstance(request, dict):
        raise ValueError('Invalid release target request')
    build_only = request.get('build_only', False)
    if not isinstance(build_only, bool):
        raise ValueError('build_only must be a boolean')
    if target == 'resolve':
        return
    if target in BUILD_TARGETS:
        if build_only:
            selected = request.get('verify_target', 'all')
            if selected not in ('all', target):
                raise ValueError('Build target differs from the selected verification target')
        return
    if build_only:
        raise ValueError('Release stages require a full release request')
    if target == 'ios-submit' and request.get('ios_action') != 'submit':
        raise ValueError('Apple submission requires ios_action=submit')


def private_task_environment(env, submodule_token):
    # The reviewed private task does not need workflow command files. Keeping
    # these paths away from it prevents accidental writes to later Actions steps.
    workflow_command_files = {'GITHUB_ENV', 'GITHUB_OUTPUT', 'GITHUB_PATH',
                              'GITHUB_STATE', 'GITHUB_STEP_SUMMARY'}
    task_env = {key: value for key, value in env.items() if key not in workflow_command_files}
    if submodule_token:
        task_env['SOURCE_SUBMODULE_TOKEN'] = submodule_token
    return task_env


def validate_integration_authority(env, repo, branch):
    """Each private source task requires its own canonical protected workflow."""
    if env.get('SOURCE_ENTRYPOINT') == 'scripts/release/agent_entrypoint.py':
        from agent_authority import validate_agent_build_authority
        validate_agent_build_authority(env, repo, branch)
        return
    builder = 'omnisolo-llc/omniterm-release'
    if (env.get('GITHUB_ACTIONS') != 'true'
            or env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or env.get('GITHUB_REPOSITORY') != builder
            or env.get('GITHUB_REF') != 'refs/heads/main'
            or env.get('GITHUB_WORKFLOW_REF') != builder + '/.github/workflows/release.yml@refs/heads/main'
            or repo != APPLICATION_SOURCE_REPOSITORY
            or env.get('SOURCE_ENTRYPOINT') != APPLICATION_SOURCE_ENTRYPOINT
            or repo != required(env, 'SOURCE_REPOSITORY') or branch != 'main'
            or not re.fullmatch('[a-f0-9]{40}', env.get('GITHUB_SHA', ''))
            or any(not re.fullmatch('[1-9][0-9]*', env.get(key, ''))
                   for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'))):
        raise ValueError('Untrusted integration execution or acquisition authority')


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
        for candidate in git_ssh_candidates(git):
            if candidate.is_file():
                return candidate.as_posix()
    raise RuntimeError('Git SSH is unavailable')


def git_ssh_candidates(git):
    return [parent / 'usr/bin/ssh.exe' for parent in Path(git).parents]


def bounded_git_output(args, cwd, env, log, max_bytes, timeout):
    """Capture bounded command output without buffering an untrusted tree listing."""
    process = subprocess.Popen(
        args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=log,
    )
    output = bytearray()
    overflow = threading.Event()
    failures = []

    def collect():
        try:
            while True:
                block = process.stdout.read(65536)
                if not block:
                    return
                remaining = max_bytes - len(output)
                if len(block) > remaining:
                    if remaining:
                        output.extend(block[:remaining])
                    overflow.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return
                output.extend(block)
        except OSError as error:
            failures.append(error)
            try:
                process.kill()
            except OSError:
                pass

    collector = threading.Thread(target=collect, daemon=True)
    collector.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        returncode = process.wait()
    finally:
        collector.join()
        process.stdout.close()
    if timed_out:
        raise ValueError('Reviewed source tree lookup timed out')
    if overflow.is_set():
        raise ValueError('Reviewed source tree exceeds its bound')
    if failures:
        raise ValueError('Reviewed source tree lookup failed') from None
    if returncode:
        raise subprocess.CalledProcessError(returncode, args)
    return bytes(output)


def verify_reviewed_worktree(source, sha, directory, env, log, *, require_read_only=False):
    """Verify every materialized regular file that can be imported."""
    directory = PurePosixPath(directory)
    if (directory.is_absolute() or not directory.parts
            or any(part in ('', '.', '..') for part in directory.parts)):
        raise ValueError('Unsafe reviewed source directory')

    git_env = {**env, 'GIT_NO_REPLACE_OBJECTS': '1'}
    records = bounded_git_output(
        ['git', 'ls-tree', '-l', '-r', '-z', sha, '--', directory.as_posix()],
        source, git_env, log, MAX_REVIEWED_TREE_BYTES,
        REVIEWED_TREE_TIMEOUT_SECONDS,
    )

    prefix = os.fsencode(directory.as_posix()) + b'/'
    expected = set()
    total_bytes = 0
    for record in records.split(b'\0'):
        if not record:
            continue
        metadata, separator, name = record.partition(b'\t')
        fields = metadata.split()
        if (not separator or len(fields) != 4 or not name.startswith(prefix)
                or len(name) > MAX_REVIEWED_PATH_BYTES
                or any(part in (b'', b'.', b'..') for part in name.split(b'/'))):
            raise ValueError('Reviewed source differs from its Git tree')
        if len(name.split(b'/')) > MAX_REVIEWED_PATH_DEPTH:
            raise ValueError('Reviewed source exceeds its verification bounds')
        mode, kind, object_id, size_text = fields
        if mode not in (b'100644', b'100755') or kind != b'blob':
            raise ValueError('Reviewed source differs from its Git tree')
        try:
            expected_size = int(size_text)
        except ValueError:
            raise ValueError('Reviewed source differs from its Git tree') from None
        if (expected_size < 0 or expected_size > MAX_REVIEWED_FILE_BYTES
                or total_bytes + expected_size > MAX_REVIEWED_TOTAL_BYTES
                or len(expected) >= MAX_REVIEWED_FILE_COUNT):
            raise ValueError('Reviewed source exceeds its verification bounds')
        total_bytes += expected_size
        path = source
        parts = name.split(b'/')
        for part in parts[:-1]:
            path /= os.fsdecode(part)
            try:
                parent_info = path.lstat()
            except OSError:
                raise ValueError('Reviewed source differs from its Git tree') from None
            if not stat.S_ISDIR(parent_info.st_mode) or is_reparse_point(parent_info):
                raise ValueError('Reviewed source differs from its Git tree')
        path /= os.fsdecode(parts[-1])
        try:
            info = path.lstat()
        except OSError:
            raise ValueError('Reviewed source differs from its Git tree') from None
        if (not stat.S_ISREG(info.st_mode) or is_reparse_point(info)
                or info.st_size != expected_size
                or info.st_nlink != 1
                or (os.name != 'nt'
                    and bool(info.st_mode & stat.S_IXUSR) != (mode == b'100755'))
                or (require_read_only and info.st_mode & 0o222)):
            raise ValueError('Reviewed source differs from its Git tree')

        digest = hashlib.sha1(
            b'blob ' + str(expected_size).encode('ascii') + b'\0'
        )
        actual_size = 0
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            raise ValueError('Reviewed source differs from its Git tree')
        with os.fdopen(descriptor, 'rb') as stream:
            opened_info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened_info.st_mode)
                    or opened_info.st_nlink != 1
                    or (info.st_dev, info.st_ino) != (opened_info.st_dev, opened_info.st_ino)):
                raise ValueError('Reviewed source differs from its Git tree')
            for block in iter(lambda: stream.read(65536), b''):
                actual_size += len(block)
                if actual_size > expected_size:
                    raise ValueError('Reviewed source differs from its Git tree')
                digest.update(block)
        if actual_size != expected_size or digest.hexdigest().encode('ascii') != object_id:
            raise ValueError('Reviewed source differs from its Git tree')
        expected.add(name)

    materialized = source.joinpath(*directory.parts)
    try:
        materialized_info = materialized.lstat()
    except OSError:
        raise ValueError('Reviewed source differs from its Git tree') from None
    if (not stat.S_ISDIR(materialized_info.st_mode) or is_reparse_point(materialized_info)
            or (require_read_only and materialized_info.st_mode & 0o222)):
        raise ValueError('Reviewed source differs from its Git tree')
    if require_read_only:
        ancestor = materialized.parent
        while ancestor != source:
            try:
                ancestor_info = ancestor.lstat()
            except OSError:
                raise ValueError('Reviewed source differs from its Git tree') from None
            if (not stat.S_ISDIR(ancestor_info.st_mode) or is_reparse_point(ancestor_info)
                    or ancestor_info.st_mode & 0o222):
                raise ValueError('Reviewed source differs from its Git tree')
            ancestor = ancestor.parent

    def relative_name(path):
        relative = path.relative_to(source)
        name = os.fsencode(relative.as_posix())
        if (len(name) > MAX_REVIEWED_PATH_BYTES
                or len(relative.parts) > MAX_REVIEWED_PATH_DEPTH):
            raise ValueError('Reviewed source exceeds its verification bounds')
        return name

    observed = set()
    pending = [materialized]
    directory_count = 1
    if directory_count > MAX_REVIEWED_DIRECTORY_COUNT:
        raise ValueError('Reviewed source exceeds its verification bounds')
    while pending:
        parent = pending.pop()
        with os.scandir(parent) as entries:
            for entry in entries:
                path = Path(entry.path)
                name = relative_name(path)
                info = reviewed_entry_info(path, entry)
                if stat.S_ISDIR(info.st_mode) and not is_reparse_point(info):
                    if require_read_only and info.st_mode & 0o222:
                        raise ValueError('Reviewed source differs from its Git tree')
                    directory_count += 1
                    if directory_count > MAX_REVIEWED_DIRECTORY_COUNT:
                        raise ValueError('Reviewed source exceeds its verification bounds')
                    pending.append(path)
                elif (stat.S_ISREG(info.st_mode) and not is_reparse_point(info)
                      and info.st_nlink == 1):
                    observed.add(name)
                    if len(observed) > MAX_REVIEWED_FILE_COUNT:
                        raise ValueError('Reviewed source exceeds its verification bounds')
                else:
                    raise ValueError('Reviewed source differs from its Git tree')
    if observed != expected:
        raise ValueError('Reviewed source differs from its Git tree')


def is_reparse_point(info):
    flag = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
    return bool(getattr(info, 'st_file_attributes', 0) & flag)


def reviewed_entry_info(path, entry):
    # Windows DirEntry metadata omits file identity and link-count fields needed
    # by the verifier and handle-bound permission updates. lstat keeps the
    # no-follow check while obtaining the corresponding file-handle metadata.
    if os.name == 'nt':
        return path.lstat()
    return entry.stat(follow_symlinks=False)


def same_reviewed_entry(expected, actual, *, directory):
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    return (expected_type(expected.st_mode) and expected_type(actual.st_mode)
            and not is_reparse_point(actual)
            and (expected.st_dev, expected.st_ino) == (actual.st_dev, actual.st_ino)
            and (directory or (expected.st_nlink == actual.st_nlink == 1)))


def windows_chmod_reviewed_entry(path, expected, mode, *, directory):
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [('CreationTime', ctypes.c_longlong),
                    ('LastAccessTime', ctypes.c_longlong),
                    ('LastWriteTime', ctypes.c_longlong),
                    ('ChangeTime', ctypes.c_longlong),
                    ('FileAttributes', wintypes.DWORD)]

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [('FileAttributes', wintypes.DWORD),
                    ('ReparseTag', wintypes.DWORD)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                            wintypes.HANDLE]
    create_file.restype = wintypes.HANDLE
    get_attribute_info = kernel.GetFileInformationByHandleEx
    get_attribute_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                   wintypes.DWORD]
    get_attribute_info.restype = wintypes.BOOL
    get_basic_info = kernel.GetFileInformationByHandleEx
    get_basic_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                               wintypes.DWORD]
    get_basic_info.restype = wintypes.BOOL
    set_basic_info = kernel.SetFileInformationByHandle
    set_basic_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                               wintypes.DWORD]
    set_basic_info.restype = wintypes.BOOL
    close_handle = kernel.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    share = 0x00000001 | 0x00000002 | 0x00000004
    flags = 0x00200000 | (0x02000000 if directory else 0)  # Open reparse points, not targets.
    handle = create_file(str(path), 0x00000080 | 0x00000100, share, None, 3, flags, None)
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in (None, invalid_handle):
        raise ValueError('Reviewed source changed during permission update')
    descriptor = None
    try:
        handle_value = handle if isinstance(handle, int) else handle.value
        try:
            descriptor = msvcrt.open_osfhandle(handle_value, os.O_RDONLY | os.O_BINARY)
        except OSError:
            close_handle(handle)
            handle = None
            raise
        handle = None  # The descriptor now owns the native handle.
        actual_stat = os.fstat(descriptor)
        if not same_reviewed_entry(expected, actual_stat, directory=directory):
            raise ValueError('Reviewed source changed during permission update')
        handle_value = msvcrt.get_osfhandle(descriptor)
        attributes = FileAttributeTagInfo()
        if not get_attribute_info(handle_value, 9, ctypes.byref(attributes),
                                  ctypes.sizeof(attributes)):
            raise ValueError('Reviewed source changed during permission update')
        attrs = attributes.FileAttributes
        if bool(attrs & 0x10) != directory or attrs & 0x400:
            raise ValueError('Reviewed source changed during permission update')

        basic = FileBasicInfo()
        if not get_basic_info(handle_value, 0, ctypes.byref(basic), ctypes.sizeof(basic)):
            raise ValueError('Secure reviewed source permissions are unavailable')
        if mode & stat.S_IWUSR:
            basic.FileAttributes &= ~0x1
        else:
            basic.FileAttributes |= 0x1
        if not set_basic_info(handle_value, 0, ctypes.byref(basic), ctypes.sizeof(basic)):
            raise ValueError('Secure reviewed source permissions are unavailable')
    finally:
        if descriptor is not None:
            os.close(descriptor)
        elif handle is not None:
            close_handle(handle)


def chmod_reviewed_entry(path, expected, mode, *, directory):
    """Change permissions through a handle bound to the enumerated inode."""
    if os.name != 'nt':
        if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'fchmod'):
            raise ValueError('Secure reviewed source permissions are unavailable')
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
        if directory:
            flags |= getattr(os, 'O_DIRECTORY', 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            raise ValueError('Reviewed source changed during permission update') from None
        try:
            actual = os.fstat(descriptor)
            if not same_reviewed_entry(expected, actual, directory=directory):
                raise ValueError('Reviewed source changed during permission update')
            os.fchmod(descriptor, mode)
        finally:
            os.close(descriptor)
        return

    try:
        windows_chmod_reviewed_entry(path, expected, mode, directory=directory)
    except (OSError, AttributeError):
        raise ValueError('Secure reviewed source permissions are unavailable') from None


def freeze_reviewed_worktree(source, directory, *, freeze_parent=False):
    directory = PurePosixPath(directory)
    if (directory.is_absolute() or not directory.parts
            or any(part in ('', '.', '..') for part in directory.parts)):
        raise ValueError('Unsafe reviewed source directory')
    materialized = source.joinpath(*directory.parts)
    try:
        root_info = materialized.lstat()
    except OSError:
        raise ValueError('Reviewed source differs from its Git tree') from None
    if not stat.S_ISDIR(root_info.st_mode) or is_reparse_point(root_info):
        raise ValueError('Reviewed source differs from its Git tree')
    pending = [materialized]
    directories = []
    directory_count = 1
    if freeze_parent and materialized.parent != source:
        parent_info = materialized.parent.lstat()
        if not stat.S_ISDIR(parent_info.st_mode) or is_reparse_point(parent_info):
            raise ValueError('Reviewed source differs from its Git tree')
        directories.append((materialized.parent, parent_info))
        directory_count += 1
    directories.append((materialized, root_info))
    files = []
    if directory_count > MAX_REVIEWED_DIRECTORY_COUNT:
        raise ValueError('Reviewed source exceeds its verification bounds')
    while pending:
        parent = pending.pop()
        with os.scandir(parent) as entries:
            for entry in entries:
                path = Path(entry.path)
                info = reviewed_entry_info(path, entry)
                if stat.S_ISDIR(info.st_mode) and not is_reparse_point(info):
                    directory_count += 1
                    if directory_count > MAX_REVIEWED_DIRECTORY_COUNT:
                        raise ValueError('Reviewed source exceeds its verification bounds')
                    pending.append(path)
                    directories.append((path, info))
                elif (stat.S_ISREG(info.st_mode) and not is_reparse_point(info)
                      and info.st_nlink == 1):
                    files.append((path, info))
                    if len(files) > MAX_REVIEWED_FILE_COUNT:
                        raise ValueError('Reviewed source exceeds its verification bounds')
                else:
                    raise ValueError('Reviewed source differs from its Git tree')
    for path, info in files:
        chmod_reviewed_entry(path, info, stat.S_IMODE(info.st_mode) & ~0o222,
                             directory=False)
    for path, info in reversed(directories):
        chmod_reviewed_entry(path, info, stat.S_IMODE(info.st_mode) & ~0o222,
                             directory=True)


def thaw_reviewed_worktree(source, directory, *, thaw_parent=False):
    directory = PurePosixPath(directory)
    if (directory.is_absolute() or not directory.parts
            or any(part in ('', '.', '..') for part in directory.parts)):
        return
    materialized = source.joinpath(*directory.parts)
    try:
        materialized_info = materialized.lstat()
    except OSError:
        return
    if not stat.S_ISDIR(materialized_info.st_mode) or is_reparse_point(materialized_info):
        return
    pending = [materialized]
    directories = []
    files = []
    directory_count = 1
    if directory_count > MAX_REVIEWED_DIRECTORY_COUNT:
        raise ValueError('Reviewed source exceeds its cleanup bounds')
    while pending:
        parent = pending.pop()
        directories.append(parent)
        with os.scandir(parent) as entries:
            for entry in entries:
                path = Path(entry.path)
                info = reviewed_entry_info(path, entry)
                if stat.S_ISDIR(info.st_mode) and not is_reparse_point(info):
                    directory_count += 1
                    if directory_count > MAX_REVIEWED_DIRECTORY_COUNT:
                        raise ValueError('Reviewed source exceeds its cleanup bounds')
                    pending.append(path)
                elif (stat.S_ISREG(info.st_mode) and not is_reparse_point(info)
                      and info.st_nlink == 1):
                    files.append((path, info))
                    if len(files) > MAX_REVIEWED_FILE_COUNT:
                        raise ValueError('Reviewed source exceeds its cleanup bounds')
    for path, info in files:
        chmod_reviewed_entry(path, info, stat.S_IMODE(info.st_mode) | 0o200,
                             directory=False)
    for path in reversed(directories):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode) and not is_reparse_point(info):
            chmod_reviewed_entry(path, info, stat.S_IMODE(info.st_mode) | 0o700,
                                 directory=True)
    if thaw_parent and materialized.parent != source:
        info = materialized.parent.lstat()
        if stat.S_ISDIR(info.st_mode) and not is_reparse_point(info):
            chmod_reviewed_entry(materialized.parent, info,
                                 stat.S_IMODE(info.st_mode) | 0o700, directory=True)


def checkout_reviewed_entrypoint(source, sha, entry, env, log):
    """Materialize executable source only after actual Git ancestry verification."""
    # Compare canonical paths on both sides: macOS temporary directories and
    # Windows short-path aliases can otherwise make a valid child look external.
    # The entrypoint itself must still be a regular, non-symlink file inside it.
    source = Path(source).resolve(strict=True)
    git_env = {**env, 'GIT_NO_REPLACE_OBJECTS': '1'}
    invoke(['git', 'merge-base', '--is-ancestor', sha, 'refs/remotes/origin/reviewed'], source, git_env, log)
    # Git can materialize a tracked symlink as an ordinary file on hosts with
    # core.symlinks=false. Check the reviewed tree mode as well as the filesystem.
    tree_record = capture(['git', 'ls-tree', '-z', sha, '--', entry.as_posix()], source, git_env)
    record, _, extra = tree_record.partition('\0')
    header, separator, path = record.partition('\t')
    fields = header.split()
    if (extra or not separator or path != entry.as_posix() or len(fields) != 3
            or fields[0] not in ('100644', '100755') or fields[1] != 'blob'):
        raise ValueError('Unsafe entrypoint')
    invoke(['git', 'sparse-checkout', 'init', '--cone'], source, git_env, log)
    checkout_directory = entry.parent.parent
    if not checkout_directory.parts:
        checkout_directory = entry.parent
    invoke(['git', 'sparse-checkout', 'set', checkout_directory.as_posix()],
           source, git_env, log)
    invoke(['git', 'checkout', '--quiet', '--detach', sha], source, git_env, log)
    verify_reviewed_worktree(source, sha, entry.parent, git_env, log)
    freeze_reviewed_worktree(source, entry.parent, freeze_parent=True)
    verify_reviewed_worktree(source, sha, entry.parent, git_env, log, require_read_only=True)
    script = source.joinpath(*entry.parts)
    if script.is_symlink() or not script.is_file() or not script.resolve().is_relative_to(source):
        raise ValueError('Unsafe entrypoint')
    return script


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
    if not isinstance(selected, str) or (selected != 'all' and selected not in BUILD_TARGETS):
        raise ValueError('Invalid verification target')
    windows_preview = request.pop('preview_windows_self_sign', False)
    allowed_fields = {'source_sha', 'version', 'build_number', 'ios_action',
                      'automatic_release', 'include_selfhost', 'build_only'}
    if set(request) - allowed_fields:
        raise ValueError('Unexpected release request field')
    request.setdefault('source_sha', '')
    request.setdefault('version', '0.1.0')
    request.setdefault('ios_action', 'skip')
    request.setdefault('automatic_release', False)
    request.setdefault('include_selfhost', True)
    required_fields = {'source_sha', 'version', 'build_number', 'ios_action',
                       'automatic_release', 'include_selfhost'}
    if not required_fields.issubset(request):
        raise ValueError('Incomplete release request')
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
    automatic_release = request.get('automatic_release', False)
    if not isinstance(automatic_release, bool):
        raise ValueError('automatic_release must be a boolean')
    if automatic_release and (build_only or request.get('ios_action') != 'submit'):
        raise ValueError('Automatic release requires a full Apple submission')
    include_selfhost = request.get('include_selfhost', True)
    if not isinstance(include_selfhost, bool):
        raise ValueError('include_selfhost must be a boolean')
    if not build_only and not include_selfhost:
        raise ValueError('Full releases require the self-hosted kit')

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
    if sha and int(sha, 16) == 0:
        raise ValueError('Invalid source revision')
    if not sha and not allow_missing_source_sha:
        raise ValueError('A source revision must be resolved before building')
    request['source_sha'] = sha.lower()
    if approved_sha is not None and request['source_sha'] != approved_sha:
        raise ValueError('The resolved release source differs from the reviewed source')

    version = request.get('version') or '0.1.0'
    if (not isinstance(version, str)
            or not re.fullmatch(r'(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})', version)):
        raise ValueError('Version components must use at most four digits')
    request['version'] = version

    build_number = request.get('build_number', '')
    if not isinstance(build_number, str):
        raise ValueError('Invalid build number')
    if not re.fullmatch(r'[1-9][0-9]{0,3}', build_number):
        raise ValueError('App builds require a build number from 1-9999')
    request['build_number'] = build_number
    if set(request) not in (required_fields, required_fields | {'build_only'}):
        raise ValueError('Release request does not match the source contract')
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


@contextmanager
def cleanup_on_termination():
    previous = signal.getsignal(signal.SIGTERM)

    def exit_for_cleanup(signum, _frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, exit_for_cleanup)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def main():
    # Public Actions are observable. These checks are in addition to, not a
    # replacement for, environment reviewers and default-branch protections.
    env = os.environ.copy()
    recipient = env.pop('DIAGNOSTICS_PUBLIC_KEY', '')
    submodule_token = env.pop('SOURCE_SUBMODULE_TOKEN', '')
    env['BUILD_CONFIG'] = env.get('BUILD_CONFIG') or '{}'
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
        raw_request = required(env, 'RELEASE_REQUEST')
        request = json.loads(raw_request)
        validate_target_request(target, request)
        env['RELEASE_REQUEST'] = task_request(
            raw_request, resolved=resolved,
            allow_missing_source_sha=(target == 'resolve'))
        repo, branch, entry, sha = validate(env)
        validate_integration_authority(env, repo, branch)
    except Exception:
        print('Release configuration is incomplete or invalid. Contact the maintainer.')
        return 1
    env['STORAGE_CONFIG'] = env.get('STORAGE_CONFIG') or '{}'
    os.umask(0o077)
    with cleanup_on_termination(), tempfile.TemporaryDirectory(
            prefix='private-task-', dir=required(env, 'RUNNER_TEMP')) as temp:
        # Resolve Windows short-name aliases before checking containment.
        root = Path(temp).resolve()
        key, hosts = root / 'identity', root / 'known_hosts'
        key.write_text(env.pop('SOURCE_DEPLOY_KEY').replace('\r\n', '\n').rstrip() + '\n', encoding='utf-8', newline='\n')
        hosts.write_text(env.pop('SOURCE_KNOWN_HOSTS').replace('\r\n', '\n').rstrip() + '\n', encoding='utf-8', newline='\n')
        key.chmod(0o600)
        hosts.chmod(0o600)
        source = root / 'source'
        source.mkdir()
        env.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_NO_REPLACE_OBJECTS='1')
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
        env['RELEASE_INTEGRATION_SOURCE_REPOSITORY'] = repo
        env['RELEASE_INTEGRATION_SOURCE_REF'] = 'refs/heads/' + branch
        env['RELEASE_STATUS_FILE'] = str(root / 'status.json')
        stage = 'checkout-initialization'
        try:
            with (root / 'bootstrap.log').open('wb') as log:
                invoke(['git', 'init', '-q'], source, env, log)
                invoke(['git', 'config', 'core.autocrlf', 'false'], source, env, log)
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
                stage = 'entrypoint-check'
                script = checkout_reviewed_entrypoint(source, sha, entry, env, log)
                # The private entrypoint expands its checkout, installs pinned tools,
                # performs release tasks and retains diagnostics only in private storage.
                stage = 'private-task'
                task_command = [sys.executable, '-I', '-B', str(script)]
                task_env = private_task_environment(env, submodule_token)
                verify_reviewed_worktree(source, sha, entry.parent, env, log,
                                         require_read_only=True)
                invoke(task_command, source, task_env, log,
                       timeout=21600 if target == 'integration' else 10800 if target == 'installation' else 10000)
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
                thaw_reviewed_worktree(source, entry.parent, thaw_parent=True)
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
