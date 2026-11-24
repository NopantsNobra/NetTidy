"""v1.6.0 进度契约：字段统一、终态由后端写、六处等待环节埋点。

进度是旁路，不改变任何扫描/规划/执行语义；这里只锁定可观察的进度行为。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from nesttidy import web
from nesttidy.duplicate import collect_files
from nesttidy.planner import build_plan, snapshot_digest
from nesttidy.recovery import undo_transaction
from nesttidy.repair import iter_paths
from nesttidy.reports import write_plan_report
from nesttidy.rules import build_tree
from nesttidy.ui import INDEX_HTML

FIELDS = {"phase", "status", "done", "total", "label", "note", "updated_at"}


def _touch(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _ServerMixin(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = Path(tempfile.mkdtemp(prefix="nesttidy_progress_"))
        cls._old_cwd = Path.cwd()
        os.chdir(cls.base)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        os.chdir(cls._old_cwd)

    def _post(self, path: str, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def _get(self, path: str) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=20) as response:
            return json.load(response)


class ProgressSlotTests(unittest.TestCase):
    def test_track_contract_and_phase_override(self) -> None:
        key, report = web._track({"progress_key": "slot-contract"}, "计划")
        self.assertIsNotNone(report)

        slot = web._progress_slot(key)
        self.assertEqual(set(slot), FIELDS)
        self.assertEqual(slot["phase"], "计划")
        self.assertEqual(slot["status"], "running")
        self.assertEqual((slot["done"], slot["total"], slot["label"], slot["note"]), (0, 0, "正在准备", ""))
        self.assertTrue(slot["updated_at"])

        assert report is not None
        report(3, 10, "文件 A", "扫描")
        slot = web._progress_slot(key)
        self.assertEqual(slot["phase"], "扫描")
        self.assertEqual((slot["done"], slot["total"], slot["label"]), (3, 10, "文件 A"))

    def test_finish_keeps_last_frame_and_records_failure_note(self) -> None:
        key, report = web._track({"progress_key": "slot-failed"}, "执行")
        assert report is not None
        report(7, 0, "正在搬第 7 个", "执行")

        web._finish_progress(key, False, "磁盘空间不足")

        slot = web._progress_slot(key)
        self.assertEqual(slot["status"], "failed")
        self.assertEqual(slot["note"], "磁盘空间不足")
        self.assertEqual((slot["done"], slot["total"], slot["label"], slot["phase"]), (7, 0, "正在搬第 7 个", "执行"))
        web._finish_progress("slot-unknown", True)

    def test_missing_key_has_no_callback(self) -> None:
        key, report = web._track({})
        self.assertEqual(key, "")
        self.assertIsNone(report)

    def test_unknown_key_returns_the_same_field_set(self) -> None:
        self.assertEqual(set(web._progress_slot("never-used")), FIELDS)


    def test_throttled_unknown_total_flushes_exact_last_frame(self) -> None:
        from unittest.mock import patch
        with patch("nesttidy.web.monotonic", return_value=10.0):
            key, report = web._track({"progress_key": "throttle-unknown"}, "扫描")
            for n in range(1, 101):
                report(n, 0, str(n))
            self.assertEqual(web._progress_slot(key)["done"], 0)
            web._finish_progress(key, False, "中断")
            final = web._progress_slot(key)
            self.assertEqual((final["done"], final["label"], final["status"]), (100, "100", "failed"))
            self.assertEqual(set(final), FIELDS)
            report(101, 0)
            self.assertEqual(web._progress_slot(key), final)

    def test_stage_total_and_completion_publish_without_wait(self) -> None:
        from unittest.mock import patch
        with patch("nesttidy.web.monotonic", return_value=10.0):
            key, report = web._track({"progress_key": "throttle-stages"}, "扫描")
            report(9, 0)
            report(1, 0, step="计划")
            self.assertEqual((web._progress_slot(key)["phase"], web._progress_slot(key)["done"]), ("计划", 1))
            report(1, 10, step="计划")
            self.assertEqual(web._progress_slot(key)["total"], 10)
            report(10, 10, step="计划")
            self.assertEqual(web._progress_slot(key)["done"], 10)
            self.assertEqual(web._progress_slot(key)["status"], "running")
            web._finish_progress(key, True)
            self.assertEqual(web._progress_slot(key)["status"], "ok")

    def test_interval_and_keys_are_independent(self) -> None:
        from unittest.mock import patch
        with patch("nesttidy.web.monotonic", return_value=10.0) as clock:
            a, first = web._track({"progress_key": "throttle-a"})
            b, second = web._track({"progress_key": "throttle-b"})
            first(3, 0)
            second(7, 0)
            clock.return_value = 10.2
            first(4, 0)
            self.assertEqual(web._progress_slot(a)["done"], 4)
            self.assertEqual(web._progress_slot(b)["done"], 0)
            web._finish_progress(b, True)
            self.assertEqual(web._progress_slot(b)["done"], 7)
            web._finish_progress(a, True)


class ProgressConcurrencyTests(unittest.TestCase):
    """多标签页同时写/读/淘汰进度槽：不许 KeyError / RuntimeError，有界不泄漏。"""

    def setUp(self) -> None:
        with web._PROGRESS_LOCK:
            self._saved = dict(web.PROGRESS)
            web.PROGRESS.clear()

    def tearDown(self) -> None:
        with web._PROGRESS_LOCK:
            web.PROGRESS.clear()
            web.PROGRESS.update(self._saved)

    def test_concurrent_track_slot_and_trim_do_not_corrupt_state(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        def worker(i: int) -> None:
            for n in range(50):
                key, report = web._track({"progress_key": f"race-{i}-{n}"}, "执行")
                assert report is not None
                report(n, 100, "x")
                web._progress_slot(key)
                web._finish_progress(key, True)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(worker, range(8)))
        # 淘汰只发生在插入新键之前：槽最多临时到 65，绝不无界增长。
        self.assertLessEqual(len(web.PROGRESS), 65)

    def test_running_slot_survives_trim_and_finished_keys_go_first(self) -> None:
        """淘汰口径与 ExecutionGuard 幂等键一致：先清已完成键，不踩运行中的槽。

        运行中的槽必须先插入：插入顺序最靠前，FIFO 淘汰必然先踩它，
        只有「优先淘汰已完成键」的口径才能让它活下来。
        """
        run_key, _ = web._track({"progress_key": "trim-running"}, "执行")
        for n in range(64):
            key, report = web._track({"progress_key": f"trim-done-{n}"}, "执行")
            assert report is not None
            web._finish_progress(key, True)

        web._track({"progress_key": "trim-trigger"}, "执行")  # 插入新键，触发一次淘汰

        self.assertIn(run_key, web.PROGRESS, "运行中的进度槽被淘汰")
        self.assertNotIn("trim-done-0", web.PROGRESS, "淘汰没有先清已完成键")
        self.assertLessEqual(len(web.PROGRESS), 65)


class InstrumentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "素材"
        _touch(self.root / "包A" / "壳" / "a.txt", "aaa")
        _touch(self.root / "包A" / "壳" / "b.txt", "bbb")

    @staticmethod
    def _collect():
        done: list[int] = []
        totals: list[int] = []
        phases: list[str] = []

        def report(done_value: int, total: int, label: str = "", phase: str | None = None) -> None:
            done.append(done_value)
            totals.append(total)
            phases.append(phase or "")

        return report, done, totals, phases

    def test_build_tree_reports_scan_phase(self) -> None:
        report, done, totals, phases = self._collect()
        tree, issues = build_tree(self.root, lambda path: None, progress=report)
        self.assertTrue(phases)
        self.assertEqual(set(phases), {"扫描"})
        self.assertEqual(set(totals), {0})
        self.assertEqual(done, list(range(1, len(done) + 1)))
        self.assertTrue(tree.dirs)
        self.assertEqual(issues, [])

    def test_snapshot_digest_reports_plan_phase(self) -> None:
        report, done, totals, phases = self._collect()
        digest = snapshot_digest([self.root], progress=report)
        self.assertTrue(phases)
        self.assertEqual(set(phases), {"计划"})
        self.assertEqual(set(totals), {0})
        self.assertEqual(len(digest), 64)

    def test_collect_files_reports_dedupe_phase(self) -> None:
        report, done, totals, phases = self._collect()
        files = collect_files(self.root, progress=report)
        self.assertEqual(len(files), 2)
        self.assertEqual(set(phases), {"去重"})
        self.assertEqual(set(totals), {0})

    def test_iter_paths_reports_scan_phase(self) -> None:
        report, done, totals, phases = self._collect()
        paths = list(iter_paths(self.root, progress=report))
        self.assertTrue(paths)
        self.assertEqual(set(phases), {"扫描"})
        self.assertEqual(set(totals), {0})

    def test_undo_transaction_reports_undo_phase(self) -> None:
        folder = self.root / "待删除的空壳"
        folder.mkdir(parents=True)
        log = Path(self._tmp.name) / "事务日志.jsonl"
        log.write_text(json.dumps({"action": "mkdir", "source": str(folder), "status": "done"}, ensure_ascii=False) + "\n", encoding="utf-8")
        report, done, totals, phases = self._collect()

        rows = undo_transaction(str(log), progress=report)

        self.assertEqual([row.status for row in rows], ["ok"])
        self.assertEqual(set(phases), {"撤销"})
        self.assertEqual(done, [1])
        self.assertEqual(totals, [1])

    def test_write_plan_report_reports_report_phase(self) -> None:
        plan = build_plan(str(self.root), mode="batch")
        report, done, totals, phases = self._collect()

        out = write_plan_report(plan, report_dir=Path(self._tmp.name) / "理巢报告", progress=report)

        self.assertTrue((out / "summary.json").exists())
        self.assertEqual(set(phases), {"报告"})
        self.assertEqual(done[-1], totals[-1])
        self.assertEqual(len(set(totals)), 1)


class EndpointProgressTests(_ServerMixin):
    def _make_root(self, name: str) -> Path:
        root = self.base / name
        _touch(root / "包A" / "壳" / "a.txt", "aaa")
        _touch(root / "包A" / "壳" / "b.txt", "bbb")
        return root

    def test_plan_endpoint_writes_ok_terminal(self) -> None:
        root = self._make_root("计划源")
        status, payload = self._post("/api/plan", {"selected_root": str(root), "mode": "batch", "progress_key": "ep-plan"})
        self.assertEqual(status, 200)
        self.assertIn("plan_id", payload)

        slot = self._get("/api/progress?key=ep-plan")
        self.assertEqual(slot["status"], "ok")
        self.assertEqual(slot["phase"], "计划")
        self.assertEqual(slot["note"], "")
        self.assertGreater(slot["done"], 0)

    def test_failed_endpoint_writes_failed_terminal_with_note(self) -> None:
        status, payload = self._post("/api/report", {"plan_id": "no-such-plan", "progress_key": "ep-failed"})
        self.assertEqual(status, 400)
        self.assertIn("计划不存在", payload["error"])

        slot = self._get("/api/progress?key=ep-failed")
        self.assertEqual(slot["status"], "failed")
        self.assertEqual(slot["phase"], "报告")
        self.assertIn("计划不存在", slot["note"])

    def test_diagnose_and_repair_endpoints_carry_their_phases(self) -> None:
        root = self._make_root("诊断源")
        status, _ = self._post("/api/duplicates", {"selected_root": str(root), "level": "fast", "progress_key": "ep-dup"})
        self.assertEqual(status, 200)
        self.assertEqual(self._get("/api/progress?key=ep-dup")["phase"], "去重")
        self.assertEqual(self._get("/api/progress?key=ep-dup")["status"], "ok")

        status, _ = self._post("/api/repair", {"selected_root": str(root), "clear_readonly": True, "progress_key": "ep-repair"})
        self.assertEqual(status, 200)
        self.assertEqual(self._get("/api/progress?key=ep-repair")["phase"], "修复")
        self.assertEqual(self._get("/api/progress?key=ep-repair")["status"], "ok")

    def test_report_endpoint_reports_before_success(self) -> None:
        root = self._make_root("报告源")
        _, plan_payload = self._post("/api/plan", {"selected_root": str(root), "mode": "batch"})
        status, _ = self._post("/api/report", {"plan_id": plan_payload["plan_id"], "progress_key": "ep-report"})
        self.assertEqual(status, 200)
        slot = self._get("/api/progress?key=ep-report")
        self.assertEqual(slot["phase"], "报告")
        self.assertEqual(slot["status"], "ok")
        self.assertEqual(slot["total"], 5)


class PageProgressContractTests(unittest.TestCase):
    def test_page_renders_backend_phase_and_terminal(self) -> None:
        for marker in (
            "PHASE_LABEL", "renderProgress", "d.status === 'ok'", "d.status === 'failed'",
            "请勿重复操作", "上一步还在执行，请勿重复操作。", "indeterminate", "d.note", 'id="undoFill"',
            "prog.fail()", "transportFailed", "请求未完成",
        ):
            self.assertIn(marker, INDEX_HTML)

    def test_stop_does_not_decide_success_on_the_client(self) -> None:
        self.assertNotIn("stop: (ok)", INDEX_HTML)
        self.assertIn("stop: () => { clearInterval(timer); if(!transportFailed) tick(); }", INDEX_HTML)
        for marker in ("/api/report',{plan_id:currentPlanId,progress_key:prog.key}",
                       "/api/undo',{transaction_log:logPath,progress_key:prog.key}"):
            self.assertIn(marker, INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
