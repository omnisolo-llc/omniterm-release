import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {request as httpsRequest} from 'node:https';
import {createServer as createTcpServer} from 'node:net';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createMoqRelay} from '../src/index.js';

const run = promisify(execFile);
const controlToken = 'omniterm-test-control-token-9f0d46c0a7e4';

async function freePort() {
  const server = createTcpServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function controlRequest(origin, certificate, path, {body, token = controlToken} = {}) {
  return new Promise((resolve, reject) => {
    const request = httpsRequest(new URL(path, origin), {
      method: 'POST', ca: certificate,
      headers: {'content-type': 'application/json', 'content-length': Buffer.byteLength(JSON.stringify(body)),
        ...(token ? {authorization: `Bearer ${token}`} : {})},
    }, response => {
      const parts = [];
      response.on('data', chunk => parts.push(chunk));
      response.on('end', () => {
        let value;
        try { value = JSON.parse(Buffer.concat(parts).toString('utf8')); }
        catch { value = null; }
        resolve({status: response.statusCode, value});
      });
    });
    request.once('error', reject);
    request.end(JSON.stringify(body));
  });
}

async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), 'omniterm-moq-control-'));
  const certificatePath = join(directory, 'cert.pem');
  const privateKeyPath = join(directory, 'key.pem');
  await run('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes',
    '-keyout', privateKeyPath, '-out', certificatePath, '-days', '1',
    '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1'], {stdio: 'ignore'});
  const certificate = await readFile(certificatePath, 'utf8');
  const port = await freePort();
  const origin = `https://127.0.0.1:${port}`;
  const service = await createMoqRelay({controlHost: '127.0.0.1', dataHost: '127.0.0.1',
    controlPort: port, dataPort: port, publicOrigin: origin, controlToken,
    certificatePath, privateKeyPath, maxRelays: 2, maxSessions: 2});
  t.after(async () => {
    await service.close();
    await rm(directory, {recursive: true, force: true});
  });
  return {origin, certificate, service};
}

test('relay creation and token admission require the control credential and exact room scope', async t => {
  const {origin, certificate} = await fixture(t);
  const unauthorized = await controlRequest(origin, certificate, '/v1/relays', {
    body: {shareId: 'MoqScop1', epoch: 'scope-epoch-0001', expiresAt: Date.now() + 60000}, token: '',
  });
  assert.equal(unauthorized.status, 401);

  const created = await controlRequest(origin, certificate, '/v1/relays', {
    body: {shareId: 'MoqScop1', epoch: 'scope-epoch-0001', expiresAt: Date.now() + 60000},
  });
  assert.equal(created.status, 201);
  assert.equal(created.value.publicOrigin, origin);
  const relayId = created.value.relayId;

  const wrongScope = await controlRequest(origin, certificate, `/v1/relays/${relayId}/tokens`, {
    body: {shareId: 'MoqScop2', epoch: 'scope-epoch-0001', participantId: 'viewer01',
      role: 'subscribe', expiresAt: Date.now() + 30000},
  });
  assert.equal(wrongScope.status, 409);

  const invalidRole = await controlRequest(origin, certificate, `/v1/relays/${relayId}/tokens`, {
    body: {shareId: 'MoqScop1', epoch: 'scope-epoch-0001', participantId: 'viewer01',
      role: 'publish,subscribe', expiresAt: Date.now() + 30000},
  });
  assert.equal(invalidRole.status, 409);

  const token = await controlRequest(origin, certificate, `/v1/relays/${relayId}/tokens`, {
    body: {shareId: 'MoqScop1', epoch: 'scope-epoch-0001', participantId: 'viewer01',
      role: 'subscribe', expiresAt: Date.now() + 30000},
  });
  assert.equal(token.status, 201);
  assert.equal(token.value.participantId, 'viewer01');
  assert.equal(token.value.role, 'subscribe');
  assert.match(token.value.secret, /^[A-Za-z0-9_-]{40,64}$/);
});

test('viewer close is token-scoped and owner close acknowledges the exact remaining leases', async t => {
  const {origin, certificate} = await fixture(t);
  const created = await controlRequest(origin, certificate, '/v1/relays', {
    body: {shareId: 'MoqClos1', epoch: 'close-epoch-0001', expiresAt: Date.now() + 60000},
  });
  const relayId = created.value.relayId;
  const issue = async (participantId, role) => controlRequest(origin, certificate,
    `/v1/relays/${relayId}/tokens`, {body: {shareId: 'MoqClos1', epoch: 'close-epoch-0001',
      participantId, role, expiresAt: Date.now() + 30000}});
  const host = await issue('host01', 'publish');
  const viewer = await issue('viewer01', 'subscribe');
  assert.equal(host.status, 201);
  assert.equal(viewer.status, 201);

  const viewerClosed = await controlRequest(origin, certificate,
    `/v1/relays/${relayId}/sessions/close`, {body: {shareId: 'MoqClos1',
      epoch: 'close-epoch-0001', tokenIds: [viewer.value.tokenId], endRoom: false}});
  assert.equal(viewerClosed.status, 200);
  assert.deepEqual(viewerClosed.value.closedTokenIds, [viewer.value.tokenId]);

  const ended = await controlRequest(origin, certificate,
    `/v1/relays/${relayId}/sessions/close`, {body: {shareId: 'MoqClos1',
      epoch: 'close-epoch-0001', tokenIds: [host.value.tokenId], endRoom: true}});
  assert.equal(ended.status, 200);
  assert.deepEqual(ended.value.closedTokenIds, [host.value.tokenId]);
  assert.equal(ended.value.endRoom, true);

  const retried = await controlRequest(origin, certificate,
    `/v1/relays/${relayId}/sessions/close`, {body: {shareId: 'MoqClos1',
      epoch: 'close-epoch-0001', tokenIds: [host.value.tokenId], endRoom: true}});
  assert.equal(retried.status, 200);
  assert.deepEqual(retried.value.closedTokenIds, [host.value.tokenId]);
});
