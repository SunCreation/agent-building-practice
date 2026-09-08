"""Fetch pinned upstream files; prepare path-relocated tasks without Docker.

Reference solutions and verifier scripts remain in the cache, never in the agent
workspace. The caller must deny the agent access to that cache. Original Docker
COPY is preserved except recover expected_keys.txt, kept evaluator-only in cache.
This is a local-port correction of answer exposure, not a security sandbox.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'benchmark' / 'tasks.json'
DEFAULT_CACHE = ROOT / '.benchmark-cache'

# Explicitly transcribed Docker COPY and file preparation, not a Docker parser.
COPY_MAP = {
    'extract-paper-metadata-to-json': [('papers', 'protected/papers')],
    'python-sudoku-solver-backtracking': [('puzzle.txt', 'app/puzzle.txt'), ('test_data', 'protected/test_data')],
    'detect-corrupted-blockchain-transaction': [('blockchain_ledger.json', 'app/blockchain_ledger.json')],
    'implement-go-board-analyzer': [(f'board_{i:03}.json', f'protected/board_{i:03}.json') for i in range(1, 4)],
    'python-sokoban-bfs-solver': [('level1.txt', 'app/level1.txt'), ('test_data', 'protected/test_data')],
    'recover-encrypted-db-credentials': [('test_data', 'protected/test_data')],
    'advanced-json-to-rfc4180-csv-converter': [('sales.json', 'home/user/sales.json')],
    'implement-depgraph-dependency-resolver': [('packages', 'protected/packages')],
    'implement-lz77-file-compressor': [('decompress.py', 'app/decompress.py'), ('test_data', 'protected/test_data'), ('test_data', 'app/test_data')],
    'implement-nonogram-puzzle-solver': [('puzzle.txt', 'app/puzzle.txt'), ('puzzles', 'protected/puzzles')],
}


def _manifest() -> dict:
    data = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if data.get('repo') != 'alibaba/terminal-bench-pro' or not re.fullmatch(r'[a-f0-9]{40}', data.get('revision', '')):
        raise ValueError('Expected the pinned upstream repository and full commit')
    return data


def _safe_file(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('Unsafe source path')
    result = root / path
    if result.is_symlink() or not result.resolve().is_relative_to(root.resolve()):
        raise ValueError('Source path escapes cache')
    return result


def _verify(path: Path, record: dict) -> None:
    content = path.read_bytes()
    if len(content) != record['size_bytes'] or hashlib.sha256(content).hexdigest() != record['sha256']:
        raise ValueError(f'Upstream SHA256/size mismatch: {path}')


def _fetch_task(name: str, cache: Path, manifest: dict) -> Path:
    task = next((t for t in manifest['tasks'] if t['name'] == name), None)
    if task is None or name not in COPY_MAP:
        raise ValueError(f'Unknown task: {name}')
    folder = _safe_file(cache, name)
    folder.mkdir(parents=True, exist_ok=True)
    for record in task['source_files']:
        path = _safe_file(folder, record['path'])
        if not path.exists():
            address = f"https://raw.githubusercontent.com/{manifest['repo']}/{manifest['revision']}/{name}/{record['path']}"
            path.parent.mkdir(parents=True, exist_ok=True)
            with urlopen(address, timeout=60) as response:
                data = response.read(record['size_bytes'] + 1)
            if len(data) != record['size_bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
                raise ValueError(f'Download SHA256/size mismatch: {name}/{record["path"]}')
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
                temp.write(data)
                pending = Path(temp.name)
            try:
                pending.replace(path)
            finally:
                pending.unlink(missing_ok=True)
        _verify(path, record)
    upstream = tomllib.loads((folder / 'task.toml').read_text())['metadata']
    if upstream['difficulty'] != task['difficulty'] or upstream['category'] != task['category']:
        raise ValueError('Manifest metadata differs from upstream task.toml')
    return folder


def ensure_sources(cache: Path | None = None) -> Path:
    """Fetch and revalidate every pinned source file in all ten tasks."""
    cache = Path(cache or DEFAULT_CACHE).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    manifest = _manifest()
    for task in manifest['tasks']:
        _fetch_task(task['name'], cache, manifest)
    return cache


def relocate(text: str, workspace: Path) -> str:
    """Rewrite upstream absolute roots once, including quoted code literals."""
    base = Path(workspace).resolve().as_posix()
    # One regex pass avoids replacing /tmp in the newly inserted workspace path.
    pattern = r'(?<![A-Za-z0-9_])/(?:home/user|protected|workspace|app|db|tmp)(?=/|[^A-Za-z0-9_]|$)'
    return re.sub(pattern, lambda match: base + match.group(0), text)


def prepare(task_name: str, target: Path, cache: Path | None = None) -> dict:
    """Prepare one clean trial workspace. Never overwrites an existing trial."""
    cache = Path(cache or DEFAULT_CACHE).resolve()
    manifest = _manifest()
    task_dir = _fetch_task(task_name, cache, manifest)
    records = next(t['source_files'] for t in manifest['tasks'] if t['name'] == task_name)
    target = Path(target).resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError('Target must be a new or empty workspace')
    # Relocation operates on source strings; reject unsafe source literal paths.
    if any(ch in target.as_posix() for ch in ("'", '"', '\n', '\r', '\\')):
        raise ValueError('Workspace path cannot contain quotes, backslashes or newlines')
    for relative in ('app', 'protected', 'db', 'home/user', 'workspace', 'tmp'):
        (target / relative).mkdir(parents=True, exist_ok=True)
    for source, destination in COPY_MAP[task_name]:
        src, dst = task_dir / 'environment' / source, target / destination
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            prefix = 'environment/' + source + '/'
            for record in records:
                if record['path'].startswith(prefix):
                    if task_name == 'recover-encrypted-db-credentials' and record['path'] == 'environment/test_data/expected_keys.txt':
                        continue  # Evaluator-only oracle data stays in the verified cache.
                    leaf = dst / record['path'][len(prefix):]
                    leaf.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(task_dir / record['path'], leaf)
        else:
            if 'environment/' + source not in {r['path'] for r in records}:
                raise ValueError('COPY source is not in the verified manifest')
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    if task_name == 'implement-lz77-file-compressor':
        (target / 'app/compressed').mkdir()
    setup_log = ''
    if task_name == 'recover-encrypted-db-credentials':
        shell = (task_dir / 'environment/setup.sh').read_text()
        marker = "python3 << 'PYTHON_SETUP'\n"
        if marker not in shell or '\nPYTHON_SETUP' not in shell:
            raise ValueError('Unknown upstream setup heredoc')
        code = shell.split(marker, 1)[1].split('\nPYTHON_SETUP', 1)[0]
        result = subprocess.run([sys.executable, '-c', relocate(code, target)], cwd=target / 'app', capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(f'Upstream setup failed: {result.stderr}')
        setup_log = result.stdout
    cwd = target / ('home/user' if task_name == 'advanced-json-to-rfc4180-csv-converter' else 'workspace' if task_name == 'implement-go-board-analyzer' else 'app')
    hashes = {p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(target.rglob('*')) if p.is_file()}
    return {'task_dir': task_dir, 'instruction': relocate((task_dir / 'instruction.md').read_text(encoding='utf-8'), target), 'cwd': cwd, 'workspace': target, 'fixture_sha256': hashes, 'setup_log': setup_log}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', nargs='?', const='all', choices=['all', *sorted(COPY_MAP)], help='Without a task name, fetch all sources; with a task name, prepare its workspace')
    parser.add_argument('--target', type=Path)
    parser.add_argument('--cache', type=Path)
    args = parser.parse_args(argv)
    if args.prepare and args.prepare != 'all':
        if args.target is None:
            parser.error('--prepare requires --target')
        result = prepare(args.prepare, args.target, args.cache)
    else:
        result = {'cache': ensure_sources(args.cache)}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
