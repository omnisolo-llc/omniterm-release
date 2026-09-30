import {createServer} from 'node:https';
import {readFile} from 'node:fs/promises';
import {randomBytes, randomUUID, timingSafeEqual} from 'node:crypto';
import * as Moq from '@moq/net';
import {Http3Server, quicheLoaded} from '@fails-components/webtransport';

const VERSION = 0xff000010;
const CLOSE_TIMEOUT_MS = 5000;
const MAX_BODY_BYTES = 16384;
const MAX_TOKEN_TTL_MS = 300000;
const MAX_RELAY_TOKENS = 10;
const MAX_TOMBSTONE_MS = 3600000;
const shareIdPattern = /^[A-Za-z0-9]{8}$/;
const epochPattern = /^[A-Za-z0-9_-]{8,128}$/;
const idPattern = /^[A-Za-z0-9_-]{1,128}$/;
const roleSet = new Set(['publish', 'subscribe']);

function validOrigin(value) {
  if (typeof value !== 'string' || value.length > 2048) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || !url.hostname || url.username || url.password ||
        url.pathname !== '/' || url.search || url.hash) return null;
    return url.origin;
  } catch { return null; }
}

function requiredSecret(value, name) {
  if (typeof value !== 'string' || !/^[\x21-\x7e]{32,512}$/.test(value)) {
    throw new Error(`${name}_must_be_32_to_512_printable_characters`);
  }
  return value;
}

function secretMatches(actual, expected) {
  const left = Buffer.from(typeof actual === 'string' ? actual : '');
  const right = Buffer.from(expected);
  return left.length === right.length && timingSafeEqual(left, right);
}

function validExpiry(value, max) {
  return Number.isSafeInteger(value) && value > Date.now() + 1000 && value <= max;
}

function json(response, value, status = 200) {
  if (response.destroyed || response.writableEnded) return;
  const body = Buffer.from(JSON.stringify(value));
  response.writeHead(status, {
    'cache-control': 'no-store',
    'connection': 'close',
    'content-length': body.length,
    'content-type': 'application/json; charset=utf-8',
    'x-content-type-options': 'nosniff',
  });
  response.end(body);
}

function readJson(request) {
  return new Promise((resolve, reject) => {
    const contentType = String(request.headers['content-type'] ?? '').toLowerCase();
    if (contentType !== 'application/json') {
      reject(new Error('invalid_content_type'));
      request.resume();
      return;
    }
    let size = 0;
    const chunks = [];
    request.on('data', chunk => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        request.resume();
        reject(new Error('request_too_large'));
        return;
      }
      chunks.push(chunk);
    });
    request.on('end', () => {
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
      catch { reject(new Error('invalid_json')); }
    });
    request.on('error', () => reject(new Error('request_failed')));
    request.on('aborted', () => reject(new Error('request_aborted')));
  });
}

function exactIds(actual, expected) {
  return Array.isArray(actual) && actual.length === expected.length &&
    [...actual].sort().every((value, index) => value === [...expected].sort()[index]);
}

function finishSession(record) {
  if (record.closed || !record.transportClosed) return;
  record.closed = true;
  record.resolveFinished();
  record.token.sessions.delete(record);
  record.relay.sessions.delete(record);
  record.owner.activeSessions.delete(record);
  try { record.connection?.close(); } catch {}
  if (record.token.role === 'publish' && record.relay.publisherToken === record.token.tokenId) {
    record.relay.publisherToken = null;
    record.relay.track.close();
  }
}

function closeSession(record, reason) {
  if (record.closed) return Promise.resolve(true);
  if (record.closePromise) return record.closePromise;
  record.closePromise = (async () => {
    record.closeInitiated = true;
    try { record.connection?.close(); } catch {}
    try { record.transport.close({closeCode: 0, reason}); } catch {}
    let timer;
    try {
      return await Promise.race([
        record.finished.then(() => true),
        new Promise(resolve => {
          timer = setTimeout(() => resolve(false), CLOSE_TIMEOUT_MS);
          timer.unref?.();
        }),
      ]);
    } finally { clearTimeout(timer); }
  })();
  return record.closePromise;
}

async function forwardPublisher(record) {
  const remote = record.connection.consume(record.relay.broadcastPath);
  const incoming = remote.subscribe('terminal', {ordered: true, latencyMax: 5000});
  await incoming.info();
  while (!record.closed && !record.relay.closed) {
    const group = await Promise.race([
      incoming.recvGroup(), record.finished.then(() => undefined),
    ]);
    if (!group || record.closed) break;
    const outgoing = record.relay.track.appendGroup();
    try {
      for (;;) {
        const frame = await Promise.race([
          group.readFrame(), record.finished.then(() => undefined),
        ]);
        if (!frame || record.closed) break;
        outgoing.writeFrame(frame);
      }
    } finally {
      group.close();
      outgoing.close();
    }
  }
}

/** A single authoritative MoQT owner. Control and HTTP/3 data use the same
 * process so token revocation can close and observe every admitted session. */
export async function createMoqRelay({
  controlHost = process.env.MOQ_CONTROL_HOST ?? '0.0.0.0',
  controlPort = Number(process.env.MOQ_CONTROL_PORT ?? 443),
  dataHost = process.env.MOQ_DATA_HOST ?? '0.0.0.0',
  dataPort = Number(process.env.MOQ_DATA_PORT ?? 443),
  publicOrigin = process.env.MOQ_PUBLIC_ORIGIN,
  controlToken = process.env.MOQ_CONTROL_TOKEN,
  certificatePath = process.env.MOQ_TLS_CERT_FILE,
  privateKeyPath = process.env.MOQ_TLS_KEY_FILE,
  maxRelays = Number(process.env.MOQ_MAX_RELAYS ?? 512),
  maxSessions = Number(process.env.MOQ_MAX_SESSIONS ?? 1024),
} = {}) {
  const origin = validOrigin(publicOrigin);
  const secret = requiredSecret(controlToken, 'MOQ_CONTROL_TOKEN');
  if (!origin || origin !== publicOrigin || !certificatePath || !privateKeyPath) {
    throw new Error('moq_tls_origin_and_certificate_required');
  }
  for (const [name, value, maximum] of [['controlPort', controlPort, 65535],
    ['dataPort', dataPort, 65535], ['maxRelays', maxRelays, 100000],
    ['maxSessions', maxSessions, 100000]]) {
    if (!Number.isSafeInteger(value) || value < 1 || value > maximum) {
      throw new Error(`invalid_${name}`);
    }
  }
  const certificate = await readFile(certificatePath, 'utf8');
  const privateKey = await readFile(privateKeyPath, 'utf8');
  await quicheLoaded;

  const relays = new Map();
  const relayScopes = new Map();
  const tombstones = new Map();
  const closedScopes = new Map();
  const tokenPaths = new Map();
  const activeSessions = new Set();
  const pendingSessions = new WeakMap();
  let stopping = false;
  let closePromise;
  let sweep;
  const scopeKey = (shareId, epoch) => `${shareId}\u0000${epoch}`;
  const h3 = new Http3Server({port: dataPort, host: dataHost,
    secret: randomBytes(32).toString('hex'), cert: certificate, privKey: privateKey});
  const onSessionVisitor = h3.onHttpWTSessionVisitor.bind(h3);
  h3.onHttpWTSessionVisitor = args => {
    onSessionVisitor(args);
    const token = tokenPaths.get(args.path);
    const transport = args.session.jsobj;
    if (!token || !transport) return;
    const admitted = token.active && !token.relay.closed &&
      token.expiresAt > Date.now() && token.sessions.size === 0 && activeSessions.size < maxSessions;
    const record = createSessionRecord(token, transport, admitted);
    pendingSessions.set(transport, record);
    if (!admitted) void closeSession(record, 'MoQ token is inactive');
  };
  const listener = createServer({cert: certificate, key: privateKey, maxHeaderSize: 8192,
    requestTimeout: 10000, headersTimeout: 8000, keepAliveTimeout: 1000}, async (request, response) => {
    if (stopping) return json(response, {error: 'moq_service_shutting_down'}, 503);
    if (!secretMatches(request.headers.authorization?.replace(/^Bearer /, ''), secret)) {
      return json(response, {error: 'unauthorized'}, 401);
    }
    let url;
    try { url = new URL(request.url, origin); }
    catch { return json(response, {error: 'invalid_request'}, 400); }
    if (url.origin !== origin || url.search || url.hash || request.headers.origin) {
      return json(response, {error: 'invalid_request'}, 400);
    }
    try {
      if (request.method === 'POST' && url.pathname === '/v1/relays') {
        const body = await readJson(request);
        if (!shareIdPattern.test(body.shareId ?? '') || !epochPattern.test(body.epoch ?? '') ||
            !validExpiry(body.expiresAt, Date.now() + 86400000)) {
          return json(response, {error: 'invalid_relay_scope'}, 400);
        }
        if (relays.size >= maxRelays) return json(response, {error: 'relay_capacity'}, 429);
        if (relayScopes.has(scopeKey(body.shareId, body.epoch))) {
          return json(response, {error: 'relay_scope_exists'}, 409);
        }
        const relayId = randomBytes(24).toString('base64url');
        const broadcastPath = `omniterm/${body.epoch}`;
        const broadcast = new Moq.Broadcast.Producer();
        const relay = {relayId, shareId: body.shareId, epoch: body.epoch,
          expiresAt: body.expiresAt, closed: false, tokens: new Map(), closedTokens: new Set(),
          sessions: new Set(), publisherToken: null, broadcast, broadcastPath,
          track: broadcast.createTrack('terminal', {ordered: true, latencyMax: 5000}),
          expiryTimer: null};
        relay.expiryTimer = setTimeout(() => void closeRelay(relay, 'room_expired'),
          Math.max(1, relay.expiresAt - Date.now()));
        relay.expiryTimer.unref?.();
        relays.set(relayId, relay);
        relayScopes.set(scopeKey(body.shareId, body.epoch), relayId);
        return json(response, {relayId, shareId: relay.shareId, epoch: relay.epoch,
          expiresAt: relay.expiresAt, publicOrigin: origin}, 201);
      }

      const tokenMatch = /^\/v1\/relays\/([A-Za-z0-9_-]{1,128})\/tokens$/.exec(url.pathname);
      if (request.method === 'POST' && tokenMatch) {
        const relay = relays.get(tokenMatch[1]);
        const body = await readJson(request);
        const expiresAt = body.expiresAt;
        if (!relay || relay.closed || relay.expiresAt <= Date.now() ||
            body.shareId !== relay.shareId || body.epoch !== relay.epoch ||
            !idPattern.test(body.participantId ?? '') || !roleSet.has(body.role) ||
            !validExpiry(expiresAt, Math.min(relay.expiresAt, Date.now() + MAX_TOKEN_TTL_MS))) {
          return json(response, {error: 'invalid_token_scope'}, 409);
        }
        if (relay.tokens.size >= MAX_RELAY_TOKENS || activeSessions.size >= maxSessions) {
          return json(response, {error: 'session_capacity'}, 429);
        }
        if ([...relay.tokens.values()].some(token => token.active &&
            (token.participantId === body.participantId ||
             (token.role === 'publish' && body.role === 'publish')))) {
          return json(response, {error: 'participant_token_exists'}, 409);
        }
        const tokenId = randomUUID();
        const token = {tokenId, secret: randomBytes(32).toString('base64url'),
          participantId: body.participantId, role: body.role, expiresAt, relay,
          active: true, sessions: new Set(), expiryTimer: null};
        token.expiryTimer = setTimeout(() => { token.active = false; },
          Math.max(1, expiresAt - Date.now()));
        token.expiryTimer.unref?.();
        relay.tokens.set(tokenId, token);
        tokenPaths.set(token.secret, token);
        const reader = h3.sessionStream(`/${token.secret}`).getReader();
        void (async () => {
          try {
            while (!stopping) {
              const next = await reader.read();
              if (next.done) break;
              void serveTokenSession(token, next.value);
            }
          } catch {} finally { reader.releaseLock(); }
        })();
        return json(response, {relayId: relay.relayId, shareId: relay.shareId,
          epoch: relay.epoch, tokenId, secret: token.secret, role: token.role,
          participantId: token.participantId, expiresAt: token.expiresAt,
          publicOrigin: origin}, 201);
      }

      if (request.method === 'POST' && url.pathname === '/v1/rooms/close') {
        const body = await readJson(request);
        if (!shareIdPattern.test(body.shareId ?? '') || !epochPattern.test(body.epoch ?? '')) {
          return json(response, {error: 'invalid_close_scope'}, 400);
        }
        const key = scopeKey(body.shareId, body.epoch);
        const relayId = relayScopes.get(key);
        const relay = relayId ? relays.get(relayId) : null;
        if (!relay) {
          const prior = closedScopes.get(key);
          return json(response, {relayId: prior?.relayId ?? null, shareId: body.shareId,
            epoch: body.epoch, closedTokenIds: prior?.closedTokenIds ?? [], endRoom: true});
        }
        relay.closed = true;
        const tokenIds = [...relay.tokens.keys()].sort();
        const results = await Promise.all([...relay.tokens.values()].map(token =>
          closeToken(token, 'owner_cutoff')));
        const closedTokenIds = tokenIds.filter(id => relay.closedTokens.has(id));
        if (results.some(result => !result) || !exactIds(closedTokenIds, tokenIds)) {
          return json(response, {error: 'active_session_close_unconfirmed', relayId: relay.relayId,
            shareId: relay.shareId, epoch: relay.epoch, closedTokenIds, endRoom: true}, 503);
        }
        clearTimeout(relay.expiryTimer);
        relay.broadcast.close(new Error('owner_cutoff'));
        const prior = {relayId: relay.relayId, shareId: relay.shareId,
          epoch: relay.epoch, closedTokenIds, expiresAt: Date.now() + MAX_TOMBSTONE_MS};
        tombstones.set(relay.relayId, prior);
        closedScopes.set(key, prior);
        relays.delete(relay.relayId);
        relayScopes.delete(key);
        return json(response, {relayId: relay.relayId, shareId: relay.shareId,
          epoch: relay.epoch, closedTokenIds, endRoom: true});
      }

      const closeMatch = /^\/v1\/relays\/([A-Za-z0-9_-]{1,128})\/sessions\/close$/.exec(url.pathname);
      if (request.method === 'POST' && closeMatch) {
        const body = await readJson(request);
        const relay = relays.get(closeMatch[1]);
        if (!relay) {
          const prior = tombstones.get(closeMatch[1]);
          if (prior && prior.shareId === body.shareId && prior.epoch === body.epoch &&
              body.endRoom === true && exactIds(body.tokenIds, prior.closedTokenIds)) {
            return json(response, {relayId: prior.relayId, shareId: prior.shareId,
              epoch: prior.epoch, closedTokenIds: prior.closedTokenIds, endRoom: true});
          }
          return json(response, {error: 'relay_unavailable'}, 404);
        }
        if (relay.shareId !== body.shareId || relay.epoch !== body.epoch ||
            !Array.isArray(body.tokenIds) || body.tokenIds.length > MAX_RELAY_TOKENS ||
            body.tokenIds.some(id => !idPattern.test(id)) ||
            new Set(body.tokenIds).size !== body.tokenIds.length || typeof body.endRoom !== 'boolean') {
          return json(response, {error: 'invalid_close_scope'}, 400);
        }
        const selected = body.endRoom ? [...relay.tokens.keys()] : body.tokenIds;
        if ((body.endRoom && !exactIds(body.tokenIds, selected)) ||
            selected.some(id => !relay.tokens.has(id) && !relay.closedTokens.has(id))) {
          return json(response, {error: 'token_scope_mismatch'}, 409);
        }
        if (body.endRoom) relay.closed = true;
        const results = await Promise.all(selected.map(id => {
          const token = relay.tokens.get(id);
          return token ? closeToken(token, 'owner_cutoff') : Promise.resolve(true);
        }));
        const closedTokenIds = selected.filter(id => relay.closedTokens.has(id)).sort();
        if (results.some(result => !result) || !exactIds(closedTokenIds, selected)) {
          return json(response, {error: 'active_session_close_unconfirmed', relayId: relay.relayId,
            shareId: relay.shareId, epoch: relay.epoch, closedTokenIds}, 503);
        }
        if (body.endRoom) {
          clearTimeout(relay.expiryTimer);
          relay.broadcast.close(new Error('owner_cutoff'));
          const prior = {relayId: relay.relayId, shareId: relay.shareId,
            epoch: relay.epoch, closedTokenIds: [...closedTokenIds].sort(),
            expiresAt: Date.now() + MAX_TOMBSTONE_MS};
          tombstones.set(relay.relayId, prior);
          closedScopes.set(scopeKey(relay.shareId, relay.epoch), prior);
          relays.delete(relay.relayId);
          relayScopes.delete(scopeKey(relay.shareId, relay.epoch));
        }
        return json(response, {relayId: relay.relayId, shareId: relay.shareId,
          epoch: relay.epoch, closedTokenIds: [...closedTokenIds].sort(), endRoom: body.endRoom});
      }
      return json(response, {error: 'not_found'}, 404);
    } catch (error) {
      return json(response, {error: error.message === 'request_too_large' ? error.message : 'request_failed'},
        error.message === 'request_too_large' ? 413 : 400);
    }
  });

  async function serveTokenSession(token, transport) {
    const record = pendingSessions.get(transport) ?? createSessionRecord(token, transport,
      token.active && !token.relay.closed && token.expiresAt > Date.now() &&
      token.sessions.size === 0 && activeSessions.size < maxSessions);
    pendingSessions.delete(transport);
    const relay = record.relay;
    if (!record.admitted || record.closed || !token.active || relay.closed) {
      await closeSession(record, 'MoQ token is inactive');
      return;
    }
    try {
      await transport.ready;
      if (!token.active || relay.closed || Date.now() >= token.expiresAt) {
        await closeSession(record, 'MoQ token is inactive');
        return;
      }
      record.connection = await Moq.Connection.accept(transport,
        new URL(`${origin}/${token.secret}`), {version: VERSION, discovery: false});
      record.connection.closed.then(() => finishSession(record), () => finishSession(record));
      if (record.closed || !token.active || relay.closed) {
        await closeSession(record, 'MoQ token is inactive');
        return;
      }
      if (token.role === 'publish') {
        if (relay.publisherToken && relay.publisherToken !== token.tokenId) {
          await closeSession(record, 'MoQ publisher is already connected');
          return;
        }
        relay.publisherToken = token.tokenId;
        await forwardPublisher(record);
      } else {
        record.connection.publish(relay.broadcastPath, relay.broadcast);
        await record.finished;
      }
    } catch {
      await closeSession(record, 'MoQ session failed');
    } finally {
      if (record.transport.closed) record.transportClosed = true;
      finishSession(record);
      activeSessions.delete(record);
    }
  }

  function createSessionRecord(token, transport, admitted) {
    let resolveFinished;
    const record = {owner: {activeSessions}, token, relay: token.relay, transport,
      connection: null, closed: false, admitted,
      finished: new Promise(resolve => { resolveFinished = resolve; }), resolveFinished};
    token.sessions.add(record);
    token.relay.sessions.add(record);
    activeSessions.add(record);
    transport.closed.then(() => {
      record.transportClosed = true;
      finishSession(record);
    }, () => {
      record.transportClosed = true;
      finishSession(record);
    });
    return record;
  }

  async function closeToken(token, reason) {
    token.active = false;
    clearTimeout(token.expiryTimer);
    const closed = await Promise.all([...token.sessions].map(record => closeSession(record, reason)));
    const confirmed = closed.every(Boolean) && [...token.sessions].every(record => record.closed);
    if (confirmed) {
      token.relay.tokens.delete(token.tokenId);
      token.relay.closedTokens.add(token.tokenId);
      tokenPaths.delete(token.secret);
    }
    return confirmed;
  }

  async function closeRelay(relay, reason) {
    if (!relay.closed) relay.closed = true;
    clearTimeout(relay.expiryTimer);
    relay.broadcast.close(new Error(reason));
    const results = await Promise.all([...relay.tokens.values()].map(token => closeToken(token, reason)));
    return results.every(Boolean) && relay.sessions.size === 0;
  }

  h3.startServer();
  await h3.ready;
  await new Promise((resolve, reject) => {
    listener.once('error', reject);
    listener.listen(controlPort, controlHost, () => {
      listener.off('error', reject);
      resolve();
    });
  });
  sweep = setInterval(() => {
    const now = Date.now();
    for (const [id, tombstone] of tombstones) if (tombstone.expiresAt <= now) tombstones.delete(id);
    for (const [key, tombstone] of closedScopes) if (tombstone.expiresAt <= now) closedScopes.delete(key);
    for (const [id, relay] of relays) {
      if (relay.expiresAt <= now) void closeRelay(relay, 'room_expired');
      if (relay.closed && !relay.sessions.size) {
        relays.delete(id);
        relayScopes.delete(scopeKey(relay.shareId, relay.epoch));
      }
    }
  }, 30000);
  sweep.unref?.();

  async function close() {
    if (closePromise) return closePromise;
    stopping = true;
    clearInterval(sweep);
    closePromise = (async () => {
      const results = await Promise.all([...relays.values()].map(relay => closeRelay(relay, 'service_shutdown')));
      await new Promise(resolve => listener.close(resolve));
      h3.stopServer();
      await Promise.race([h3.closed.catch(() => {}), new Promise(resolve => setTimeout(resolve, 5000))]);
      return results.every(Boolean);
    })();
    return closePromise;
  }

  return {origin, controlHost, controlPort, dataHost, dataPort, close,
    get relayCount() { return relays.size; }, get activeSessionCount() { return activeSessions.size; }};
}

if (process.argv[1] && new URL(import.meta.url).pathname === process.argv[1]) {
  const relay = await createMoqRelay();
  process.stdout.write(`OmniTerm MoQT relay listening at ${relay.origin}\n`);
  for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => {
    relay.close().then(confirmed => { process.exitCode = confirmed ? 0 : 1; });
  });
}
