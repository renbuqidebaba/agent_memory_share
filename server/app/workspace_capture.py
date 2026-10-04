"""Bounded, read-only workspace evidence. No command is read from a handoff."""
import hashlib
import os
import platform
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .handoff_artifacts import _safe_path, _sha256
from .models import SnapshotFile, WorkspaceSnapshot

MAX_FILES = 200
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_DIFF_BYTES = 128 * 1024
DEPENDENCIES = ('pyproject.toml', 'uv.lock', 'requirements.txt', 'package.json',
                'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock', 'Cargo.toml',
                'Cargo.lock', 'go.mod', 'go.sum', 'Gemfile', 'Gemfile.lock')


def git_env():
    # Caller GIT_DIR/GIT_WORK_TREE must never redirect capture to another repo.
    return {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}


def git(root, *args):
    try:
        return subprocess.run(['git', '--literal-pathspecs', '-c', 'core.fsmonitor=false', *args], cwd=root,
                              env=git_env(), capture_output=True, timeout=15, check=False)
    except FileNotFoundError:
        return subprocess.CompletedProcess(['git'], 1, b'', b'git unavailable')


def excluded_path(name):
    path = Path(name)
    for part in path.parts:
        lower = part.lower()
        if lower in ('.git', '.ssh', '.aws', 'node_modules', '.venv', '__pycache__'):
            return True
        if lower in ('.env', '.npmrc', '.pypirc', 'credentials.json', 'id_rsa', 'id_ed25519'):
            return True
        if lower.startswith('.env.') and lower not in ('.env.example', '.env.sample'):
            return True
        if lower.endswith(('.pem', '.key', '.p12', '.pfx')):
            return True
    return False


def git_state(root):
    status = git(root, 'status', '--porcelain=v1', '-z', '--no-renames', '--untracked-files=all', '--', '.')
    if status.returncode:
        if (root / '.git').exists():
            raise ValueError('无法读取 Git 工作区状态，不能完成自动采集')
        return None, None, [], []
    top = git(root, 'rev-parse', '--show-toplevel')
    if Path(os.fsdecode(top.stdout).strip()).resolve() != root:
        raise ValueError('自动采集请使用 Git 仓库根目录，避免遗漏或误读其他目录')
    changed, untracked = [], []
    for item in status.stdout.split(b'\0'):
        if item:
            name = os.fsdecode(item[3:])
            (untracked if item[:2] == b'??' else changed).append(name)
    head = git(root, 'rev-parse', '--verify', 'HEAD')
    branch = git(root, 'symbolic-ref', '--short', '-q', 'HEAD')
    return (head.stdout.decode().strip() if head.returncode == 0 else None,
            branch.stdout.decode().strip() if branch.returncode == 0 else None,
            sorted(changed), sorted(untracked))


def file_state(root, name):
    path = _safe_path(root, name)
    # Do not dereference even in-workspace symlinks during automatic capture.
    relative = Path(name)
    if any((root / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)):
        raise ValueError(f'自动采集不跟随符号链接: {name}')
    if not path.exists():
        return SnapshotFile(path=name)
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f'产物不是普通文件或超过 10 MiB: {name}')
    return SnapshotFile(path=name, sha256=_sha256(path), executable=bool(path.stat().st_mode & 0o111))


def capture_workspace(workspace, include=()):
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise ValueError(f'工作区不存在: {root}')
    head, branch, changed, untracked = git_state(root)
    explicit = set(include)
    for name in explicit:
        _safe_path(root, name)
        if excluded_path(name):
            raise ValueError(f'敏感或内部路径不能自动采集: {name}')
        if not (root / name).is_file():
            raise ValueError(f'明确纳入的文件不存在: {name}')
    excluded = sorted(n for n in changed + untracked if excluded_path(n))
    tracked = [n for n in changed if not excluded_path(n)]
    names = sorted(set(tracked) | explicit | {n for n in DEPENDENCIES if (root / n).is_file()})
    if len(names) > MAX_FILES:
        raise ValueError(f'自动采集最多 {MAX_FILES} 个文件；请缩小工作区')
    files = [file_state(root, n) for n in names]
    diff, truncated = '', False
    if tracked:
        args = ['git', '--literal-pathspecs', '-c', 'core.fsmonitor=false', 'diff', '--no-ext-diff', '--no-textconv', '--no-renames']
        # HEAD diff includes staged + unstaged edits; unborn repos use cached diff.
        args += ['HEAD'] if head else ['--cached']
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(args + ['--', *tracked], cwd=root, env=git_env(),
                                    stdout=output, stderr=subprocess.PIPE, timeout=15)
            if result.returncode:
                raise ValueError('无法收集 Git diff')
            output.seek(0)
            raw = output.read(MAX_DIFF_BYTES + 1)
        truncated = len(raw) > MAX_DIFF_BYTES
        diff = raw[:MAX_DIFF_BYTES].decode('utf-8', errors='replace')
    snapshot = WorkspaceSnapshot(captured_at=datetime.now(timezone.utc).isoformat(),
        git_head=head, git_branch=branch, tracked_changes=tracked, files=files, diff=diff,
        diff_truncated=truncated, excluded=excluded,
        untracked_not_included=[n for n in untracked if n not in explicit and not excluded_path(n)],
        capture_runtime={'python': platform.python_version(), 'platform': platform.platform()})
    if not head and not files:
        raise ValueError('没有可核验的文件；非 Git 工作区请明确指定产物')
    if not verify_snapshot(snapshot.model_dump(), root)['ok']:
        raise ValueError('采集期间工作区发生变化，请重试')
    return snapshot


def verify_snapshot(snapshot, workspace):
    root = Path(workspace).resolve()
    checks = []
    try:
        snapshot = WorkspaceSnapshot.model_validate(snapshot)
        head, _, changed, _ = git_state(root)
        if snapshot.git_head is not None or snapshot.tracked_changes:
            checks.append({'kind': 'snapshot_git', 'ok': head == snapshot.git_head and
                [n for n in changed if not excluded_path(n)] == snapshot.tracked_changes,
                'expected': snapshot.git_head, 'actual': head})
        for expected in snapshot.files:
            try:
                actual = file_state(root, expected.path)
                checks.append({'kind': 'snapshot_file', 'path': expected.path,
                               'ok': actual == expected, 'expected': expected.model_dump(),
                               'actual': actual.model_dump()})
            except (ValueError, OSError) as exc:
                checks.append({'kind': 'snapshot_file', 'path': expected.path, 'ok': False, 'detail': str(exc)})
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        checks.append({'kind': 'snapshot', 'ok': False, 'detail': str(exc)})
    return {'ok': bool(checks) and all(c['ok'] for c in checks), 'checks': checks}


def snapshot_digest(snapshot):
    """Fingerprint the evidence scope, not capture time or machine-specific metadata."""
    import json
    value = {k: snapshot[k] for k in ('git_head', 'tracked_changes', 'files')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
