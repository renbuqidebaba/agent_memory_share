"""Public demo and evaluation entry points with hostile caller environment."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ScriptTests(unittest.TestCase):
    def test_prepared_trials_are_identical_isolated_and_not_counted_as_runs(self):
        with tempfile.TemporaryDirectory(prefix="handoff_trials_") as temp:
            base = Path(temp)
            fixture = base / "fixture"
            workspace = fixture / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "code.py").write_text("value = 1")
            (fixture / "payload.json").write_text(json.dumps({"task_id": "fixture",
                "from_agent": "sender", "goal": "set value to 2", "status_summary": "not done",
                "artifacts": [{"path": "code.py"}]}))
            (fixture / "progress.md").write_text("set value to 2; not done")
            output = base / "trials"
            result = subprocess.run([sys.executable, str(ROOT / "scripts/prepare_evaluation.py"),
                "--fixture", str(fixture), "--output", str(output), "--repeats", "1"],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["completed_trials"], 0)
            self.assertFalse(manifest["uses_llm"])
            self.assertEqual(len(manifest["trials"]), 3)
            self.assertTrue(all(t["files"] == manifest["trials"][0]["files"] for t in manifest["trials"]))
            first, second = [Path(t["workspace"]) for t in manifest["trials"][:2]]
            (first / "code.py").write_text("changed")
            self.assertEqual((second / "code.py").read_text(), "value = 1")
            records = [str(output / t["trial_id"] / "record.json") for t in manifest["trials"]]
            summary = subprocess.run([sys.executable, str(ROOT / "scripts/summarize_evaluation.py"), *records],
                capture_output=True, text=True)
            self.assertNotEqual(summary.returncode, 0)

    def test_demo_does_not_follow_inherited_git_repository(self):
        with tempfile.TemporaryDirectory(prefix="handoff_demo_git_") as temp:
            base = Path(temp)
            outside = base / "outside"
            outside.mkdir()
            (outside / "pagination.py").write_text("private work\n")
            def git(*args):
                return subprocess.check_output(["git", "-C", str(outside),
                    "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args], text=True)
            git("init", "-q")
            git("add", ".")
            git("commit", "-qm", "outside")
            original = git("rev-parse", "HEAD").strip()
            env = {**os.environ, "GIT_DIR": str(outside / ".git"),
                   "GIT_WORK_TREE": str(outside), "GIT_INDEX_FILE": str(outside / "other-index")}
            output = base / "demo"
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / "demo_handoff.py"),
                "--output", str(output)], cwd=base, env=env, text=True, capture_output=True, timeout=35)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((output / "result.json").read_text())["status"], "completed")
            self.assertTrue((output / "workspace" / ".git").is_dir())
            self.assertEqual(git("rev-parse", "HEAD").strip(), original)
            self.assertEqual(git("status", "--porcelain"), "")

    def test_evaluation_rejects_nonfinite_metrics(self):
        with tempfile.TemporaryDirectory(prefix="handoff_eval_") as temp:
            record = Path(temp) / "trial.json"
            body = {"trial_id": "one", "task_id": "sample", "condition": "handoff",
                    "model": "test", "client_version": "test", "source_revision": "abc",
                    "status": "completed", "elapsed_seconds": 2, "input_tokens": 4,
                    "output_tokens": 5, "repeated_checks": 0, "evidence": "test log"}
            def run():
                return subprocess.run([sys.executable, str(ROOT / "scripts" / "summarize_evaluation.py"),
                    str(record)], capture_output=True, text=True)
            record.write_text(json.dumps(body))
            self.assertEqual(json.loads(run().stdout)["groups"]["handoff"]["median_total_tokens"], 9)
            self.assertIsNone(json.loads(run().stdout)["groups"]["handoff"]["median_end_to_end_tokens"])
            body.update(sender_elapsed_seconds=3, sender_input_tokens=10, sender_output_tokens=2)
            record.write_text(json.dumps(body))
            group = json.loads(run().stdout)["groups"]["handoff"]
            self.assertEqual(group["median_end_to_end_seconds"], 5)
            self.assertEqual(group["median_end_to_end_tokens"], 21)
            for raw in ("NaN", "Infinity", "-Infinity"):
                with self.subTest(value=raw):
                    record.write_text(json.dumps(body).replace('"elapsed_seconds": 2',
                        f'"elapsed_seconds": {raw}'))
                    self.assertNotEqual(run().returncode, 0)


if __name__ == "__main__":
    unittest.main()
