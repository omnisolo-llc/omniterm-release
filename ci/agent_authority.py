"""Approve only the pinned, manual, build-only native agent workflow.

This does not grant application-release, signing, installation, or publication
permission. Those retain their separate default-branch and environment gates.
"""
import json
from pathlib import Path
import re
import stat

BUILDER = 'omnisolo-llc/omniterm-release'
SOURCE = 'ql-owo-lp/omniterm'
ENTRYPOINT = 'scripts/release/agent_entrypoint.py'
PLATFORMS = {family + '-' + arch: target
             for family, target in (('linux', 'linux'), ('darwin', 'macos'), ('windows', 'windows'))
             for arch in ('x86_64', 'aarch64')}


def approved_source():
    path = Path(__file__).with_name('approved_agent_source.json')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= 4096:
        raise ValueError('Invalid pinned agent source file')
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or set(value) != {'source_sha', 'source_branch', 'version'}:
        raise ValueError('Invalid pinned agent source fields')
    if not all(isinstance(item, str) for item in value.values()):
        raise ValueError('Invalid pinned agent source types')
    if (not re.fullmatch('[a-f0-9]{40}', value['source_sha'])
            or int(value['source_sha'], 16) == 0
            or not re.fullmatch(r'release/omni-agent-[A-Za-z0-9.-]+', value['source_branch'])
            or '..' in value['source_branch']
            or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value['version'])):
        raise ValueError('Invalid pinned agent identity')
    return value


def validate_agent_build_authority(env, repo, branch):
    pin = approved_source()
    fixed = {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
             'GITHUB_REPOSITORY': BUILDER, 'GITHUB_REF': 'refs/heads/main',
             'GITHUB_WORKFLOW_REF': BUILDER + '/.github/workflows/omni-agent.yml@refs/heads/main',
             'SOURCE_REPOSITORY': SOURCE, 'SOURCE_ENTRYPOINT': ENTRYPOINT,
             'SOURCE_BRANCH': pin['source_branch'], 'RESOLVED_SOURCE_SHA': pin['source_sha']}
    if (any(env.get(key) != value for key, value in fixed.items())
            or repo != SOURCE or branch != pin['source_branch']
            or not re.fullmatch('[a-f0-9]{40}', env.get('GITHUB_SHA', ''))
            or any(not re.fullmatch('[1-9][0-9]*', env.get(key, ''))
                   for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'))
            or env.get('OMNI_AGENT_PLATFORM') not in PLATFORMS
            or env.get('RELEASE_TARGET') != PLATFORMS.get(env.get('OMNI_AGENT_PLATFORM'))):
        raise ValueError('Untrusted native build authority')
    request = json.loads(env.get('RELEASE_REQUEST', 'null'))
    expected = {'source_sha': pin['source_sha'], 'version': pin['version'],
                'build_number': '1', 'ios_action': 'skip', 'build_only': True,
                'automatic_release': False, 'include_selfhost': True}
    if (not isinstance(request, dict) or request != expected
            or any(type(request.get(key)) is not bool
                   for key in ('build_only', 'automatic_release', 'include_selfhost'))):
        raise ValueError('Native builds must match the approved source and cannot publish')
