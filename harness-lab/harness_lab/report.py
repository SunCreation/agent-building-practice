"""Read Harbor 0.22.0 trial result.json files without changing benchmark results.

Score = binary passes / (the selected 10 tasks * requested attempts). This is a
mean over attempts, NOT pass@k. Missing/error trials remain in the denominator.
Schema sources: harbor/models/{trial/result,job/result,agent/context}.py.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

METRICS = ("elapsed_seconds", "n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd")
TERMINAL = {"completed", "succeeded", "failed", "error", "cancelled", "interrupted", "finished"}


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: value)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def load_manifest(path: Path) -> tuple[list[dict], str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    tasks = value.get("tasks") if isinstance(value, dict) else value
    if not isinstance(tasks, list) or len(tasks) != 10:
        raise ValueError("Manifest must contain exactly 10 selected tasks")
    names = []
    for task in tasks:
        if not isinstance(task, dict) or not all(isinstance(task.get(k), str) and task[k].strip() for k in ("name", "difficulty", "category")):
            raise ValueError("Every task needs nonempty name/difficulty/category")
        names.append(task["name"])
    if len(set(names)) != 10:
        raise ValueError("Manifest task names must be unique")
    if Counter(t["difficulty"] for t in tasks) != {"easy": 2, "hard": 8}:
        raise ValueError("Selected benchmark must contain 2 easy and 8 hard tasks")
    return tasks, hashlib.sha256(path.read_bytes()).hexdigest()


def numeric(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def elapsed(start: Any, end: Any) -> float | None:
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        duration = (datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds()
        return duration if duration >= 0 else None
    except (ValueError, TypeError):
        return None


def trial_metrics(result: dict) -> dict:
    metrics = {"elapsed_seconds": elapsed(result.get("started_at"), result.get("finished_at"))}
    if isinstance(result.get("agent_result"), dict):
        contexts = [result["agent_result"]]
    elif isinstance(result.get("step_results"), list):
        contexts = [s.get("agent_result") if isinstance(s, dict) else None for s in result["step_results"]]
    else:
        contexts = []
    for key in METRICS[1:]:
        values = [numeric(c.get(key)) if isinstance(c, dict) else None for c in contexts]
        # Unlike Harbor's nullable partial sums, expose a total only if every
        # contributing step is known; unknown is never substituted with zero.
        metrics[key] = sum(values) if values and all(v is not None for v in values) else None
    return metrics


def classify(result: dict) -> tuple[str, str, Any]:
    verifier = result.get("verifier_result")
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    errors = result.get("exception_info")
    if not errors and isinstance(result.get("step_results"), list):
        errors = next((s.get("exception_info") for s in result["step_results"] if isinstance(s, dict) and s.get("exception_info")), None)
    if errors:
        reason = errors.get("exception_type", "trial_exception") if isinstance(errors, dict) else "trial_exception"
        return "error", str(reason), reward
    if not result.get("finished_at"):
        return "pending", "trial_not_finished", reward
    if isinstance(reward, bool) or not isinstance(reward, (int, float)) or not math.isfinite(reward):
        return "error", "missing_or_invalid_binary_reward", reward
    if reward == 1:
        return "pass", "binary_reward_1", reward
    if reward == 0:
        return "fail", "binary_reward_0", reward
    return "error", "nonbinary_reward_not_scored", reward


def aggregate(rows: list[dict]) -> dict:
    counts = {status: sum(r["status"] == status for r in rows) for status in ("pass", "fail", "error", "pending")}
    expected = len(rows)
    result = {"expected": expected, **counts, "score": counts["pass"] / expected if expected else None}
    result["metrics"] = {}
    for key in METRICS:
        values = [r[key] for r in rows if r[key] is not None]
        result["metrics"][key] = {
            "total": sum(values) if len(values) == expected and expected else None,
            "known_sum": sum(values) if values else None,
            "known_trials": len(values),
            "expected_trials": expected,
        }
    return result


def build_report(job_dir: Path | str, manifest: Path | str, attempts: int = 1) -> dict:
    job_dir, manifest = Path(job_dir), Path(manifest)
    if isinstance(attempts, bool) or attempts < 1:
        raise ValueError("attempts must be a positive integer")
    tasks, manifest_hash = load_manifest(manifest)
    if not job_dir.is_dir():
        raise ValueError(f"Job directory does not exist: {job_dir}")
    warnings = []
    metadata = {}
    metadata_path = job_dir / "run-metadata.json"
    if metadata_path.exists():
        try:
            metadata = read_object(metadata_path)
        except (ValueError, OSError) as exc:
            warnings.append(f"Metadata is unreadable (possibly being written): {exc}")
    else:
        warnings.append("No run-metadata.json: attempt count and execution conditions cannot be independently checked")
    if metadata.get("attempts") is not None and metadata["attempts"] != attempts:
        raise ValueError("--attempts differs from run-metadata.json; refusing a changed denominator")
    expected_names = {t["name"] for t in tasks}
    if metadata.get("expected_tasks") is not None and (not isinstance(metadata["expected_tasks"], list) or len(metadata["expected_tasks"]) != 10 or set(metadata["expected_tasks"]) != expected_names):
        raise ValueError("Run expected_tasks differs from selected manifest")
    if metadata.get("manifest_sha256") and metadata["manifest_sha256"] != manifest_hash:
        raise ValueError("Manifest SHA256 differs from the run's selected task manifest")
    job_result = {}
    if (job_dir / "result.json").exists():
        try:
            job_result = read_object(job_dir / "result.json")
        except (ValueError, OSError):
            warnings.append("Job result.json is incomplete/unreadable")
    finished = bool(job_result.get("finished_at")) or str(metadata.get("status", "")).lower() in TERMINAL
    grouped = defaultdict(list)
    # Only Harbor's direct trial folders. Never ingest task artifacts or nested
    # verifier outputs named result.json as additional benchmark trials.
    for path in sorted(job_dir.glob("*/result.json")):
        try:
            result = read_object(path)
        except (ValueError, OSError):
            warnings.append(f"Unreadable trial result: {path.parent.name}")
            continue
        name = result.get("task_name")
        if name not in expected_names:
            warnings.append(f"Unselected/unknown task result ignored: {path.parent.name}")
            continue
        grouped[name].append((path, result))
    rows = []
    for task in tasks:
        found = grouped[task["name"]]
        ids = [r.get("id") for _, r in found if r.get("id")]
        ambiguous = len(found) > attempts or len(ids) != len(set(ids))
        if ambiguous:
            warnings.append(f"Ambiguous extra/duplicate trials for {task['name']}: {len(found)} found, {attempts} expected; no best-attempt selection")
        for index in range(attempts):
            row = {"task": task["name"], "difficulty": task["difficulty"], "category": task["category"], "attempt": index + 1,
                   "trial_name": None, "trial_path": None, "raw_reward": None, "status": "pending", "reason": "result_not_yet_available", **dict.fromkeys(METRICS)}
            if ambiguous:
                row.update(status="error", reason="ambiguous_extra_or_duplicate_trials")
            elif index < len(found):
                path, result = found[index]
                status, reason, reward = classify(result)
                if finished and status == "pending":
                    status, reason = "error", "unfinished_trial_in_finished_job"
                row.update(status=status, reason=reason, raw_reward=reward, trial_name=result.get("trial_name", path.parent.name), trial_path=str(path.relative_to(job_dir)), **trial_metrics(result))
            elif finished:
                row.update(status="error", reason="missing_result_in_finished_job")
            rows.append(row)
    breakdown = {}
    for key in ("difficulty", "category", "task"):
        breakdown[key] = {value: aggregate([r for r in rows if r[key] == value]) for value in sorted({r[key] for r in rows})}
    return {
        "schema_version": 1, "kind": metadata.get("kind", "unrecorded"), "generated_at": datetime.now(timezone.utc).isoformat(), "job_dir": str(job_dir.resolve()),
        "manifest_sha256": manifest_hash, "attempts": attempts, "task_count": 10, "metadata": metadata,
        "job_finished": finished, "job_elapsed_seconds": elapsed(job_result.get("started_at"), job_result.get("finished_at")),
        "score_definition": "binary reward == 1 passes / (10 selected tasks * attempts); mean over attempts, not pass@k; errors and missing remain in denominator",
        "attempt_order": "Lexicographic trial folder order within each task; labels are not paired RNG seeds across runs",
        "summary": aggregate(rows), "breakdown": breakdown, "rows": rows, "warnings": warnings,
    }


def compare_reports(current: dict, baseline: dict) -> dict:
    if current.get("kind") == "oracle_environment_check" or baseline.get("kind") == "oracle_environment_check":
        raise ValueError("Oracle environment-check results cannot be compared as agent evaluations")
    mismatches = []
    if current.get("kind") != "agent_evaluation" or baseline.get("kind") != "agent_evaluation":
        mismatches.append("Agent evaluation kind is unrecorded")
    for key in ("manifest_sha256", "attempts"):
        if current[key] != baseline[key]:
            mismatches.append(f"{key} differs")
    for key in ("provider", "model", "revision", "limits"):
        a, b = current["metadata"].get(key), baseline["metadata"].get(key)
        if a is None or b is None:
            mismatches.append(f"{key} unrecorded")
        elif a != b:
            mismatches.append(f"{key} differs")
    if current["warnings"] or baseline["warnings"]:
        mismatches.append("One or both reports have diagnostics; inspect warnings")
    comparable = not mismatches and current["job_finished"] and baseline["job_finished"]
    return {"baseline_job_dir": baseline["job_dir"], "baseline_summary": baseline["summary"], "current_summary": current["summary"],
            "controlled_comparison": comparable, "differences_or_unknowns": mismatches,
            "score_delta": current["summary"]["score"] - baseline["summary"]["score"] if current["manifest_sha256"] == baseline["manifest_sha256"] and current["attempts"] == baseline["attempts"] else None,
            "interpretation": "Descriptive difference only; not statistical significance or pass@k. Check model/limits/task revision and unfinished trials.",
            "tasks": [{"task": name, "current": values, "baseline": baseline["breakdown"]["task"].get(name)} for name, values in current["breakdown"]["task"].items()]}


def display(value: Any) -> str:
    return "unknown" if value is None else str(value)


def render_html(report: dict, refresh: float = 0) -> str:
    e = lambda value: html.escape(display(value), quote=True)
    summary = report["summary"]
    oracle = report.get("kind") == "oracle_environment_check"
    heading = "Oracle 환경 검증 · 학생 하네스 성능 아님" if oracle else "하네스 벤치마크 모니터"
    kind_notice = "정답 솔버가 환경과 채점 경로를 확인한 결과입니다. 이 통과율은 에이전트 점수가 아니며 개선 비교에서 제외합니다." if oracle else ("실제 에이전트 평가 기록" if report.get("kind") == "agent_evaluation" else "실행 종류 미기록: agent/oracle 여부를 확인하기 전 에이전트 성능으로 해석하지 마세요.")
    headers = ("task", "difficulty", "category", "attempt", "status", "raw_reward", "reason", *METRICS)
    rows = "".join("<tr>" + "".join(f"<td>{e(row[h])}</td>" for h in headers) + "</tr>" for row in report["rows"])
    groups = "".join(f"<tr><td>{e(kind)}</td><td>{e(name)}</td><td>{v['pass']} / {v['expected']}</td><td>{v['fail']}</td><td>{v['error']}</td><td>{v['pending']}</td></tr>" for kind in ("difficulty", "category") for name, v in report["breakdown"][kind].items())
    metrics = "".join(f"<tr><td>{e(k)}</td><td>{e(v['total'])}</td><td>{e(v['known_sum'])}</td><td>{v['known_trials']} / {v['expected_trials']}</td></tr>" for k,v in summary["metrics"].items())
    comparison = ""
    if "comparison" in report:
        comparison = "<h2>Baseline comparison</h2><pre>" + e(json.dumps(report["comparison"], ensure_ascii=False, indent=2)) + "</pre>"
    meta_refresh = f'<meta http-equiv="refresh" content="{max(1, int(refresh))}">' if refresh else ""
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{meta_refresh}<title>Harness benchmark snapshot</title>
<style>body{{font-family:system-ui,sans-serif;background:#f6f8fa;color:#18352d;margin:32px}}h1{{margin-bottom:8px}}.score{{font-size:36px;font-weight:700}}table{{border-collapse:collapse;background:white;margin:18px 0;min-width:600px}}td,th{{padding:10px;border-bottom:1px solid #ddd;text-align:left}}.scroll{{overflow:auto}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef1ed;padding:16px}}small{{color:#53645e}}</style>
<h1>{e(heading)}</h1><p><strong>{e(kind_notice)}</strong></p><small>{e(report['job_dir'])} · {e(report['generated_at'])}</small>
<p class="score">{summary['pass']} / {summary['expected']} · {summary['score']:.1%}</p>
<p>pass {summary['pass']} · fail {summary['fail']} · error {summary['error']} · pending {summary['pending']}</p>
<p>분모는 10 × {report['attempts']}회로 고정합니다. 오류·누락을 제외하지 않습니다. 반복 평균이며 pass@k가 아닙니다.</p>
<p>Job finished: {e(report['job_finished'])} · 전체 경과 초: {e(report['job_elapsed_seconds'])}</p>
<h2>난도·분야별 결과</h2><div class="scroll"><table><tr><th>group</th><th>name</th><th>pass / expected</th><th>fail</th><th>error</th><th>pending</th></tr>{groups}</table></div>
<h2>측정값과 관측 범위</h2><p>unknown은 0이 아닙니다. 일부만 알려진 경우 total 대신 known sum과 관측 수를 확인하세요. 캐시 토큰은 입력 토큰에 포함되어 있어 더하지 않습니다.</p><div class="scroll"><table><tr><th>metric</th><th>total</th><th>known sum</th><th>known / expected</th></tr>{metrics}</table></div>
<h2>시도별 결과</h2><p>{e(report['attempt_order'])}</p><div class="scroll"><table><tr>{''.join('<th>'+e(h)+'</th>' for h in headers)}</tr>{rows}</table></div>
<h2>확인할 사항</h2><pre>{e(chr(10).join(report['warnings']) or '진단 경고 없음')}</pre>{comparison}</html>'''


def csv_safe(value: Any) -> str:
    text = "" if value is None else str(value)
    # CSV escaping prevents separators from breaking cells; this additional
    # prefix prevents untrusted task names from becoming spreadsheet formulas.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r")) else text


def write_snapshot(report: dict, output: Path | str, refresh: float = 0) -> None:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    fields = list(report["rows"][0])
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows({k: csv_safe(v) for k,v in row.items()} for row in report["rows"])
    contents = {"report.json": json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), "trials.csv": stream.getvalue(), "index.html": render_html(report, refresh)}
    for filename, content in contents.items():
        temporary = output / (filename + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(output / filename)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("--manifest", type=Path, default=Path("benchmark/tasks.json"))
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--watch", type=float, default=0, metavar="SECONDS", help="Refresh snapshots until Ctrl+C")
    parser.add_argument("--compare", type=Path, help="Baseline Harbor job directory")
    parser.add_argument("--output", type=Path, default=Path("report"))
    args = parser.parse_args(argv)
    if args.attempts < 1 or not math.isfinite(args.watch) or args.watch < 0:
        parser.error("attempts must be positive; watch must be finite and nonnegative")
    try:
        while True:
            report = build_report(args.job_dir, args.manifest, args.attempts)
            if args.compare:
                report["comparison"] = compare_reports(report, build_report(args.compare, args.manifest, args.attempts))
            write_snapshot(report, args.output, args.watch)
            s = report["summary"]
            print(f"{report['generated_at']} kind={report['kind']} pass={s['pass']}/{s['expected']} score={s['score']:.1%} fail={s['fail']} error={s['error']} pending={s['pending']} → {args.output / 'index.html'}", flush=True)
            if not args.watch:
                return 0
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("Monitor stopped; benchmark job was not modified.")
        return 0
    except (ValueError, OSError) as exc:
        parser.exit(2, f"report error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
