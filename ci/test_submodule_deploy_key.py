"""The read-only website deploy key belongs only to protected source preparation."""
import ast
from pathlib import Path
import unittest
import test_bootstrap

FIELD = 'SOURCE_SUBMODULE_DEPLOY_KEY_BASE64'


class SubmoduleDeployKeyHandoffTests(unittest.TestCase):
    def test_key_has_an_explicit_private_handoff_and_no_command_files(self):
        env = {'PATH': '/trusted/bin', 'GITHUB_OUTPUT': '/runner/output'}
        function = test_bootstrap.b.private_task_environment
        import inspect
        self.assertIn('submodule_deploy_key_base64', inspect.signature(function).parameters)
        actual = function(env, '', submodule_deploy_key_base64='test-credential-not-a-key')
        self.assertEqual(actual[FIELD], 'test-credential-not-a-key')
        self.assertNotIn(FIELD, env)
        self.assertNotIn('GITHUB_OUTPUT', actual)

    def test_launcher_extracts_key_before_running_checkout_processes(self):
        source = Path(test_bootstrap.b.__file__).read_text()
        main = next(n for n in ast.parse(source).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'main')
        pops = [n for n in ast.walk(main) if isinstance(n, ast.Call)
                and ast.unparse(n.func) == 'env.pop' and n.args
                and isinstance(n.args[0], ast.Constant) and n.args[0].value == FIELD]
        self.assertEqual(len(pops), 1)
        calls = [n for n in ast.walk(main) if isinstance(n, ast.Call)
                 and ast.unparse(n.func) == 'invoke']
        self.assertLess(pops[0].lineno, min(n.lineno for n in calls))

    def test_workflow_wires_key_at_the_same_protected_steps_as_token(self):
        text = (Path(__file__).resolve().parents[1] / '.github/workflows/release.yml').read_text()
        lines = text.splitlines()
        token_lines = [n for n, line in enumerate(lines) if 'SOURCE_SUBMODULE_TOKEN:' in line]
        self.assertGreater(len(token_lines), 0)
        for n in token_lines:
            self.assertEqual(lines[n+1].strip(), FIELD + ': ${{ secrets.' + FIELD + ' }}')
        self.assertEqual(text.count(FIELD + ':'), len(token_lines))


if __name__ == '__main__':
    unittest.main()
