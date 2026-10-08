import {join,resolve} from 'node:path';
import {createHash} from 'node:crypto';
import {copyFile, lstat, readFile, readdir, readlink} from 'node:fs/promises';

// Upstream prefers build_<platform>_<arch>, not the generic prebuilt directory.
export function nativeBuildPath(root, platform = process.platform, arch = process.arch) {
  if (!['linux','darwin','win32'].includes(platform) || !['x64','arm64'].includes(arch)) {
    throw Error('unsupported_native_build_platform');
  }
  return join(root, `build_${platform}_${arch}`, 'Release', 'webtransport.node');
}

export function requireLoadedAddon(expected, actual) {
  if (!Array.isArray(actual) || actual.length !== 1 || resolve(actual[0]) !== resolve(expected)) {
    throw Error('native_addon_identity_mismatch');
  }
}

export async function treeDigest(root, {excludeBuildOutput = false, ignoreGeneratedMarker = false} = {}) {
  const hash = createHash('sha256');

  async function visit(directory, relativeDirectory = '') {
    const entries = await readdir(directory, {withFileTypes: true});
    entries.sort((left, right) => left.name.localeCompare(right.name));
    for (const entry of entries) {
      const relativePath = relativeDirectory ? `${relativeDirectory}/${entry.name}` : entry.name;
      if (entry.name === '.git' || entry.name === 'node_modules') continue;
      if (relativeDirectory === '' && excludeBuildOutput &&
          (entry.name === 'build' || entry.name.startsWith('build_') || entry.name === 'third_party')) {
        continue;
      }
      if (relativeDirectory === '' && ignoreGeneratedMarker &&
          entry.name === '.omniterm-quiche-close-ack') continue;

      const fullPath = join(directory, entry.name);
      const metadata = await lstat(fullPath);
      if (metadata.isDirectory()) {
        hash.update(`directory\0${relativePath}\0`);
        await visit(fullPath, relativePath);
      } else if (metadata.isSymbolicLink()) {
        hash.update(`symlink\0${relativePath}\0${await readlink(fullPath)}\0`);
      } else if (metadata.isFile()) {
        hash.update(`file\0${relativePath}\0`);
        hash.update(await readFile(fullPath));
        hash.update('\0');
      } else {
        throw new Error(`Unsupported source closure entry: ${relativePath}`);
      }
    }
  }

  await visit(root);
  return hash.digest('hex');
}

// The caller supplies the integrity-verified pristine tarball and applies only
// the tracked patches to that private copy. An interrupted compiler may leave
// the installed adapter exactly patched but without a final build receipt.
export async function prepareAdapterSource({adapterRoot, pristineRoot, patchSources, applyPatches}) {
  const upstreamSourceDigest = await treeDigest(pristineRoot, {excludeBuildOutput: true});
  await applyPatches();
  const expectedPatchedSourceDigest = await treeDigest(pristineRoot, {excludeBuildOutput: true});
  const installedSourceDigest = await treeDigest(adapterRoot, {excludeBuildOutput: true});
  let state;
  if (installedSourceDigest === expectedPatchedSourceDigest) {
    state = 'patched';
  } else if (installedSourceDigest === upstreamSourceDigest) {
    state = 'pristine';
    for (const path of patchSources) {
      await copyFile(join(pristineRoot, path), join(adapterRoot, path));
    }
    if (await treeDigest(adapterRoot, {excludeBuildOutput: true}) !== expectedPatchedSourceDigest) {
      throw new Error('Installed adapter source does not match the patched tarball source');
    }
  } else {
    throw new Error('Installed adapter source differs from its locked tarball and exact patched source');
  }
  return {state, upstreamSourceDigest, expectedPatchedSourceDigest};
}
