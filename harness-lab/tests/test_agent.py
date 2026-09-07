"""Offline protocol, side-effect, adapter and cancellation tests; no model claims."""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from harness_lab.agent import Agent, Limits, ModelReply, SessionStore, ToolRequest, ToolResult, ToolSpec
from harness_lab.providers import OllamaProvider, OpenAIProvider
from harness_lab.tools import LocalTools


SPEC = ToolSpec("echo", "Return text", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})


class ScriptedProvider:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.inputs = []

    async def complete(self, messages, tools):
        self.inputs.append(json.loads(json.dumps(messages)))
        reply = next(self.replies)
        if isinstance(reply, BaseException):
            raise reply
        return reply


class Echo:
    definitions = [SPEC]
    def __init__(self):
        self.calls = []

    async def execute(self, request):
        self.calls.append(request)
        args = json.loads(request.arguments) if isinstance(request.arguments, str) else request.arguments
        return ToolResult(request.id, request.name, True, args["text"])


def call(identity="one", text="evidence"):
    return ToolRequest(identity, "echo", {"text": text})


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_calls_return_matching_results_before_next_judgment(self):
        backend = Echo()
        provider = ScriptedProvider(ModelReply(tool_requests=[call("a"), call("b")]), ModelReply("done"))
        result = await Agent(provider, backend).run("read")
        tool_messages = [m for m in provider.inputs[1] if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tool_messages], ["a", "b"])
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.metrics.tool_calls, 2)
        self.assertFalse(result.metrics.usage_known)

    async def test_tool_failure_is_recoverable(self):
        provider = ScriptedProvider(ModelReply(tool_requests=[ToolRequest("a", "missing", {})]), ModelReply("could not execute"))
        result = await Agent(provider, Echo()).run("x")
        self.assertEqual(result.status, "completed")
        self.assertFalse(json.loads(provider.inputs[1][-1]["content"])["ok"])
        self.assertEqual(result.metrics.tool_errors, 1)

    async def test_invalid_arguments_never_reach_backend(self):
        backend = Echo()
        provider = ScriptedProvider(ModelReply(tool_requests=[ToolRequest("a", "echo", {"text": 12})]), ModelReply("invalid input"))
        result = await Agent(provider, backend).run("x")
        self.assertEqual(backend.calls, [])
        self.assertEqual(result.metrics.tool_errors, 1)

    async def test_call_limit_never_runs_excess_call(self):
        backend = Echo()
        provider = ScriptedProvider(ModelReply(tool_requests=[call("a"), call("b")]))
        result = await Agent(provider, backend, Limits(max_tool_calls=1)).run("x")
        self.assertEqual(result.status, "tool_limit")
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual([m["tool_call_id"] for m in result.messages if m["role"] == "tool"], ["a", "b"])

    async def test_step_and_context_limits_are_not_completion(self):
        provider = ScriptedProvider(ModelReply(tool_requests=[call()]))
        result = await Agent(provider, Echo(), Limits(max_steps=1)).run("x")
        self.assertEqual(result.status, "step_limit")
        provider = ScriptedProvider()
        result = await Agent(provider, Echo(), Limits(max_context_chars=10)).run("x" * 100)
        self.assertEqual(result.status, "context_limit")
        self.assertEqual(result.metrics.model_calls, 0)

    async def test_total_timeout_stops_provider(self):
        class Waiting:
            async def complete(self, messages, tools):
                await asyncio.sleep(10)
        result = await Agent(Waiting(), Echo(), Limits(total_timeout_seconds=0.02)).run("x")
        self.assertEqual(result.status, "timeout")

    async def test_tool_timeout_has_result_and_can_continue(self):
        class Waiting(Echo):
            async def execute(self, request):
                await asyncio.sleep(10)
        provider = ScriptedProvider(ModelReply(tool_requests=[call()]), ModelReply("timed out"))
        result = await Agent(provider, Waiting(), Limits(tool_timeout_seconds=0.02)).run("x")
        self.assertEqual(result.status, "completed")
        self.assertFalse(json.loads(provider.inputs[1][-1]["content"])["ok"])

    async def test_output_limit_includes_truncation_marker(self):
        provider = ScriptedProvider(ModelReply(tool_requests=[call(text="x" * 1000)]), ModelReply("done"))
        await Agent(provider, Echo(), Limits(max_tool_output_chars=40)).run("x")
        output = json.loads(provider.inputs[1][-1]["content"])["output"]
        self.assertEqual(len(output), 40)
        self.assertIn("truncated", output)

    async def test_provider_failure_trace_has_type_not_secret(self):
        events = []
        result = await Agent(ScriptedProvider(RuntimeError("secret-key")), Echo(), trace=events.append).run("x")
        self.assertEqual(result.status, "failed")
        self.assertTrue(any(e.get("error_type") == "RuntimeError" for e in events))
        self.assertNotIn("secret-key", json.dumps(events))

    async def test_usage_is_summed_when_reported(self):
        provider = ScriptedProvider(ModelReply("done", usage={"input_tokens": 7, "output_tokens": 3}))
        result = await Agent(provider, Echo()).run("x")
        self.assertTrue(result.metrics.usage_known)
        self.assertEqual((result.metrics.input_tokens, result.metrics.output_tokens), (7, 3))

    async def test_cancellation_persists_pending_error_without_replay(self):
        started = asyncio.Event()
        class Waiting(Echo):
            async def execute(self, request):
                started.set()
                await asyncio.sleep(10)
        with TemporaryDirectory() as directory:
            store = SessionStore(Path(directory), "cancel")
            agent = Agent(ScriptedProvider(ModelReply(tool_requests=[call("a"), call("b")])), Waiting())
            task = asyncio.create_task(agent.run("x", session=store, settings={"model": "one"}))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            saved = store.load({"model": "one"})
            self.assertEqual([m["tool_call_id"] for m in saved if m["role"] == "tool"], ["a", "b"])
            self.assertTrue(all(not json.loads(m["content"])["ok"] for m in saved if m["role"] == "tool"))
            with self.assertRaises(ValueError):
                store.load({"model": "two"})

    async def test_session_resumes_previous_text(self):
        with TemporaryDirectory() as directory:
            store = SessionStore(Path(directory), "resume")
            await Agent(ScriptedProvider(ModelReply("previous")), Echo()).run("first", session=store)
            provider = ScriptedProvider(ModelReply("next"))
            await Agent(provider, Echo()).run("second", session=store)
            self.assertTrue(any(m.get("content") == "previous" for m in provider.inputs[0]))


class LocalTests(unittest.IsolatedAsyncioTestCase):
    async def test_paths_and_denied_write_preserve_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "work"
            workspace.mkdir()
            (workspace / "note.txt").write_text("before")
            (root / "outside.txt").write_text("private")
            (workspace / "link").symlink_to(root, target_is_directory=True)
            tools = LocalTools(workspace, lambda _: False)
            for value in ["../outside.txt", str(root / "outside.txt"), ".env", "link/outside.txt"]:
                result = await tools.execute(ToolRequest("a", "read_file", {"path": value}))
                self.assertFalse(result.ok)
            result = await tools.execute(ToolRequest("b", "write_file", {"path": "note.txt", "content": "after"}))
            self.assertFalse(result.ok)
            self.assertEqual((workspace / "note.txt").read_text(), "before")

    async def test_fifo_read_returns_without_blocking_process(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO fixture requires POSIX")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.mkfifo(root / "pipe")
            code = (
                "import asyncio,json,sys; from pathlib import Path; "
                "from harness_lab.tools import LocalTools; "
                "from harness_lab.agent import ToolRequest; "
                "t=LocalTools(Path(sys.argv[1]),lambda _:False); "
                "r=asyncio.run(t.execute(ToolRequest('id','read_file',{'path':'pipe'}))); "
                "print(json.dumps({'ok':r.ok,'error':r.error}))"
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-c", code, directory,
                cwd=Path(__file__).resolve().parents[1], stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), 3)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertFalse(json.loads(stdout)["ok"])
            self.assertIn("regular", json.loads(stdout)["error"])

    async def test_changed_file_during_approval_is_preserved(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "note.txt"
            target.write_text("original")
            def approve(_):
                target.write_text("another writer's update")
                return True
            tools = LocalTools(root, approve)
            result = await tools.execute(ToolRequest("id", "write_file", {"path":"note.txt", "content":"proposal"}))
            self.assertFalse(result.ok)
            self.assertEqual(target.read_text(), "another writer's update")
            self.assertEqual(list(root.glob(".harness-write-*")), [])

    async def test_new_file_created_during_approval_is_preserved(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "new.txt"
            def approve(_):
                target.write_text("")
                return True
            tools = LocalTools(root, approve)
            result = await tools.execute(ToolRequest("id", "write_file", {"path":"new.txt", "content":"proposal"}))
            self.assertFalse(result.ok)
            self.assertEqual(target.read_text(), "")

    async def test_atomic_replacement_failure_preserves_original_and_cleans_temp(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "note.txt"
            target.write_text("original")
            tools = LocalTools(root, lambda _: True)
            with patch("harness_lab.tools.os.replace", side_effect=OSError("simulated failure")):
                result = await tools.execute(ToolRequest("id", "write_file", {"path":"note.txt", "content":"proposal"}))
            self.assertFalse(result.ok)
            self.assertEqual(target.read_text(), "original")
            self.assertEqual(list(root.glob(".harness-write-*")), [])

    async def test_approved_write_and_real_command(self):
        with TemporaryDirectory() as directory:
            tools = LocalTools(Path(directory), lambda _: True)
            result = await tools.execute(ToolRequest("a", "write_file", {"path": "code.py", "content": "print(6 * 7)\n"}))
            self.assertTrue(result.ok)
            result = await tools.execute(ToolRequest("b", "run_command", {"argv": [sys.executable, "code.py"]}))
            self.assertTrue(result.ok)
            self.assertEqual(json.loads(result.output)["output"].strip(), "42")

    async def test_denied_command_never_creates_marker(self):
        with TemporaryDirectory() as directory:
            tools = LocalTools(Path(directory), lambda _: False)
            result = await tools.execute(ToolRequest("a", "run_command", {"argv": [sys.executable, "-c", "open('marker','w').close()"]}))
            self.assertFalse(result.ok)
            self.assertFalse((Path(directory) / "marker").exists())

    async def test_api_key_not_inherited_by_command(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret"}):
            tools = LocalTools(Path(directory), lambda _: True)
            result = await tools.execute(ToolRequest("a", "run_command", {"argv": [sys.executable, "-c", "import os; print('OPENAI_API_KEY' in os.environ)"]}))
            self.assertEqual(json.loads(result.output)["output"].strip(), "False")

    async def test_command_cancellation_kills_real_process(self):
        if os.name == "nt":
            self.skipTest("POSIX process liveness assertion")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tools = LocalTools(root, lambda _: True)
            code = "import os,time; open('pid','w').write(str(os.getpid())); time.sleep(30)"
            task = asyncio.create_task(tools.execute(ToolRequest("a", "run_command", {"argv": [sys.executable, "-c", code]})))
            for _ in range(100):
                if (root / "pid").exists():
                    break
                await asyncio.sleep(.01)
            pid = int((root / "pid").read_text())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_responses_preserves_reasoning_and_pairs_call_id(self):
        class Item(NS):
            def model_dump(self, **kwargs):
                return vars(self)
        items = [Item(type="reasoning", id="rs-1", encrypted_content="opaque", summary=[]),
                 Item(type="function_call", call_id="call-1", name="echo", arguments='{"text":"x"}', id="fc-1")]
        requests = []
        async def create(**kwargs):
            requests.append(kwargs)
            return NS(status="completed", output=items, usage=NS(input_tokens=10, output_tokens=4))
        provider = OpenAIProvider(NS(responses=NS(create=create)), "chosen")
        reply = await provider.complete([{"role": "user", "content": "x"}], [SPEC])
        self.assertEqual(reply.tool_requests[0].id, "call-1")
        messages = [{"role": "assistant", "content": "", "provider_items": reply.provider_items},
                    {"role": "tool", "tool_call_id": "call-1", "content": "result"}]
        converted = provider.input_items(messages)
        self.assertEqual(converted[0]["encrypted_content"], "opaque")
        self.assertEqual(converted[-1]["call_id"], "call-1")
        self.assertFalse(requests[0]["store"])

    async def test_incomplete_responses_rejected(self):
        async def create(**kwargs):
            return NS(status="incomplete")
        with self.assertRaises(ValueError):
            await OpenAIProvider(NS(responses=NS(create=create)), "x").complete([], [SPEC])

    async def test_ollama_assigns_internal_id_and_preserves_thinking(self):
        class Response:
            def raise_for_status(self): pass
            def json(self):
                return {"done": True, "done_reason": "stop", "message": {"content": "", "thinking": "reason",
                        "tool_calls": [{"function": {"name": "echo", "arguments": {"text": "x"}}}]}}
        class Client:
            async def post(self, path, json): return Response()
        provider = OllamaProvider(Client(), "local")
        reply = await provider.complete([], [SPEC])
        self.assertTrue(reply.tool_requests[0].id.startswith("local-"))
        self.assertEqual(reply.usage, {})
        converted = provider.input_messages([{"role": "assistant", "content": "", "tool_requests": [vars(reply.tool_requests[0])], "provider_items": reply.provider_items}])
        self.assertEqual(converted[0]["thinking"], "reason")
        self.assertEqual(converted[0]["tool_calls"][0]["function"]["arguments"], {"text": "x"})


if __name__ == "__main__":
    unittest.main()
