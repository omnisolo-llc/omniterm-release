# OmniTerm

This is the public home for **OmniTerm downloads** and the **self-hosted Cloudflare relay**.
The relay source is available here. The desktop and mobile application's source is not.

## Run your own relay

The relay lets compatible OmniTerm clients and agents connect through your own
Cloudflare account. You control the deployment and its shared access token.
It does not require an OmniTerm backend database or access to our build system.

**[Follow the Cloudflare setup guide →](relay/cloudflare/README.md)**

For browser live-share over MoQ, deploy the first-party HTTP/3 owner from
[relay/moq](relay/moq/README.md) and set `moq_origin` in the web deployment's
`config.json` to that provider's HTTPS origin. Its control API and WebTransport
data plane must use the same origin.

Use Node.js 24.21.0 or newer for the self-hosted relay kit. The Cloudflare guide covers installing
the tools, deploying the Worker, creating a fresh token, and checking connections.
Cloudflare's Free plan is supported within its usage limits; persistent connections
can use up the free allowance. This is a WebSocket relay, not an SSH server or a
STUN/TURN service.

## Download the app

Open **[Releases](https://github.com/omnisolo-llc/omniterm-release/releases)**.
Use the R2 download links in the selected release's notes; the same files remain
under **Assets** as a GitHub fallback.

| Platform | File to look for |
| --- | --- |
| Windows x64 | `omniterm-<version>-windows-x64.zip` — extract the portable app |
| Linux x64 | `omniterm-<version>-linux-x64.tar.gz` — desktop bundle built on Ubuntu 24.04 |
| macOS Apple Silicon (13.5+) | `omniterm-<version>-macos-arm64.zip` — application bundle |
| Android | Universal APK, separate `armeabi-v7a`, `arm64-v8a`, and `x86_64` APKs, and an AAB |
| Native services | `omniterm-<version>-services-{linux-x64,windows-x64,macos-arm64}` archives |
| Linux agent | `omniterm-<version>-agent-linux-amd64.deb` and `omniterm-<version>-agent-linux-x86_64.rpm` |
| Browser | `omniterm-<version>-flutter-web.tar.gz` and `omniterm-<version>-workstation-web.tar.gz` |
| iPhone and iPad | Signed device build delivered privately to App Store Connect; no public IPA |

The release inventory contains **15 downloadable application packages**, plus
checksums and an optional self-hosted relay kit. Docker images are excluded.
Each application download has a SHA-256 sidecar; `SHA256SUMS` covers all 15 packages.
The Windows ZIP is portable, not an installer; its executables and libraries are
Authenticode-signed. The macOS release application is Developer ID signed and
notarized before packaging. Apple delivery does not imply App Store approval or
availability.

**The application downloads are not available until a full release build succeeds.**
A relay-only release contains the relay kit, not the apps. A build-verification run
never publishes files, and an unsigned verification APK is not a release download.

## For maintainers

Run **Actions → Release → Run workflow** on `main`. Build-only runs may leave
the source SHA blank to inspect the latest private branch tip. Before a full
release, review the exact private source commit and approve its SHA through a
reviewed change to `ci/approved_release_source.json` on this protected branch.
Enter that same full SHA in the workflow; a blank, different, or unresolved
approval blocks publication. Every job uses the same resolved commit. The version
defaults to `0.1.0`. Platform job names include a UTC workflow identifier in
`yyyymmddHHmm` format. Supply a fresh shared app build number from `1` to `9999`.
Configure the source secrets for `ql-owo-lp/omniterm`, branch `main`, and
`scripts/release/entrypoint.py`; the launcher rejects a different source identity.
Leave **build_only** enabled and
**ios_action=skip** to check Windows, Linux, macOS, Android, browser bundles, and unsigned iOS device builds
without signing, storage credentials, or publication. Choose **verify_target** to
check one platform or leave it on **all**. Up to six platform jobs run concurrently.
Unsigned verification output is discarded after the job. This checks compilation
and package structure, not the complete source-quality or production signing gates.
Public launcher, encryption, and relay contracts also run on every push and pull request
on Linux, Windows, and macOS without private credentials.

Disable **build_only** only for an actual release with an approved source SHA and the original signing identities
and production application configuration installed in the protected environments.
Set **ios_action=upload** for App Store Connect delivery, or **submit** for review;
a full release cannot skip Apple. Automatic store release remains an explicit opt-in.
Full Apple releases use the same shared Apple-compatible build number.

Private TestFlight processing and installation happen before external acceptance;
they never request App Store submission or automatic public availability. For
`ios_action=submit`, the separate protected `apple_submission` job runs only after
all required external tests succeed. It retains actual App Store Connect version
and build evidence in private storage. Public promotion rechecks that evidence;
missing, foreign, failed or ambiguous submission evidence prevents publication.
A previously attempted version/build must be inspected before retrying rather
than resubmitted blindly.

The release path runs the complete source gates, stages every required download in
a draft, verifies downloaded bytes and SHA-256 checksums, and requires a matching
Apple delivery receipt before publication. Missing, stale, wrong-platform, or
unexpected artifacts prevent publication. All jobs use one source revision resolved
before validation and builds begin.
Private build and delivery receipts are removed before the release becomes public.

Full releases first run genuine application integration on six protected native
and browser runners. Validation acquires all six artifacts from private storage
for that exact source revision, workflow run and attempt. Missing hardware,
incomplete results or mismatched evidence prevent certification. Build-only
verification does not substitute for these application tests.

Before promotion, three additional protected `external-tests` runners execute the
exact Linux, macOS, and Windows acceptance inventory against the frozen candidate.
Configure the `external-tests` environment variables
`OMNI_EXTERNAL_CONFIG_LINUX`, `OMNI_EXTERNAL_CONFIG_MACOS`, and
`OMNI_EXTERNAL_CONFIG_WINDOWS` as paths to private JSON configuration files.
Each file contains only `platform` and `fixtures`. Linux fixture keys are
`storage_test_config_file`, `redirect_url`, `turn_test_env_file`,
`rust_test_environment_file`, and `live_codex_environment_file`. macOS fixture keys
are `ios_remote_host` and `ios_signing_test_env_file`; Windows uses an empty
fixtures object. Configure `OMNI_EXTERNAL_CANDIDATE_GUARD_CONFIG_LINUX`,
`OMNI_EXTERNAL_CANDIDATE_GUARD_CONFIG_MACOS`, and
`OMNI_EXTERNAL_CANDIDATE_GUARD_CONFIG_WINDOWS` as paths to the corresponding
runner's private candidate-guard configuration. Each guard must match its pinned
hash and isolate the verified candidate from external test owners. On Unix
runners, the installed guard and any privilege launcher must be root-owned and
non-writable.

Windows Azure signing runs in the separate protected `external-windows-signing`
environment. Configure `OMNI_EXTERNAL_CANDIDATE_GUARD_CONFIG_FILE` there as the
path to the Windows candidate-guard configuration and provide the
`WINDOWS_SIGNING_CONFIG` secret. That job uses the protected Windows runner,
GitHub OIDC, .NET 8, and the Azure Artifact Signing provider; the Windows
`external-tests` fixture does not carry signing authority.

The Linux environment also needs a read-only `RELEASE_METADATA_READ_TOKEN` for
the real GitHub metadata checks. Keep each configuration file and referenced
fixture file on its protected runner with owner-only access. The Linux storage
fixture must be limited to release-contract test objects, and its redirect URL
must point to a controlled HTTPS endpoint that returns an HTTP redirect. The
macOS runner needs the genuine processed TestFlight build for the candidate's
exact version and build number, plus its configured iOS signing-test host.
Configure `SOURCE_SUBMODULE_TOKEN` with read-only access to the approved source
modules; source preflight requires this secret and the launcher uses it only
while materializing those pinned modules.

The external Linux runner needs a verified Ubuntu archive keyring, current signed
APT metadata, and passwordless `sudo`. The macOS runner needs the genuine
processed TestFlight build for the candidate's exact version and build number.
The Windows runner needs the .NET 8 runtime and access to the configured Azure
Artifact Signing provider. External evidence is stored privately for the exact
workflow attempt; absent runners, provider access, or evidence blocks promotion.

Private object storage is required for this integration evidence and the existing
Apple signing, diagnostic retention, and upload-intent safeguards.
Failed drafts or attempted Apple version/build pairs need review before retrying.
Do not replace an established Android keystore or Apple signing identity to make a build pass.

The generic launcher retrieves a private build script using protected secrets.
It does not print private compiler output or upload source, logs, symbols, or IPA
files in plaintext to public Actions artifacts. Build checks can retain encrypted
log files for one day when a maintainer supplies a diagnostic public key; the
private decryption key stays on the maintainer's machine. Without that key or
private diagnostics storage, temporary logs are discarded. Workflow inputs and job status are public, so review both the
requested revision and workflow before approving an environment.

## License

The public relay and launcher source use the [GPL-3.0 license](LICENSE).
Separately distributed application binaries retain their own license.
# Omni Agent Native Builds

The `Omni Agent Native Release` manual workflow builds the standalone agent on
native Linux and macOS runners for x64 and ARM64. Its exact source revision and
version are pinned in `ci/approved_agent_source.json`. It uses the existing
protected `downloads` environment and read-only source deploy key. Private
source and compiler output are removed after the task; diagnostics are encrypted
for `OMNI_AGENT_DIAGNOSTICS_PUBLIC_KEY`. Only the four actual compiled binaries
are uploaded as distributable artifacts. Signing and download promotion require
the agent lifecycle gates and the production release authority.

After all six candidate builds, protected `installation` jobs execute the actual
produced packages on the corresponding dedicated platform runners. Configure
`OMNI_INSTALL_CONFIG_<PLATFORM>` as a path to that runner's private installation
configuration; it identifies the real upgrade baseline and hardware/browser
prerequisites. The jobs receive draft-download and private evidence storage
credentials, without signing credentials. Installation evidence must identify
the exact candidate bytes and the current workflow run and attempt.

The iOS build retains its signed candidate privately. Apple delivery is a
separate protected `ios_delivery` job after all six installation jobs succeed.
Public promotion additionally requires successful installation and Apple
delivery. Missing native runners, genuine prior packages, OS probes or valid
receipts block promotion. Workflow definitions alone do not demonstrate those
checks have executed. No workflow was triggered during this remediation.
