# Self-hosted Cloudflare relay

The [Cloudflare relay](cloudflare/README.md) connects compatible OmniTerm clients
and agents over WebSockets. Deploy it to your own Cloudflare account and create
your own access token; no private source or maintainer credentials are needed.

The kit contains a Worker, its routing code, a deployment configuration, and tests.
It does not include the desktop/mobile apps, an SSH server, or a STUN/TURN server.

**[Deploy the relay →](cloudflare/README.md)**

The source is licensed under [GPL-3.0](../LICENSE). Cloudflare's service limits
and charges are separate from the software license.
