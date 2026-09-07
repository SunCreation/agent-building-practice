"""Run a pinned ten-task subset through Harbor, without copying answers into prompts."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmark/tasks.json"


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text())
    tasks = data["tasks"]
    if len(tasks) != 10 or len({t["name"] for t in tasks}) != 10:
        raise ValueError("The course subset must contain ten distinct tasks")
    if sum(t["difficulty"] == "easy" for t in tasks) != 2 or sum(t["difficulty"] == "hard" for t in tasks) != 8:
        raise ValueError("The course subset must contain two easy and eight hard tasks")
    if not re.fullmatch(r"[0-9a-f]{40}", data["revision"]):
        raise ValueError("Pin a full Git commit, not a moving branch")
    if any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]+", t["name"]) for t in tasks):
        raise ValueError("Invalid task name")
    return data


def make_config(args, manifest: dict) -> dict:
    """A real Harbor JobConfig, validated against the pinned installed package."""
    from harbor.models.job.config import JobConfig
    agent = {"name": "oracle"} if args.oracle else {
        "import_path": args.agent,
        "model_name": f"{args.provider}/{args.model}",
        "kwargs": {"provider": args.provider, "max_steps": args.max_steps,
                   "max_seconds": args.max_seconds, "command_timeout": args.command_timeout},
    }
    config = {
        "job_name": args.name, "jobs_dir": str(args.jobs.resolve()),
        "n_attempts": args.attempts, "n_concurrent_trials": 1,
        "retry": {"max_retries": 0},
        "environment": {"type": "docker", "delete": True},
        "agents": [agent],
        "datasets": [{"repo": manifest.get("url", manifest["repo"]),
                      "ref": manifest["revision"],
                      "task_names": [t["name"] for t in manifest["tasks"]]}],
    }
    return JobConfig.model_validate(config).model_dump(mode="json")


def source_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted((ROOT / "harness_lab").glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def preflight(args) -> list[str]:
    errors = []
    if shutil.which("docker") is None:
        errors.append("Docker CLI is missing. Install and start a Docker-compatible engine.")
    else:
        try:
            result = subprocess.run(["docker", "info"], capture_output=True, timeout=15)
            if result.returncode:
                errors.append("Docker engine is not reachable.")
        except subprocess.TimeoutExpired:
            errors.append("Docker engine check timed out.")
    if not args.oracle and args.provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        errors.append("OPENAI_API_KEY is not set in the process running the harness.")
    if not args.oracle and not args.model:
        errors.append("Choose --model explicitly.")
    if importlib.metadata.version("harbor") != "0.22.0":
        errors.append("Use the pinned Harbor 0.22.0 (uv sync --extra benchmark --extra dev).")
    return errors


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--name", required=True, help="New experiment name; existing runs are never overwritten")
    p.add_argument("--manifest", type=Path, default=MANIFEST)
    p.add_argument("--jobs", type=Path, default=ROOT / "jobs")
    p.add_argument("--provider", choices=("openai", "ollama"), default="openai")
    p.add_argument("--model", default="")
    p.add_argument("--agent", default="harness_lab.harbor_agent:HarnessAgent")
    p.add_argument("--attempts", type=int, default=1)
    p.add_argument("--max-steps", type=int, default=80)
    p.add_argument("--max-seconds", type=int, default=900)
    p.add_argument("--command-timeout", type=int, default=120)
    p.add_argument("--oracle", action="store_true", help="Environment check using reference solutions; NOT an agent score")
    p.add_argument("--dry-run", action="store_true", help="Validate and print configuration; does not execute or score")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.name):
        raise SystemExit("Use a simple unique experiment name")
    if min(args.attempts, args.max_steps, args.max_seconds, args.command_timeout) < 1:
        raise SystemExit("Attempts and limits must be positive")
    manifest = load_manifest(args.manifest)
    config = make_config(args, manifest)
    if args.dry_run:
        print(json.dumps(config, indent=2))
        return 0
    errors = preflight(args)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    job = args.jobs.resolve() / args.name
    if job.exists():
        raise SystemExit("Experiment already exists. Use a new --name; never overwrite a baseline.")
    job.mkdir(parents=True)
    meta = {
        "variant": args.name, "kind": "oracle_environment_check" if args.oracle else "agent_evaluation",
        "provider": args.provider, "model": args.model, "attempts": args.attempts,
        "expected_tasks": [t["name"] for t in manifest["tasks"]],
        "revision": manifest["revision"], "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "code_sha256": source_hash(), "agent_import_path": args.agent,
        "limits": {"max_steps": args.max_steps, "max_seconds": args.max_seconds,
                   "command_timeout": args.command_timeout, "n_concurrent_trials": 1, "max_retries": 0},
        "harbor_version": importlib.metadata.version("harbor"),
        "started_at": datetime.now(timezone.utc).isoformat(), "status": "running",
    }
    write_json(job / "run-metadata.json", meta)
    # Preserve the submitted implementation independently of later edits.
    shutil.copytree(ROOT / "harness_lab", job / "source" / "harness_lab", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "pyproject.toml", job / "source" / "pyproject.toml")
    shutil.copy2(ROOT / "uv.lock", job / "source" / "uv.lock")
    shutil.copy2(args.manifest, job / "manifest.json")
    # Store configuration separately from Harbor's own config.json to avoid accidental resume.
    config_path = job / "requested-config.json"
    write_json(config_path, config)
    print(f"Results: {job}", flush=True)
    print("Monitor: " + shlex.join(["uv", "run", "python", "-m", "harness_lab.report", str(job), "--manifest", str(args.manifest.resolve()), "--attempts", str(args.attempts), "--watch", "5"]), flush=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(job / "source") + os.pathsep + str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        completed = subprocess.run([str(Path(sys.executable).with_name("harbor")), "run", "--config", str(config_path)], cwd=job / "source", env=env)
        meta["status"] = "finished" if completed.returncode == 0 else "error"
        meta["exit_code"] = completed.returncode
    except KeyboardInterrupt:
        meta["status"] = "interrupted"
        meta["exit_code"] = 130
    finally:
        meta["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(job / "run-metadata.json", meta)
    return meta["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
