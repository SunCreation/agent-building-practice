"""Bounded local Python execution. This is process control, NOT an OS sandbox."""
from __future__ import annotations
import asyncio
import math
import os
from pathlib import Path
import signal
import stat
import sys

OUTPUT_LIMIT = 256_000
DRIVER = """import runpy,sys
workspace, script = sys.argv[1:3]
sys.path.insert(0, workspace)
sys.argv = [script] + sys.argv[3:]
runpy.run_path(script, run_name='__main__')
"""


async def _execute(workspace: Path, argv: list[str], stdin: str, timeout: float) -> dict:
    """Bound bytes retained while continuing to drain both pipes until shutdown.

    POSIX: kill the original process group, including descendants that remain in
    it. Children that deliberately create another session can escape this group.
    Windows: kill only the direct process. Pipe cleanup is bounded on both OSes;
    this function does not claim descendant containment or an OS sandbox.
    """
    if not math.isfinite(timeout) or timeout <= 0 or len(stdin.encode('utf-8')) > 1_000_000:
        raise ValueError('Invalid timeout or input too large')
    env = {k: v for k, v in os.environ.items() if k in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR', 'LANG'}}
    env['PYTHONIOENCODING'] = 'utf-8'
    process = await asyncio.create_subprocess_exec(
        *argv, cwd=workspace, env=env, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name != 'nt',
    )
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    exceeded = asyncio.Event()

    async def collect(stream, name):
        while chunk := await stream.read(8192):
            available = max(0, OUTPUT_LIMIT - len(buffers['stdout']) - len(buffers['stderr']))
            buffers[name].extend(chunk[:available])
            if len(chunk) > available:
                # Do not raise or stop reading here: asyncio's process.wait()
                # can wait for pipe EOF even after the child has been killed.
                exceeded.set()

    async def feed():
        try:
            process.stdin.write(stdin.encode('utf-8'))
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    io_tasks = [asyncio.create_task(collect(process.stdout, 'stdout')),
                asyncio.create_task(collect(process.stderr, 'stderr')),
                asyncio.create_task(feed())]

    async def finish():
        await asyncio.gather(*io_tasks)
        await process.wait()

    completion = asyncio.create_task(finish())
    limit_wait = asyncio.create_task(exceeded.wait())
    timed_out = cleanup_incomplete = False
    try:
        # asyncio.wait does not cancel the pipe readers when the timeout or
        # output-limit event wins. They must stay alive during process teardown.
        done, _ = await asyncio.wait(
            {completion, limit_wait}, timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        timed_out = not done
        if completion in done:
            completion.result()  # Propagate genuine reader/input errors.
    finally:
        if os.name != 'nt':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        # Readers continue draining bytes (discarding bytes over the cap).
        # An escaped descendant can retain pipe handles, so teardown also has
        # its own deadline rather than waiting for EOF forever.
        finished, _ = await asyncio.wait({completion}, timeout=2)
        if not finished:
            cleanup_incomplete = True
            # asyncio exposes no public API to close its subprocess pipe read
            # transports. Closing the subprocess transport releases them when
            # a descendant outside our control keeps its copy open.
            process._transport.close()
            completion.cancel()
        limit_wait.cancel()
        for task in io_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(completion, limit_wait, *io_tasks, return_exceptions=True)
    return {'exit_code': process.returncode,
            'stdout': buffers['stdout'].decode('utf-8', errors='replace'),
            'stderr': buffers['stderr'].decode('utf-8', errors='replace'),
            'timed_out': timed_out, 'truncated': exceeded.is_set(),
            'cleanup_incomplete': cleanup_incomplete}


def script_path(workspace: Path, path: str) -> Path:
    root = workspace.resolve(strict=True)
    relative = Path(path)
    if not isinstance(path, str) or relative.is_absolute() or '..' in relative.parts or relative.suffix != '.py':
        raise ValueError('Use a workspace-relative .py file')
    current = root
    for part in relative.parts:
        if part.startswith('.'):
            raise ValueError('Hidden paths are not allowed')
        current /= part
        if current.is_symlink():
            raise ValueError('Symbolic links are not allowed')
    if not current.resolve().is_relative_to(root) or not stat.S_ISREG(current.stat().st_mode):
        raise ValueError('Only regular Python files inside workspace are executable')
    return current


async def run_python(workspace: Path, path: str, args: list[str], stdin: str = '', timeout: float = 10) -> dict:
    root = workspace.resolve(strict=True)
    target = script_path(root, path)
    if not isinstance(args, list) or len(args) > 30 or any(not isinstance(a, str) or len(a) > 2000 for a in args):
        raise ValueError('Invalid argument list')
    return await _execute(root, [sys.executable, '-I', '-u', '-c', DRIVER, str(root), str(target), *args], stdin, timeout)
