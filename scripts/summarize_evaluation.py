"""Summarize measured manual trials; missing token data stays unknown."""
import argparse
import json
import math
import statistics
from pathlib import Path

CONDITIONS = ("repository_only", "progress_note", "handoff")


def summarize(paths):
    groups = {name: [] for name in CONDITIONS}
    seen = set()
    for path in paths:
        row = json.loads(path.read_text(encoding="utf-8"),
                         parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"{path}: invalid {value}")))
        required = ("trial_id", "task_id", "condition", "model", "client_version", "source_revision",
                    "status", "elapsed_seconds", "input_tokens", "output_tokens", "repeated_checks", "evidence")
        if any(key not in row for key in required):
            raise ValueError(f"{path}: incomplete trial record")
        if row["trial_id"] in seen:
            raise ValueError(f"{path}: duplicate trial_id")
        seen.add(row["trial_id"])
        if row["condition"] not in groups or row["status"] not in ("completed", "failed", "timeout"):
            raise ValueError(f"{path}: invalid condition or status")
        for key in ("trial_id", "task_id", "model", "client_version", "source_revision"):
            if not isinstance(row[key], str) or not row[key].strip():
                raise ValueError(f"{path}: missing {key}")
        for key in ("elapsed_seconds", "input_tokens", "output_tokens", "repeated_checks"):
            value = row[key]
            if value is None and key in ("input_tokens", "output_tokens"):
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{path}: invalid {key}")
        for key in ("sender_elapsed_seconds", "sender_input_tokens", "sender_output_tokens"):
            value = row.get(key)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError(f"{path}: invalid {key}")
        if not row["evidence"]:
            raise ValueError(f"{path}: evidence reference required")
        groups[row["condition"]].append(row)
    output = {}
    for name, rows in groups.items():
        token_rows = [r for r in rows if r["input_tokens"] is not None and r["output_tokens"] is not None]
        total_time_rows = [r for r in rows if r.get("sender_elapsed_seconds") is not None]
        total_token_rows = [r for r in token_rows if r.get("sender_input_tokens") is not None and r.get("sender_output_tokens") is not None]
        output[name] = {
            "trials": len(rows), "completed": sum(r["status"] == "completed" for r in rows),
            "median_elapsed_seconds": statistics.median(r["elapsed_seconds"] for r in rows) if rows else None,
            "median_repeated_checks": statistics.median(r["repeated_checks"] for r in rows) if rows else None,
            "trials_with_tokens": len(token_rows),
            "median_total_tokens": statistics.median(r["input_tokens"] + r["output_tokens"] for r in token_rows) if token_rows else None,
            "trials_with_end_to_end_time": len(total_time_rows),
            "median_end_to_end_seconds": statistics.median(r["elapsed_seconds"] + r["sender_elapsed_seconds"] for r in total_time_rows) if total_time_rows else None,
            "trials_with_end_to_end_tokens": len(total_token_rows),
            "median_end_to_end_tokens": statistics.median(r["input_tokens"] + r["output_tokens"] + r["sender_input_tokens"] + r["sender_output_tokens"] for r in total_token_rows) if total_token_rows else None,
        }
    return {"groups": output, "note": "Descriptive results only. Check matched tasks, models and settings before comparing."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path, nargs="+")
    args = parser.parse_args()
    try:
        print(json.dumps(summarize(args.records), indent=2, allow_nan=False))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
