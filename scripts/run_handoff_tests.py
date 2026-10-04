"""运行交接测试并输出逐项 JSON 结果、文本日志。"""
import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def startTest(self, test):
        self.started = time.perf_counter()
        self.current_status = "passed"
        self.details = []
        super().startTest(test)

    def addFailure(self, test, err):
        self.current_status = "failed"
        self.details.append(self._exc_info_to_string(err, test))
        super().addFailure(test, err)

    def addError(self, test, err):
        self.current_status = "error"
        self.details.append(self._exc_info_to_string(err, test))
        super().addError(test, err)

    def addSkip(self, test, reason):
        self.current_status = "skipped"
        self.details.append(reason)
        super().addSkip(test, reason)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self.current_status = "failed"
            self.details.append(f"{subtest}: {self._exc_info_to_string(err, test)}")
        super().addSubTest(test, subtest, err)

    def stopTest(self, test):
        self.records.append({"id": test.id(), "description": test.shortDescription(),
                             "status": self.current_status, "seconds": round(time.perf_counter() - self.started, 4),
                             "details": self.details})
        super().stopTest(test)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "handoff-final.json")
    parser.add_argument("--require-mcp", action="store_true")
    args = parser.parse_args()
    if args.require_mcp and importlib.util.find_spec("mcp") is None:
        parser.error("--require-mcp 要求先安装 .[mcp] 可选依赖")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / "server"))
    with tempfile.TemporaryDirectory(prefix="handoff_suite_") as temp:
        os.environ["MEMORY_DATA_DIR"] = temp
        os.environ["MEMORY_DB_PATH"] = str(Path(temp) / "bootstrap.db")
        os.environ.pop("HANDOFF_DB_PATH", None)
        os.environ.pop("HANDOFF_DATA_DIR", None)
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
        started = time.perf_counter()
        with args.output.with_suffix(".log").open("w", encoding="utf-8") as log:
            result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=RecordedResult).run(suite)
        versions = {}
        for package in ("fastapi", "pydantic", "httpx", "httpx2", "mcp"):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = None
        data = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version,
                "platform": platform.platform(), "sqlite": sqlite3.sqlite_version, "dependencies": versions,
                "duration_seconds": round(time.perf_counter() - started, 3),
                "tests_run": result.testsRun, "passed": sum(r["status"] == "passed" for r in result.records),
                "failed_cases": sum(r["status"] in ("failed", "error") for r in result.records),
                "skipped": len(result.skipped), "successful": result.wasSuccessful(), "cases": result.records}
        args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in data.items() if k not in ("cases", "python", "platform", "dependencies")}, ensure_ascii=False))
        for case in data["cases"]:
            if case["status"] in ("failed", "error"):
                print(case["description"], "\n", "\n".join(case["details"]))
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
