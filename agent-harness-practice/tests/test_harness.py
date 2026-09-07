from dataclasses import replace
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest

from core import SessionStore, Settings, run_agent
from tools import WorkspaceTools


def reply(content=None, calls=(), reason=None):
    tool_calls = [NS(id=i, type="function", function=NS(name=n, arguments=a)) for i, n, a in calls]
    return NS(choices=[NS(finish_reason=reason or ("tool_calls" if calls else "stop"),
                          message=NS(content=content, tool_calls=tool_calls))])


class FakeClient:
    """테스트가 준비한 응답만 반환합니다. 실제 CLI에는 사용하지 않습니다."""
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs)))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


class HarnessTests(unittest.TestCase):
    def directory_link(self, path):
        try:
            path.symlink_to(self.root, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"이 환경에서 심볼릭 링크를 만들 수 없습니다: {type(error).__name__}")

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "note.txt").write_text("근거: 금요일 홍보 초안", encoding="utf-8")
        self.tools = WorkspaceTools(self.workspace, lambda _: False)
        self.settings = Settings("openai", "test-model", "https://example.invalid/v1", str(self.workspace))
        self.messages = [{"role": "user", "content": "홍보 일정 확인"}]

    def test_multicall_ids_and_recovery_are_delivered_to_next_model_request(self):
        client = FakeClient([
            reply(calls=[("read-1", "read_file", '{"path":"note.txt"}'),
                         ("bad-2", "read_file", '{"path":"missing.txt"}')]),
            reply("금요일입니다. 두 번째 파일은 없었습니다."),
        ])
        answer = run_agent(client, self.settings, self.messages, self.tools)
        sent = client.requests[1]["messages"]
        self.assertEqual([m["tool_call_id"] for m in sent if m["role"] == "tool"], ["read-1", "bad-2"])
        self.assertTrue(json.loads(sent[-2]["content"])["ok"])
        self.assertFalse(json.loads(sent[-1]["content"])["ok"])
        self.assertIn("금요일", answer)

    def test_invalid_json_and_unknown_tool_return_errors(self):
        self.assertFalse(self.tools.execute("read_file", "broken")["ok"])
        self.assertFalse(self.tools.execute("shell", '{}')["ok"])
        self.assertFalse(self.tools.execute("read_file", '{"path":3}')["ok"])
        self.assertFalse(self.tools.execute("list_files", '{"extra":true}')["ok"])

    def test_denied_write_preserves_original(self):
        result = self.tools.execute("write_file", '{"path":"note.txt","content":"수정"}')
        self.assertFalse(result["ok"])
        self.assertIn("금요일", (self.workspace / "note.txt").read_text(encoding="utf-8"))

    def test_path_escape_hidden_file_and_symlink_are_rejected(self):
        secret = self.root / "secret.txt"
        secret.write_text("private")
        self.directory_link(self.workspace / "link")
        for path in ["../secret.txt", str(secret), ".env", "link/secret.txt"]:
            with self.subTest(path=path):
                result = self.tools.execute("read_file", json.dumps({"path": path}))
                self.assertFalse(result["ok"])
        self.assertNotIn("link/secret.txt", self.tools.list_files()["files"])

    def test_approval_time_symlink_change_is_rejected(self):
        probe = self.workspace / "link-probe"
        self.directory_link(probe)
        probe.unlink()
        def change_path(_):
            (self.workspace / "sub").symlink_to(self.root, target_is_directory=True)
            return True
        tools = WorkspaceTools(self.workspace, change_path)
        result = tools.execute("write_file", '{"path":"sub/escaped.txt","content":"x"}')
        self.assertTrue((self.workspace / "sub").is_symlink())
        self.assertFalse(result["ok"])
        self.assertIn("심볼릭 링크", result["error"])
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_call_budget_skips_extra_call_and_keeps_protocol_complete(self):
        client = FakeClient([reply(calls=[("a", "list_files", "{}"), ("b", "list_files", "{}")])])
        result = run_agent(client, self.settings, self.messages, self.tools, max_tool_calls=1)
        self.assertIn("상한", result)
        self.assertEqual(len([m for m in self.messages if m["role"] == "tool"]), 2)
        self.assertFalse(json.loads(self.messages[-1]["content"])["ok"])

    def test_interrupt_resolves_outstanding_calls_without_executing_next(self):
        def interrupt(name, arguments):
            raise KeyboardInterrupt()
        self.tools.execute = interrupt
        client = FakeClient([reply(calls=[("a", "write_file", '{}'), ("b", "list_files", '{}')])])
        with self.assertRaises(KeyboardInterrupt):
            run_agent(client, self.settings, self.messages, self.tools)
        self.assertEqual([m["tool_call_id"] for m in self.messages if m["role"] == "tool"], ["a", "b"])
        self.assertTrue(all(not json.loads(m["content"])["ok"] for m in self.messages if m["role"] == "tool"))

    def test_truncated_response_never_executes_tool(self):
        client = FakeClient([reply(calls=[("a", "list_files", '{}')], reason="length")])
        with self.assertRaises(RuntimeError):
            run_agent(client, self.settings, self.messages, self.tools)
        self.assertEqual(len(self.messages), 1)

    def test_session_resumes_and_rejects_different_execution_context(self):
        store = SessionStore(self.root / "sessions", "work")
        store.save(self.settings, self.messages)
        self.assertEqual(store.load(self.settings), self.messages)
        for settings in [replace(self.settings, provider="ollama"),
                         replace(self.settings, model="other"),
                         replace(self.settings, workspace="/another")]:
            with self.assertRaises(ValueError):
                store.load(settings)

    def test_interrupted_session_can_load_complete_protocol(self):
        store = SessionStore(self.root / "sessions", "interrupted")
        self.messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": "pending", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}]})
        store.save(self.settings, self.messages)
        loaded = store.load(self.settings)
        self.assertEqual(loaded[-1]["tool_call_id"], "pending")
        self.assertFalse(json.loads(loaded[-1]["content"])["ok"])

    def test_coding_fixture_fails_then_correct_change_passes_real_subprocess(self):
        example = Path(__file__).resolve().parents[1] / "examples" / "coding"
        target = self.root / "coding"
        shutil.copytree(example, target)
        tools = WorkspaceTools(target, lambda _: True)
        red = tools.execute("run_tests", '{}')
        self.assertFalse(red["ok"])
        self.assertIn("FAILED (failures=2)", red["output"])
        path = target / "budget.py"
        corrected = path.read_text(encoding="utf-8").replace("people * (per_person + venue)", "people * per_person + venue")
        self.assertTrue(tools.execute("write_file", json.dumps({"path": "budget.py", "content": corrected}))["ok"])
        green = tools.execute("run_tests", '{}')
        self.assertTrue(green["ok"], green)
        self.assertIn("Ran 3 tests", green["output"])

    def test_denied_test_execution_does_not_start_python(self):
        (self.workspace / "tests").mkdir()
        (self.workspace / "tests" / "test_sideeffect.py").write_text(
            'from pathlib import Path\nPath("marker").write_text("ran")\n')
        self.assertFalse(self.tools.execute("run_tests", '{}')["ok"])
        self.assertFalse((self.workspace / "marker").exists())


if __name__ == "__main__":
    unittest.main()
