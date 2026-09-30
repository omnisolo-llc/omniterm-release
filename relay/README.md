# Self-hosted Cloudflare relay

The first-party browser live-share MoQT provider is a separate service in
[moq](moq/README.md). It owns real HTTP/3 sessions and provides the protected
token-control endpoint required for authoritative participant cutoff.

The [Cloudflare relay](cloudflare/README.md) connects compatible OmniTerm clients
and agents over WebSockets, with optional native WebRTC signaling through a
separately deployed RTC ingress. Deploy it to your own Cloudflare account and
create your own access token; no private source or maintainer credentials are
needed.

The kit contains a Worker, its routing code, a deployment configuration, and tests.
It does not include the desktop/mobile apps, an SSH server, or a STUN/TURN server.

**[Deploy the relay →](cloudflare/README.md)**

**[Deploy the MoQT provider →](moq/README.md)**

Use Node.js 24.21.0 or newer for the self-hosted relay kit.

The source is licensed under [GPL-3.0](../LICENSE). Cloudflare's service limits
and charges are separate from the software license.
