"""Prepare independent trial workspaces; never fabricate model trials or scores."""
import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from app.workspace_capture import excluded_path, git_env  # noqa: E402

CONDITIONS = ('repository_only', 'progress_note', 'handoff')


def prepare(fixture, output, repeats=2, seed=42):
    fixture, output = Path(fixture).resolve(), Path(output).resolve()
    if not 1 <= repeats <= 20:
        raise ValueError('repeats must be between 1 and 20')
    if output == fixture or output.is_relative_to(fixture):
        raise ValueError('output must be outside the fixture')
    payload = json.loads((fixture / 'payload.json').read_text())
    note = (fixture / 'progress.md').read_text()
    workspace = fixture / 'workspace'
    if not workspace.is_dir():
        raise ValueError('fixture/workspace is required')
    # Explicit fixture input: refuse symlinks/sensitive names instead of copying a
    # real project's credentials or following an external checkout's .git file.
    files = []
    for path in workspace.rglob('*'):
        name = path.relative_to(workspace)
        if '.git' in name.parts or '__pycache__' in name.parts:
            continue
        if path.is_symlink():
            raise ValueError(f'fixture contains a symlink: {name}')
        if path.is_file():
            if excluded_path(str(name)) or path.stat().st_size > 10 * 1024 * 1024:
                raise ValueError(f'fixture contains excluded/large file: {name}')
            files.append(path)
    if not files or len(files) > 200:
        raise ValueError('fixture must contain 1..200 regular files')
    # Strip machine observations; each handoff trial must capture its own clone.
    payload.pop('workspace_snapshot', None)
    payload.pop('observed_checks', None)
    payload.pop('workspace_root', None)
    payload.update(schema_version=1, require_verification=False)
    output.mkdir(parents=True, exist_ok=False)
    seed_workspace = output / '_seed'
    seed_workspace.mkdir()
    for path in files:
        dest = seed_workspace / path.relative_to(workspace)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        shutil.copymode(path, dest)
    env = git_env()
    env.update(GIT_AUTHOR_DATE='2026-01-01T00:00:00Z', GIT_COMMITTER_DATE='2026-01-01T00:00:00Z')
    def git(*args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', '-c',
            'commit.gpgsign=false', '-c', 'user.name=Handoff Evaluation', '-c',
            'user.email=evaluation@example.invalid', *args], cwd=seed_workspace, env=env, stderr=subprocess.PIPE).decode().strip()
    git('init', '-q')
    git('add', '--all')
    git('commit', '-qm', 'Identical evaluation starting point')
    revision = git('rev-parse', 'HEAD')
    manifest = {'kind': 'prepared-evaluation', 'uses_llm': False, 'completed_trials': 0,
                'fixture': str(fixture), 'task_id': payload['task_id'], 'seed': seed,
                'source_revision': revision, 'trials': []}
    order = [(repeat, condition) for repeat in range(1, repeats + 1) for condition in CONDITIONS]
    random.Random(seed).shuffle(order)
    for position, (repeat, condition) in enumerate(order, 1):
        trial_id = f'{position:02d}-{condition}-{repeat}'
        directory = output / trial_id
        trial_workspace = directory / 'workspace'
        shutil.copytree(seed_workspace, trial_workspace)
        trial_payload = {**payload, 'task_id': f"{payload['task_id']}-{trial_id}", 'to_agent': None}
        original = f"目标：{payload['goal']}\n完成标准：" + json.dumps(payload.get('done_when', []), ensure_ascii=False)
        prompt = (f'{original}\n只在本试验工作区 {trial_workspace} 内执行任务。'
                  '不要读取其他试验、种子目录或项目实现来获得答案。结束时报告改动和实际验收结果。\n')
        hid = None
        if condition == 'progress_note':
            (directory / 'progress.md').write_text(note)
            prompt += f"进度说明：{directory / 'progress.md'}\n"
        elif condition == 'handoff':
            source = directory / 'payload.json'
            source.write_text(json.dumps(trial_payload, ensure_ascii=False, indent=2))
            trial_env = {k:v for k,v in os.environ.items() if not k.startswith(('HANDOFF_', 'MEMORY_', 'GIT_'))}
            trial_env.update(HANDOFF_DB_PATH=str(directory / 'handoff.db'), PYTHONPATH=str(ROOT / 'server'))
            command = [sys.executable, '-m', 'app.handoff_cli', 'create', str(source),
                       '--workspace', str(trial_workspace), '--capture']
            result = subprocess.run(command, cwd=ROOT, env=trial_env, text=True, capture_output=True, timeout=30)
            if result.returncode:
                raise RuntimeError(result.stderr)
            hid = json.loads(result.stdout)['id']
            prompt += (f"交接 ID：{hid}；数据库：{directory / 'handoff.db'}。"
                       '通过 MCP 或 CLI 领取、保存核验记录、接受，然后继续完成任务。\n')
        (directory / 'receiver-prompt.md').write_text(prompt)
        record = {'trial_id': trial_id, 'task_id': payload['task_id'], 'condition': condition,
                  'model': '', 'client_version': '', 'source_revision': revision,
                  'status': 'not_run', 'elapsed_seconds': None, 'input_tokens': None,
                  'output_tokens': None, 'sender_elapsed_seconds': None,
                  'sender_input_tokens': None, 'sender_output_tokens': None,
                  'repeated_checks': None, 'evidence': ''}
        (directory / 'record.json').write_text(json.dumps(record, indent=2))
        hashes = {str(p.relative_to(trial_workspace)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in trial_workspace.rglob('*') if p.is_file() and '.git' not in p.relative_to(trial_workspace).parts}
        manifest['trials'].append({'trial_id': trial_id, 'condition': condition, 'repeat': repeat,
            'workspace': str(trial_workspace), 'handoff_id': hid, 'files': hashes})
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True,
                        help='Directory containing workspace/, payload.json and progress.md')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    try:
        result = prepare(args.fixture, args.output, args.repeats, args.seed)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    print(json.dumps({'output': str(args.output.resolve()), 'prepared_trials': len(result['trials']),
                      'completed_trials': 0, 'uses_llm': False}))


if __name__ == '__main__':
    main()
