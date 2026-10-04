"""Execute only an explicitly supplied argv; retain measured local evidence."""
import os
import signal
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .workspace_capture import capture_workspace, git_env, verify_snapshot

MAX_LOG_BYTES = 64 * 1024


def run_check(workspace, argv, timeout_seconds=120, include=()):
    if not argv or any(not isinstance(a, str) or '\0' in a for a in argv) or not argv[0].strip():
        raise ValueError('检查命令必须是非空 argv 数组')
    if not 1 <= timeout_seconds <= 600:
        raise ValueError('检查超时必须在 1 到 600 秒之间')
    root = Path(workspace).resolve()
    before = capture_workspace(root, include).model_dump()
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    timed_out = False
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(argv, cwd=root, env=git_env(), stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, shell=False, start_new_session=os.name != 'nt')
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == 'nt':
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
        def read(stream):
            stream.seek(0)
            data = stream.read(MAX_LOG_BYTES + 1)
            return data[:MAX_LOG_BYTES].decode('utf-8', errors='replace'), len(data) > MAX_LOG_BYTES
        out, out_cut = read(stdout)
        err, err_cut = read(stderr)
    return {'source': 'tool_observed', 'argv': argv, 'workspace': str(root),
        'started_at': started_at, 'duration_seconds': round(time.monotonic() - started, 3),
        'exit_code': process.returncode, 'timed_out': timed_out,
        'outcome': 'passed' if process.returncode == 0 and not timed_out else 'failed',
        'stdout': out, 'stderr': err, 'logs_truncated': out_cut or err_cut,
        'workspace_snapshot': before,
        'snapshot_unchanged': verify_snapshot(before, root)['ok']}
