"""Runner contracts using a synthetic suite/agent; not course task scores."""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness_lab import bench


def fixture(root):
    package = root / "harness_lab"
    package.mkdir()
    (package / "__init__.py").write_text("")
    shutil.copy2(Path(bench.__file__), package / "bench.py")
    (package / "grading.py").write_text('''async def grade(task_dir, workspace, timeout):
    passed = (workspace / 'solution.py').read_text() == 'answer = 42\\n'
    return {'reward':int(passed), 'checks_passed':int(passed), 'checks_total':1, 'details':[]}
''')
    (package / "report.py").write_text('''import json
from pathlib import Path
def build_report(job, manifest, attempts):
    files = list(Path(job).glob('*/result.json'))
    return {'finished':len(files)}
def write_snapshot(report, output):
    Path(output).mkdir(exist_ok=True)
    Path(output,'report.json').write_text(json.dumps(report))
''')
    (package / "benchmark_source.py").write_text('''from pathlib import Path
import shutil
def ensure_sources(cache=None):
    return Path(cache) if cache else Path(__file__).resolve().parents[1]/'benchmark/upstream'
def prepare(task_name,target,cache=None):
    task=ensure_sources(cache)/task_name
    shutil.copytree(task/'workspace',target)
    return {'task_dir':task,'instruction':(task/'instruction.md').read_text(),'cwd':target}
''')
    (package / "reference.py").write_text('''import shutil
async def run_reference(task_dir,workspace,timeout=360):
    shutil.copy2(task_dir/'ref.py',workspace/'solution.py')
    return {'exit_code':0}
''')
    local = root / "benchmark/upstream"
    local.mkdir(parents=True)
    tasks = []
    for i, difficulty in enumerate(["easy"]*2 + ["medium"]*4 + ["hard"]*4):
        name = f"task-{i}"
        task = local / name
        (task / "workspace").mkdir(parents=True)
        (task / "instruction.md").write_text("Implement answer = 42")
        (task / "workspace/solution.py").write_text("answer = 0\n")
        (task / "workspace/check.py").write_text("# public example\n")
        (task / "cases.json").write_text('{"hidden":42}')
        (task / "ref.py").write_text("answer = 42\n")
        (task / ".dockerignore").write_text("hidden source fixture")
        records = [{"path":str(p.relative_to(task)), "size_bytes":p.stat().st_size,
                    "sha256":hashlib.sha256(p.read_bytes()).hexdigest()} for p in task.rglob("*") if p.is_file()]
        tasks.append({"name":name, "difficulty":difficulty, "category":"synthetic", "source_files":records})
        (task / "unlisted.txt").write_text("exclude me")
    manifest = root / "benchmark/tasks.json"
    manifest.write_text(json.dumps({"suite_id":"local-harness-v1", "revision":"1.0.0", "tasks":tasks}))
    return manifest


class BenchTests(unittest.TestCase):
    def test_manifest_counts_are_pinned(self):
        with TemporaryDirectory() as directory:
            path = fixture(Path(directory))
            self.assertEqual(len(bench.load_manifest(path)["tasks"]), 10)
            data = json.loads(path.read_text())
            data["tasks"][2]["difficulty"] = "hard"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                bench.load_manifest(path)

    def test_dry_run_needs_no_model_key_and_creates_no_job(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = fixture(root)
            with patch.object(bench, "ROOT", root):
                code = bench.main(["--name","dry", "--manifest",str(manifest), "--jobs",str(root / "jobs"), "--dry-run"])
            self.assertEqual(code, 0)
            self.assertFalse((root / "jobs").exists())

    def test_self_check_is_separate_and_never_copies_hidden_files_to_workspace(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = fixture(root)
            args = ["--name","grader", "--manifest",str(manifest), "--jobs",str(root / "jobs"), "--self-check"]
            with patch.object(bench, "ROOT", root), patch.dict(sys.modules, {"harness_lab.benchmark_source": SimpleNamespace(ensure_sources=lambda:root / "benchmark/upstream")}):
                self.assertEqual(bench.main(args), 0)
                with self.assertRaises(SystemExit):
                    bench.main(args)
            job = root / "jobs/grader"
            metadata = json.loads((job / "run-metadata.json").read_text())
            self.assertEqual(metadata["kind"], "grader_self_check")
            self.assertTrue((job / "source/benchmark/upstream/task-0/.dockerignore").exists())
            self.assertFalse((job / "source/benchmark/upstream/task-0/unlisted.txt").exists())
            self.assertEqual(len(list(job.glob("*/result.json"))), 10)
            for workspace in job.glob("*/workspace"):
                self.assertFalse((workspace / "cases.json").exists())
                self.assertFalse((workspace / "ref.py").exists())
                self.assertEqual((workspace / "solution.py").read_text(), "answer = 42\n")
            self.assertEqual(json.loads((job / "scoreboard/report.json").read_text())["finished"], 10)

    def test_custom_agent_executes_snapshot_not_mutated_source(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = fixture(root)
            custom = root / "synthetic_agent.py"
            custom.write_text('''from pathlib import Path
async def solve_task(instruction, workspace, logs_dir, options):
    assert not (workspace/'cases.json').exists()
    assert not (workspace/'ref.py').exists()
    (workspace/'solution.py').write_text('answer = 42\\n')
    (logs_dir/'loaded-from.txt').write_text(__file__)
    return {'status':'completed','answer':'synthetic','metrics':{'usage_known':False}}
''')
            sys.path.insert(0, str(root))
            try:
                original_run = bench.subprocess.run
                def mutate_then_run(*args, **kwargs):
                    custom.write_text("raise RuntimeError('working tree changed')\n")
                    return original_run(*args, **kwargs)
                with patch.object(bench, "ROOT", root), patch("harness_lab.bench.subprocess.run", side_effect=mutate_then_run), patch.dict(sys.modules, {"harness_lab.benchmark_source": SimpleNamespace(ensure_sources=lambda:root / "benchmark/upstream")}):
                    code = bench.main(["--name","agent", "--manifest",str(manifest), "--jobs",str(root / "jobs"), "--agent","synthetic_agent:solve_task", "--model","synthetic"])
            finally:
                sys.path.remove(str(root))
            self.assertEqual(code, 0)
            job = root / "jobs/agent"
            self.assertIn(str(job / "source"), (job / "task-0__1/agent/loaded-from.txt").read_text())
            self.assertIn("async def solve_task", (job / "source/synthetic_agent.py").read_text())
            metadata = json.loads((job / "run-metadata.json").read_text())
            self.assertEqual(metadata["source_sha256"], bench.hash_tree(job / "source"))
            self.assertEqual(metadata["kind"], "agent_evaluation")
            self.assertEqual(metadata["execution_mode"], "local-port")
            self.assertEqual(metadata["hostlimits"], "unrestricted")
            self.assertTrue(metadata["python_version"])
