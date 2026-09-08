"""Reproducible adapter checks; never invoke a live model or image service."""
import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import engines


class JsonTests(unittest.TestCase):
    def test_full_and_fenced_json(self):
        self.assertEqual(engines.extract_json('{"ok": true}'), {"ok": True})
        self.assertEqual(engines.extract_json('Result:\n```json\n[1, 2]\n```'), [1, 2])

    def test_ambiguous_or_non_json_is_rejected(self):
        for text in ('not JSON', '```json\n{}\n```\n```json\n[]\n```', '__import__("os")'):
            with self.assertRaises(ValueError):
                engines.extract_json(text)


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def events(self):
        return [json.loads(line) for line in (self.cwd / "engine-events.jsonl").read_text().splitlines()]

    async def test_process_success_does_not_journal_raw_output(self):
        secret = "raw-output-must-not-be-journalled"
        result = await engines._run([sys.executable, "-c", f"print({secret!r})"], self.cwd, "success")
        self.assertEqual(result.strip(), secret)
        self.assertNotIn(secret, (self.cwd / "engine-events.jsonl").read_text())

    async def test_timeout_and_cancellation(self):
        argv = [sys.executable, "-c", "import time; time.sleep(30)"]
        with patch.object(engines, "TIMEOUT_SECONDS", .1):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                await engines._run(argv, self.cwd, "timeout")
        task = asyncio.create_task(engines._run(argv, self.cwd, "cancel"))
        await asyncio.sleep(.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(any(e.get("reason") == "timeout" for e in self.events()))
        self.assertTrue(any(e["event"] == "cancelled" for e in self.events()))

    async def test_claude_explicit_resume_and_tool_evidence(self):
        sid = str(uuid.uuid4())

        async def fake_run(argv, cwd, call_id):
            self.assertEqual(argv[argv.index("--resume") + 1], sid)
            self.assertNotIn("--continue", argv)
            self.assertIn("WebSearch,WebFetch,Read", argv)
            self.assertEqual(argv[-1], "prompt with $(shell syntax)")
            return json.dumps([
                {"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "id": "search", "name": "WebSearch"},
                    {"type": "tool_use", "id": "fetch", "name": "WebFetch"},
                ]}},
                {"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "search", "content": "source content"},
                    {"type": "tool_result", "tool_use_id": "fetch", "is_error": True,
                     "content": "permission denied"},
                ]}},
                {"type": "result", "subtype": "success", "is_error": False,
                 "result": '{"verified": true}', "session_id": sid},
            ])

        with patch.object(engines, "_run", fake_run):
            result = await engines.ask("prompt with $(shell syntax)", self.cwd, sid, "claude")
        self.assertEqual(result["session_id"], sid)
        self.assertEqual(result["tools_used"], ["WebSearch", "WebFetch"])
        self.assertEqual(result["successful_tools"], ["WebSearch"])
        self.assertEqual(result["failed_tools"], ["WebFetch"])
        self.assertNotIn("source content", (self.cwd / "engine-events.jsonl").read_text())

    async def test_attempt_without_result_is_not_success(self):
        async def fake_run(*args):
            return json.dumps([
                {"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "id": "unreturned", "name": "WebSearch"},
                ]}},
                {"type": "result", "subtype": "success", "result": "text",
                 "session_id": str(uuid.uuid4())},
            ])
        with patch.object(engines, "_run", fake_run):
            result = await engines.ask("test", self.cwd)
        self.assertEqual(result["tools_used"], ["WebSearch"])
        self.assertEqual(result["successful_tools"], [])
        self.assertEqual(result["failed_tools"], [])

    async def test_claude_error_result_rejected(self):
        async def fake_run(*args):
            return json.dumps({"type": "result", "subtype": "error", "is_error": True,
                               "result": "authentication required"})
        with patch.object(engines, "_run", fake_run):
            with self.assertRaisesRegex(RuntimeError, "authentication_required"):
                await engines.ask("test", self.cwd)

    async def test_image_and_provenance(self):
        async def fake_run(argv, cwd, call_id):
            self.assertIn("--dangerously-skip-permissions", argv)
            Image.new("RGB", (32, 40), "white").save(cwd / "background.png")
            return '{"status":"SUCCESS","response":"saved"}'

        with patch.object(engines, "_run", fake_run):
            result = await engines.generate_image("공개 이미지 설명", self.cwd)
        provenance = json.loads((self.cwd / "image-provenance.json").read_text())
        self.assertEqual(provenance["prompt"], "공개 이미지 설명")
        self.assertEqual(provenance["tool"], "Antigravity CLI")
        self.assertEqual((provenance["width"], provenance["height"]), (32, 40))
        self.assertTrue(provenance["generated"])
        self.assertEqual(provenance["sha256"], hashlib.sha256(result.read_bytes()).hexdigest())

    async def test_success_without_new_image_rejects_stale_png(self):
        Image.new("RGB", (32, 40)).save(self.cwd / "background.png")
        async def fake_run(*args):
            return '{"status":"SUCCESS"}'
        with patch.object(engines, "_run", fake_run):
            with self.assertRaisesRegex(RuntimeError, "was not created"):
                await engines.generate_image("test", self.cwd)
        self.assertEqual(len(list(self.cwd.glob("background.previous-*.png"))), 1)

    async def test_canceled_and_invalid_image_rejected(self):
        async def canceled(argv, cwd, call_id):
            Image.new("RGB", (32, 40)).save(cwd / "background.png")
            return '{"status":"CANCELED"}'
        with patch.object(engines, "_run", canceled):
            with self.assertRaisesRegex(RuntimeError, "did not report SUCCESS"):
                await engines.generate_image("test", self.cwd)
        async def invalid(argv, cwd, call_id):
            (cwd / "background.png").write_text("not an image")
            return '{"status":"SUCCESS"}'
        with patch.object(engines, "_run", invalid):
            with self.assertRaises(OSError):
                await engines.generate_image("test", self.cwd)
        self.assertFalse((self.cwd / "image-provenance.json").exists())


if __name__ == "__main__":
    unittest.main()
