import argparse
import json
from pathlib import Path
import pytest
from harness_lab.bench import load_manifest, make_config, parser


def example():
    return {"repo": "https://github.com/alibaba/terminal-bench-pro.git", "revision": "a" * 40,
            "tasks": [{"name": f"task-{i}", "difficulty": "easy" if i < 2 else "hard", "category": "test"} for i in range(10)]}


def test_config_pins_tasks_attempts_and_no_retries(tmp_path):
    args = parser().parse_args(["--name", "baseline", "--model", "example", "--attempts", "3"])
    cfg = make_config(args, example())
    assert cfg["n_attempts"] == 3
    assert cfg["retry"]["max_retries"] == 0
    assert cfg["datasets"][0]["ref"] == "a" * 40
    assert len(cfg["datasets"][0]["task_names"]) == 10
    assert cfg["agents"][0]["import_path"] == "harness_lab.harbor_agent:HarnessAgent"
    assert cfg["environment"]["type"] == "docker"


def test_missing_hard_task_cannot_silently_change_denominator(tmp_path):
    data = example()
    data["tasks"][-1]["difficulty"] = "medium"
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="two easy"):
        load_manifest(path)


def test_duplicate_tasks_rejected(tmp_path):
    data = example()
    data["tasks"][-1] = data["tasks"][0]
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="distinct"):
        load_manifest(path)


def test_oracle_is_not_custom_agent_config():
    args = parser().parse_args(["--name", "oracle-check", "--oracle"])
    cfg = make_config(args, example())
    assert cfg["agents"][0]["name"] == "oracle"
    assert cfg["agents"][0]["import_path"] is None


def test_run_preserves_source_and_forwards_monitor_settings(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from harness_lab import bench
    manifest = tmp_path / 'custom tasks.json'
    manifest.write_text(json.dumps(example()))
    jobs = tmp_path / 'jobs'
    captured = {}
    monkeypatch.setattr(bench, 'preflight', lambda args: [])
    def execute(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bench.subprocess, 'run', execute)
    assert bench.main(['--name', 'test-run', '--manifest', str(manifest), '--jobs', str(jobs),
                       '--model', 'example', '--attempts', '3']) == 0
    job = jobs / 'test-run'
    metadata = json.loads((job / 'run-metadata.json').read_text())
    assert metadata['attempts'] == 3 and metadata['kind'] == 'agent_evaluation'
    assert metadata['status'] == 'finished'
    assert (job / 'source/harness_lab/agent.py').exists()
    assert captured['cwd'] == job / 'source'
    assert captured['env']['PYTHONPATH'].split(__import__('os').pathsep)[0] == str(job / 'source')
    display = capsys.readouterr().out
    assert '--attempts 3' in display and '--manifest' in display and 'custom tasks.json' in display
    from harness_lab.report import build_report
    report = build_report(job, manifest, attempts=3)
    assert report['summary']['pass'] == 0 and report['summary']['error'] == 30
