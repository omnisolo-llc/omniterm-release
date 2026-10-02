// Build-path contracts supplement, never replace, real HTTP/3 acceptance.
import test from 'node:test';
import assert from 'node:assert/strict';
import {nativeBuildPath, requireLoadedAddon} from '../scripts/native-build-identity.mjs';

test('receipt identifies the platform addon the upstream loader actually uses', () => {
  assert.equal(nativeBuildPath('/owned/adapter', 'linux', 'x64'),
    '/owned/adapter/build_linux_x64/Release/webtransport.node');
  assert.equal(nativeBuildPath('/owned/adapter', 'linux', 'arm64'),
    '/owned/adapter/build_linux_arm64/Release/webtransport.node');
  assert.throws(() => nativeBuildPath('/owned/adapter', '../escape', 'x64'));
});

test('a generic prebuilt, debug addon or second addon cannot certify the patched build', () => {
  const expected = nativeBuildPath('/owned/adapter', 'linux', 'x64');
  assert.doesNotThrow(() => requireLoadedAddon(expected, [expected]));
  for (const paths of [[], ['/owned/adapter/build/Release/webtransport.node'],
    ['/owned/adapter/build_linux_x64/Debug/webtransport.node'], [expected, '/other/webtransport.node']]) {
    assert.throws(() => requireLoadedAddon(expected, paths), /native_addon_identity_mismatch/);
  }
});
