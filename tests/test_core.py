"""Regression checks for the MCP/CLI-only architecture and existing local data."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from app import db
from app.storage.handoff_store import HandoffStore
from test_handoff import BASE, IsolatedCase
from app.models import HandoffCreate

ROOT = Path(__file__).resolve().parents[1]


class CoreSurfaceTests(IsolatedCase):
    def test_new_database_contains_only_handoff_tables_without_fts(self):
        new_path = self.root / 'minimal.db'
        connection = sqlite3.connect(new_path)
        connection.row_factory = sqlite3.Row
        connection.set_authorizer(lambda action, *args:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_VTABLE else sqlite3.SQLITE_OK)
        with patch.object(db, 'get_conn', return_value=connection):
            db.init_db()
        with sqlite3.connect(new_path) as check:
            tables = {r[0] for r in check.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        self.assertEqual(tables, {'handoffs', 'events', 'handoff_verifications', 'handoff_check_runs'})

    def test_old_data_remains_untouched_across_supported_database_versions(self):
        for version in (0, 1, 2):
            with self.subTest(version=version):
                path = self.root / f'legacy-{version}.db'
                with patch.object(db, 'DB_PATH', path):
                    store = HandoffStore()
                    h = store.create(HandoffCreate(**BASE))
                    with sqlite3.connect(path) as connection:
                        connection.execute('CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT)')
                        connection.execute("INSERT INTO memories VALUES ('legacy', 'keep this data')")
                        connection.execute(f'PRAGMA user_version={version}')
                    db.init_db()
                    self.assertEqual(store.get(h['id'])['payload']['goal'], BASE['goal'])
                    with sqlite3.connect(path) as connection:
                        self.assertEqual(connection.execute('SELECT * FROM memories').fetchall(),
                                         [('legacy', 'keep this data')])

    def test_cli_and_core_do_not_import_web_or_legacy_services(self):
        source = '''import importlib.abc, json, sys
class RejectRemoved(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'fastapi', 'uvicorn', 'starlette', 'httpx', 'mcp'}:
            raise AssertionError('unexpected dependency: ' + fullname)
        removed = ('app.main', 'app.routers', 'app.llm', 'app.pipeline')
        if any(fullname == name or fullname.startswith(name + '.') for name in removed):
            raise AssertionError('unexpected legacy import: ' + fullname)
sys.meta_path.insert(0, RejectRemoved())
from app.handoff_cli import main
assert main(['schema']) == 0
from app.storage.handoff_store import HandoffStore
assert HandoffStore().list() == []
'''
        env = {**os.environ, 'HANDOFF_DB_PATH': str(self.root / 'subprocess.db'),
               'PYTHONPATH': str(ROOT / 'server')}
        result = subprocess.run([sys.executable, '-c', source], env=env, text=True,
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
