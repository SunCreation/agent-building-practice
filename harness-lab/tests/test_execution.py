import asyncio
import os
from pathlib import Path
import pytest
from harness_lab.execution import run_python


def test_python_import_and_no_inherited_key(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-leak')
    (tmp_path/'helper.py').write_text('x=42')
    (tmp_path/'main.py').write_text("import os,helper; print(helper.x); print(os.getenv('OPENAI_API_KEY'))")
    result=asyncio.run(run_python(tmp_path,'main.py',[]))
    assert result['exit_code']==0 and result['stdout']=='42\nNone\n'


def test_hanging_and_flooding_programs_are_bounded(tmp_path):
    (tmp_path/'hang.py').write_text('while True: pass')
    assert asyncio.run(run_python(tmp_path,'hang.py',[],timeout=.1))['timed_out']
    (tmp_path/'flood.py').write_text("while True: print('x'*10000)")
    result=asyncio.run(run_python(tmp_path,'flood.py',[],timeout=2))
    assert result['truncated'] and len(result['stdout']) <= 256000


def test_path_escape_rejected(tmp_path):
    with pytest.raises(ValueError):
        asyncio.run(run_python(tmp_path,'../outside.py',[]))


def test_stderr_flood_and_combined_budget(tmp_path):
    from harness_lab.execution import OUTPUT_LIMIT
    import time
    (tmp_path/'both.py').write_text("import sys\nwhile True:\n print('o'*10000)\n print('e'*10000,file=sys.stderr)")
    started=time.monotonic()
    result=asyncio.run(run_python(tmp_path,'both.py',[],timeout=3))
    assert time.monotonic()-started < 5
    assert result['truncated'] and not result['timed_out']
    assert len(result['stdout'].encode())+len(result['stderr'].encode()) <= OUTPUT_LIMIT
    assert not result['cleanup_incomplete']


def test_large_stdin_to_child_that_does_not_read(tmp_path):
    (tmp_path/'ignore.py').write_text('import time; time.sleep(60)')
    result=asyncio.run(run_python(tmp_path,'ignore.py',[],stdin='x'*900000,timeout=.1))
    assert result['timed_out']
    assert not result['cleanup_incomplete']


@pytest.mark.skipif(os.name=='nt',reason='POSIX process-group guarantee only')
def test_parent_exit_with_child_holding_pipes(tmp_path):
    import time
    (tmp_path/'spawn.py').write_text(
        "import subprocess,sys\nsubprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\nprint('parent exited')"
    )
    started=time.monotonic()
    result=asyncio.run(run_python(tmp_path,'spawn.py',[],timeout=.2))
    assert time.monotonic()-started < 3
    assert result['timed_out'] and 'parent exited' in result['stdout']
    assert not result['cleanup_incomplete']


def test_cancellation_finishes_cleanup(tmp_path):
    (tmp_path/'sleep.py').write_text('import time; time.sleep(60)')
    async def check():
        task=asyncio.create_task(run_python(tmp_path,'sleep.py',[],timeout=30))
        await asyncio.sleep(.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task,timeout=4)
    asyncio.run(check())


def test_nonfinite_timeout_rejected(tmp_path):
    (tmp_path/'ok.py').write_text('print(42)')
    for timeout in [float('nan'),float('inf')]:
        with pytest.raises(ValueError):
            asyncio.run(run_python(tmp_path,'ok.py',[],timeout=timeout))


@pytest.mark.skipif(os.name=='nt',reason='POSIX escaped session demonstration')
def test_escaped_descendant_does_not_hang_pipe_cleanup(tmp_path):
    import signal
    import time
    (tmp_path/'escape.py').write_text(
        "import subprocess,sys,pathlib\n"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)\n"
        "pathlib.Path('escaped.pid').write_text(str(p.pid))\n"
    )
    try:
        started=time.monotonic()
        result=asyncio.run(run_python(tmp_path,'escape.py',[],timeout=.2))
        assert time.monotonic()-started < 4
        assert result['timed_out'] and result['cleanup_incomplete']
        # Escaping the group is possible: bounded pipe teardown is not an OS sandbox.
        os.kill(int((tmp_path/'escaped.pid').read_text()),0)
    finally:
        if (tmp_path/'escaped.pid').exists():
            try:
                os.killpg(int((tmp_path/'escaped.pid').read_text()),signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_output_exactly_at_limit_is_not_truncated(tmp_path):
    from harness_lab.execution import OUTPUT_LIMIT
    (tmp_path/'exact.py').write_text(f"import sys; sys.stdout.write('x'*{OUTPUT_LIMIT})")
    result=asyncio.run(run_python(tmp_path,'exact.py',[],timeout=2))
    assert result['exit_code']==0
    assert not result['truncated'] and not result['timed_out']
    assert len(result['stdout'])==OUTPUT_LIMIT
