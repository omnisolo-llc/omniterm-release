# OmniTerm

This is the public home for **OmniTerm downloads** and the **self-hosted Cloudflare relay**.
The relay source is available here. The desktop and mobile application's source is not.

## Run your own relay

The relay lets compatible OmniTerm clients and agents connect through your own
Cloudflare account. You control the deployment and its shared access token.
It does not require an OmniTerm backend database or access to our build system.

**[Follow the Cloudflare setup guide →](relay/cloudflare/README.md)**

You need a Cloudflare account and Node.js 22 or newer. The guide covers installing
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

The release path runs the complete source gates, stages every required download in
a draft, verifies downloaded bytes and SHA-256 checksums, and requires a matching
Apple delivery receipt before publication. Missing, stale, wrong-platform, or
unexpected artifacts prevent publication. All jobs use one source revision resolved
before validation and builds begin.
Private build and delivery receipts are removed before the release becomes public.

Private object storage is optional for desktop/Android diagnostics but is required
for the existing Apple signing, diagnostic retention, and upload-intent safeguards.
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
