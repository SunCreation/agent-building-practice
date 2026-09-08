"""Local tools: scoped file operations and human-approved argv execution.

An approved command runs as the user, not inside an OS sandbox. The local
benchmark uses a separate backend with a fixed Python execution tool.
"""
from __future__ import annotations

import asyncio
import difflib
import inspect
import json
import os
from pathlib import Path
import signal
import stat
import tempfile
from typing import Callable

from .agent import ToolRequest, ToolResult, ToolSpec


class LocalTools:
    def __init__(self, workspace: Path, approve: Callable, max_file_bytes: int = 100_000):
        self.root = workspace.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Workspace must be a directory")
        self.approve = approve
        self.max_file_bytes = max_file_bytes
        string = {"type": "string"}
        def spec(name, description, fields):
            return ToolSpec(name, description, {"type": "object", "properties": fields,
                                                "required": list(fields), "additionalProperties": False})
        self.definitions = [
            spec("list_files", "List up to 200 visible files under the workspace", {}),
            spec("read_file", "Read a UTF-8 file by workspace-relative path", {"path": string}),
            spec("write_file", "Propose the full UTF-8 content of a file; human approval required", {"path": string, "content": string}),
            spec("run_command", "Execute an argv array in the workspace after human approval. No shell interpretation. Not an OS sandbox.",
                 {"argv": {"type": "array", "items": string, "minItems": 1, "maxItems": 30}}),
        ]
        self.registry = {"list_files": self.list_files, "read_file": self.read_file,
                         "write_file": self.write_file, "run_command": self.run_command}

    def path(self, value: str) -> Path:
        if not isinstance(value, str) or not value or len(value) > 500:
            raise ValueError("A nonempty relative path is required")
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Absolute paths and parent traversal are not allowed")
        target = self.root
        for part in relative.parts:
            if part.startswith("."):
                raise ValueError("Hidden paths are not allowed")
            target /= part
            if target.is_symlink():
                raise ValueError("Symbolic links are not allowed")
        if not target.resolve().is_relative_to(self.root):
            raise ValueError("Path leaves workspace")
        return target

    async def confirmed(self, description: str) -> bool:
        value = self.approve(description)
        return bool(await value) if inspect.isawaitable(value) else bool(value)

    async def list_files(self):
        files = []
        for directory, dirs, names in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__"
                             and not (Path(directory) / d).is_symlink())
            for name in sorted(names):
                target = Path(directory) / name
                if not name.startswith(".") and not target.is_symlink() and target.is_file():
                    files.append(str(target.relative_to(self.root)))
                    if len(files) == 200:
                        return {"files": files, "limit": 200}
        return {"files": files, "limit": 200}

    def file_snapshot(self, target: Path):
        """Bounded bytes plus identity/version, or None for an absent path.

        POSIX nonblocking/no-follow flags prevent FIFO waits and final-link
        traversal. Platforms without these flags use lstat/fstat identity checks;
        they still require a trusted workspace with one writer. Parent-directory
        races and arbitrary hostile concurrent filesystem changes are not a sandbox.
        """
        try:
            initial = target.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(initial.st_mode):
            raise ValueError("Only regular files are supported")
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_BINARY", 0)
        fd = os.open(target, flags)
        try:
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino)):
                raise ValueError("File changed during inspection")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                content = stream.read(self.max_file_bytes + 1)
            after = os.fstat(fd)
            if len(content) > self.max_file_bytes:
                raise ValueError("File exceeds byte limit")
            if (opened.st_mtime_ns, opened.st_size) != (after.st_mtime_ns, after.st_size):
                raise ValueError("File changed during read")
            return (content, after.st_dev, after.st_ino, after.st_mtime_ns, stat.S_IMODE(after.st_mode))
        finally:
            os.close(fd)

    async def read_file(self, path: str):
        snapshot = self.file_snapshot(self.path(path))
        if snapshot is None:
            raise ValueError("File does not exist")
        return {"path": path, "content": snapshot[0].decode("utf-8")}

    async def write_file(self, path: str, content: str):
        target = self.path(path)
        if not isinstance(content, str) or len(content.encode("utf-8")) > self.max_file_bytes:
            raise ValueError("Content must be UTF-8 text within byte limit")
        before = self.file_snapshot(target)
        before_text = before[0].decode("utf-8") if before is not None else ""
        diff = "".join(difflib.unified_diff(before_text.splitlines(True), content.splitlines(True), fromfile=path, tofile=path))
        if not await self.confirmed("Write " + path + "\n" + diff):
            return {"ok": False, "error": "User denied file write"}
        target = self.path(path)
        if self.file_snapshot(target) != before:
            return {"ok": False, "error": "File changed during approval; review a fresh diff"}
        target.parent.mkdir(parents=True, exist_ok=True)
        # Same-directory replacement avoids exposing a half-written file. It is
        # not compare-and-swap: use one writer; recheck narrows, not removes, races.
        fd, temporary = tempfile.mkstemp(prefix=".harness-write-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, before[4] if before is not None else 0o600)
            target = self.path(path)
            if self.file_snapshot(target) != before:
                return {"ok": False, "error": "File changed before replacement; review a fresh diff"}
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {"path": path, "bytes": len(content.encode("utf-8"))}

    async def run_command(self, argv: list[str]):
        if (not isinstance(argv, list) or not 1 <= len(argv) <= 30
                or any(not isinstance(arg, str) or not arg or len(arg) > 2000 for arg in argv)):
            raise ValueError("argv must be an array of 1–30 nonempty strings")
        if not await self.confirmed("Run in " + str(self.root) + ":\n" + json.dumps(argv, ensure_ascii=False)
                                    + "\nThis code has your OS permissions. Approve only trusted commands."):
            return {"ok": False, "error": "User denied command execution"}
        env = {key: value for key, value in os.environ.items()
               if key in {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TMPDIR", "TEMP", "LANG"}}
        with tempfile.TemporaryFile() as capture:
            process = await asyncio.create_subprocess_exec(
                *argv, cwd=self.root, env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=capture, stderr=asyncio.subprocess.STDOUT, start_new_session=os.name != "nt",
            )
            try:
                code = await process.wait()
            finally:
                if process.returncode is None:
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    await process.wait()
            size = capture.seek(0, 2)
            capture.seek(0)
            output = capture.read(32_000)
            if size > 32_000:
                capture.seek(-16_000, 2)
                output = output[:16_000] + b"\n[output truncated]\n" + capture.read(16_000)
        return {"ok": code == 0, "exit_code": code, "output": output.decode("utf-8", errors="replace")}

    async def execute(self, request: ToolRequest) -> ToolResult:
        try:
            args = json.loads(request.arguments) if isinstance(request.arguments, str) else request.arguments
            if not isinstance(args, dict) or request.name not in self.registry:
                raise ValueError("Unknown tool or invalid argument object")
            data = await self.registry[request.name](**args)
            return ToolResult(request.id, request.name, data.get("ok", True),
                              json.dumps(data, ensure_ascii=False), data.get("error"))
        except (ValueError, TypeError, OSError, UnicodeError) as exc:
            return ToolResult(request.id, request.name, False, "", f"{type(exc).__name__}: {exc}")
