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
data plane use the same origin.

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
checksums and the required self-hosted relay kit containing the Cloudflare,
native-process, and first-party MoQ relay sources. Docker images are excluded.
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
Configure repository variable `OMNITERM_VPN_PROVIDER_PUBLIC_KEY` as the canonical
unpadded URL-safe base64 encoding of the managed VPN Worker's Ed25519 public key.
It must decode to exactly 32 nonzero bytes. This public key is release metadata;
keep its private signing key in the Worker environment only. Every release task
except input resolution requires the variable and fails closed when it is missing.
The protected `BUILD_CONFIG` secret must be a JSON object with
`"OMNI_ENABLE_VPN":"true"`; all full-release evidence and artifact proofs bind
the managed VPN contract and its provider-key fingerprint. Build-only verification
does not publish and may omit that release setting.
Full public builds do not need desktop tun2socks or Android Hev artifacts. Keep
`OMNI_ENABLE_LEGACY_SOCKS_VPN` unset or `false`; the launcher rejects it for
publishing requests. A separate non-publishing compatibility job may enable it
with its own pinned fixture/runtime inputs. Windows full-device VPN still needs
the pinned `OMNI_WINTUN_WINDOWS_URL` and `OMNI_WINTUN_WINDOWS_SHA256` inputs.
The protected `vpn-installation-linux`, `vpn-installation-macos`,
`vpn-installation-windows`, `vpn-installation-android`, and
`vpn-installation-ios` environments each need the installation job's required
source, build, storage, and diagnostics secrets, plus that platform's device,
install-config, and owner-only profile variables. Each environment must have the
same reviewers and `main` branch restriction as `downloads`. Configure the
owner-only profile variable as `OMNI_VPN_INSTALLATION_PROFILE_<PLATFORM>` for its
matching platform. Set `OMNI_VPN_RECEIVER_TRUSTED_JWKS_JSON` and
`OMNI_VPN_TRUSTED_GATEWAY_POLICIES_JSON` in each environment to the reviewed
receiver signing keys and exact gateway/policy/CIDR trust map. The web installation
job remains in `downloads` and does not produce VPN route evidence. The installation
stage attests each native artifact's 16 route and egress cells to its package hash
across public and behind-NAT gateways, direct and relay underlays, DNS off/on, and
IPv4/IPv6. Web/workstation-web and service/agent packages must prove the full-device
VPN surface is unavailable. Receiver observer credentials remain in the runner's
native Secret Service and are never workflow variables or profile literals.
The protected `installation` and `vpn-container` jobs use `id-token: write` only
for signed same-run VPN evidence; neither receives app-signing configuration.
The protected `vpn-container` environment needs an owner-only
`OMNITERM_VPN_E2E_PROFILE_FILE` and distinct self-hosted labels
`omniterm-release-vpn-kmod-present` and `omniterm-release-vpn-kmod-absent`.
Configure `OMNITERM_VPN_E2E_CLIENT_BASE_IMAGE`, `OMNITERM_VPN_E2E_GATEWAY_IMAGE`,
`OMNITERM_VPN_E2E_RELAY_IMAGE`, `OMNITERM_VPN_E2E_RECEIVER_IMAGE`, and
`OMNITERM_VPN_E2E_DNS_IMAGE` as OCI references pinned to `sha256` digests. The
source task builds the client fixture locally from the exact candidate Linux
package and binds its image ID, base-image digest, helper, installer, native
library, build context, and four service-image digests into private run evidence;
no registry push is used for that candidate image.
Each Linux runner must have Docker and `/dev/net/tun`, with `/sys/module/wireguard`
matching its assigned state. Each executes 16 route cells plus cutoff cases;
together they produce the required 32-cell container matrix. Missing profiles,
runners, or same-run route receipts block iOS delivery and publication.
Protected release tasks require an isolated runner that accepts one job at a time
and is not shared with untrusted jobs. The launcher verifies and marks its import
tree read-only before execution, but those permissions are not immutable against
another process running as the same OS account.
Register dedicated self-hosted runner labels `omniterm-release-linux`,
`omniterm-release-macos`, `omniterm-release-windows`, `omniterm-release-android`,
`omniterm-release-ios`, `omniterm-release-web`, and
`omniterm-release-managed-rtc`. Integration and installation jobs require
attached devices or browser fixtures on those runners; the external acceptance
jobs reuse the Linux, macOS, and Windows runners.
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
Those producer jobs have `id-token: write` only to request short-lived GitHub OIDC
attestations bound to the exact report and execution-log bytes; they receive no
application signing configuration. Keep these jobs behind the protected
`external-tests` environment.
Configure the `external-tests` environment variables
`OMNI_EXTERNAL_CONFIG_LINUX`, `OMNI_EXTERNAL_CONFIG_MACOS`, and
`OMNI_EXTERNAL_CONFIG_WINDOWS` as paths to private JSON configuration files,
and `OMNI_EXTERNAL_CANDIDATE_GUARD_CONFIG_LINUX`,
`OMNI_EXTERNAL_CANDIDATE_GUARD_CONFIG_MACOS`, and
`OMNI_EXTERNAL_CANDIDATE_GUARD_CONFIG_WINDOWS` as paths to each runner's
protected candidate-guard configuration.
Each file contains only `platform` and `fixtures`. Linux requires
`storage_test_config_file`, `redirect_url`, `turn_test_env_file`,
`rust_test_environment_file`, and `live_codex_environment_file`, plus the
owner-only `live_share_provider_environment_file` and
`production_oidc_environment_file` settings files described below. It also
requires an owner-only `managed_rtc_provider_environment_file` containing only
the approved provider environment. Set that JSON file to exactly
`CLOUDFLARE_ACCOUNT_ID`,
`RTC_INGRESS_CLOUDFLARE_ZONE_ID`, `CLOUDFLARE_TURN_KEY_ID`,
`NATIVE_RTC_INGRESS`, `LIVE_SHARE_MOQ_CONTROL_ORIGIN`,
`LIVE_SHARE_MOQ_PUBLIC_ORIGIN`, `LIVE_SHARE_MOQ_USAGE_ORIGIN`,
`MANAGED_VPN_PROVIDER_ID`, `MANAGED_VPN_GATEWAY_ID`,
`MANAGED_VPN_GATEWAY_PUBLIC_KEY`, `MANAGED_VPN_PROVIDER_PUBLIC_KEY`,
`MANAGED_VPN_ENDPOINTS`, `MANAGED_VPN_DNS_SERVERS`,
`MANAGED_VPN_RELAY_REPLICA_IDS`, and
`MANAGED_RTC_APPROVED_COST_CEILING_MICRO_USD`. Do not put provider results or
`MANAGED_RTC_ACCEPTANCE_EVIDENCE` in this file. Its values must match the
`MANAGED_RTC_PROVIDER_ENVIRONMENT_FILE` used by the protected producer exactly.

The separate `managed-rtc-provider` job runs after candidate preparation and
before the Linux external-test job on a dedicated
`omniterm-release-managed-rtc` runner. Configure that protected environment's
`MANAGED_RTC_PROVIDER_ENVIRONMENT_FILE` and
`MANAGED_RTC_IDENTITY_FIXTURE_FILE` variables as paths to owner-only files on the
runner. The provider file must use the exact keys listed above. The identity
fixture is a reusable owner-only template for two disposable account/connector
identities, independent receiver SSH/key configuration paths, a low quota
budget, Cloudflare TURN Analytics access, and an analytics deadline. The
producer injects the current protected source SHA, builder SHA, workflow run and
attempt in memory; do not hard-code those run identities in the fixture. It
builds the reviewed RTC probe, executes the five live cases, obtains a GitHub
OIDC signature over the exact report and log, and stores the bundle only in
private object storage under the source, builder, run and attempt identity.
`external_tests` reads and verifies that same-run object; do not enter acceptance
results in a repository or environment secret, and do not upload the bundle as a
GitHub Actions artifact.

Keep provider values inside those owner-only files. The workflow passes their
paths and grants `id-token: write` to the dedicated producer job; it does not
pass the Cloudflare Analytics token or disposable account sessions as workflow
variables. The fixture JSON has exactly `schema`, `active_identity`,
`quota_identity`, `quota_remaining_bytes`, `quota_attempt_bytes`,
`provider_usage_deadline_seconds`, `api_origin`, and
`CLOUDFLARE_TURN_ANALYTICS_TOKEN`. The identities bind separate disposable
accounts, active sessions, connectors, targets, and owner-only receiver config
paths. The active identity also binds the current registration revision and a
fresh, one-use action-bound step-up token. Each receiver config pins its SSH
identity/known-hosts files and Ed25519 event-signing public key. Protect the
fixture and every referenced file with owner-only permissions.

`OMNI_EXTERNAL_CONFIG_LINUX.fixtures.live_share_provider_environment_file` to
an absolute path to a second JSON file on the Linux runner; protect it with mode `0600`. Replace these placeholders while keeping exactly these six string fields
shown, with both enable flags set to `true`:

```json
{
  "LIVE_SHARE_SFU_ENABLED": "true",
  "CLOUDFLARE_SFU_APP_ID": "...",
  "CLOUDFLARE_SFU_APP_SECRET": "...",
  "LIVE_SHARE_MOQ_ENABLED": "true",
  "CLOUDFLARE_MOQ_ACCOUNT_ID": "32 hexadecimal characters",
  "CLOUDFLARE_MOQ_API_TOKEN": "..."
}
```

The SFU app ID is 1-128 ASCII letters, digits, `_`, or `-`; its secret is
1-4096 printable ASCII characters without spaces. The MoQ API token is 1-8192
printable ASCII characters without spaces. No extra fields are accepted. The
Linux and referenced provider files must remain regular, owner-only files on the
protected runner. macOS requires `ios_remote_host` and
`ios_signing_test_env_file`; Windows requires an empty fixtures object. The Linux
storage fixture must be limited to release-contract test objects, and its redirect
URL must point to a controlled HTTPS endpoint that returns an HTTP redirect. The
provider file supplies the live SFU/MoQ owner. The TURN, Rust, and live
MCP/Codex files provide real provider access for their exact external cases.

The provider values are loaded only into the isolated Playwright owner environment
for the SFU/MoQ live-share cases. Do not add them as runner-wide variables or to
other fixture files. The owner-only provider file is removed after that owner
finishes. Missing or invalid provider configuration prevents the Linux evidence
receipt from completing; `require_all` requires all live provider results, so
Apple submission and public promotion remain blocked.
Azure signing credentials are available only to the separate protected
`external-windows-signing` environment, not the Windows external-test fixture.

Set `OMNI_EXTERNAL_CONFIG_LINUX.fixtures.production_oidc_environment_file` to a
separate owner-only JSON settings file for the production OIDC browser case. It
must contain exactly these ten string fields:
`OMNI_E2E_EDGE_URL`, `OMNI_E2E_IDENTITY_URL`,
`OMNI_E2E_IDENTITY_WORKLOAD_TOKEN`, `OMNI_E2E_OIDC_ISSUER_URL`,
`OMNI_E2E_OIDC_AUTHORIZATION_ENDPOINT`, `OMNI_E2E_OIDC_REDIRECT_URI`,
`OMNI_E2E_OIDC_CLIENT_ID`, `OMNI_E2E_OIDC_EXPECTED_SUBJECT_ID`,
`OMNI_E2E_OIDC_TENANT_ID`, and `OMNI_E2E_OIDC_STORAGE_STATE`. The last value
must point to a second owner-only Playwright storage-state JSON file with an active
session for that provider; the state object may contain only `cookies` and `origins`. Keep the OIDC settings and browser state separate from the SFU/MoQ provider file. Both are sent only to the distinct guarded OIDC owner within the
existing Linux `external_tests` job, never to runner-wide settings or other owners.
Missing or invalid OIDC configuration prevents its receipt and blocks
`require_all`.

The current external gate requires 181 exact case receipts: 38 Python platform or
toolchain cases, 131 Rust cases, one live MCP/Node case, four live relay/provider
cases, one production OIDC browser case, five managed RTC provider cases, and one
Windows signing case. The managed RTC receipts prove a nominated TURN candidate,
bidirectional receiver bytes, owner cutoff and lease/device revocation, byte quota
enforcement, and measured provider cost below the approved ceiling. All receipts
must match the candidate source, builder, run, attempt, and provider environment
before publication.

The Linux environment also needs a read-only `RELEASE_METADATA_READ_TOKEN` for
the real GitHub metadata checks. These configurations supply real provider access
and never replace the candidate, test inventory, or evidence identity. Configure
`SOURCE_SUBMODULE_TOKEN` with read-only access to the approved source modules;
source preflight requires this secret and the launcher uses it only while
materializing those pinned modules.

The external Linux runner needs a verified Ubuntu archive keyring, current signed
APT metadata, passwordless `sudo`, `g++`, `pkg-config` with `gio-unix-2.0`, and
the `x86_64-w64-mingw32-gcc` and `x86_64-w64-mingw32-g++` cross compilers. The
macOS runner needs the genuine processed TestFlight build for the candidate's
exact version and build number. The Windows external-test runner needs the .NET 8
runtime and `x86_64-w64-mingw32-gcc` on `PATH`. A separate protected
`external-windows-signing` job uses the same dedicated Windows runner and the
Azure Artifact Signing provider through OIDC. Configure that environment with the
`OMNI_EXTERNAL_CANDIDATE_GUARD_CONFIG_FILE` variable pointing to its private
candidate-guard configuration and the `WINDOWS_SIGNING_CONFIG` secret. The
signing environment uses its own guard configuration value, separate from the
ordinary `external-tests` platform variables. Its
candidate and signed-output digests are retained as private evidence for the exact
workflow attempt. Missing runner, guard configuration, provider access, or signed
receipt blocks Apple submission and publication.

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

Each candidate guard must match its pinned hash and isolate the verified candidate from external test owners. On Unix runners, the installed guard and any privilege launcher must be root-owned and non-writable.

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
