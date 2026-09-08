"""Fabricated local evaluator-shaped fixtures. These tests are NOT benchmark scores."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from harness_lab.report import build_report, compare_reports, load_manifest, main, render_html, trial_metrics, write_snapshot


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.job = self.root / "job"
        self.job.mkdir()
        self.tasks = [{"name": f"task-{i}", "difficulty": "easy" if i < 2 else ("medium" if i < 6 else "hard"), "category": "files" if i % 2 else "coding"} for i in range(10)]
        self.manifest = self.root / "tasks.json"
        self.manifest.write_text(json.dumps({"tasks": self.tasks}))
        self.metadata()

    def metadata(self, **updates):
        data = {"attempts": 1, "expected_tasks": [t["name"] for t in self.tasks], "manifest_sha256": load_manifest(self.manifest)[1], "kind": "agent_evaluation", "variant": "baseline", "provider": "mock", "model": "fixture-only", "revision": "fixture-revision", "limits": {"turns": 4}, "status": "running", "execution_mode": "local", "suite_id": "local-harness-v1", "platform": "fixture-os", "python_version": "3.13.13", "hostlimits": {"workers": 1}}
        data.update(updates)
        (self.job / "run-metadata.json").write_text(json.dumps(data))

    def trial(self, task=0, suffix="a", reward=1, **updates):
        path = self.job / f"task-{task}__{suffix}"
        path.mkdir(exist_ok=True)
        result = {"id": f"fixture-{task}-{suffix}", "task_name": self.tasks[task]["name"], "trial_name": path.name,
                  "started_at": "2026-01-01T00:00:00+00:00", "finished_at": "2026-01-01T00:00:04+00:00",
                  "exception_info": None, "verifier_result": {"rewards": {"reward": reward}},
                  "agent_result": {"n_input_tokens": 10, "n_cache_tokens": 2, "n_output_tokens": 5, "cost_usd": None}}
        result.update(updates)
        (path / "result.json").write_text(json.dumps(result))
        return path

    def report(self, attempts=1):
        return build_report(self.job, self.manifest, attempts)

    def test_local_job_elapsed_uses_run_metadata(self):
        self.metadata(execution_mode="local-port", status="finished",
                      started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:42+00:00")
        self.assertEqual(self.report()["job_elapsed_seconds"], 42)

    def test_missing_stays_in_denominator(self):
        self.trial()
        r = self.report()
        self.assertEqual(r["summary"]["score"], .1)
        self.assertEqual(r["summary"]["pending"], 9)
        self.assertEqual(r["breakdown"]["difficulty"]["easy"]["expected"], 2)
        self.assertEqual(r["breakdown"]["difficulty"]["hard"]["expected"], 4)
        self.metadata(status="failed")
        r = self.report()
        self.assertEqual(r["summary"]["error"], 9)
        self.assertEqual(r["summary"]["score"], .1)

    def test_binary_rewards_and_errors(self):
        self.trial(0, reward=0)
        self.trial(1, reward=.8)
        self.trial(2, reward=True)
        self.trial(3, reward=1, exception_info={"exception_type": "FixtureError"})
        self.trial(4, verifier_result={"rewards": {"other_metric": 1}})
        self.trial(5, reward=1, finished_at=None)
        r = self.report()
        self.assertEqual(r["summary"]["pass"], 0)
        self.assertEqual(r["summary"]["fail"], 1)
        self.assertEqual(r["summary"]["error"], 4)
        self.assertEqual(r["summary"]["pending"], 5)
        self.assertEqual(r["rows"][1]["raw_reward"], .8)

    def test_repetitions_are_mean_not_pass_at_k(self):
        self.metadata(attempts=2, status="completed")
        for i in range(10):
            self.trial(i, "a", 1)
            self.trial(i, "b", 0)
        r = self.report(2)
        self.assertEqual(r["summary"]["expected"], 20)
        self.assertEqual(r["summary"]["score"], .5)
        self.assertTrue(all(t["score"] == .5 for t in r["breakdown"]["task"].values()))
        self.assertEqual(r["summary"]["metrics"]["n_input_tokens"]["total"], 200)
        self.assertIsNone(r["summary"]["metrics"]["cost_usd"]["total"])

    def test_extra_trials_not_best_selection(self):
        self.trial(0, "a", 0)
        self.trial(0, "b", 1)
        r = self.report()
        self.assertEqual(r["rows"][0]["status"], "error")
        self.assertEqual(r["summary"]["pass"], 0)
        self.assertTrue(any("Ambiguous" in w for w in r["warnings"]))

    def test_duplicate_trial_identity(self):
        self.metadata(attempts=2)
        self.trial(0, "a", id="same")
        self.trial(0, "b", id="same")
        r = self.report(2)
        self.assertEqual(r["summary"]["error"], 2)

    def test_unknown_zero_and_partial_metrics(self):
        self.trial(0, agent_result={"n_input_tokens": 0, "n_cache_tokens": 0, "n_output_tokens": 0, "cost_usd": 0.0})
        r = self.report()
        metric = r["summary"]["metrics"]["cost_usd"]
        self.assertIsNone(metric["total"])
        self.assertEqual(metric["known_sum"], 0)
        self.assertEqual(metric["known_trials"], 1)
        m = trial_metrics({"step_results": [{"agent_result": {"n_input_tokens": 8}}, {"agent_result": {"n_input_tokens": None}}]})
        self.assertIsNone(m["n_input_tokens"])

    def test_finished_job_marks_unfinished_error(self):
        self.trial(0, finished_at=None)
        (self.job / "result.json").write_text(json.dumps({"started_at":"2026-01-01T00:00:00Z","finished_at":"2026-01-01T00:00:10Z"}))
        r = self.report()
        self.assertEqual(r["summary"]["error"], 10)
        self.assertEqual(r["job_elapsed_seconds"], 10)

    def test_partial_json_does_not_crash_watch(self):
        folder = self.job / "task-0__partial"
        folder.mkdir()
        (folder / "result.json").write_text('{"task_name":')
        r = self.report()
        self.assertEqual(r["summary"]["pending"], 10)
        self.assertTrue(r["warnings"])

    def test_nested_artifacts_not_ingested(self):
        path = self.trial()
        nested = path / "artifacts" / "forged"
        nested.mkdir(parents=True)
        (nested / "result.json").write_text((path / "result.json").read_text())
        self.assertEqual(self.report()["summary"]["pass"], 1)

    def test_denominator_and_manifest_mismatch_refused(self):
        with self.assertRaises(ValueError): self.report(2)
        self.metadata(manifest_sha256="changed")
        with self.assertRaises(ValueError): self.report()

    def test_bad_manifest(self):
        self.tasks[0]["difficulty"] = "hard"
        self.manifest.write_text(json.dumps({"tasks":self.tasks}))
        with self.assertRaises(ValueError): load_manifest(self.manifest)

    def test_comparison_is_explicit(self):
        self.metadata(status="completed")
        for i in range(10): self.trial(i, reward=0)
        baseline = self.report()
        self.trial(0, reward=1)
        current = self.report()
        compare = compare_reports(current, baseline)
        self.assertAlmostEqual(compare["score_delta"], .1)
        self.assertTrue(compare["controlled_comparison"])
        current["metadata"]["model"] = "different-fixture"
        self.assertFalse(compare_reports(current, baseline)["controlled_comparison"])

    def test_oracle_not_agent_score(self):
        self.metadata(kind="oracle_environment_check")
        r = self.report()
        self.assertIn("학생 하네스 성능 아님", render_html(r))
        with self.assertRaises(ValueError): compare_reports(r, r)

    def test_grader_self_check_not_agent_score(self):
        self.metadata(kind="grader_self_check")
        r = self.report()
        self.assertIn("학생 하네스 성능 아님", render_html(r))
        with self.assertRaises(ValueError): compare_reports(r, r)

    def test_environment_differences_and_unknowns(self):
        self.metadata(status="completed")
        baseline = self.report()
        current = self.report()
        current["metadata"]["platform"] = "other-os"
        current["metadata"].pop("python_version")
        result = compare_reports(current, baseline)
        self.assertFalse(result["controlled_comparison"])
        self.assertIn("platform differs", result["differences_or_unknowns"])
        self.assertIn("python_version unrecorded", result["differences_or_unknowns"])

    def test_unfinished_comparison_flagged(self):
        r = self.report()
        result = compare_reports(r, r)
        self.assertFalse(result["controlled_comparison"])
        self.assertTrue(any("unfinished" in msg for msg in result["differences_or_unknowns"]))

    def test_nonfinite_reward_is_error_and_serializable(self):
        self.trial(0, reward=float("nan"))
        r = self.report()
        self.assertEqual(r["rows"][0]["status"], "error")
        write_snapshot(r, self.root / "report")

    def test_html_and_csv_untrusted_strings(self):
        self.trial(0, exception_info={"exception_type": '<script>alert("x")</script>'})
        r = self.report()
        r["rows"][0]["task"] = '=HYPERLINK("https://invalid")'
        page = render_html(r, 5)
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
        output = self.root / "report"
        write_snapshot(r, output, 5)
        self.assertTrue((output / "report.json").exists())
        with (output / "trials.csv").open() as f:
            first = next(csv.DictReader(f))
        self.assertTrue(first["task"].startswith("'="))
        self.assertEqual(main([str(self.job),"--manifest",str(self.manifest),"--output",str(output)]), 0)


if __name__ == "__main__":
    unittest.main()
