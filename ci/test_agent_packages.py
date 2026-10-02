import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
class AgentPackageWorkflowTests(unittest.TestCase):
    def test_both_windows_targets_and_all_package_assets_are_retained(self):
        source=(ROOT/'.github/workflows/omni-agent.yml').read_text()
        for name in ('linux-x86_64','linux-aarch64','darwin-x86_64','darwin-aarch64','windows-x86_64','windows-aarch64'):
            self.assertIn('platform: '+name,source)
        self.assertIn('path: ${{ runner.temp }}/agent-release/',source)
        self.assertNotIn('path: ${{ runner.temp }}/agent-release/omniterm-agent-',source)
        self.assertIn('if-no-files-found: error',source)

if __name__=='__main__':unittest.main()
