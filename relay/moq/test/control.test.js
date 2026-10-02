import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash, X509Certificate} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {request as httpsRequest} from 'node:https';
import {createServer as createTcpServer} from 'node:net';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {WebTransport} from '@fails-components/webtransport';
import * as Moq from '@moq/net';
import {createMoqRelay} from '../src/index.js';

globalThis.WebTransport ??= WebTransport;

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

async function controlGet(origin, certificate, path, token = controlToken) {
  return new Promise((resolve, reject) => {
    const request = httpsRequest(new URL(path, origin), {method:'GET',ca:certificate,
      headers:{...(token?{authorization:`Bearer ${token}`}:{})}}, response => {
      const parts=[];
      response.on('data',chunk=>parts.push(chunk));
      response.on('end',()=>{
        let value;try{value=JSON.parse(Buffer.concat(parts).toString('utf8'));}catch{value=null;}
        resolve({status:response.statusCode,value});
      });
    });
    request.once('error',reject);request.end();
  });
}

async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), 'omniterm-moq-control-'));
  const certificatePath = join(directory, 'cert.pem');
  const privateKeyPath = join(directory, 'key.pem');
  await run('openssl', ['req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
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

test('HTTP/3 owner cutoff closes the native session and rejects token replay', async t => {
  const {origin, certificate, service} = await fixture(t);
  const created = await controlRequest(origin, certificate, '/v1/relays', {
    body: {shareId: 'MoqH3Ow1', epoch: 'h3-owner-epoch-0001', expiresAt: Date.now() + 60000},
  });
  const relayId = created.value.relayId;
  const issue = (participantId) => controlRequest(origin, certificate,
    `/v1/relays/${relayId}/tokens`, {body: {shareId: 'MoqH3Ow1', epoch: 'h3-owner-epoch-0001',
      participantId, role: 'subscribe', expiresAt: Date.now() + 30000}});
  const certificateHash = createHash('sha256')
    .update(new X509Certificate(certificate).raw)
    .digest();
  const urlFor = (secret) => new URL('/' + secret, origin);
  const clientOptions = {serverCertificateHashes: [{algorithm: 'sha-256', value: certificateHash}]};
  const invalid = await issue('invalid01');
  const rejectedTransport = new WebTransport(urlFor(invalid.value.secret), {
    ...clientOptions, protocols: ['unsupported-moq-protocol'],
  });
  rejectedTransport.closed.catch(() => {});
  const rejected = await Promise.race([
    rejectedTransport.ready.then(() => false, () => true),
    new Promise(resolve => setTimeout(() => resolve(false), 5000)),
  ]);
  assert.equal(rejected, true);
  rejectedTransport.close();
  const invalidClosed = await controlRequest(origin, certificate,
    `/v1/relays/${relayId}/sessions/close`, {body: {shareId: 'MoqH3Ow1',
      epoch: 'h3-owner-epoch-0001', tokenIds: [invalid.value.tokenId], endRoom: false}});
  assert.equal(invalidClosed.status, 200);

  const firstToken = await issue('viewer001');
  const connect = (secret) => Moq.Connection.connect(urlFor(secret), {
    webtransport: clientOptions, websocket: {enabled: false}, discovery: false,
  });
  const first = await connect(firstToken.value.secret);
  assert.equal(first.version, 'moq-transport-16');
  first.close();
  await first.closed;
  const firstClosed = await controlRequest(origin, certificate,
    `/v1/relays/${relayId}/sessions/close`, {body: {shareId: 'MoqH3Ow1',
      epoch: 'h3-owner-epoch-0001', tokenIds: [firstToken.value.tokenId], endRoom: false}});
  assert.equal(firstClosed.status, 200);
  await first.closed;

  const secondToken = await issue('viewer002');
  const second = await connect(secondToken.value.secret);
  assert.equal(second.version, 'moq-transport-16');
  const ownerClosed = await controlRequest(origin, certificate, '/v1/rooms/close', {
    body: {shareId: 'MoqH3Ow1', epoch: 'h3-owner-epoch-0001'},
  });
  assert.equal(ownerClosed.status, 200);
  assert.deepEqual(ownerClosed.value.closedTokenIds, [secondToken.value.tokenId]);
  await second.closed;
  assert.equal(service.activeSessionCount, 0);

  const replay = new WebTransport(urlFor(secondToken.value.secret), clientOptions);
  replay.closed.catch(() => {});
  const replayDenied = await Promise.race([
    replay.ready.then(() => false, () => true),
    new Promise(resolve => setTimeout(() => resolve(false), 5000)),
  ]);
  assert.equal(replayDenied, true);
  replay.close();

  const cutoffRetry = await controlRequest(origin, certificate, '/v1/rooms/close', {
    body: {shareId: 'MoqH3Ow1', epoch: 'h3-owner-epoch-0001'},
  });
  assert.equal(cutoffRetry.status, 200);
  assert.deepEqual(cutoffRetry.value.closedTokenIds, [secondToken.value.tokenId]);
});

test('managed MoQ readiness requires an authenticated Worker usage authority callback',async t=>{
  const directory=await mkdtemp(join(tmpdir(),'omniterm-moq-ready-'));
  const certificatePath=join(directory,'cert.pem'),privateKeyPath=join(directory,'key.pem');
  await run('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',privateKeyPath,
    '-out',certificatePath,'-days','1','-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1'],{stdio:'ignore'});
  const certificate=await readFile(certificatePath,'utf8'),port=await freePort(),origin=`https://127.0.0.1:${port}`;
  const usageToken='independent-worker-moq-usage-token-0001';
  const usageCalls=[];
  let oversizedUsageResponse=false;
  const service=await createMoqRelay({controlHost:'127.0.0.1',dataHost:'127.0.0.1',controlPort:port,
    dataPort:port,publicOrigin:origin,controlToken,usageUrl:'https://api.example.test/internal/v1/live-share/moq/usage',
    usageToken,managedUsageRequired:true,usageFetcher:async(url,init)=>{
      usageCalls.push({url:new URL(url),init});
      if(oversizedUsageResponse)return new Response(new ReadableStream({start(controller){
        controller.enqueue(new Uint8Array(16*1024+1));controller.close();
      }}),{headers:{'content-type':'application/json'}});
      return Response.json({status:'ready',protocol:'omniterm-moq-usage-v1',
        probe_id:JSON.parse(init.body).probe_id});
    },certificatePath,privateKeyPath});
  t.after(async()=>{await service.close();await rm(directory,{recursive:true,force:true});});
  const response=await controlGet(origin,certificate,'/v1/readyz');
  assert.equal(response.status,200);
  assert.deepEqual(response.value,{status:'ready',protocol:'omniterm-moq-v1',
    worker_usage_metering:true,active_session_cutoff:true});
  assert.equal(usageCalls.length,1);
  assert.equal(usageCalls[0].url.href,'https://api.example.test/internal/v1/live-share/moq/readyz');
  assert.equal(usageCalls[0].init.headers['x-live-share-moq-usage-token'],usageToken);
  oversizedUsageResponse=true;
  const oversized=await controlGet(origin,certificate,'/v1/readyz');
  assert.equal(oversized.status,503);
  const unauthorized=await controlGet(origin,certificate,'/v1/readyz','');
  assert.equal(unauthorized.status,401);
});

test('managed MoQ cannot start without its separately authenticated Worker usage authority',async()=>{
  await assert.rejects(createMoqRelay({controlToken,managedUsageRequired:true}),
    /managed_worker_usage_required/);
});

test('self-hosted readiness is honest without a managed Worker account', async t => {
  const {origin,certificate} = await fixture(t);
  const response = await controlGet(origin,certificate,'/v1/readyz');
  assert.equal(response.status,200);
  assert.deepEqual(response.value,{status:'ready',protocol:'omniterm-moq-v1',
    worker_usage_metering:false,active_session_cutoff:true});
  assert.equal((await controlGet(origin,certificate,'/v1/readyz','')).status,401);
});
