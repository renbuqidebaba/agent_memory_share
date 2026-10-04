"""Real Git, child processes and SQLite receipts at the trust boundaries."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import db
from app.models import HandoffCreate
from app.maintenance import export_data, purge_data
from app.storage.handoff_store import HandoffConflict
from app.workspace_capture import capture_workspace, verify_snapshot
from test_handoff import BASE, IsolatedCase


class EvidenceTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.file = self.workspace / 'main.py'
        self.file.write_text('value = 1\n')
        self.git('init', '-q')
        self.git('add', 'main.py')
        self.git('commit', '-qm', 'initial')

    def git(self, *args):
        env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        return subprocess.check_output(['git', '-c', 'user.name=Test', '-c',
            'user.email=test@example.invalid', *args], cwd=self.workspace, env=env)

    def capture(self, **kwargs):
        return self.store.capture(HandoffCreate(**BASE), self.workspace, **kwargs)

    def claim_verify(self, h):
        self.store.claim(h['id'], 'bob')
        return self.store.verify(h['id'], 'bob', self.workspace)

    def test_collects_staged_unstaged_deleted_and_explicit_untracked(self):
        self.file.write_text('value = 2\n')
        self.git('add', 'main.py')
        self.file.write_text('value = 3\n')
        (self.workspace / 'new.txt').write_text('new')
        (self.workspace / 'ignored.txt').write_text('not selected')
        snapshot = capture_workspace(self.workspace, ['new.txt']).model_dump()
        self.assertEqual({f['path'] for f in snapshot['files']}, {'main.py', 'new.txt'})
        self.assertIn('+value = 3', snapshot['diff'])
        self.assertIn('ignored.txt', snapshot['untracked_not_included'])
        self.file.unlink()
        self.assertFalse(verify_snapshot(snapshot, self.workspace)['ok'])
        deleted = capture_workspace(self.workspace).model_dump()
        self.assertIsNone(next(f for f in deleted['files'] if f['path'] == 'main.py')['sha256'])
        self.file.write_text('restored')
        self.assertFalse(verify_snapshot(deleted, self.workspace)['ok'])

    def test_excludes_secret_diff_and_rejects_explicit_secrets(self):
        secret = self.workspace / '.env'
        secret.write_text('TOKEN=original')
        self.git('add', '.env')
        self.git('commit', '-qm', 'fixture')
        secret.write_text('TOKEN=must-not-appear')
        snapshot = capture_workspace(self.workspace).model_dump()
        self.assertIn('.env', snapshot['excluded'])
        self.assertNotIn('must-not-appear', json.dumps(snapshot))
        with self.assertRaises(ValueError):
            capture_workspace(self.workspace, ['.env'])

    def test_refuses_symlinks_traversal_and_git_subdirectory(self):
        (self.workspace / 'link').symlink_to(self.file)
        for name in ('link', '../outside'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                capture_workspace(self.workspace, [name])
        sub = self.workspace / 'sub'
        sub.mkdir()
        with self.assertRaises(ValueError):
            capture_workspace(sub)

    def test_capture_ignores_inherited_git_redirect(self):
        with patch.dict(os.environ, {'GIT_DIR': '/missing/repo', 'GIT_WORK_TREE': '/missing/work'}):
            snapshot = capture_workspace(self.workspace)
        self.assertEqual(snapshot.git_head, self.git('rev-parse', 'HEAD').decode().strip())

    def test_new_tracked_change_invalidates_snapshot(self):
        snapshot = capture_workspace(self.workspace).model_dump()
        self.file.write_text('changed')
        self.assertFalse(verify_snapshot(snapshot, self.workspace)['ok'])

    def test_measured_failed_check_can_be_attached_without_becoming_passed(self):
        check = self.store.run_check(self.workspace, [sys.executable, '-c',
            'import sys; print("actual failure"); sys.exit(7)'])
        self.assertEqual(check['exit_code'], 7)
        self.assertEqual(check['outcome'], 'failed')
        h = self.capture(check_ids=[check['id']])
        self.assertEqual(h['payload']['observed_checks'][0]['exit_code'], 7)
        self.assertIn('actual failure', h['payload']['observed_checks'][0]['stdout'])
        self.assertTrue(h['payload']['require_verification'])

    def test_stale_or_other_workspace_check_cannot_be_attached(self):
        check = self.store.run_check(self.workspace, [sys.executable, '-c', 'print("ok")'])
        self.file.write_text('changed')
        with self.assertRaises(HandoffConflict):
            self.capture(check_ids=[check['id']])
        other = self.root / 'other'
        other.mkdir()
        (other / 'a').write_text('x')
        with self.assertRaises(ValueError):
            self.store.capture(HandoffCreate(**BASE), other, ['a'], [check['id']])

    def test_check_timeout_and_bounded_logs(self):
        result = self.store.run_check(self.workspace, [sys.executable, '-c',
            'import time; print("started", flush=True); time.sleep(10)'], timeout_seconds=1)
        self.assertTrue(result['timed_out'])
        self.assertEqual(result['outcome'], 'failed')
        self.assertIn('started', result['stdout'])
        noisy = self.store.run_check(self.workspace, [sys.executable, '-c', 'print("a" * 100000)'])
        self.assertTrue(noisy['logs_truncated'])
        self.assertLessEqual(len(noisy['stdout'].encode()), 65536)

    def test_capture_never_executes_declared_commands_or_accepts_forged_observation(self):
        marker = self.workspace / 'marker'
        body = HandoffCreate(**BASE, validation=[{'command': f'touch {marker}', 'outcome': 'passed'}])
        self.store.capture(body, self.workspace)
        self.assertFalse(marker.exists())
        forged = HandoffCreate(**BASE, observed_checks=[{'outcome': 'passed'}])
        with self.assertRaises(ValueError):
            self.store.create(forged)
        with self.assertRaises(ValueError):
            self.store.capture(forged, self.workspace)

    def test_receipt_required_and_valid_receipt_accepts(self):
        h = self.capture()
        self.assertEqual(h['payload']['schema_version'], 2)
        self.store.claim(h['id'], 'bob')
        with self.assertRaises(HandoffConflict):
            self.store.ack(h['id'], 'bob', True)
        receipt = self.store.verify(h['id'], 'bob', self.workspace)
        self.assertTrue(receipt['ok'])
        result = self.store.ack(h['id'], 'bob', True, verification_id=receipt['verification_id'])
        self.assertEqual(result['status'], 'accepted')
        self.assertIn('handoff.verified', self.events(h['id']))

    def test_v2_cannot_disable_verification_and_legacy_still_works(self):
        with self.assertRaises(ValueError):
            HandoffCreate(**BASE, schema_version=2, require_verification=False)
        old = self.create()
        self.store.claim(old['id'], 'bob')
        self.assertEqual(self.store.ack(old['id'], 'bob', True)['status'], 'accepted')

    def test_inbox_filters_include_open_packages_in_workspace(self):
        h = self.capture()
        self.create(task_id='other', to_agent='eve')
        self.assertEqual([r['id'] for r in self.store.list(to_agent='bob', workspace=str(self.workspace))], [h['id']])
        self.assertEqual(self.store.list(workspace=str(self.root / 'other')), [])

    def test_changed_after_verify_rejects_acceptance_without_state_change(self):
        h = self.capture()
        receipt = self.claim_verify(h)
        self.file.write_text('modified after verify')
        with self.assertRaises(HandoffConflict):
            self.store.ack(h['id'], 'bob', True, verification_id=receipt['verification_id'])
        self.assertEqual(self.store.get(h['id'])['status'], 'claimed')

    def test_failed_cross_task_and_wrong_owner_receipts_rejected(self):
        a = self.capture()
        b = self.capture()
        receipt = self.claim_verify(a)
        self.store.claim(b['id'], 'bob')
        with self.assertRaises(HandoffConflict):
            self.store.ack(b['id'], 'bob', True, verification_id=receipt['verification_id'])
        with self.assertRaises(HandoffConflict):
            self.store.verify(a['id'], 'eve', self.workspace)
        self.file.write_text('changed')
        failed = self.store.verify(b['id'], 'bob', self.workspace)
        self.assertFalse(failed['ok'])
        with self.assertRaises(HandoffConflict):
            self.store.ack(b['id'], 'bob', True, verification_id=failed['verification_id'])

    def test_reclaimed_lease_invalidates_prior_receipt(self):
        h = self.capture()
        start = datetime.now(timezone.utc)
        with patch('app.storage.handoff_store._now', return_value=start):
            self.store.claim(h['id'], 'bob', 60)
            old = self.store.verify(h['id'], 'bob', self.workspace)
        with patch('app.storage.handoff_store._now', return_value=start + timedelta(seconds=61)):
            self.store.claim(h['id'], 'bob', 60)
            with self.assertRaises(HandoffConflict):
                self.store.ack(h['id'], 'bob', True, verification_id=old['verification_id'])

    def test_receipt_and_event_roll_back_together(self):
        h = self.capture()
        self.store.claim(h['id'], 'bob')
        with patch('app.storage.handoff_store.log_event', side_effect=RuntimeError('injected')):
            with self.assertRaises(RuntimeError):
                self.store.verify(h['id'], 'bob', self.workspace)
        self.assertEqual(self.store.verifications(h['id']), [])

    def test_export_and_purge_include_receipts(self):
        h = self.capture()
        receipt = self.claim_verify(h)
        self.store.ack(h['id'], 'bob', True, verification_id=receipt['verification_id'])
        self.store.complete(h['id'], 'bob')
        self.assertEqual(len(export_data()['handoff_verifications']), 1)
        conn = db.get_conn()
        with conn:
            conn.execute("UPDATE handoffs SET updated_at='2000-01-01' WHERE id=?", (h['id'],))
        conn.close()
        purge_data(30, apply=True)
        self.assertEqual(self.store.verifications(h['id']), [])

    def test_v1_migration_preserves_rows_and_creates_evidence_tables(self):
        h = self.create()
        conn = db.get_conn()
        with conn:
            conn.execute('DROP TABLE handoff_verifications')
            conn.execute('DROP TABLE handoff_check_runs')
            conn.execute('PRAGMA user_version=1')
        conn.close()
        db.init_db()
        self.assertEqual(self.store.get(h['id'])['id'], h['id'])
        self.assertEqual(self.store.verifications(h['id']), [])
