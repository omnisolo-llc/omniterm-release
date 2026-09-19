import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../src/index.js';
import { FreeNativeConnectorRelay } from '../src/relay.js';
import { nativeConnectorId } from '../src/relay-core.js';
import { validateRelayToken } from '../src/security.js';
import { encodeFrame, FrameDecoder } from '../src/connector-wire.js';

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
test('header and query authentication route only to scoped object', async () => {
  for (const request of [new Request(base, {headers: {'x-workload-token': token}}), new Request(base + '&token=' + token)]) {
    let routed;
    const env = {RELAY_AUTH_TOKEN: token, NATIVE_CONNECTORS: {
      idFromName: value => { routed = value; return value; },
      get: () => ({fetch: () => new Response('scoped', {status: 200})})
    }};
    assert.equal((await worker.fetch(request, env)).status, 200);
    assert.equal(routed, JSON.stringify(['free-v1','test']));
  }
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
