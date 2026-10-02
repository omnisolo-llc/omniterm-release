import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {cp, mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {dirname, join, resolve} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';

const adapterCommit = '212ef743f0cf52adb234d60d5b41c48257e967b4';
const quicheCommit = '80bf9559d3a4c08dde4b85abc46d190a88ffef64';
const adapterPackageName = '@fails-components/webtransport-transport-http3-quiche';
const submodules = [
  'transports/http3-quiche/third_party/boringssl/src',
  'transports/http3-quiche/third_party/abseil-cpp',
  'transports/http3-quiche/third_party/quiche',
  'transports/http3-quiche/third_party/zlib',
  'transports/http3-quiche/third_party/googleurl',
  'transports/http3-quiche/third_party/protobuf',
];

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    env: options.env ?? process.env,
    encoding: options.capture ? 'utf8' : undefined,
    stdio: options.capture ? 'pipe' : 'inherit',
    maxBuffer: 16 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const detail = options.capture ? result.stderr || result.stdout : '';
    throw new Error(`${command} ${args.join(' ')} failed (${result.status}): ${detail}`);
  }
  return options.capture ? result.stdout.trim() : '';
}

async function main() {
  const adapterEntry = fileURLToPath(import.meta.resolve(adapterPackageName));
  const adapterRoot = resolve(dirname(adapterEntry), '..');
  const adapterMetadata = JSON.parse(await readFile(join(adapterRoot, 'package.json'), 'utf8'));
  if (adapterMetadata.version !== '1.6.8') {
    throw new Error(`Unsupported WebTransport adapter version: ${adapterMetadata.version}`);
  }

  const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
  const patchFile = join(packageRoot, 'patches', 'quiche-server-close-ack.patch');
  const markerPrefix = `omniterm-quiche-close-ack-v1\nwebtransport=${adapterCommit}\nquiche=${quicheCommit}\n`;
  const thirdPartyRoot = join(adapterRoot, 'third_party');
  const markerPath = join(thirdPartyRoot, '.omniterm-quiche-close-ack');
  const nativeBinary = join(adapterRoot, 'build', 'Release', 'webtransport.node');

  try {
    const marker = await readFile(markerPath, 'utf8');
    const binary = await readFile(nativeBinary);
    const binaryDigest = createHash('sha256').update(binary).digest('hex');
    if (marker === `${markerPrefix}binary_sha256=${binaryDigest}\n`) {
      process.stdout.write('Verified patched WebTransport native adapter\n');
      return;
    }
  } catch {}

  const tempRoot = await mkdtemp(join(tmpdir(), 'omniterm-webtransport-source-'));
  const sourceRoot = join(tempRoot, 'webtransport');
  try {
    run('git', ['init', sourceRoot]);
    run('git', ['-C', sourceRoot, 'remote', 'add', 'origin',
      'https://github.com/fails-components/webtransport.git']);
    run('git', ['-C', sourceRoot, 'fetch', '--depth=1', 'origin', adapterCommit]);
    run('git', ['-C', sourceRoot, 'checkout', '--detach', 'FETCH_HEAD']);
    const checkedOutAdapter = run('git', ['-C', sourceRoot, 'rev-parse', 'HEAD'],
      {capture: true});
    if (checkedOutAdapter !== adapterCommit) {
      throw new Error('WebTransport source revision mismatch');
    }

    for (const path of submodules) {
      run('git', ['-C', sourceRoot, 'submodule', 'update', '--init', '--recursive',
        '--depth=1', '--', path]);
    }

    const quichePath = 'transports/http3-quiche/third_party/quiche';
    const gitlink = run('git', ['-C', sourceRoot, 'ls-tree', 'HEAD', '--', quichePath],
      {capture: true});
    if (!gitlink.includes(`commit ${quicheCommit}\t${quichePath}`)) {
      throw new Error('Pinned WebTransport source points to an unexpected Quiche revision');
    }
    const quicheRoot = join(sourceRoot, quichePath);
    const checkedOutQuiche = run('git', ['-C', quicheRoot, 'rev-parse', 'HEAD'],
      {capture: true});
    if (checkedOutQuiche !== quicheCommit) throw new Error('Quiche source revision mismatch');

    run('git', ['-C', quicheRoot, 'apply', '--check', patchFile]);
    run('git', ['-C', quicheRoot, 'apply', patchFile]);

    await rm(thirdPartyRoot, {recursive: true, force: true});
    await cp(join(sourceRoot, 'transports/http3-quiche/third_party'), thirdPartyRoot,
      {recursive: true});

    const buildEnv = {
      ...process.env,
      CMAKE_BUILD_PARALLEL_LEVEL: process.env.CMAKE_BUILD_PARALLEL_LEVEL ?? '2',
      npm_config_build_from_source: 'true',
    };
    run(process.execPath, ['build.js', 'install'], {cwd: adapterRoot, env: buildEnv});
    const builtBinary = await readFile(nativeBinary);
    const binaryDigest = createHash('sha256').update(builtBinary).digest('hex');
    await writeFile(markerPath, `${markerPrefix}binary_sha256=${binaryDigest}\n`,
      {mode: 0o644});
    process.stdout.write('Built WebTransport with peer-confirmed server session closure\n');
  } finally {
    await rm(tempRoot, {recursive: true, force: true});
  }
}

await main();
