"""Local verifier adapter tests; synthetic assertions are not benchmark scores."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from harness_lab.grading import grade


class GradingTests(unittest.IsolatedAsyncioTestCase):
    async def test_expected_keys_remain_outside_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            task = root / 'cache/recover-encrypted-db-credentials'
            (task / 'tests').mkdir(parents=True)
            (task / 'environment/test_data').mkdir(parents=True)
            (task / 'environment/test_data/expected_keys.txt').write_text('private expected value')
            (task / 'tests/test_outputs.py').write_text("from pathlib import Path\ndef test_expected():\n assert Path('/protected/test_data/expected_keys.txt').read_text() == 'private expected value'\n")
            workspace = root / 'trial/workspace'
            (workspace / 'app').mkdir(parents=True)
            verdict = await grade(task, workspace, timeout=10)
            self.assertEqual((verdict['reward'], verdict['checks_passed']), (1, 1))
            self.assertFalse((workspace / 'protected/test_data/expected_keys.txt').exists())

    async def test_incomplete_cleanup_is_not_success_even_with_passing_junit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            task = root / 'task'
            (task / 'tests').mkdir(parents=True)
            (task / 'tests/test_outputs.py').write_text('def test_ok(): assert True')
            workspace = root / 'trial/workspace'
            (workspace / 'app').mkdir(parents=True)
            async def incomplete(*args):
                (workspace.parent / 'verifier/results.xml').write_text('<testsuites><testsuite><testcase name="ok"/></testsuite></testsuites>')
                return dict(exit_code=0, stdout='', stderr='', timed_out=False, truncated=False, cleanup_incomplete=True)
            with patch('harness_lab.grading._execute', side_effect=incomplete):
                verdict = await grade(task, workspace)
            self.assertEqual(verdict['checks_passed'], 1)
            self.assertEqual(verdict['reward'], 0)
            self.assertTrue(verdict['cleanup_incomplete'])
