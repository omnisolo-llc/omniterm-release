// Free Self-Hosted Cloudflare Worker Relay Entrypoint.
// Zero database dependencies, single pre-shared token authentication.

import {
  FreeNativeConnectorRelay,
  nativeConnectorRoute,
  nativeConnectorId,
} from './relay.js';
import { timingSafeEqual, validateRelayToken } from './security.js';
import {
  authorizedBrowserOrigin,
  browserTicketCorsHeaders,
  browserTicketPreflight,
  parseBrowserTicketProtocols,
} from './browser-tickets.js';

const browserTicketRoute = /^\/internal\/v1\/connectors\/[A-Za-z0-9_-]{1,128}\/browser-ticket$/;
const browserDialRoute = /^\/internal\/v1\/connectors\/[A-Za-z0-9_-]{1,128}\/dial-stream$/;
const bearerToken = request => /^Bearer ([\x21-\x7e]{10,256})$/.exec(request.headers.get('authorization') || '')?.[1] ?? null;

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

    if (browserTicketRoute.test(url.pathname) && request.method === 'OPTIONS') {
      if (url.search) {
        return Response.json({ error: 'relay_credentials_require_headers' }, {
          status: 400, headers: { 'cache-control': 'no-store' },
        });
      }
      return browserTicketPreflight(request, env);
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

      const ticketRoute = browserTicketRoute.test(url.pathname);
      const offeredTicket = parseBrowserTicketProtocols(request.headers.get('sec-websocket-protocol'));
      const browserOrigin = ticketRoute || offeredTicket.present
        ? authorizedBrowserOrigin(request, env)
        : null;
      const token = ticketRoute ? bearerToken(request) : request.headers.get('x-workload-token');

      if (url.searchParams.has('token')) {
        return Response.json({ error: 'header_authentication_required' }, {
          status: 401, headers: { 'cache-control': 'no-store' },
        });
      }
      if (ticketRoute && (request.method !== 'POST' || !browserOrigin || !token || request.body)) {
        return Response.json({ error: 'browser_ticket_unauthorized' }, {
          status: 401, headers: browserOrigin ? browserTicketCorsHeaders(browserOrigin) : { 'cache-control': 'no-store' },
        });
      }
      if (offeredTicket.present && (!offeredTicket.valid || !browserDialRoute.test(url.pathname) ||
          request.method !== 'GET' || request.headers.get('upgrade')?.toLowerCase() !== 'websocket' ||
          !browserOrigin)) {
        return Response.json({ error: 'invalid_browser_ticket' }, {
          status: 401, headers: { 'cache-control': 'no-store' },
        });
      }
      if (!token && !offeredTicket.present) {
        return Response.json(
          {
            error: 'unauthorized',
            message: 'Relay token must be provided via the x-workload-token header.',
          },
          { status: 401, headers: { 'cache-control': 'no-store' } }
        );
      }

      const clientTokenError = token ? validateRelayToken(token) : null;
      if (clientTokenError) {
        return Response.json(
          {
            error: 'unauthorized',
            message: `Invalid relay token format: ${clientTokenError}`,
          },
          { status: 401, headers: { 'cache-control': 'no-store' } }
        );
      }

      if (token && !timingSafeEqual(token, expected)) {
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

      let relayRequest = request;
      if ((ticketRoute || offeredTicket.present) && browserOrigin) {
        const headers = new Headers(request.headers);
        if (ticketRoute) {
          headers.delete('authorization');
          headers.set('x-workload-token', token);
        }
        headers.set('x-native-relay-browser-origin', browserOrigin);
        relayRequest = new Request(request, { headers });
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
      return env.NATIVE_CONNECTORS.get(doId).fetch(relayRequest);
    }

    return Response.json(
      { error: 'not_found' },
      { status: 404, headers: { 'cache-control': 'no-store' } }
    );
  },
};
