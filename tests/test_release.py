"""Upgrade and data lifecycle checks for the first public release."""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import db
from app.handoff_artifacts import verify_handoff
from app.maintenance import backup_database, export_data, purge_data
from app.models import HandoffCreate
from test_handoff import IsolatedCase, BASE


class ReleaseTests(IsolatedCase):
    def test_migrate_legacy_database_preserves_payload_and_events(self):
        h = self.create()
        conn = db.get_conn()
        with conn:
            legacy = dict(h["payload"])
            legacy.pop("schema_version")
            conn.execute("UPDATE handoffs SET payload = ? WHERE id = ?", (json.dumps(legacy), h["id"]))
            conn.execute("PRAGMA user_version=0")
        conn.close()
        db.init_db()
        db.init_db()
        self.assertEqual(self.store.get(h["id"])["payload"]["schema_version"], 1)
        self.assertEqual(self.events(h["id"]), ["handoff.created"])
        conn = db.get_conn()
        try:
            self.assertEqual(json.loads(conn.execute("SELECT payload FROM handoffs").fetchone()[0]), legacy)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
        finally:
            conn.close()

    def test_newer_database_is_not_downgraded(self):
        conn = db.get_conn()
        with conn:
            conn.execute("PRAGMA user_version=999")
        conn.close()
        with self.assertRaises(RuntimeError):
            db.init_db()
        conn = db.get_conn()
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 999)
        finally:
            conn.close()

    def test_unknown_payload_version_rejected(self):
        with self.assertRaises(ValueError):
            HandoffCreate.model_validate({**BASE, "schema_version": 3})
        result = verify_handoff({"payload": {"schema_version": 3}}, self.root)
        self.assertFalse(result["ok"])

    def test_export_includes_more_than_list_limit(self):
        for i in range(102):
            self.create(task_id=str(i))
        result = export_data()
        self.assertEqual(len(result["handoffs"]), 102)
        self.assertEqual(len(result["events"]), 102)
        self.assertEqual(result["schema_version"], 1)

    def test_backup_restores_committed_wal_without_overwrite(self):
        h = self.create()
        keeper = db.get_conn()
        self.addCleanup(keeper.close)
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        with keeper:
            keeper.execute("UPDATE handoffs SET response = 'WAL evidence' WHERE id = ?", (h["id"],))
        destination = self.root / "backup.db"
        backup_database(destination)
        restored = sqlite3.connect(destination)
        try:
            self.assertEqual(restored.execute("SELECT response FROM handoffs").fetchone()[0], "WAL evidence")
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            restored.close()
        original = destination.read_bytes()
        with self.assertRaises(FileExistsError):
            backup_database(destination)
        self.assertEqual(destination.read_bytes(), original)

    def test_backup_rejects_database_sidecar_names(self):
        self.create()
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                with self.assertRaises(ValueError):
                    backup_database(Path(str(self.database) + suffix))

    def test_purge_preview_retains_active_and_recent_records(self):
        old = datetime.now(timezone.utc) - timedelta(days=40)
        with patch("app.storage.handoff_store._now", return_value=old):
            terminal = self.create()["id"]
            self.store.claim(terminal, "bob")
            self.store.ack(terminal, "bob", True)
            self.store.complete(terminal, "bob")
            active = self.create()["id"]
            self.store.claim(active, "bob")
            self.store.ack(active, "bob", True)
        recent = self.create()["id"]
        self.store.claim(recent, "bob")
        self.store.ack(recent, "bob", False, "retry later")
        preview = purge_data(30)
        self.assertEqual(preview["ids"], [terminal])
        self.assertIsNotNone(self.store.get(terminal))
        self.assertTrue(self.events(terminal))
        result = purge_data(30, apply=True)
        self.assertEqual(result["ids"], [terminal])
        self.assertIsNone(self.store.get(terminal))
        self.assertEqual(self.events(terminal), [])
        self.assertIsNotNone(self.store.get(active))
        self.assertIsNotNone(self.store.get(recent))

    def test_purge_rolls_back_on_event_failure(self):
        h = self.create()["id"]
        conn = db.get_conn()
        with conn:
            conn.execute("UPDATE handoffs SET status='expired', updated_at='2000-01-01' WHERE id=?", (h,))
        conn.close()
        with patch("app.maintenance.log_event", side_effect=RuntimeError("injected failure")):
            with self.assertRaises(RuntimeError):
                purge_data(30, apply=True)
        self.assertIsNotNone(self.store.get(h))
        self.assertEqual(self.events(h), ["handoff.created"])

    def test_config_supports_legacy_and_new_names(self):
        source = str(Path(__file__).resolve().parents[1] / "server")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("HANDOFF_", "MEMORY_"))}
        env.update(PYTHONPATH=source, MEMORY_DATA_DIR=str(self.root / "legacy"))
        code = "from app.config import DB_PATH; print(DB_PATH)"
        def path():
            return subprocess.check_output([sys.executable, "-c", code], env=env, text=True).strip()
        self.assertEqual(path(), str(self.root / "legacy" / "memory.db"))
        env["HANDOFF_DATA_DIR"] = str(self.root / "new")
        self.assertEqual(path(), str(self.root / "new" / "memory.db"))
        env["MEMORY_DB_PATH"] = str(self.root / "legacy.db")
        self.assertEqual(path(), str(self.root / "legacy.db"))
        env["HANDOFF_DB_PATH"] = str(self.root / "new.db")
        self.assertEqual(path(), str(self.root / "new.db"))
