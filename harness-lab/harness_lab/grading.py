"""Run the upstream pytest assertions, relocating container paths for local use.

This is a local port, not an official container score or an OS security boundary.
"""
from __future__ import annotations
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from .execution import _execute

DRIVER = r'''import os,sys,subprocess
import pytest
original_run = subprocess.run
def local_run(args,*pos,**kw):
    if isinstance(args,(list,tuple)) and args:
        args=list(args)
        if args[0] in ('python','python3'):
            args[0]=sys.executable
        elif args[0] in ('mypy','flake8'):
            args=[sys.executable,'-m',args[0],*args[1:]]
    return original_run(args,*pos,**kw)
subprocess.run=local_run
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
raise SystemExit(pytest.main([sys.argv[1],'-q','-p','no:cacheprovider','--rootdir',sys.argv[2],'--junitxml',sys.argv[3]]))
'''


async def grade(task_dir: Path, workspace: Path, timeout: float = 360) -> dict:
    from .benchmark_source import relocate
    # Tests stay outside the tool-visible workspace. They are public upstream
    # material; an unrestricted local process can still access host files.
    destination = workspace.parent / 'verifier'
    destination.mkdir(exist_ok=False)
    test = destination / 'test_outputs.py'
    code = relocate((task_dir/'tests/test_outputs.py').read_text(encoding='utf-8'), workspace)
    if task_dir.name == 'recover-encrypted-db-credentials':
        # Keep expected answers in the verifier cache, outside the agent fixture.
        # Replace after relocation so a cache rooted at /tmp is not rewritten.
        exposed = str(workspace.resolve() / 'protected/test_data/expected_keys.txt')
        private = str(task_dir.resolve() / 'environment/test_data/expected_keys.txt')
        code = code.replace(exposed, private)
    test.write_text(code, encoding='utf-8')
    junit = destination / 'results.xml'
    cwd = workspace / ('home/user' if task_dir.name == 'advanced-json-to-rfc4180-csv-converter' else 'workspace' if task_dir.name == 'implement-go-board-analyzer' else 'app')
    result = await _execute(cwd, [sys.executable, '-I', '-u', '-c', DRIVER, str(test), str(destination), str(junit)], '', timeout)
    (destination/'stdout.txt').write_text(result['stdout'],encoding='utf-8')
    (destination/'stderr.txt').write_text(result['stderr'],encoding='utf-8')
    passed = total = skipped = failed = errors = 0
    if junit.exists():
        try:
            cases = ET.parse(junit).getroot().findall('.//testcase')
            total = len(cases)
            failed = sum(case.find('failure') is not None for case in cases)
            errors = sum(case.find('error') is not None for case in cases)
            skipped = sum(case.find('skipped') is not None for case in cases)
            passed = total - failed - errors - skipped
        except ET.ParseError:
            errors = 1
    success = result['exit_code'] == 0 and total > 0 and not (failed or errors or result['timed_out'] or result['truncated'] or result.get('cleanup_incomplete', False))
    return {'reward': int(success), 'checks_passed': passed, 'checks_total': total,
            'checks_failed': failed, 'checks_errors': errors, 'checks_skipped': skipped,
            'cleanup_incomplete': result.get('cleanup_incomplete', False),
            'exit_code': result['exit_code'], 'timed_out':result['timed_out'], 'truncated':result['truncated'],
            'details': {'junit':str(junit), 'stdout':str(destination/'stdout.txt'), 'stderr':str(destination/'stderr.txt')}}
