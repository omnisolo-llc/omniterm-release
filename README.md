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

Open **[Releases](https://github.com/omnisolo-llc/omniterm-release/releases)** and
look under the selected release's **Assets** section.

| Platform | File to look for |
| --- | --- |
| Windows x64 | `omniterm-<version>-windows-x64.zip` — extract the portable app |
| Linux x64 | `omniterm-<version>-linux-x64.tar.gz` — desktop bundle built on Ubuntu 24.04 |
| Android | `omniterm-<version>-android-universal.apk` — release-signed APK |

Each application download has a SHA-256 checksum file. Windows downloads are not
currently Authenticode-signed installers. iOS distribution is separate.

**The application downloads are not available until a full release build succeeds.**
A relay-only release contains the relay kit, not the apps. A build-verification run
never publishes files, and an unsigned verification APK is not a release download.

## For maintainers

Run **Actions → Release → Run workflow** on `main` with a reviewed source revision,
version, and build number. Leave **build_only** enabled to check Windows, Linux,
and Android compilation without signing, storage credentials, or publication.
The Android output in this mode is unsigned and is discarded after the job.
This checks compilation, not the complete release-quality or signing gates.

Disable **build_only** only for an actual release with the required signing and
application configuration in place. That path runs the full source checks, stages
all three downloads in a draft, verifies their checksums, and then publishes them.
R2 is not required for GitHub downloads. Failed drafts need review before retrying.

The generic launcher retrieves a private build script using protected secrets.
It does not print private compiler output or upload source, logs, symbols, or IPA
files to public Actions artifacts. Without private diagnostics storage, temporary
logs are discarded. Workflow inputs and job status are public, so review both the
requested revision and workflow before approving an environment.

## License

The public relay and launcher source use the [GPL-3.0 license](LICENSE).
Separately distributed application binaries retain their own license.
