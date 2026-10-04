"""Reproduce a coding handoff with deterministic sender/receiver scripts, no LLM."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

INITIAL = "def page_count(total, size):\n    return total // size\n"
PARTIAL = "def page_count(total, size):\n    return (total + size - 1) // size\n"
FIXED = """def page_count(total, size):
    if total < 0 or size <= 0:
        raise ValueError("total must be nonnegative and size must be positive")
    return (total + size - 1) // size
"""
TESTS = """import unittest
from pagination import page_count

class PaginationTests(unittest.TestCase):
    def test_partial_page(self):
        self.assertEqual(page_count(11, 10), 2)
    def test_exact_page(self):
        self.assertEqual(page_count(20, 10), 2)
    def test_empty(self):
        self.assertEqual(page_count(0, 10), 0)
    def test_invalid_size(self):
        for size in (0, -1):
            with self.subTest(size=size), self.assertRaises(ValueError):
                page_count(10, size)
    def test_invalid_total(self):
        with self.assertRaises(ValueError):
            page_count(-1, 10)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("demo-output"))
    parser.add_argument("--prepare-only", action="store_true", help="Stop after the sender for a manual agent trial")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    workspace = root / "workspace"
    workspace.mkdir()
    # Git environment overrides can redirect a demo commit into the caller's
    # actual repository. Drop them before starting any subprocess.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(HANDOFF_DB_PATH=str(root / "handoff.db"),
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    def run(command, *, expected=0):
        result = subprocess.run(command, cwd=workspace, env=env, text=True, capture_output=True, timeout=30)
        if result.returncode != expected:
            raise RuntimeError(f"{command!r}: exit {result.returncode}\n{result.stdout}\n{result.stderr}")
        return result
    def git(*args):
        return run(["git", "-c", "init.templateDir=/dev/null", "-c", "core.hooksPath=/dev/null",
                    "-c", "commit.gpgsign=false", "-c", "user.name=Handoff Demo",
                    "-c", "user.email=demo@example.invalid", *args])
    def cli(*args, expected=0):
        return json.loads(run([sys.executable, "-m", "app.handoff_cli", *args], expected=expected).stdout)
    (workspace / "pagination.py").write_text(INITIAL, encoding="utf-8")
    (workspace / "test_pagination.py").write_text(TESTS, encoding="utf-8")
    git("init", "-q")
    git("add", "pagination.py", "test_pagination.py")
    git("commit", "-qm", "Add pagination bug and acceptance tests")
    (workspace / "pagination.py").write_text(PARTIAL, encoding="utf-8")
    # Sender leaves an incomplete patch and measured failing test output.
    command = [sys.executable, "-B", "-m", "unittest", "discover", "-v"]
    before = cli("run-check", "--workspace", str(workspace), "--", *command, expected=2)
    (workspace / "sender-tests.log").write_text(before["stdout"] + before["stderr"], encoding="utf-8")
    payload = {
        "schema_version": 1, "task_id": "pagination-boundaries", "from_agent": "agent-a", "to_agent": "agent-b",
        "goal": "Return the number of pages; reject negative totals and nonpositive page sizes with ValueError.",
        "status_summary": "Rounding is fixed; input validation is still missing.",
        "done_when": ["All five acceptance test methods pass", "Keep the existing page_count API"],
        "completed": ["Fixed partial-page rounding"],
        "decisions": ["Use integer arithmetic"],
        "next_steps": ["Read sender-tests.log", "Validate inputs before division", "Run acceptance tests"],
        "artifacts": [{"path": name} for name in ("pagination.py", "test_pagination.py", "sender-tests.log")],
        "validation": [{"command": "python -B -m unittest discover -v", "outcome": "failed",
                        "detail": "Input validation cases still fail; see sender-tests.log."}],
    }
    source = root / "payload.json"
    source.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    h = cli("create", str(source), "--workspace", str(workspace), "--capture", "--check-id", before["id"],
            "--markdown", str(root / "handoff.md"))
    (root / "progress.md").write_text(
        "Fixed partial-page rounding using integer arithmetic. Input validation remains incomplete. "
        "Read sender-tests.log and run python -B -m unittest discover -v. Keep the API unchanged.\n", encoding="utf-8")
    report = {"kind": "scripted-demo", "uses_llm": False, "handoff_id": h["id"],
              "sender_test_exit": before["exit_code"], "status": "pending", "schema_version": 2}
    if not args.prepare_only:
        cli("claim", h["id"], "--agent", "agent-b")
        report["verification"] = cli("verify", h["id"], "--workspace", str(workspace), "--agent", "agent-b")
        if not report["verification"]["ok"]:
            raise RuntimeError("Receiver evidence verification failed")
        cli("ack", h["id"], "--agent", "agent-b", "--accept", "--verification-id", report["verification"]["verification_id"])
        (workspace / "pagination.py").write_text(FIXED, encoding="utf-8")
        after = run(command)
        (root / "receiver-tests.log").write_text(after.stdout + after.stderr, encoding="utf-8")
        report["receiver_test_exit"] = after.returncode
        report["status"] = cli("complete", h["id"], "--agent", "agent-b")["status"]
    (root / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(root), **report}, indent=2))


if __name__ == "__main__":
    main()
