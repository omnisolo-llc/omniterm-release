import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../src/index.js';
import { FreeNativeConnectorRelay } from '../src/relay.js';
import { nativeConnectorId } from '../src/relay-core.js';
import { validateRelayToken } from '../src/security.js';
import { encodeFrame, FrameDecoder } from '../src/connector-wire.js';
import {
  consumeBrowserTicket,
  issueBrowserTicket,
  parseBrowserTicketProtocols,
} from '../src/browser-tickets.js';

// Synthetic test-only tokens, never deployment credentials.
const token = '00000000-0000-4000-8000-000000000001';
const other = '00000000-0000-4000-8000-000000000002';
const base = 'https://relay.example/v1/connectors/stream?connector_id=test';
const tick = () => new Promise(resolve => setImmediate(resolve));

test('health is reachable without revealing configuration', async () => {
  const response = await worker.fetch(new Request('https://relay.example/healthz'), {});
  assert.equal(response.status, 200);
  assert.equal(await response.text(), 'OK');
});
test('missing server token fails closed', async () => {
  assert.equal((await worker.fetch(new Request(base), {})).status, 500);
});
test('missing or wrong client token fails closed', async () => {
  for (const headers of [{}, {'x-workload-token': other}]) {
    assert.equal((await worker.fetch(new Request(base, {headers}), {RELAY_AUTH_TOKEN: token})).status, 401);
  }
});
test('header authentication routes to the scoped object and query tokens fail closed', async () => {
  let routed;
  const env = {RELAY_AUTH_TOKEN: token, NATIVE_CONNECTORS: {
    idFromName: value => { routed = value; return value; },
    get: () => ({fetch: () => new Response('scoped', {status: 200})})
  }};
  assert.equal((await worker.fetch(new Request(base, {headers: {'x-workload-token': token}}), env)).status, 200);
  assert.equal(routed, JSON.stringify(['free-v1','test']));
  for (const request of [
    new Request(base + '&token=' + token),
    new Request(base + '&token=' + token, {headers: {'x-workload-token': token}}),
  ]) {
    assert.equal((await worker.fetch(request, env)).status, 401);
  }
});
test('public relay kit keeps RTC unavailable even when RTC settings are present', async () => {
  let allocated = false;
  const response = await worker.fetch(new Request(
    'https://relay.example/internal/v1/connectors/test/rtc-offer', {
      method: 'POST',
      headers: { 'x-workload-token': token },
      body: '{}',
    }), {
      RELAY_AUTH_TOKEN: token,
      NATIVE_PUBLIC_RTC_ENABLED: 'true',
      NATIVE_RTC_INGRESS: 'https://rtc.example.test',
      NATIVE_RTC_SERVICE_TOKEN: 'Free-Relay-RTC-Service-Token-2026!',
      NATIVE_CONNECTORS: {
        idFromName: () => { allocated = true; return 'fixture'; },
        get: () => ({fetch: () => new Response('scoped', {status: 200})}),
      },
    });
  assert.equal(response.status, 404);
  assert.equal((await response.json()).error, 'not_found');
  assert.equal(allocated, false);
});
test('browser ticket preflight rejects query strings without reflecting them', async () => {
  const response = await worker.fetch(new Request(
    'https://relay.example/internal/v1/connectors/test/browser-ticket?token=synthetic-secret', {
      method: 'OPTIONS',
      headers: {
        origin: 'https://app.example',
        'access-control-request-method': 'POST',
        'access-control-request-headers': 'authorization,content-type',
      },
    }), {NATIVE_RELAY_BROWSER_ORIGINS: 'https://app.example'});
  assert.equal(response.status, 400);
  const body = await response.text();
  assert(!body.includes('synthetic-secret'));
  assert.equal(response.headers.get('access-control-allow-origin'), null);
});
test('browser ticket handshakes are bound to one connector and consumed once', async () => {
  const values = new Map(); let tail = Promise.resolve();
  const storage = {transaction(fn) {
    const next = tail.then(() => fn({
      get: async key => structuredClone(values.get(key)),
      put: async (key, value) => values.set(key, structuredClone(value)),
      delete: async key => values.delete(key),
    }));
    tail = next.catch(() => {});
    return next;
  }};
  const relay = new FreeNativeConnectorRelay({storage}, {
    RELAY_AUTH_TOKEN: token,
    NATIVE_RELAY_BROWSER_ORIGINS: 'https://app.example',
  });
  const issued = await relay.fetch(new Request('https://relay.example/internal/v1/connectors/test/browser-ticket', {
    method: 'POST',
    headers: {
      origin: 'https://app.example',
      'x-workload-token': token,
    },
  }));
  assert.equal(issued.status, 200);
  const ticket = await issued.json();
  assert(ticket.expires_at_epoch <= Math.floor(Date.now() / 1000) + 30);
  const headers = {
    origin: 'https://app.example', upgrade: 'websocket',
    'sec-websocket-protocol': ticket.websocket_protocols.join(', '),
  };
  const wrongScope = await relay.authorizeWorkload(new Request(
    'https://relay.example/internal/v1/connectors/other/dial-stream', {headers}));
  assert.equal(wrongScope, false);
  const request = new Request('https://relay.example/internal/v1/connectors/test/dial-stream', {headers});
  assert.equal(await relay.authorizeWorkload(request), true);
  assert.equal(await relay.authorizeWorkload(request), false);
});
test('browser tickets are origin and connector bound, short-lived and consumed once', async () => {
  const values = new Map(); let tail = Promise.resolve();
  const storage = {transaction(fn) {
    const next = tail.then(() => fn({
      get: async key => structuredClone(values.get(key)),
      put: async (key, value) => values.set(key, structuredClone(value)),
      delete: async key => values.delete(key),
    }));
    tail = next.catch(() => {});
    return next;
  }};
  const relay = new FreeNativeConnectorRelay({storage}, {
    RELAY_AUTH_TOKEN: token,
    NATIVE_RELAY_BROWSER_ORIGINS: 'https://app.example',
  });
  const response = await relay.fetch(new Request('https://relay.example/internal/v1/connectors/test/browser-ticket', {
    method: 'POST',
    headers: {
      origin: 'https://app.example',
      'x-workload-token': token,
    },
  }));
  assert.equal(response.status, 200);
  const value = await response.json();
  assert.equal(value.expires_at_epoch <= Math.floor(Date.now() / 1000) + 30, true);
  assert.ok(value.websocket_protocols.length === 2 && value.websocket_protocols[0] === 'omni-relay.v1');
  assert.equal(value.ticket.length, 43);
  assert.ok(parseBrowserTicketProtocols(value.websocket_protocols.join(',')).ticket === value.ticket);
  const request = new Request('https://relay.example/internal/v1/connectors/test/dial-stream', {
    headers: {
      origin: 'https://app.example',
      upgrade: 'websocket',
      'sec-websocket-protocol': value.websocket_protocols.join(', '),
    },
  });
  assert.equal(await relay.authorizeWorkload(request), true);
  assert.equal(await relay.authorizeWorkload(request), false);
});
test('self-hosted browser ticket issuance does not trust a forged forwarded Origin', async () => {
  const values = new Map();
  const storage = {transaction: async fn => fn({
    get: async key => structuredClone(values.get(key)),
    put: async (key, value) => values.set(key, structuredClone(value)),
    delete: async key => values.delete(key),
  })};
  const relay = new FreeNativeConnectorRelay({storage}, {
    RELAY_AUTH_TOKEN: token,
    NATIVE_RELAY_BROWSER_ORIGINS: 'https://app.example',
  });
  const response = await relay.fetch(new Request(
    'https://relay.example/internal/v1/connectors/test/browser-ticket', {
      method: 'POST',
      headers: {
        origin: 'https://attacker.example',
        'x-native-relay-browser-origin': 'https://attacker.example',
        'x-workload-token': token,
      },
    }));
  assert.equal(response.status, 403);
  assert.equal((await response.json()).error, 'origin_forbidden');
});
test('browser tickets expire at the deadline and concurrent replay consumes only once', async () => {
  const makeStorage = () => {
    const values = new Map(); let tail = Promise.resolve();
    return {transaction(fn) {
      const next = tail.then(() => fn({
        get: async key => structuredClone(values.get(key)),
        put: async (key, value) => values.set(key, structuredClone(value)),
        delete: async key => values.delete(key),
      }));
      tail = next.catch(() => {});
      return next;
    }};
  };

  const expiredStorage = makeStorage();
  const expired = await issueBrowserTicket(expiredStorage, {
    connectorId: 'test', origin: 'https://app.example', now: 1000,
  });
  const expiredRequest = new Request('https://relay.example/internal/v1/connectors/test/dial-stream', {
    headers: {
      origin: 'https://app.example',
      'sec-websocket-protocol': expired.websocket_protocols.join(', '),
    },
  });
  assert.equal((await consumeBrowserTicket(expiredStorage, expiredRequest, 'test', 1030)).valid, false);

  const storage = makeStorage();
  const issued = await issueBrowserTicket(storage, {
    connectorId: 'test', origin: 'https://app.example', now: 2000,
  });
  const request = new Request('https://relay.example/internal/v1/connectors/test/dial-stream', {
    headers: {
      origin: 'https://app.example',
      'sec-websocket-protocol': issued.websocket_protocols.join(', '),
    },
  });
  const attempts = await Promise.all([
    consumeBrowserTicket(storage, request, 'test', 2029),
    consumeBrowserTicket(storage, request, 'test', 2029),
  ]);
  assert.equal(attempts.filter(result => result.valid).length, 1);
});
test('duplicate connector identifiers are rejected', async () => {
  assert.equal(nativeConnectorId(new Request(base + '&connector_id=other')), null);
  const req = new Request(base + '&connector_id=other', {headers: {'x-workload-token': token}});
  assert.equal((await worker.fetch(req, {RELAY_AUTH_TOKEN: token})).status, 400);
});
test('token validation rejects whitespace and weak passwords', () => {
  assert.equal(validateRelayToken(token), null);
  for (const value of ['', 'password', token + '\n', 'abc DEF 123!']) assert.notEqual(validateRelayToken(value), null);
});
test('authenticated free streams can transfer more than handshake budget', async () => {
  const relay = new FreeNativeConnectorRelay({}, {});
  const stream = {authenticated: true, handshakeBytes: 64000};
  let total = 0; const closed = [];
  const send = relay.meteredQueue('self-host', stream, () => true, data => total += data.length, why => closed.push(why));
  for (let i=0; i<16; i++) { send(new Uint8Array(32768)); await tick(); }
  assert.equal(total, 524288);
  assert.deepEqual(closed, []);
  assert.equal(relay.queuedBytes, 0);
});
test('unauthenticated handshake remains bounded', async () => {
  const relay = new FreeNativeConnectorRelay({}, {});
  const stream = {authenticated: false, handshakeBytes: 0};
  let total = 0; const closed = [];
  const send = relay.meteredQueue('self-host', stream, () => true, data => total += data.length, why => closed.push(why));
  send(new Uint8Array(65536)); await tick(); send(new Uint8Array(1)); await tick();
  assert.equal(total, 65536); assert.deepEqual(closed, ['relay_authentication_budget_exceeded']);
});
test('backpressure rejects oversized messages even after authentication', () => {
  const relay = new FreeNativeConnectorRelay({}, {});
  let reason;
  relay.meteredQueue('self-host', {authenticated: true}, () => true, () => assert.fail(), value => reason=value)(new Uint8Array(262145));
  assert.equal(reason, 'relay_backpressure_limit');
});
test('wire codec preserves 64-bit heartbeat values', () => {
  const frames = new FrameDecoder().push(encodeFrame({type:'Ping',value:9007199254740993n}));
  assert.equal(frames[0].value, 9007199254740993n);
});
test('free relay denies datagram routes', async () => {
  const relay = new FreeNativeConnectorRelay({}, {RELAY_AUTH_TOKEN:token});
  const request = new Request('https://relay.example/internal/v1/connectors/test/dial-datagram', {headers:{'x-workload-token':token}});
  assert.equal((await relay.fetch(request)).status,403);
});
