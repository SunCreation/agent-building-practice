"""Resumable engines. No silent fallback: demo is a deliberately selected fixture."""
import asyncio
import json
import os
import signal
import sys
import uuid
from pathlib import Path


def opencode_event(event):
    session = event.get("sessionID")
    kind = event.get("type", "unknown")
    part = event.get("part") or {}
    text = part.get("text", "") if kind == "text" else ""
    if kind == "error":
        raise RuntimeError("OpenCode error: " + json.dumps(event.get("error", {}), ensure_ascii=False))
    return session, text, kind


async def run_process(argv, cwd, env, emit, parse):
    process = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=os.name != "nt", limit=2**20)
    async def drain_stderr():
        # Drain fully to avoid pipe deadlock; keep a bounded diagnostic tail.
        tail = b""
        while chunk := await process.stderr.read(4096):
            tail = (tail + chunk)[-8000:]
        return tail.decode(errors="replace")
    stderr_task = asyncio.create_task(drain_stderr())
    session, chunks = None, []
    try:
        while line := await process.stdout.readline():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                raise RuntimeError("Engine emitted a non-JSON event")
            sid, text, kind = parse(event)
            session = sid or session
            if text:
                chunks.append(text)
            emit(kind)
            if sum(map(len, chunks)) > 100000:
                raise RuntimeError("Engine response too large")
        code = await process.wait()
        stderr = await stderr_task
        if code:
            raise RuntimeError(f"Engine exited with {code}: {stderr[-1500:]}")
        if not session or not chunks:
            raise RuntimeError("Engine did not return both session ID and text")
        return "".join(chunks), session
    finally:
        if process.returncode is None:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                await process.wait()
        if not stderr_task.done():
            stderr_task.cancel()
        await asyncio.gather(stderr_task, return_exceptions=True)


async def run_engine(engine, prompt, session_id, cwd, emit):
    env = os.environ.copy()
    if engine == "opencode":
        # A fresh agent name avoids inheriting the user's build-agent permissions.
        agent_name = "card-news-" + uuid.uuid4().hex
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps({
            "permission": {"*": "deny"}, "mcp": {},
            "agent": {agent_name: {"mode": "primary", "description": "Card-news JSON editor",
                                    "permission": {"*": "deny"}}},
        })
        argv = ["opencode", "run", "--pure", "--format", "json", "--agent", agent_name]
        if session_id:
            argv += ["--session", session_id]
        if env.get("STUDIO_OPENCODE_MODEL"):
            argv += ["--model", env["STUDIO_OPENCODE_MODEL"]]
        argv += ["--", prompt]
        return await run_process(argv, cwd, env, emit, opencode_event)
    if engine == "claude":
        # SDK runs in an isolated worker process, allowing actual cancellation
        # of SDK + child Claude processes rather than only hiding UI progress.
        request = Path(cwd) / "engine-request.json"
        request.write_text(json.dumps({"prompt": prompt, "session_id": session_id}), encoding="utf-8")
        def parse(event):
            if event.get("type") == "error":
                raise RuntimeError(event["message"])
            return event.get("sessionID"), event.get("text", ""), event["type"]
        return await run_process([sys.executable, str(Path(__file__).with_name("sdk_worker.py")), str(request)], cwd, env, emit, parse)
    raise ValueError("Unknown real engine")
