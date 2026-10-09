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
- `bash ci/native-release/run.sh windows-sdk`: acquire the resolved application
  source, produce its reviewed Windows SDK, retain it in the selected private
  pipeline store, and export the complete source manifest SHA-256 after retention.
- `bash ci/native-release/run.sh retain-artifacts`: reacquire the exact approved
  application or agent source, freeze and test its native tools, and retain only
  the fixed encrypted diagnostic/evaluation output or verified agent candidate.
  The uploader receives a narrow storage environment and captured pipeline identity.

All six build targets and all existing full-release stages are supported.
Unknown targets, commands and request fields fail. There is no interpreter
fallback or legacy source-entrypoint input. The only executable build entry is
tools/release-cli/Cargo.toml.

## Application trust-key prerequisite and phase reporting

Every application build, including unsigned build-only verification, requires
`OMNITERM_VPN_PROVIDER_PUBLIC_KEY`. It must be the reviewed public trust key for
the deployed managed VPN provider, encoded as canonical unpadded base64url for
32 nonzero bytes. Configure the existing workflow variable with that public
value; never substitute a generated key or a test fixture. The launcher checks
its format before checkout or private compilation and names only the missing or
invalid variable, never its contents. This format check is not provider identity
or signature verification. Source resolution and standalone-agent builds do
not gain an application-only requirement.

Public phase markers distinguish `native-toolchain`, `native-tool-tests`,
`native-tool-build`, and `private-task`. A nonzero private task exit is reported
as a task failure after the CLI compilation/tests passed, not a compiler error.
The private task can reject configuration before writing its own status file.
Compiler output, application output, and diagnostic contents remain private.
A passing launcher test or compilation does not certify application execution,
signing, installation, or release publication.

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
preview selection sets WINDOWS_PREVIEW_OUTPUT_DIR only for the explicitly requested
build-only Windows preview. The private producer must create the actual preview ZIP
and SHA-256 pair. The launcher then recipient-encrypts that ZIP and removes the
plaintext pair before the separate retention step runs. Preview retention cannot
satisfy a production signing or publication gate.

For an explicitly selected self-signed Windows release, `BUILD_CONFIG` carries
`"OMNI_WINDOWS_SELF_SIGNED":"true"`; only string values `"true"` and `"false"`
are accepted, and an absent flag defaults to false. The native publication plan
may include a `windows_self_signed` boolean, also defaulting to false. The
launcher requires those values to agree and binds the selection to its reviewed
publication identity. Existing plans with an absent or false flag retain their
normal release notes and identity. A true flag adds disclosure of self-signed
Authenticode, Windows trust warnings, and detached OpenPGP package verification.
The Windows ZIP includes only the public DER `omniterm-windows-signing.cer`, not
private key material. Native certificate identity, timestamp, catalog, package
signature, same-run identity, and complete byte/hash readback checks remain required.

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
existing Node RSA/AES-GCM sealer; sealed output is retained in selected private
storage. The public workflows use no Actions artifact or cache storage. The
application storage configuration has kind `application`; the agent workflow uses
the separate `OMNI_AGENT_STORAGE_CONFIG` with kind `agent`. SDK, evaluation and agent
candidate manifests expire after seven days; diagnostic manifests expire after one.
Identical retries adopt the original immutable expiry. Expiry does not itself
delete a private asset; candidate cleanup remains a separate guarded operation.
Python and pip executables are refused by the native process runner.

Failed subprocesses retain their numeric exit code (or Unix signal) in the public
log. Bounded hints may identify compiler, test, dependency-download, storage-full,
child-killed, or build-script failures and at most eight distinct Rust error
codes. Raw output, private paths, test names, command arguments and configuration
are never copied into that summary. A signal or matching log hint alone does not
establish an out-of-memory diagnosis. The original failure still propagates;
these summaries neither rerun a stage nor satisfy any release gate.

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
