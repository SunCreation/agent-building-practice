"""Local backend integration with scripted model replies; no real model calls."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from harness_lab.agent import Agent, ModelReply, ToolRequest
from harness_lab.local_agent import LocalBenchmarkTools, solve_task


class Scripted:
    def __init__(self):
        self.step = 0
    async def complete(self, messages, tools):
        self.step += 1
        if self.step == 1:
            return ModelReply(tool_requests=[ToolRequest("write", "write_file", {"path":"solution.py", "content":"print(42)\n"})])
        if self.step == 2:
            return ModelReply(tool_requests=[ToolRequest("test", "run_python", {"path":"solution.py", "args":[]})])
        if self.step == 3:
            result = json.loads(messages[-1]["content"])
            assert "42" in result["output"]
            return ModelReply("Observed 42")
        raise AssertionError("Unexpected loop")


class LocalAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_loop_writes_and_runs_python_without_shell_tool(self):
        with TemporaryDirectory() as directory:
            backend = LocalBenchmarkTools(Path(directory))
            self.assertNotIn("run_command", [s.name for s in backend.definitions])
            result = await Agent(Scripted(), backend).run("Implement and check")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.metrics.tool_calls, 2)
            self.assertEqual((Path(directory) / "solution.py").read_text(), "print(42)\n")

    async def test_escape_and_arbitrary_command_are_rejected(self):
        with TemporaryDirectory() as directory:
            backend = LocalBenchmarkTools(Path(directory))
            for request in [ToolRequest("x", "run_command", {"argv":["echo","no"]}),
                            ToolRequest("y", "run_python", {"path":"../outside.py", "args":[]}),
                            ToolRequest("z", "write_file", {"path":"../outside.py", "content":"x"})]:
                self.assertFalse((await backend.execute(request)).ok)

    async def test_internal_absolute_paths_write_read_and_run_without_mutating_request(self):
        with TemporaryDirectory() as directory:
            backend = LocalBenchmarkTools(Path(directory))
            # Test the caller's spelling and the canonical approved root; these
            # differ on macOS installations where /var points to /private/var.
            for index, root in enumerate((Path(directory), backend.workspace)):
                path = str(root / f"check{index}.py")
                args = {"path": path, "content": "print(42)\n"}
                written = await backend.execute(ToolRequest("write", "write_file", args))
                self.assertTrue(written.ok, written.error)
                self.assertEqual(args["path"], path)
                read = await backend.execute(ToolRequest("read", "read_file", json.dumps({"path": path})))
                self.assertTrue(read.ok, read.error)
                self.assertEqual(json.loads(read.output)["content"], "print(42)\n")
                run = await backend.execute(ToolRequest("run", "run_python", {"path": path, "args": []}))
                self.assertTrue(run.ok, run.error)
                self.assertEqual(json.loads(run.output)["stdout"], "42\n")

    async def test_external_absolute_paths_and_parent_traversal_are_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            work = root / "work"
            work.mkdir()
            (work / "check.py").write_text("print(42)\n")
            backend = LocalBenchmarkTools(work)
            forbidden = [str(root / "outside.py"), str(root / "work-extra/check.py"),
                         str(work / "sub/../check.py"), "sub/../check.py",
                         str(work / "../work/check.py"), str(work / ".hidden.py")]
            for path in forbidden:
                for name, extra in (("read_file", {}), ("write_file", {"content": "x"}),
                                    ("run_python", {"args": []})):
                    with self.subTest(path=path, tool=name):
                        result = await backend.execute(ToolRequest("reject", name, {"path": path, **extra}))
                        self.assertFalse(result.ok)
            self.assertFalse((root / "outside.py").exists())
            self.assertEqual((work / "check.py").read_text(), "print(42)\n")

    async def test_absolute_paths_do_not_hide_symlinks_even_when_target_is_internal(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            work = root / "work"
            work.mkdir()
            (work / "real").mkdir()
            (work / "real/check.py").write_text("print(42)\n")
            try:
                (work / "link.py").symlink_to(work / "real/check.py")
                (work / "linked").symlink_to(work / "real", target_is_directory=True)
                (work / "external").symlink_to(root, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Symlink creation unavailable on this platform: {exc}")
            backend = LocalBenchmarkTools(work)
            for relative in ("link.py", "linked/check.py", "external/outside.py"):
                for path in (relative, str(work / relative)):
                    for name, extra in (("read_file", {}), ("write_file", {"content": "x"}),
                                        ("run_python", {"args": []})):
                        with self.subTest(path=path, tool=name):
                            self.assertFalse((await backend.execute(
                                ToolRequest("reject", name, {"path": path, **extra})
                            )).ok)
            self.assertEqual((work / "real/check.py").read_text(), "print(42)\n")

    async def test_incomplete_cleanup_or_truncated_output_is_not_tool_success(self):
        with TemporaryDirectory() as directory:
            backend = LocalBenchmarkTools(Path(directory))
            for flag in ('cleanup_incomplete', 'truncated'):
                value = dict(exit_code=0, stdout='', stderr='', timed_out=False, truncated=False)
                value[flag] = True
                with patch('harness_lab.local_agent.run_python', new=AsyncMock(return_value=value)) as execute:
                    result = await backend.execute(ToolRequest('check', 'run_python', {'path':'check.py', 'args':[]}))
                execute.assert_awaited_once()
                self.assertFalse(result.ok)

    async def test_default_entry_point_uses_provider_adapter_and_writes_trace(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY":"test-only"}):
            root = Path(directory)
            workspace = root / "work"
            workspace.mkdir()
            client = SimpleNamespace(close=AsyncMock())
            options = {"provider":"openai", "model":"scripted", "max_steps":5, "max_seconds":10, "command_timeout":2}
            with patch("harness_lab.local_agent.AsyncOpenAI", return_value=client), patch("harness_lab.local_agent.OpenAIProvider", return_value=Scripted()):
                result = await solve_task("check", workspace, root / "logs", options)
            self.assertEqual(result["status"], "completed")
            events = (root / "logs/events.jsonl").read_text()
            self.assertIn('"event": "run_end"', events)
            self.assertNotIn("test-only", events)
            transcript = (root / "logs/sessions/trial.json").read_text()
            self.assertNotIn("test-only", transcript)
            session = json.loads(transcript)
            self.assertEqual(session["settings"], {
                "provider": "openai", "model": "scripted",
                "workspace": str(workspace.resolve()), "task_cwd": ".",
            })
            messages = session["messages"]
            self.assertTrue(any(m["role"] == "user" and m["content"].startswith("check\n") for m in messages))
            calls = [call for m in messages for call in m.get("tool_requests", [])]
            self.assertEqual(calls[0]["arguments"], {"path": "solution.py", "content": "print(42)\n"})
            self.assertEqual(calls[1]["arguments"], {"path": "solution.py", "args": []})
            outputs = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
            self.assertEqual(len(outputs), 2)
            self.assertTrue(all(output["ok"] for output in outputs))
            self.assertEqual(json.loads(outputs[1]["output"])["stdout"], "42\n")
            self.assertEqual(messages[-1]["content"], "Observed 42")
            client.close.assert_awaited_once()

    async def test_provider_failure_preserves_prior_tool_inputs_and_results(self):
        class FailsAfterTools(Scripted):
            async def complete(self, messages, tools):
                if self.step == 2:
                    raise RuntimeError("Synthetic provider failure")
                return await super().complete(messages, tools)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "work"
            workspace.mkdir()
            client = SimpleNamespace(aclose=AsyncMock())
            options = {"provider": "ollama", "model": "scripted-failure", "max_steps": 5,
                       "max_seconds": 10, "command_timeout": 2, "task_cwd": "app"}
            with patch("harness_lab.local_agent.httpx.AsyncClient", return_value=client), patch(
                "harness_lab.local_agent.OllamaProvider", return_value=FailsAfterTools()
            ):
                result = await solve_task("Synthetic test task", workspace, root / "logs", options)
            self.assertEqual(result["status"], "failed")
            session = json.loads((root / "logs/sessions/trial.json").read_text())
            self.assertEqual(session["settings"]["provider"], "ollama")
            self.assertEqual(session["settings"]["model"], "scripted-failure")
            self.assertEqual(session["settings"]["task_cwd"], "app")
            messages = session["messages"]
            calls = [call for m in messages for call in m.get("tool_requests", [])]
            self.assertEqual(calls[0]["arguments"]["content"], "print(42)\n")
            output = json.loads(messages[-1]["content"])
            self.assertEqual(messages[-1]["tool_call_id"], "test")
            self.assertEqual(json.loads(output["output"])["stdout"], "42\n")
            self.assertIn('"status": "failed"', (root / "logs/events.jsonl").read_text())
            client.aclose.assert_awaited_once()
