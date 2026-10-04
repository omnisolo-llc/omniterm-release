# Native public release launcher

Standalone Rust 1.95.0 launcher with a committed lockfile and a thin Bash wrapper.
Only public authority, acquisition, process and handoff code lives here. The
application release implementation stays in the private source repository.

## Commands

- `bash ci/native-release/run.sh validate-request`: validate the canonical workflow,
  optional source SHA and executing builder without source credentials or manual
  SHA variables. GitHub's enforced execution policy authorizes release operators.
- `bash ci/native-release/run.sh approve-source`: retained legacy compatibility
  command and parity tests; the automated release workflow no longer calls it.
- `bash ci/native-release/run.sh resolve`: fetch canonical private main, select its
  latest commit or an explicitly supplied ancestor, and export one frozen source
  identity and UTC workflow identifier. The source gitlink must match the builder.
- `bash ci/native-release/run.sh run`: acquire the approved source closure,
  verify committed bytes/modes/paths/configuration/gitlinks, consume checkout
  credentials, compile and test the locked private CLI, then dispatch the stage.
- `bash ci/native-release/run.sh agent-source`: read and validate the existing
  public agent pin file and export its exact identity.
- `bash ci/native-release/run.sh agent-run`: the separate pinned manual agent
  authority, using the same source protections and private native build/handoff.
- `bash ci/native-release/run.sh verify-agent-handoff --directory PATH --version VERSION --platform PLATFORM`:
  verify the complete six-platform package inventories and SHA-256 file bytes.
  Signing and installation remain separate gates.
- `bash ci/native-release/run.sh verify-source --root PATH --sha SHA`: read-only
  integrity check for an initialized local Git tree and its pinned submodules.
- `bash ci/native-release/run.sh verify-ios`: retained explicit unsigned iOS
  compatibility command; requires macOS, build-only mode and ios_action=skip,
  and dispatches the canonical native run contract.

All six build targets and all existing full-release stages are supported.
Unknown targets, commands and request fields fail. There is no interpreter
fallback. SOURCE_ENTRYPOINT is an optional validated compatibility identifier;
the only executable build entry is tools/release-cli/Cargo.toml.

## Exact Private Handoff

The compiled executable is native-target/release/omni-release (omni-release.exe
on Windows), invoked with argv:

```text
omni-release run --root SOURCE --work-dir WORK
omni-release agent-run --root SOURCE --work-dir WORK
```

RELEASE_TARGET is the canonical existing workflow stage. RELEASE_REQUEST is
strict normalized JSON containing exactly source_sha, version, build_number,
ios_action, automatic_release, include_selfhost and build_only. Public-only
builder_sha, verify_target and preview_windows_self_sign are removed. Windows
preview selection remains the existing scoped WINDOWS_PREVIEW_OUTPUT_DIR.

SOURCE is the canonical verified checkout path. The launcher supplies
PUBLIC_BUILDER_SHA, RELEASE_INTEGRATION_SOURCE_REPOSITORY,
RELEASE_INTEGRATION_SOURCE_REF, PRIVATE_BOOTSTRAP_LOG, PRIVATE_DIAGNOSTIC_LOG and
RELEASE_STATUS_FILE. Workflow identity and protected configuration/evidence,
OIDC and GitHub token variables are forwarded only from the invoking workflow
step through task_environment's named protocol allowlist. RUNNER_TEMP and
allowed OS/tool locations are retained. Isolated HOME/CARGO_HOME and the native
Cargo target directory are retained for the private task.

Agent-run additionally needs OMNI_AGENT_PLATFORM, OMNI_AGENT_OUTPUT and, on
Windows, OMNI_AGENT_WIX. It must produce the exact existing packages-PLATFORM.json
schema and package inventory consumed by verify-agent-handoff. It cannot sign,
install or publish. The agent source branch/SHA/version must equal the existing
approved_agent_source.json identity. Its public-builder gitlink remains pinned
to its approved source, but does not have to equal the current agent builder;
the current-builder binding is mandatory for application release/build jobs.

The coordinator owns those private commands. The public launcher does not
create release receipts or substitute helper-only success for a private stage.

## Protections

Canonical repository/ref/workflow identities and all protected workflow
environments, dependency edges, success conditions, matrices, evidence routes,
publication destinations and existing signing/token assignments are retained.
The agent build step also receives the existing downloads-scoped submodule
credentials to acquire its complete approved source closure.

Source acquisition accepts only the exact public-builder and website gitlinks
and exact canonical URLs. Website authentication uses an exact-URL HTTPS
environment header or the existing read-only base64 SSH key with strict known
hosts and forwarding/agent/config disabled. Tokens never enter argv or local
Git configuration. Checkout secrets are consumed in acquisition, key files are
removed and secrets are excluded before Cargo dependencies or the private task
execute. Reviewed tools and their parent tools directory are frozen using
no-follow handles, verified again before Cargo, after compilation and after
dispatch, and safely thawed for cleanup. Ignored compiler inputs, symlinks,
hardlinks, reparse points, changed modes/bytes, replacement refs, hidden index
changes, extra files, unsafe local config and unapproved cached remotes fail.

Public and private compilation use an environment allowlist and --locked.
Child output is bounded and private; timeouts, excessive output and termination
kill the process group or Windows Job Object, including descendants. Temporary
source, keys, build outputs and plaintext logs are removed. Diagnostics use the
existing Node RSA/AES-GCM sealer; only sealed output is publicly retained.
Python and pip executables are refused by the native process runner.

## Verification And Activation

`TMPDIR=/home/kevin/omniterm/.git/python-removal-20261004/tmp CARGO_BUILD_JOBS=1 bash ci/test-native-release.sh`
runs Rust tests, formatting and clippy with Python/pip commands blocked.
See MIGRATION.md for every legacy test's equivalent native assertions.

Local runner tests use real Git commits/gitlinks/files, actual locked Cargo
builds and a public synthetic CLI. Only canonical remote transport and toolchain
installation are adapted inside those local fixtures. These tests demonstrate
launcher behavior; they are not hardware, signing, provider or release receipts.
The workflow contracts run on Linux, Windows and macOS. The complete acquisition
fixture and POSIX signal/race tests run on Unix; Windows native handle/process
tests run on the Windows contracts job.

Activation still requires reviewed public changes and the private release
source gitlink to bind that exact public builder revision. The existing agent
pin predates this complete native CLI migration and must be deliberately updated
to a reviewed source containing the private CLI and its lockfile. Neither pin
checks nor native-tool requirements are bypassed.
