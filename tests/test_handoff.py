"""独立数据库、真实文件/Git 仓库与并发请求上的任务交接回归测试。"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import db
from app.handoff_artifacts import prepare_handoff, render_markdown, verify_handoff
from app.models import HandoffCreate
from app.storage.handoff_store import HandoffConflict, HandoffStore

ROOT = Path(__file__).resolve().parents[1]
BASE = {"task_id": "任务-1", "from_agent": "alice", "goal": "完成修复", "status_summary": "待接手"}
T0 = datetime(2026, 10, 4, tzinfo=timezone.utc)


class IsolatedCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="handoff_case_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "db" / "test.db"
        self.db_patch = patch.object(db, "DB_PATH", self.database)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.store = HandoffStore()

    def create(self, **overrides):
        return self.store.create(HandoffCreate.model_validate({**BASE, **overrides}))

    def events(self, hid):
        conn = db.get_conn()
        try:
            return [r["type"] for r in conn.execute(
                "SELECT type FROM events WHERE json_extract(payload, '$.id') = ? ORDER BY seq", (hid,)
            )]
        finally:
            conn.close()

    def raw_status(self, hid):
        conn = db.get_conn()
        try:
            return conn.execute("SELECT status FROM handoffs WHERE id = ?", (hid,)).fetchone()[0]
        finally:
            conn.close()


class StoreTests(IsolatedCase):
    def test_complete_lifecycle_and_event_order(self):
        """ST01 创建→领取→接受→完成，状态和审计事件一致。"""
        h = self.create()
        self.assertEqual(h["status"], "pending")
        self.store.claim(h["id"], "bob")
        self.store.ack(h["id"], "bob", True)
        self.assertEqual(self.store.complete(h["id"], "bob")["status"], "completed")
        self.assertEqual(self.events(h["id"]), ["handoff.created", "handoff.claimed", "handoff.accepted", "handoff.completed"])

    def test_rejection_is_terminal(self):
        """ST02 退回原因持久化，退回包不能继续领取或完成。"""
        h = self.create()
        self.store.claim(h["id"], "bob")
        self.assertEqual(self.store.ack(h["id"], "bob", False, "文件缺失")["response"], "文件缺失")
        for action in (lambda: self.store.claim(h["id"], "bob"), lambda: self.store.complete(h["id"], "bob")):
            with self.assertRaises(HandoffConflict):
                action()

    def test_claim_retry_does_not_extend_lease_or_duplicate_event(self):
        """ST03 相同接收者重试领取幂等，不延长租约或重复记事件。"""
        h = self.create()
        first = self.store.claim(h["id"], "bob", 60)
        second = self.store.claim(h["id"], "bob", 3600)
        self.assertEqual(first, second)
        self.assertEqual(self.events(h["id"]).count("handoff.claimed"), 1)

    def test_concurrent_claim_has_single_winner(self):
        """ST04 八个独立连接并发领取，只能有一个成功。"""
        hid = self.create()["id"]
        barrier = threading.Barrier(8)
        def claim(i):
            barrier.wait(timeout=10)
            try:
                return self.store.claim(hid, f"agent-{i}")["claimed_by"]
            except HandoffConflict:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            winners = [r for r in pool.map(claim, range(8)) if r]
        self.assertEqual(len(winners), 1)
        self.assertEqual(self.store.get(hid)["claimed_by"], winners[0])
        self.assertEqual(self.events(hid).count("handoff.claimed"), 1)

    def test_lease_boundary_reclaim_and_old_owner_denied(self):
        """ST05 租约精确到期时可重领，旧接收者不能确认。"""
        with patch("app.storage.handoff_store._now", return_value=T0):
            hid = self.create()["id"]
            self.store.claim(hid, "old", 60)
        with patch("app.storage.handoff_store._now", return_value=T0 + timedelta(seconds=60)):
            self.assertEqual(self.store.claim(hid, "new")["claimed_by"], "new")
            with self.assertRaises(HandoffConflict):
                self.store.ack(hid, "old", True)

    def test_expired_lease_cannot_ack(self):
        """ST06 租约到期后未经重新领取不能确认。"""
        with patch("app.storage.handoff_store._now", return_value=T0):
            hid = self.create()["id"]
            self.store.claim(hid, "bob", 60)
        with patch("app.storage.handoff_store._now", return_value=T0 + timedelta(seconds=60)):
            with self.assertRaises(HandoffConflict):
                self.store.ack(hid, "bob", True)

    def test_ttl_boundary_and_event_once(self):
        """ST07 TTL 到期前可见，到期后为 expired，重复读取只记一次事件。"""
        with patch("app.storage.handoff_store._now", return_value=T0):
            hid = self.create(ttl_seconds=60)["id"]
        with patch("app.storage.handoff_store._now", return_value=T0 + timedelta(seconds=59)):
            self.assertEqual(self.store.get(hid)["status"], "pending")
        with patch("app.storage.handoff_store._now", return_value=T0 + timedelta(seconds=60)):
            self.assertEqual(self.store.get(hid)["status"], "expired")
            self.store.get(hid)
            self.store.list()
        self.assertEqual(self.events(hid).count("handoff.expired"), 1)

    def test_claim_conflict_still_persists_expiration(self):
        """ST08 直接领取已过期包时，拒绝请求同时持久化过期状态。"""
        with patch("app.storage.handoff_store._now", return_value=T0):
            hid = self.create(ttl_seconds=60)["id"]
        with patch("app.storage.handoff_store._now", return_value=T0 + timedelta(seconds=60)):
            with self.assertRaises(HandoffConflict):
                self.store.claim(hid, "bob")
        self.assertEqual(self.raw_status(hid), "expired")
        self.assertEqual(self.events(hid).count("handoff.expired"), 1)

    def test_accepted_task_survives_package_ttl(self):
        """ST09 已接受任务不会被待领取 TTL 自动终止。"""
        with patch("app.storage.handoff_store._now", return_value=T0):
            hid = self.create(ttl_seconds=60)["id"]
            self.store.claim(hid, "bob")
            self.store.ack(hid, "bob", True)
        with patch("app.storage.handoff_store._now", return_value=T0 + timedelta(days=1)):
            self.assertEqual(self.store.get(hid)["status"], "accepted")

    def test_create_rolls_back_if_event_write_fails(self):
        """ST10 事件写入失败时创建操作整体回滚。"""
        with patch("app.storage.handoff_store.log_event", side_effect=RuntimeError("injected failure")):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertEqual(self.store.list(), [])

    def test_claim_rolls_back_if_event_write_fails(self):
        """ST11 事件写入失败时领取状态整体回滚。"""
        hid = self.create()["id"]
        with patch("app.storage.handoff_store.log_event", side_effect=RuntimeError("injected failure")):
            with self.assertRaises(RuntimeError):
                self.store.claim(hid, "bob")
        self.assertEqual(self.store.get(hid)["status"], "pending")
        self.assertEqual(self.events(hid), ["handoff.created"])

    def test_persistence_across_store_instances(self):
        """ST12 重新创建存储实例后，交接包仍可读取。"""
        h = self.create()
        self.assertEqual(HandoffStore().get(h["id"]), h)

    def test_concurrent_expiration_emits_single_event(self):
        """ST13 八个连接同时读取到期包，过期事件只写入一次。"""
        with patch("app.storage.handoff_store._now", return_value=T0):
            hid = self.create(ttl_seconds=60)["id"]
        barrier = threading.Barrier(8)
        def read(_):
            barrier.wait(timeout=10)
            return self.store.get(hid)["status"]
        with patch("app.storage.handoff_store._now", return_value=T0 + timedelta(seconds=60)):
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(list(pool.map(read, range(8))), ["expired"] * 8)
        self.assertEqual(self.events(hid).count("handoff.expired"), 1)

    def test_ack_conflict_still_persists_expiration(self):
        """ST14 直接确认到期交接包时，拒绝请求同时持久化过期状态。"""
        with patch("app.storage.handoff_store._now", return_value=T0):
            hid = self.create(ttl_seconds=60)["id"]
            self.store.claim(hid, "bob")
        with patch("app.storage.handoff_store._now", return_value=T0 + timedelta(seconds=60)):
            with self.assertRaises(HandoffConflict):
                self.store.ack(hid, "bob", True)
        self.assertEqual(self.raw_status(hid), "expired")
        self.assertEqual(self.events(hid).count("handoff.expired"), 1)


class CoreInputTests(IsolatedCase):
    def test_invalid_payload_rejected_without_write(self):
        invalid = [{}, {**BASE, "goal": ""}, {**BASE, "ttl_seconds": 59},
                   {**BASE, "ttl_seconds": 2592001},
                   {**BASE, "validation": [{"command": "x", "outcome": "unknown"}]}]
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.store.create(HandoffCreate.model_validate(body))
        self.assertEqual(self.store.list(), [])

    def test_blank_required_text_rejected(self):
        for field in ("task_id", "from_agent", "goal", "status_summary", "to_agent"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                HandoffCreate.model_validate({**BASE, field: "  \n"})

    def test_missing_resource_operations(self):
        self.assertIsNone(self.store.get("missing"))
        for action in (lambda: self.store.claim("missing", "bob"),
                       lambda: self.store.ack("missing", "bob", True),
                       lambda: self.store.complete("missing", "bob")):
            with self.assertRaises(KeyError):
                action()

    def test_designated_recipient(self):
        hid = self.create(to_agent="bob")["id"]
        with self.assertRaises(HandoffConflict):
            self.store.claim(hid, "eve")
        self.assertEqual(self.store.get(hid)["status"], "pending")

    def test_wrong_owner_cannot_ack_or_complete(self):
        hid = self.create()["id"]
        self.store.claim(hid, "bob")
        with self.assertRaises(HandoffConflict):
            self.store.ack(hid, "eve", True)
        self.store.ack(hid, "bob", True)
        with self.assertRaises(HandoffConflict):
            self.store.complete(hid, "eve")

    def test_cannot_skip_state_transitions(self):
        hid = self.create()["id"]
        with self.assertRaises(HandoffConflict):
            self.store.ack(hid, "bob", True)
        with self.assertRaises(HandoffConflict):
            self.store.complete(hid, "bob")
        self.store.claim(hid, "bob")
        with self.assertRaises(HandoffConflict):
            self.store.complete(hid, "bob")

    def test_reject_requires_reason(self):
        hid = self.create()["id"]
        self.store.claim(hid, "bob")
        with self.assertRaises(ValueError):
            self.store.ack(hid, "bob", False, "  ")
        self.assertEqual(self.store.get(hid)["status"], "claimed")

    def test_lease_validation(self):
        hid = self.create()["id"]
        for seconds in (0, 59, 86401):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                self.store.claim(hid, "bob", seconds)

    def test_filter_values_are_not_sql(self):
        a = self.create(task_id="a")
        self.create(task_id="b")
        self.store.claim(a["id"], "bob")
        self.assertEqual([r["id"] for r in self.store.list("a", "claimed")], [a["id"]])
        self.assertEqual(self.store.list("' OR 1=1 --"), [])


class ArtifactTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.file = self.workspace / "结果.txt"
        self.file.write_text("任务证据\n", encoding="utf-8")

    def snapshot(self, **fields):
        body = HandoffCreate.model_validate({**BASE, "artifacts": [{"path": self.file.name}], **fields})
        return {"payload": prepare_handoff(body, self.workspace).model_dump()}

    def test_sha_matches_independent_hash(self):
        """AR01 实际文件摘要与独立 SHA-256 计算一致。"""
        h = self.snapshot()
        self.assertEqual(h["payload"]["artifacts"][0]["sha256"], hashlib.sha256(self.file.read_bytes()).hexdigest())
        self.assertTrue(verify_handoff(h, self.workspace)["ok"])

    def test_modified_and_missing_file_fail(self):
        """AR02 文件修改或删除后核验失败。"""
        h = self.snapshot()
        self.file.write_text("changed")
        self.assertFalse(verify_handoff(h, self.workspace)["ok"])
        self.file.unlink()
        self.assertFalse(verify_handoff(h, self.workspace)["ok"])

    def test_empty_file_is_valid(self):
        """AR03 空文件可正确保存和校验摘要。"""
        self.file.write_bytes(b"")
        self.assertTrue(verify_handoff(self.snapshot(), self.workspace)["ok"])

    def test_missing_hash_fails(self):
        """AR04 文件存在但没有预期摘要时不能通过核验。"""
        h = self.snapshot()
        h["payload"]["artifacts"][0]["sha256"] = None
        self.assertFalse(verify_handoff(h, self.workspace)["ok"])

    def test_no_evidence_fails(self):
        """AR05 没有可核验证据时不宣称通过。"""
        self.assertFalse(verify_handoff({"payload": {}}, self.workspace)["ok"])

    def test_missing_workspace_fails(self):
        """AR06 不存在的工作区返回失败，创建时明确报错。"""
        self.assertFalse(verify_handoff(self.snapshot(), self.root / "missing")["ok"])
        with self.assertRaises(ValueError):
            prepare_handoff(HandoffCreate(**BASE), self.root / "missing")

    def test_missing_artifact_create_fails(self):
        """AR07 创建时引用不存在的文件必须失败。"""
        with self.assertRaises(ValueError):
            self.snapshot(artifacts=[{"path": "missing"}])

    def test_parent_traversal_rejected(self):
        """AR08 父目录路径在创建和核验两端均被拒绝。"""
        with self.assertRaises(ValueError):
            self.snapshot(artifacts=[{"path": "../outside"}])
        h = {"payload": {"artifacts": [{"path": "../outside", "sha256": "x"}]}}
        self.assertFalse(verify_handoff(h, self.workspace)["ok"])

    def test_absolute_path_inside_workspace_rejected(self):
        """AR09 交接引用必须是相对路径，即使绝对路径位于工作区内。"""
        with self.assertRaises(ValueError):
            self.snapshot(artifacts=[{"path": str(self.file)}])

    def test_symlink_escape_rejected(self):
        """AR10 指向工作区外部的符号链接不能作为产物。"""
        outside = self.root / "outside"
        outside.write_text("private")
        (self.workspace / "link").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.snapshot(artifacts=[{"path": "link"}])

    def test_read_error_returns_failed_check(self):
        """AR11 文件读取失败返回结构化失败项，核验流程不崩溃。"""
        h = self.snapshot()
        with patch("app.handoff_artifacts._sha256", side_effect=PermissionError("permission denied")):
            result = verify_handoff(h, self.workspace)
        self.assertFalse(result["ok"])
        self.assertIn("permission denied", result["checks"][0]["detail"])

    def test_git_revision_change_fails(self):
        """AR12 真实 Git 仓库 HEAD 改变时核验失败。"""
        def git(*args):
            return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args], cwd=self.workspace, check=True, capture_output=True)
        git("init", "-q")
        git("add", ".")
        git("commit", "-qm", "initial")
        h = self.snapshot()
        self.assertTrue(verify_handoff(h, self.workspace)["ok"])
        git("commit", "--allow-empty", "-qm", "new revision")
        self.assertFalse(verify_handoff(h, self.workspace)["ok"])

    def test_evidence_commands_are_not_executed(self):
        """AR13 验证记录中的命令只作为数据保存，核验不会执行它。"""
        marker = self.workspace / "must-not-exist"
        h = self.snapshot(validation=[{"command": f"touch {marker}", "outcome": "not_run"}])
        self.assertTrue(verify_handoff(h, self.workspace)["ok"])
        self.assertFalse(marker.exists())

    def test_markdown_contains_handoff_details(self):
        """AR14 Markdown 输出保留目标、下一步、文件与失败证据。"""
        h = self.create(next_steps=["修复回归"], validation=[{"command": "pytest", "outcome": "failed", "detail": "2 failures"}])
        rendered = render_markdown(h)
        for expected in (h["id"], "完成修复", "修复回归", "pytest", "failed", "2 failures"):
            self.assertIn(expected, rendered)


class CliTests(IsolatedCase):
    def cli(self, *args):
        env = {**os.environ, "HANDOFF_DB_PATH": str(self.database), "MEMORY_DB_PATH": str(self.database), "MEMORY_DATA_DIR": str(self.database.parent),
               "PYTHONPATH": str(ROOT / "server") + os.pathsep + os.environ.get("PYTHONPATH", "")}
        return subprocess.run([sys.executable, "-m", "app.handoff_cli", *args], env=env, capture_output=True, text=True, timeout=20)

    def test_cli_lifecycle_and_export(self):
        """CLI01 独立进程执行创建/导出/领取/核验/接受/完成/查询。"""
        file = self.root / "work.txt"
        file.write_text("work")
        source = self.root / "input.json"
        source.write_text(json.dumps({**BASE, "artifacts": [{"path": "work.txt"}]}))
        out = self.root / "handoff.md"
        r = self.cli("create", str(source), "--workspace", str(self.root), "--markdown", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        hid = json.loads(r.stdout)["id"]
        self.assertIn(hid, out.read_text())
        for args in [("claim", hid, "--agent", "bob"), ("verify", hid, "--workspace", str(self.root)),
                     ("ack", hid, "--agent", "bob", "--accept"), ("complete", hid, "--agent", "bob")]:
            r = self.cli(*args)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(self.cli("show", hid).stdout)["status"], "completed")
        self.assertIn("任务交接", self.cli("show", hid, "--markdown").stdout)

    def test_verification_failure_exit_code(self):
        """CLI02 核验失败返回退出码 2 且输出可解析报告。"""
        h = self.create()
        r = self.cli("verify", h["id"], "--workspace", str(self.root))
        self.assertEqual(r.returncode, 2)
        self.assertFalse(json.loads(r.stdout)["ok"])

    def test_missing_resource_exit_code(self):
        """CLI03 不存在的交接包返回退出码 1，错误写入 stderr。"""
        r = self.cli("show", "missing")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("错误", r.stderr)

    def test_invalid_json_does_not_create(self):
        """CLI04 非法 JSON 输入不写入交接包。"""
        source = self.root / "invalid.json"
        source.write_text("{bad json")
        r = self.cli("create", str(source), "--workspace", str(self.root))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.store.list(), [])

    def test_reject_command(self):
        """CLI05 接收者可以通过命令行退回并保留原因。"""
        h = self.create()
        self.store.claim(h["id"], "bob")
        r = self.cli("ack", h["id"], "--agent", "bob", "--reject", "--reason", "缺少测试")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["response"], "缺少测试")

    def test_capture_check_and_receipt_commands(self):
        (self.root / "code.py").write_text("print('works')")
        source = self.root / "payload.json"
        source.write_text(json.dumps(BASE))
        check = self.cli("run-check", "--workspace", str(self.root), "--include", "code.py",
                         "--", sys.executable, "code.py")
        self.assertEqual(check.returncode, 0, check.stderr)
        check_id = json.loads(check.stdout)["id"]
        created = self.cli("create", str(source), "--workspace", str(self.root), "--capture",
                           "--include", "code.py", "--check-id", check_id)
        self.assertEqual(created.returncode, 0, created.stderr)
        hid = json.loads(created.stdout)["id"]
        self.assertEqual(self.cli("claim", hid, "--agent", "bob").returncode, 0)
        self.assertEqual(self.cli("ack", hid, "--agent", "bob", "--accept").returncode, 1)
        verified = self.cli("verify", hid, "--workspace", str(self.root), "--agent", "bob")
        self.assertEqual(verified.returncode, 0, verified.stderr)
        accepted = self.cli("ack", hid, "--agent", "bob", "--accept", "--verification-id",
                            json.loads(verified.stdout)["verification_id"])
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(len(json.loads(self.cli("verifications", hid).stdout)), 1)
