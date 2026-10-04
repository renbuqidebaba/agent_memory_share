"""Install a built wheel into a clean venv and exercise its public entry points.

Run: python scripts/check_wheel.py dist/*.whl
Optional: --wheelhouse /path/to/wheels (offline), --constraints locked-requirements.txt.
The installed checks run outside the checkout; every database is temporary.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import zipfile


# Kept self-contained so this file can inspect the installed distribution without
# copying the source package or importing anything from the checkout.
INSTALLED_CHECKS = r'''
import asyncio
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import app
if "--core-only" not in sys.argv:
    from mcp import Client, StdioServerParameters

root = Path.cwd()
prefix = Path(sys.prefix).resolve()
module = Path(app.__file__).resolve()
if not module.is_relative_to(prefix):
    raise AssertionError(f"Imported app outside the clean environment: {module}")
version = importlib.metadata.version("agent-handoff-local")
if app.__version__ != version:
    raise AssertionError("Package and distribution versions disagree")
scripts = prefix / ("Scripts" if os.name == "nt" else "bin")
suffix = ".exe" if os.name == "nt" else ""
cli = scripts / ("agent-handoff" + suffix)
mcp_executable = scripts / ("agent-handoff-mcp" + suffix)
assert cli.is_file() and mcp_executable.is_file(), "Missing installed entry points"
artifact = root / "work.txt"
artifact.write_text("installed wheel evidence\n", encoding="utf-8")
payload = {
    "schema_version": 1, "task_id": "wheel-cli", "from_agent": "sender",
    "goal": "Verify an installed release", "status_summary": "Ready to resume",
    "next_steps": ["Verify the artifact and finish"],
    "artifacts": [{"path": artifact.name}],
}
source = root / "handoff.json"
source.write_text(json.dumps(payload), encoding="utf-8")


def command(*args, json_output=True):
    result = subprocess.run(
        [str(cli), *map(str, args)], check=True, capture_output=True,
        text=True, encoding="utf-8", timeout=20,
    )
    return json.loads(result.stdout) if json_output else result.stdout.strip()


assert command("--version", json_output=False) == version
assert "properties" in command("schema")
handoff = command("create", source, "--workspace", root, "--markdown", root / "handoff.md")
hid = handoff["id"]
assert (root / "handoff.md").read_text(encoding="utf-8")
assert any(row["id"] == hid for row in command("list", "--task-id", "wheel-cli"))
assert command("show", hid)["id"] == hid
assert command("claim", hid, "--agent", "receiver")["status"] == "claimed"
assert command("verify", hid, "--workspace", root)["ok"]
assert command("ack", hid, "--agent", "receiver", "--accept")["status"] == "accepted"
assert command("complete", hid, "--agent", "receiver")["status"] == "completed"
exported = command("export")
assert exported["kind"] == "agent-handoff-export"
assert any(row["id"] == hid and row["status"] == "completed" for row in exported["handoffs"])
assert exported["events"], "Export omitted handoff events"
backup = root / "backup.db"
assert Path(command("backup", backup)["backup"]) == backup
with sqlite3.connect(backup) as connection:
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("SELECT status FROM handoffs WHERE id = ?", (hid,)).fetchone()[0] == "completed"
preview = command("purge", "--older-than-days", "30")
assert preview["dry_run"] is True and preview["count"] == 0

async def mcp_lifecycle():
    # Official SDK client starts the installed stdio executable, not a source module.
    params = StdioServerParameters(command=str(mcp_executable), args=[], env=dict(os.environ))
    async with Client(params) as client:
        async def call(name, **arguments):
            result = await client.call_tool(name, arguments)
            assert not result.is_error, str(result)
            value = result.structured_content
            if value is None:
                value = json.loads(result.content[0].text)
            return value["result"] if isinstance(value, dict) and set(value) == {"result"} else value

        discovery = await client.list_tools()
        names = {tool.name for tool in discovery.tools}
        required = {"handoff_create", "handoff_list", "handoff_get", "handoff_claim",
                    "handoff_verify", "handoff_ack", "handoff_complete", "handoff_capture", "handoff_run_check"}
        assert required <= names, names
        created = await call("handoff_create", payload={**payload, "task_id": "wheel-mcp"}, workspace=str(root))
        mid = created["id"]
        assert (await call("handoff_claim", handoff_id=mid, agent_id="mcp-receiver"))["status"] == "claimed"
        assert (await call("handoff_verify", handoff_id=mid, workspace=str(root)))["ok"]
        assert (await call("handoff_ack", handoff_id=mid, agent_id="mcp-receiver", accepted=True))["status"] == "accepted"
        assert (await call("handoff_complete", handoff_id=mid, agent_id="mcp-receiver"))["status"] == "completed"
        assert (await call("handoff_get", handoff_id=mid))["status"] == "completed"
        assert any(row["id"] == mid for row in await call("handoff_list", task_id="wheel-mcp"))
        check = await call("handoff_run_check", workspace=str(root),
            argv=[sys.executable, "-c", "print('wheel check')"], include=[artifact.name])
        captured = await call("handoff_capture", payload={**payload, "task_id": "wheel-v2"},
                              workspace=str(root), check_ids=[check["id"]])
        cid = captured["id"]
        assert captured["payload"]["schema_version"] == 2
        await call("handoff_claim", handoff_id=cid, agent_id="mcp-receiver")
        receipt = await call("handoff_verify", handoff_id=cid, workspace=str(root), agent_id="mcp-receiver")
        assert receipt["ok"]
        await call("handoff_ack", handoff_id=cid, agent_id="mcp-receiver", accepted=True,
                   verification_id=receipt["verification_id"])
        prompts = await client.list_prompts()
        assert {p.name for p in prompts.prompts} == {"prepare_handoff", "resume_handoff"}
        return sorted(names)


async def run_mcp():
    return await asyncio.wait_for(mcp_lifecycle(), timeout=45)


assert importlib.util.find_spec("fastapi") is None, "FastAPI leaked into the installation"
assert importlib.util.find_spec("app.main") is None, "Removed HTTP entry point is still installed"
with sqlite3.connect(os.environ["HANDOFF_DB_PATH"]) as connection:
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
assert not {"memories", "messages", "memories_fts"} & tables
if "--core-only" in sys.argv:
    assert importlib.util.find_spec("mcp") is None
    assert importlib.util.find_spec("uvicorn") is None
    names = []
else:
    names = asyncio.run(run_mcp())
print(json.dumps({
    "version": version, "python": sys.version, "imported_from": str(module),
    "checks": ["installed_import", "cli_lifecycle", "export", "backup_integrity",
               "purge_preview", "no_legacy_interfaces"] + (["minimal_cli_dependencies"] if not names else ["mcp_stdio_lifecycle"]),
    "mcp_tools": names,
}))
'''


def clean_environment(directory: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("HANDOFF_", "MEMORY_", "PYTHON", "PIP_", "GIT_"))}
    env.update({"PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1",
                "HANDOFF_DATA_DIR": str(directory / "data"),
                "HANDOFF_DB_PATH": str(directory / "data" / "check.db")})
    return env


def check_wheel(wheel: Path, wheelhouse: Path | None, constraints: Path | None) -> dict:
    started = time.monotonic()
    with zipfile.ZipFile(wheel) as archive:
        removed = ("app/main.py", "app/routers/", "app/llm/", "app/pipeline/",
                   "app/storage/base.py", "app/storage/sqlite_store.py")
        unexpected = [name for name in archive.namelist() if name.startswith(removed)]
        if unexpected:
            raise ValueError(f"Removed service code remains in the wheel: {unexpected}")
    with tempfile.TemporaryDirectory(prefix="handoff_wheel_") as temp:
        root = Path(temp).resolve()
        env = clean_environment(root)
        environment = root / "venv"
        subprocess.run([sys.executable, "-I", "-m", "venv", str(environment)],
                       cwd=root, env=env, check=True, timeout=120)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        install = [str(python), "-I", "-m", "pip", "--isolated", "--disable-pip-version-check",
                   "install"]
        if wheelhouse:
            install += ["--no-index", "--find-links", str(wheelhouse)]
        if constraints:
            install += ["--constraint", str(constraints)]
        # First prove the CLI works with only the base dependencies.
        install.append(str(wheel))
        subprocess.run(install, cwd=root, env=env, check=True, timeout=300)
        runner = root / "installed_checks.py"
        runner.write_text(textwrap.dedent(INSTALLED_CHECKS), encoding="utf-8")
        core = subprocess.run([str(python), "-I", str(runner), "--core-only"],
            cwd=root, env=env, check=True, capture_output=True, text=True, timeout=60)
        core_report = json.loads(core.stdout)
        # The second phase uses independent data to keep lifecycle assertions exact.
        env["HANDOFF_DB_PATH"] = str(root / "mcp-data" / "check.db")
        (root / "backup.db").unlink()
        install[-1] = f"{wheel}[mcp]"
        subprocess.run(install, cwd=root, env=env, check=True, timeout=300)
        subprocess.run([str(python), "-I", "-m", "pip", "check"],
                       cwd=root, env=env, check=True, timeout=30)
        runner = root / "installed_checks.py"
        runner.write_text(textwrap.dedent(INSTALLED_CHECKS), encoding="utf-8")
        with subprocess.Popen(
            [str(python), "-I", str(runner)], cwd=root, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            start_new_session=os.name != "nt",
        ) as process:
            try:
                stdout, stderr = process.communicate(timeout=180)
            finally:
                # Also reap descendants if a broken server hangs or the check is
                # interrupted. The group contains only this disposable check.
                if os.name != "nt":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                elif process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
        if process.returncode:
            raise RuntimeError(f"Installed checks failed for {wheel.name}:\n{stdout}\n{stderr}")
        report = json.loads(stdout)
        report["minimal_cli"] = core_report
        report.update({"wheel": wheel.name, "successful": True,
                       "seconds": round(time.monotonic() - started, 3)})
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheels", type=Path, nargs="+")
    parser.add_argument("--wheelhouse", type=Path, help="Install dependencies only from this local wheel directory")
    parser.add_argument("--constraints", type=Path, help="Pin dependency versions, e.g. uv export --no-emit-project --no-hashes")
    parser.add_argument("--output", type=Path, help="Also write the verification report as JSON")
    args = parser.parse_args()
    wheels = [wheel.resolve() for wheel in args.wheels]
    if any(not wheel.is_file() or wheel.suffix != ".whl" for wheel in wheels):
        parser.error("Every positional argument must be an existing .whl file")
    wheelhouse = args.wheelhouse.resolve() if args.wheelhouse else None
    constraints = args.constraints.resolve() if args.constraints else None
    if wheelhouse and not wheelhouse.is_dir():
        parser.error("--wheelhouse must be an existing directory")
    if constraints and not constraints.is_file():
        parser.error("--constraints must be an existing requirements file")
    reports = []
    try:
        for wheel in wheels:
            reports.append(check_wheel(wheel, wheelhouse, constraints))
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
        reports.append({"successful": False, "error": str(exc)})
    report = {"successful": all(item["successful"] for item in reports), "wheels": reports}
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
