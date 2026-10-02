"""Keep the public download guidance aligned with the actual release boundaries."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class DownloadGuideTests(unittest.TestCase):
    def test_agent_guide_describes_all_native_package_families(self):
        text = (ROOT / 'README.md').read_text().split('# Omni Agent Native Builds', 1)[1]
        for name in ['x86-64', 'ARM64', 'Windows', 'PKG', 'DMG', 'MSI', 'setup EXE', 'DEB', 'RPM']:
            with self.subTest(name=name):
                self.assertTrue(name in text, name)
        self.assertNotIn('Only the four actual compiled binaries', text)

    def test_complete_private_release_and_mobile_packages_have_explicit_destinations(self):
        text = (ROOT / 'README.md').read_text()
        self.assertIn('https://github.com/ql-owo-lp/omniterm/releases', text)
        self.assertIn('signed IPA is attached', text)
        self.assertIn('Public APKs', text)
        self.assertIn('Receipts never enter public release drafts', text)

if __name__ == '__main__':
    unittest.main()
