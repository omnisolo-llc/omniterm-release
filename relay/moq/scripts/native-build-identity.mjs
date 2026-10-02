import {join,resolve} from 'node:path';

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
