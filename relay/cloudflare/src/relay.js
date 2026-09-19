// Free Self-Hosted Native Connector Relay.
// Uses the local standalone relay-core.js module.
// Runs completely in-memory with ZERO database dependencies and simple token-based auth.

import { RelayCore, nativeConnectorRoute, nativeConnectorId, json, scopeId, OPEN } from './relay-core.js';
import { timingSafeEqual, isValidRelayToken } from './security.js';

export { nativeConnectorRoute, nativeConnectorId };

export class FreeNativeConnectorRelay extends RelayCore {
  constructor(state, env) {
    super(state, env);
    this.isFreeRelay = true;
  }

  authorizeWorkload(request) {
    const expected = this.env.RELAY_AUTH_TOKEN || '';
    if (!expected || !isValidRelayToken(expected)) return false;
    const supplied =
      request.headers.get('x-workload-token') ||
      new URL(request.url).searchParams.get('token');
    if (!supplied || !isValidRelayToken(supplied)) return false;
    return timingSafeEqual(supplied, expected);
  }

  async authorizeRegistration(connectorId, hello, agent) {
    // Free relay does not query a central database.
    // Authentication is verified at connection time via RELAY_AUTH_TOKEN.
    // The agent lease is dynamically created in-memory.
    const record = {
      connector_id: hello.connector_id,
      tenant_id: hello.tenant_id,
      target_id: 'default',
      hostname: '127.0.0.1',
      port: 22,
      public_key: '',
      status: 'enrolled',
      subject_id: 'free-self-host',
    };
    return record;
  }

  async authorizeDial(connectorId, request) {
    const now = Math.floor(Date.now() / 1000);
    if (
      !request ||
      !scopeId(connectorId) ||
      request.connector_id !== connectorId ||
      !scopeId(request.tenant_id) ||
      !scopeId(request.subject_id) ||
      !scopeId(request.target_id) ||
      typeof request.hostname !== 'string' ||
      !request.hostname ||
      request.hostname.length > 253 ||
      !Number.isInteger(request.port) ||
      request.port < 1 ||
      request.port > 65535 ||
      !Number.isSafeInteger(request.expires_at_epoch) ||
      request.expires_at_epoch <= now
    ) {
      return json({ error: 'invalid_connector_scope' }, 400);
    }

    const agent = this.agents.get(connectorId);
    if (
      !agent ||
      agent.closed ||
      agent.socket.readyState !== OPEN ||
      agent.lastSeen + 90_000 <= Date.now()
    ) {
      return json({ error: 'connector_unavailable' }, 503);
    }

    if (request.expires_at_epoch <= Math.floor(Date.now() / 1000)) {
      return json({ error: 'connector_request_expired' }, 403);
    }

    // Return immutable snapshot of authorized dial parameters without mutating agent.record
    const destination = Object.freeze({
      target_id: request.target_id,
      hostname: request.hostname,
      port: request.port,
    });

    return {
      agent,
      destination,
      accountId: agent.record?.subject_id || 'free-self-host',
      policyEpoch: agent.record?.policy_epoch || 1,
    };
  }

  authorizeTurnCredentials(_authorized, request) {
    // Shared-token holders form one trusted owner group in free deployments.
    // Individual peer identity and target authorization are still verified by
    // the native peer transport before opening the destination TCP stream.
    return this.isFreeRelay && ['client', 'agent'].includes(request.peer);
  }

  async onStreamBytes(accountId, stream, byteLength) {
    // No OmniTerm billing; the owner's Cloudflare account limits still apply.
  }
}
