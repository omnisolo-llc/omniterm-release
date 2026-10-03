"""Unsigned verification must run despite an intentionally skipped release approval."""
import unittest
from pathlib import Path

from test_release_matrix import workflow_jobs


class BuildOnlyDependencyGateTests(unittest.TestCase):
    def test_verification_requires_resolve_but_not_a_full_release_approval(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/release.yml').read_text()
        job = workflow_jobs(workflow)['verify']
        condition = next(line.strip() for line in job.splitlines() if line.strip().startswith('if:'))
        self.assertIn('!cancelled()', condition)
        self.assertIn("needs.resolve.result == 'success'", condition)
        self.assertIn('inputs.build_only', condition)
        self.assertIn("github.ref == 'refs/heads/main'", condition)
        self.assertIn("github.repository == 'omnisolo-llc/omniterm-release'", condition)
        self.assertNotIn('always()', condition)

    def test_build_only_gate_never_receives_production_signing_credentials(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/release.yml').read_text()
        job = workflow_jobs(workflow)['verify']
        self.assertNotIn('SIGNING_CONFIG:', job)
        self.assertNotIn('STORAGE_CONFIG:', job)
        self.assertNotIn('APP_STORE_CONNECT_', job)


if __name__ == '__main__':
    unittest.main()
