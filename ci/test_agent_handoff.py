"""Handoff contract tests; sample bytes are not native installation evidence."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import verify_agent_handoff as gate


class AgentHandoffTests(unittest.TestCase):
    def fixture(self, root, platform='windows-aarch64'):
        names = gate.package_names('0.1.1', platform)
        rows = []
        for name in sorted(names):
            data = ('contract-only:' + name).encode()
            (root / name).write_bytes(data)
            rows.append({'name': name, 'size': len(data),
                         'sha256': hashlib.sha256(data).hexdigest()})
        payload = {name: {'size': 1, 'sha256': 'a' * 64}
                   for name in gate.payload_names(platform)}
        value = {'schema': 1, 'version': '0.1.1', 'platform': platform,
                 'platform_signing': 'not_performed', 'payload': payload,
                 'assets': rows}
        path = root / ('packages-' + platform + '.json')
        path.write_text(json.dumps(value))
        return path, value

    def test_complete_six_platform_hash_inventories_are_accepted(self):
        for platform in gate.PLATFORMS:
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.fixture(root, platform)
                self.assertEqual(gate.verify(root, '0.1.1', platform),
                                 len(gate.package_names('0.1.1', platform)))

    def test_missing_installer_changed_bytes_and_private_extras_are_rejected(self):
        for fault in ('missing-msi', 'changed', 'private-file', 'private-field',
                      'false-signing', 'duplicate', 'link', 'wrong-version'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                manifest, value = self.fixture(root)
                package = next(root.glob('*.msi'))
                if fault == 'missing-msi': package.unlink()
                if fault == 'changed': package.write_bytes(b'changed')
                if fault == 'private-file': (root / 'auth.json').write_text('{}')
                if fault == 'private-field': value['private_diagnostics'] = 'must not leak'
                if fault == 'false-signing': value['platform_signing'] = 'verified'
                if fault == 'duplicate': value['assets'].append(value['assets'][0])
                if fault == 'link':
                    package.unlink()
                    package.symlink_to(manifest)
                if fault == 'wrong-version': value['version'] = '0.1.0'
                manifest.write_text(json.dumps(value))
                with self.assertRaises(ValueError): gate.verify(root, '0.1.1', 'windows-aarch64')

    def test_duplicate_manifest_properties_are_rejected_as_ambiguous(self):
        for source, duplicate in (
            ('"schema": 1', '"schema": 0, "schema": 1'),
            ('"size": 1', '"size": 0, "size": 1'),
        ):
            with self.subTest(property=source), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                manifest, _ = self.fixture(root)
                valid = manifest.read_text()
                self.assertIn(source, valid)
                manifest.write_text(valid.replace(source, duplicate, 1))
                with self.assertRaisesRegex(ValueError, 'duplicate object keys'):
                    gate.verify(root, '0.1.1', 'windows-aarch64')

    def test_versions_and_platforms_cannot_select_arbitrary_paths(self):
        for version in ('../1.2.3', '1.2.3\n', '01.2.3', '1.2', '256.0.0'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                gate.package_names(version, 'linux-x86_64')
        with self.assertRaises(ValueError): gate.package_names('0.1.1', '../windows')

    def test_workflow_checks_all_outputs_before_retaining_them(self):
        source = (Path(__file__).resolve().parents[1] /
                  '.github/workflows/omni-agent.yml').read_text()
        check = source.find('python3 ci/verify_agent_handoff.py')
        upload = source.find('Retain the complete native distribution')
        self.assertGreater(check, 0)
        self.assertLess(check, upload)


if __name__ == '__main__':
    unittest.main()
