"""Developer diagnostic: execute reviewed upstream Python heredocs without Bash.

Never used as the evaluated agent. Upstream scripts are not promised correct.
"""
from __future__ import annotations
from pathlib import Path
import re
import shlex
import sys
from .execution import _execute


async def run_reference(task_dir: Path, workspace: Path, timeout: float = 360) -> dict:
    from .benchmark_source import relocate
    lines = (task_dir/'solution/solve.sh').read_text(encoding='utf-8').splitlines()
    cwd = workspace/'app'
    records = []
    index = 0
    while index < len(lines):
        line = lines[index].strip(); index += 1
        marker = re.search(r"<<\s*['\"]?(\w+)['\"]?", line)
        if marker:
            body = []
            while index < len(lines) and lines[index].strip() != marker[1]:
                body.append(lines[index]); index += 1
            if index == len(lines):
                raise ValueError('Unclosed upstream heredoc')
            index += 1
            code = relocate('\n'.join(body)+'\n', workspace)
            if line.startswith('cat '):
                target_match = re.search(r'(?<![<>])>\s*([^\s]+)', line)
                if not target_match:
                    raise ValueError('Unknown upstream cat output')
                target = Path(relocate(target_match[1].strip("'\""), workspace))
                if not target.resolve().is_relative_to(workspace.resolve()):
                    raise ValueError('Reference output escapes trial')
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(code,encoding='utf-8')
            elif line.startswith('python3 '):
                records.append(await _execute(cwd,[sys.executable,'-I','-u','-c',code],'',timeout))
            else:
                raise ValueError('Unrecognized reference heredoc')
        elif line.startswith('python3 '):
            args = shlex.split(relocate(line, workspace))
            records.append(await _execute(cwd,[sys.executable,'-I','-u',*args[1:]],'',timeout))
        elif line.startswith('mkdir '):
            for token in shlex.split(relocate(line, workspace))[1:]:
                if token.startswith('-'):
                    continue
                path=Path(token)
                if not path.resolve().is_relative_to(workspace.resolve()):
                    raise ValueError('Reference directory escapes trial')
                path.mkdir(parents=True,exist_ok=True)
        elif not line or line.startswith(('#','echo ','chmod ','pip ','PAPERS_DIR=','OUTPUT_FILE=')):
            continue
        else:
            raise ValueError(f'Unreviewed reference shell statement: {line[:80]}')
    return {'status':'grader_self_check','answer':'Upstream reference diagnostic, not an agent result',
            'metrics':{},'reference_commands':records}
