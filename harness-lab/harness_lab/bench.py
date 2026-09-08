"""Pinned local Python benchmark. Disposable copies are NOT an OS sandbox.

Run only trusted course agents/code on Windows/macOS/Linux. Grader cases and
reference files are never copied into agent workspaces. Source snapshots, not
later working-tree edits, execute each experiment.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import inspect
import json
import os
import platform
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmark/tasks.json"
DEFAULT_AGENT = "harness_lab.local_agent:solve_task"


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: dict):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = data["tasks"]
    if not isinstance(data.get("suite_id"), str) or not data["suite_id"].strip() or not isinstance(data.get("revision"), str) or not data["revision"].strip():
        raise ValueError("Manifest requires a suite_id and pinned revision")
    if len(tasks) != 10 or len({task["name"] for task in tasks}) != 10:
        raise ValueError("Manifest must contain ten distinct tasks")
    counts = Counter(task["difficulty"] for task in tasks)
    expected = data.get("selection_counts")
    if expected is None and data["suite_id"] == "local-harness-v1":
        expected = {"easy": 2, "medium": 4, "hard": 4}
    if expected is not None and counts != expected:
        raise ValueError("Task difficulties differ from pinned selection_counts")
    for task in tasks:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", task["name"]):
            raise ValueError("Invalid task name")
    return data


def copy_tree(source: Path, target: Path, *, python_only=False, verified_fixtures=False):
    """No links, hidden files, caches, environments or credential files."""
    target.mkdir(parents=True, exist_ok=False)
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part == "__pycache__" or (part.startswith(".") and not verified_fixtures) for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Snapshot source contains a symbolic link: {relative}")
        if path.is_file() and (not python_only or path.suffix == ".py"):
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def hash_tree(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(path.relative_to(directory).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def agent_parts(value: str):
    if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", value):
        raise ValueError("Agent must be module:async_function")
    return value.split(":")


def snapshot_sources(root: Path, destination: Path, manifest_path: Path, agent: str, upstream: Path) -> dict:
    destination.mkdir()
    copy_tree(root / "harness_lab", destination / "harness_lab", python_only=True)
    benchmark = destination / "benchmark"
    benchmark.mkdir()
    manifest = load_manifest(manifest_path)
    for task in manifest["tasks"]:
        for record in task["source_files"]:
            relative = Path(task["name"]) / record["path"]
            source = upstream / relative
            if source.is_symlink() or not source.resolve().is_relative_to(upstream.resolve()):
                raise ValueError("Upstream source escapes cache")
            content = source.read_bytes()
            if len(content) != record["size_bytes"] or hashlib.sha256(content).hexdigest() != record["sha256"]:
                raise ValueError("Upstream snapshot does not match manifest")
            target = benchmark / "upstream" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    shutil.copy2(manifest_path, benchmark / "tasks.json")
    for name in ("pyproject.toml", "uv.lock"):
        if (root / name).exists():
            shutil.copy2(root / name, destination / name)
    module, _ = agent_parts(agent)
    source_path = None
    if not module.startswith("harness_lab."):
        top = module.split(".")[0]
        spec = importlib.util.find_spec(top)
        if spec is None or spec.origin is None:
            raise ValueError("Cannot locate custom agent source")
        source_path = str(Path(spec.origin).resolve())
        if spec.submodule_search_locations:
            copy_tree(Path(spec.origin).parent, destination / top, python_only=True)
        else:
            shutil.copy2(spec.origin, destination / (top + ".py"))
    return {"agent_source_path": source_path or str(root / (module.replace(".", "/") + ".py")),
            "source_sha256": hash_tree(destination)}


def preflight(args):
    if args.self_check:
        return []
    errors = []
    if not args.model:
        errors.append("Choose --model explicitly")
    if args.agent == DEFAULT_AGENT and args.provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        errors.append("OPENAI_API_KEY is not set")
    return errors


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--name", required=True)
    p.add_argument("--manifest", type=Path, default=MANIFEST)
    p.add_argument("--jobs", type=Path, default=ROOT / "jobs")
    p.add_argument("--provider", choices=["openai", "ollama"], default="openai")
    p.add_argument("--model", default="")
    p.add_argument("--agent", default=DEFAULT_AGENT)
    p.add_argument("--attempts", type=int, default=1)
    p.add_argument("--max-steps", type=int, default=40)
    p.add_argument("--max-seconds", type=float, default=300)
    p.add_argument("--command-timeout", type=float, default=10)
    p.add_argument("--self-check", action="store_true", help="Reference-to-grader check; NOT an agent score")
    p.add_argument("--dry-run", action="store_true", help="Validate configuration without executing")
    return p


def refresh_report(job: Path, manifest: Path, attempts: int):
    from .report import build_report, write_snapshot
    write_snapshot(build_report(job, manifest, attempts), job / "scoreboard")


async def worker(request_path: Path):
    from .grading import grade
    from .benchmark_source import prepare
    from .reference import run_reference
    request = json.loads(request_path.read_text(encoding="utf-8"))
    job = Path(request["job"])
    options = request["options"]
    manifest = load_manifest(ROOT / "benchmark/tasks.json")
    metadata = json.loads((job / "run-metadata.json").read_text(encoding="utf-8"))
    solve = None
    if not options["self_check"]:
        module, name = agent_parts(options["agent"])
        solve = getattr(importlib.import_module(module), name)
        if not inspect.iscoroutinefunction(solve):
            raise ValueError("Custom solve_task must be async")
    self_check_passed = True
    try:
        for task in manifest["tasks"]:
            for attempt in range(1, options["attempts"] + 1):
                trial = job / f"{task['name']}__{attempt}"
                trial.mkdir()
                workspace = trial / "workspace"
                prepared = prepare(task["name"], workspace, cache=ROOT / "benchmark/upstream")
                task_dir = Path(prepared["task_dir"])
                task_options = {**options, "task_cwd": str(Path(prepared["cwd"]).relative_to(workspace))}
                # prepare copies only verified public fixtures, never verifier/solution sources.
                logs = trial / "agent"
                logs.mkdir()
                result = {"id": uuid.uuid4().hex, "task_name": task["name"], "trial_name": trial.name,
                          "started_at": now(), "finished_at": None, "agent_result": None,
                          "fixture_sha256": prepared.get("fixture_sha256", {}),
                          "verifier_result": None, "exception_info": None}
                write_json(trial / "result.json", result)
                try:
                    if options["self_check"]:
                        reference_result = await run_reference(task_dir, workspace, timeout=360)
                        agent_result = {"status": "grader_self_check", "answer": "Reference used only for grader validation", "metrics": {}, "reference_execution": reference_result}
                    else:
                        async with asyncio.timeout(options["max_seconds"] + 5):
                            agent_result = await solve(prepared["instruction"], workspace, logs, task_options)
                        if not isinstance(agent_result, dict):
                            raise ValueError("solve_task must return a result dictionary")
                    metrics = agent_result.get("metrics", {})
                    known = metrics.get("usage_known", False)
                    result["agent_result"] = {**agent_result,
                        "n_input_tokens": metrics.get("input_tokens") if known else None,
                        "n_output_tokens": metrics.get("output_tokens") if known else None,
                        "n_cache_tokens": None, "cost_usd": None}
                    verdict = await grade(task_dir, workspace, timeout=360)
                    result["verifier_result"] = {"rewards": {"reward": verdict["reward"]}, **verdict}
                    if not options["self_check"] and agent_result.get("status") != "completed":
                        result["exception_info"] = {"exception_type": "AgentIncomplete", "message": str(agent_result.get("status"))}
                except asyncio.CancelledError:
                    result["exception_info"] = {"exception_type": "Interrupted"}
                    raise
                except Exception as exc:
                    result["exception_info"] = {"exception_type": type(exc).__name__}
                finally:
                    if options["self_check"] and (result["exception_info"] or (result.get("verifier_result") or {}).get("rewards", {}).get("reward") != 1):
                        self_check_passed = False
                    result["finished_at"] = now()
                    write_json(trial / "result.json", result)
                    refresh_report(job, ROOT / "benchmark/tasks.json", options["attempts"])
        metadata["status"] = "finished"
        if options["self_check"]:
            metadata["self_check_passed"] = self_check_passed
    except BaseException:
        metadata["status"] = "interrupted"
        raise
    finally:
        metadata["finished_at"] = now()
        write_json(job / "run-metadata.json", metadata)
        refresh_report(job, ROOT / "benchmark/tasks.json", options["attempts"])
    return 0 if not options["self_check"] or self_check_passed else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "--_worker":
        return asyncio.run(worker(Path(argv[1])))
    args = parser().parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.name):
        raise SystemExit("Use a simple unique experiment name")
    if min(args.attempts, args.max_steps, args.max_seconds, args.command_timeout) <= 0:
        raise SystemExit("Attempts and limits must be positive")
    manifest = load_manifest(args.manifest)
    agent_parts(args.agent)
    options = {key: getattr(args, key) for key in ("provider", "model", "agent", "attempts", "max_steps", "max_seconds", "command_timeout", "self_check")}
    if args.dry_run:
        print(json.dumps({"suite_id": manifest["suite_id"], "revision": manifest["revision"], "tasks": [t["name"] for t in manifest["tasks"]], "options": options}, ensure_ascii=False, indent=2))
        return 0
    errors = preflight(args)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 2
    from .benchmark_source import ensure_sources
    upstream = ensure_sources()
    job = args.jobs.resolve() / args.name
    if job.exists():
        raise SystemExit("Experiment already exists; choose a new --name")
    job.mkdir(parents=True)
    snapshot = snapshot_sources(ROOT, job / "source", args.manifest.resolve(), args.agent, upstream)
    metadata = {"variant": args.name, "kind": "grader_self_check" if args.self_check else "agent_evaluation",
        "suite_id": manifest["suite_id"], "revision": manifest["revision"], "provider": args.provider,
        "execution_mode": "local-port", "platform": platform.platform(), "python_version": platform.python_version(),
        "hostlimits": "unrestricted",
        "model": args.model, "attempts": args.attempts, "expected_tasks": [t["name"] for t in manifest["tasks"]],
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(), **snapshot,
        "code_sha256": snapshot["source_sha256"], "agent_import_path": args.agent,
        "limits": {key: options[key] for key in ("max_steps", "max_seconds", "command_timeout")},
        "started_at": now(), "status": "running"}
    write_json(job / "run-metadata.json", metadata)
    shutil.copy2(args.manifest, job / "manifest.json")
    request = job / "requested-config.json"
    write_json(request, {"job": str(job), "options": options})
    env = os.environ.copy()
    env["PYTHONPATH"] = str(job / "source")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    print(f"Results: {job}\nLive score: {job / 'scoreboard/index.html'}", flush=True)
    try:
        completed = subprocess.run([sys.executable, "-m", "harness_lab.bench", "--_worker", str(request)], cwd=job / "source", env=env)
        current = json.loads((job / "run-metadata.json").read_text(encoding="utf-8"))
        current["exit_code"] = completed.returncode
        if current["status"] == "running":
            current.update(status="error", finished_at=now())
        write_json(job / "run-metadata.json", current)
        return completed.returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
