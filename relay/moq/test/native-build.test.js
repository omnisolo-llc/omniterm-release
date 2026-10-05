// Build-path contracts supplement, never replace, real HTTP/3 acceptance.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
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

test('fresh native builds disable upstream Python discovery and Python-only tests', async () => {
  const source = await readFile(new URL('../scripts/build-patched-webtransport.mjs', import.meta.url), 'utf8');
  assert.match(source, /--CDgtest_build_tests=OFF/);
  assert.match(source, /--CDCMAKE_DISABLE_FIND_PACKAGE_Python3=TRUE/);
  assert.match(source, /--CDCMAKE_DISABLE_FIND_PACKAGE_Python=TRUE/);
});

test('npm lifecycle scripts cannot run the upstream unpinned addon build', async () => {
  const packageJson = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
  assert.equal(packageJson.scripts.postinstall, undefined);
  assert.equal(packageJson.scripts['native-build'], 'node scripts/build-patched-webtransport.mjs');
  const npmPolicy = await readFile(new URL('../.npmrc', import.meta.url), 'utf8');
  assert.match(npmPolicy, /^ignore-scripts=true$/m);

  for (const workflowName of ['contracts', 'release']) {
    const workflow = await readFile(
      new URL(`../../../.github/workflows/${workflowName}.yml`, import.meta.url), 'utf8');
    const install = workflow.indexOf('npm ci --ignore-scripts --prefix relay/moq');
    const build = workflow.indexOf('npm run native-build --prefix relay/moq');
    const test = workflow.indexOf('npm test --prefix relay/moq');
    assert.ok(install >= 0, `${workflowName}: lifecycle scripts must be disabled`);
    assert.ok(build > install, `${workflowName}: native build must follow install`);
    assert.ok(test > build, `${workflowName}: MoQ tests must follow native build`);
  }
});
