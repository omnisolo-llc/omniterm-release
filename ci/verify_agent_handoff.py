#!/usr/bin/env python3
"""Check the public builder's complete, immutable native package handoff.

This verifies inventory and bytes, not platform signing or installation. Those
remain protected release gates in the source repository.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat

PLATFORMS = ('linux-x86_64', 'linux-aarch64', 'darwin-x86_64',
             'darwin-aarch64', 'windows-x86_64', 'windows-aarch64')
MAX_BYTES = 512 * 1024 * 1024


def package_names(version, platform):
    if (not isinstance(version, str) or not re.fullmatch(
            r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version)
            or platform not in PLATFORMS):
        raise ValueError('Invalid package version or native platform')
    if any(int(value) > bound for value, bound in zip(version.split('.'), (255, 255, 65535))):
        raise ValueError('Package version exceeds MSI bounds')
    stem = f'omniterm-agent-v{version}-{platform}'
    raw = 'omniterm-agent-' + platform
    names = {raw + ('.exe' if platform.startswith('windows-') else ''), stem + '.zip'}
    if platform.startswith('windows-'):
        names.update((stem + '.msi', stem + '-setup.exe'))
    elif platform.startswith('darwin-'):
        names.update((stem + '.tar.gz', stem + '.pkg', stem + '.dmg'))
    else:
        deb, rpm = ('amd64', 'x86_64') if platform.endswith('x86_64') else ('arm64', 'aarch64')
        names.update((stem + '.tar.gz', f'omniterm-agent_{version}_{deb}.deb',
                      f'omniterm-agent-{version}-1.{rpm}.rpm'))
    return names


def payload_names(platform):
    names = {'README.txt', 'LICENSE.txt'}
    return names | ({'omniterm-agent.exe'} if platform.startswith('windows-')
                    else {'omniterm-agent', 'omniterm-task-runner'})


def regular(path, maximum=MAX_BYTES):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or not 0 < info.st_size <= maximum):
        raise ValueError('Package handoff must contain bounded, unlinked regular files')
    return info


def valid_record(value, fields):
    return (isinstance(value, dict) and set(value) == fields
            and type(value.get('size')) is int and 0 < value['size'] <= MAX_BYTES
            and isinstance(value.get('sha256'), str)
            and re.fullmatch('[a-f0-9]{64}', value['sha256']) is not None)


def unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Package manifest contains duplicate object keys')
        value[key] = item
    return value


def verify(directory, version, platform):
    expected = package_names(version, platform)
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Package handoff must be a real directory')
    manifest_name = 'packages-' + platform + '.json'
    if {path.name for path in directory.iterdir()} != expected | {manifest_name}:
        raise ValueError('Incomplete package formats or unexpected private handoff files')
    manifest_path = directory / manifest_name
    regular(manifest_path, 65536)
    value = json.loads(manifest_path.read_bytes(), object_pairs_hook=unique_json_object)
    if (not isinstance(value, dict) or set(value) != {
            'schema', 'version', 'platform', 'platform_signing', 'payload', 'assets'}
            or type(value['schema']) is not int or value['schema'] != 1
            or value['version'] != version or value['platform'] != platform
            or value['platform_signing'] != 'not_performed'):
        raise ValueError('Package manifest identity or signing status differs from the build')
    payload = value['payload']
    if (not isinstance(payload, dict) or set(payload) != payload_names(platform)
            or not all(valid_record(row, {'size', 'sha256'}) for row in payload.values())):
        raise ValueError('Unexpected package payload metadata')
    rows = value['assets']
    if (not isinstance(rows, list) or len(rows) != len(expected)
            or not all(valid_record(row, {'name', 'size', 'sha256'}) for row in rows)
            or any(not isinstance(row['name'], str) for row in rows)
            or {row['name'] for row in rows} != expected):
        raise ValueError('Package manifest does not cover every required format exactly once')
    for row in rows:
        path = directory / row['name']
        before = regular(path)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = regular(path)
        if (before.st_size != row['size'] or digest != row['sha256']
                or (before.st_ino, before.st_mtime_ns, before.st_size) !=
                   (after.st_ino, after.st_mtime_ns, after.st_size)):
            raise ValueError('Package bytes changed or differ from the native build manifest')
    if {path.name for path in directory.iterdir()} != expected | {manifest_name}:
        raise ValueError('Package inventory changed during verification')
    return len(expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--platform', choices=PLATFORMS, required=True)
    args = parser.parse_args()
    count = verify(args.directory, args.version, args.platform)
    print(f'Verified {count} native package hashes for {args.platform}; signing and installation remain separate gates.')


if __name__ == '__main__':
    main()
