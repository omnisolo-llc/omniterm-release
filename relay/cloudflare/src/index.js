// Free Self-Hosted Cloudflare Worker Relay Entrypoint.
// Zero database dependencies, single pre-shared token authentication.

import {
  FreeNativeConnectorRelay,
  nativeConnectorRoute,
  nativeConnectorId,
} from './relay.js';
import { timingSafeEqual, validateRelayToken } from './security.js';

export { FreeNativeConnectorRelay as NativeConnectorRelay };

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Health check endpoints
    if (url.pathname === '/healthz' || url.pathname === '/readyz') {
      return new Response('OK', {
        status: 200,
        headers: { 'cache-control': 'no-store' },
      });
    }

    if (nativeConnectorRoute(url.pathname)) {
      const expected = env.RELAY_AUTH_TOKEN;
      const configError = validateRelayToken(expected);
      if (configError) {
        return Response.json(
          {
            error: 'server_misconfiguration',
            message: `Server RELAY_AUTH_TOKEN is invalid: ${configError}`,
          },
          { status: 500, headers: { 'cache-control': 'no-store' } }
        );
      }

      // Validate pre-shared token on both agent stream and client dial stream
      const token =
        request.headers.get('x-workload-token') || url.searchParams.get('token');

      if (!token) {
        return Response.json(
          {
            error: 'unauthorized',
            message:
              'Missing relay token. Provide via x-workload-token header or ?token= query parameter.',
          },
          { status: 401, headers: { 'cache-control': 'no-store' } }
        );
      }

      const clientTokenError = validateRelayToken(token);
      if (clientTokenError) {
        return Response.json(
          {
            error: 'unauthorized',
            message: `Invalid relay token format: ${clientTokenError}`,
          },
          { status: 401, headers: { 'cache-control': 'no-store' } }
        );
      }

      if (!timingSafeEqual(token, expected)) {
        return Response.json(
          {
            error: 'unauthorized',
            message: 'Invalid relay token.',
          },
          { status: 401, headers: { 'cache-control': 'no-store' } }
        );
      }

      const connectorId = nativeConnectorId(request);
      if (!connectorId) {
        return Response.json(
          { error: 'invalid_connector_scope' },
          { status: 400, headers: { 'cache-control': 'no-store' } }
        );
      }

      if (!env.NATIVE_CONNECTORS) {
        return Response.json(
          { error: 'native_relay_unavailable' },
          { status: 503, headers: { 'cache-control': 'no-store' } }
        );
      }

      // Durable Object routing by connectorId
      const doId = env.NATIVE_CONNECTORS.idFromName(
        JSON.stringify(['free-v1', connectorId])
      );
      return env.NATIVE_CONNECTORS.get(doId).fetch(request);
    }

    return Response.json(
      { error: 'not_found' },
      { status: 404, headers: { 'cache-control': 'no-store' } }
    );
  },
};
