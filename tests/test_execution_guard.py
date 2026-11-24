"""P1-2 · 服务端 per-plan 执行锁 + 请求级幂等的契约测试。

- 同一 plan_id 并发执行：一个跑、一个 409，底层 execute_plan 只调用一次；
- 同一 progress_key 重复提交：409，不产生第二份报告/事务日志；
- 幂等键按 64 条有界淘汰，不无限增长；
- 被拒请求不释放他人持有的锁（防串锁回归）。
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
from types import SimpleNamespace
from unittest import mock

from nesttidy import web


def _post(port: int, path: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


class _ServerMixin(unittest.TestCase):
    def setUp(self) -> None:
        web.PLANS.clear()
        web.EXECUTION_GUARD = web.ExecutionGuard()
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        (self.base / "srcA").mkdir()
        (self.base / "srcA" / "a.txt").write_text("a", encoding="utf-8")
        (self.base / "outA").mkdir()
        self._old_cwd = Path.cwd()
        os.chdir(self.base)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def _plan(self, output: bool = True) -> str:
        status, payload = _post(
            self.port, "/api/plan",
            {
                "selected_root": str(self.base / "srcA"),
                "mode": "batch",
                "output_root": str(self.base / "outA") if output else None,
            },
        )
        self.assertEqual(status, 200)
        return payload["plan_id"]


class ConcurrencyGuardTests(_ServerMixin):
    def test_same_plan_concurrent_execute_second_is_409(self) -> None:
        """同一 plan_id 两个并发请求：一个执行、一个 409；底层只跑一次。"""
        pid = self._plan()
        entered = threading.Event()
        release = threading.Event()
        calls: list = []

        def fake_execute(plan, mode, **kwargs):
            calls.append(1)
            entered.set()
            release.wait(timeout=10)
            return SimpleNamespace(completed=1, skipped=0, failed=0, issues=[], transaction_log="")

        outcomes: dict[str, tuple[int, dict]] = {}

        def first():
            outcomes["first"] = _post(self.port, "/api/execute-copy", {"plan_id": pid, "progress_key": "k-exec-1"})

        with mock.patch("nesttidy.web.execute_plan", side_effect=fake_execute):
            t = threading.Thread(target=first)
            t.start()
            self.assertTrue(entered.wait(timeout=10), "第一个请求未进入 execute_plan")
            # 此时第一个请求已 acquire plan 锁并卡住，第二个应被拒。
            outcomes["second"] = _post(self.port, "/api/execute-copy", {"plan_id": pid, "progress_key": "k-exec-2"})
            release.set()
            t.join(timeout=10)

        self.assertEqual(outcomes["first"][0], 200)
        self.assertEqual(outcomes["second"][0], 409, "同一 plan 并发执行未返回 409")
        self.assertIn("执行", outcomes["second"][1]["error"])
        self.assertEqual(len(calls), 1, "第二个请求重复执行了底层 execute_plan")

    def test_same_plan_concurrent_execute_move_second_is_409(self) -> None:
        """原地整理同样受 per-plan 锁保护，不能产生两份事务日志。"""
        pid = self._plan(output=False)
        entered = threading.Event()
        release = threading.Event()
        calls: list = []

        def fake_execute(plan, mode, **kwargs):
            calls.append(mode)
            entered.set()
            release.wait(timeout=10)
            return SimpleNamespace(completed=1, skipped=0, failed=0, issues=[], transaction_log="tx.jsonl")

        outcomes: dict[str, tuple[int, dict]] = {}

        def first():
            outcomes["first"] = _post(
                self.port, "/api/execute-move",
                {"plan_id": pid, "confirm": True, "progress_key": "k-move-1"},
            )

        with mock.patch("nesttidy.web.execute_plan", side_effect=fake_execute):
            t = threading.Thread(target=first)
            t.start()
            self.assertTrue(entered.wait(timeout=10), "第一个请求未进入 execute_plan")
            outcomes["second"] = _post(
                self.port, "/api/execute-move",
                {"plan_id": pid, "confirm": True, "progress_key": "k-move-2"},
            )
            release.set()
            t.join(timeout=10)

        self.assertEqual(outcomes["first"][0], 200)
        self.assertEqual(outcomes["second"][0], 409, "同一 plan 并发原地整理未返回 409")
        self.assertEqual(calls, ["move"], "第二个请求重复执行了底层 execute_plan")

    def test_rejected_request_does_not_release_others_lock(self) -> None:
        """被 409 拒绝的请求不得释放正在执行请求持有的锁。"""
        pid = self._plan()
        entered = threading.Event()
        release = threading.Event()

        def fake_execute(plan, mode, **kwargs):
            entered.set()
            release.wait(timeout=10)
            return SimpleNamespace(completed=1, skipped=0, failed=0, issues=[], transaction_log="")

        with mock.patch("nesttidy.web.execute_plan", side_effect=fake_execute):
            t = threading.Thread(
                target=lambda: _post(self.port, "/api/execute-copy", {"plan_id": pid, "progress_key": "k-hold"})
            )
            t.start()
            self.assertTrue(entered.wait(timeout=10))
            rejected = _post(self.port, "/api/execute-copy", {"plan_id": pid, "progress_key": "k-reject"})
            self.assertEqual(rejected[0], 409)
            # 第三个请求仍应被拒——证明第二个请求没有误释放锁。
            still_locked = _post(self.port, "/api/execute-copy", {"plan_id": pid, "progress_key": "k-probe"})
            self.assertEqual(still_locked[0], 409, "被拒请求错误释放了他人持有的锁")
            release.set()
            t.join(timeout=10)


class IdempotencyEndpointTests(_ServerMixin):
    def test_report_same_key_twice_generates_one_report(self) -> None:
        """端到端：同一 progress_key 重复提交报告，第二次 409 且只写一份报告。"""
        pid = self._plan()
        first = _post(self.port, "/api/report", {"plan_id": pid, "progress_key": "k-report-once"})
        self.assertEqual(first[0], 200)
        report_dir = Path(first[1]["report_dir"])
        self.assertTrue((report_dir / "summary.json").exists())

        before = {p.name for p in report_dir.parent.iterdir() if p.is_dir()}
        second = _post(self.port, "/api/report", {"plan_id": pid, "progress_key": "k-report-once"})
        self.assertEqual(second[0], 409, "同一 progress_key 重复提交应被拒")
        after = {p.name for p in report_dir.parent.iterdir() if p.is_dir()}
        self.assertEqual(before, after, "重复提交生成了第二份报告目录")

    def test_undo_same_key_twice_is_rejected(self) -> None:
        """撤销端点同样受 per-key 幂等保护，重复提交不再执行第二遍。"""
        folder = self.base / "待删除的空壳"
        folder.mkdir()
        log = self.base / "undo.jsonl"
        log.write_text(
            json.dumps({"action": "mkdir", "source": str(folder), "status": "done"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        first = _post(self.port, "/api/undo", {"transaction_log": str(log), "progress_key": "k-undo-once"})
        self.assertEqual(first[0], 200)
        self.assertFalse(folder.exists(), "第一次撤销没有执行")
        second = _post(self.port, "/api/undo", {"transaction_log": str(log), "progress_key": "k-undo-once"})
        self.assertEqual(second[0], 409, "同一 progress_key 重复撤销应被拒")


class IdempotencyTests(unittest.TestCase):
    def test_same_progress_key_repeated_is_rejected(self) -> None:
        guard = web.ExecutionGuard()
        self.assertIsNone(guard.acquire("", "key-once"), "首次提交应获得执行权")
        guard.release("", "key-once")
        # 完成态仍保留用于幂等：重复提交同一 key 应被拒。
        self.assertIn("重复", guard.acquire("", "key-once") or "")

    def test_inflight_eviction_prefers_completed_keys(self) -> None:
        guard = web.ExecutionGuard(max_keys=2)
        self.assertIsNone(guard.acquire("", "completed"))
        guard.release("", "completed")
        self.assertIsNone(guard.acquire("", "running"))
        # 新 key 应淘汰已完成的 key，而不是仍在运行的 key。
        self.assertIsNone(guard.acquire("", "new"))
        self.assertIn("running", guard._inflight)
        self.assertIn("new", guard._inflight)
        self.assertNotIn("completed", guard._inflight)
        self.assertLessEqual(len(guard._inflight), 2)

    def test_inflight_full_of_running_keys_rejects_new(self) -> None:
        guard = web.ExecutionGuard(max_keys=2)
        self.assertIsNone(guard.acquire("", "k0"))
        self.assertIsNone(guard.acquire("", "k1"))
        self.assertIn("过多", guard.acquire("", "k2") or "")
        self.assertNotIn("k2", guard._inflight)
        self.assertEqual(set(guard._inflight), {"k0", "k1"})

    def test_different_plans_do_not_block_each_other(self) -> None:
        guard = web.ExecutionGuard()
        self.assertIsNone(guard.acquire("plan-a", "key-a"))
        self.assertIsNone(guard.acquire("plan-b", "key-b"), "不同 plan 不应互相阻塞")
        guard.release("plan-a", "key-a")
        guard.release("plan-b", "key-b")



class PlanLifecycleTests(_ServerMixin):
    def test_finished_plan_rejects_new_progress_key(self) -> None:
        pid = self._plan()
        with mock.patch("nesttidy.web.execute_plan", return_value=SimpleNamespace(completed=1, skipped=0, failed=0, issues=[])) as run:
            first = _post(self.port, "/api/execute-copy", {"plan_id": pid, "progress_key": "first"})
            replay = _post(self.port, "/api/execute-copy", {"plan_id": pid, "progress_key": "second"})
        self.assertEqual(first[0], 200)
        self.assertEqual(replay[0], 409)
        self.assertEqual(run.call_count, 1)

    def test_failed_plan_cannot_be_replayed(self) -> None:
        pid = self._plan()
        with mock.patch("nesttidy.web.execute_plan", side_effect=OSError("执行中断")) as run:
            first = _post(self.port, "/api/execute-copy", {"plan_id": pid, "progress_key": "failed"})
            replay = _post(self.port, "/api/execute-copy", {"plan_id": pid, "progress_key": "retry"})
        self.assertEqual(first[0], 400)
        self.assertEqual(replay[0], 409)
        self.assertEqual(run.call_count, 1)

    def test_unconfirmed_move_does_not_consume_plan(self) -> None:
        pid = self._plan(output=False)
        with mock.patch("nesttidy.web.execute_plan", return_value=SimpleNamespace(completed=1, skipped=0, failed=0, issues=[], transaction_log="")) as run:
            first = _post(self.port, "/api/execute-move", {"plan_id": pid, "progress_key": "unconfirmed"})
            second = _post(self.port, "/api/execute-move", {"plan_id": pid, "confirm": True, "progress_key": "confirmed"})
        self.assertEqual(first[0], 400)
        self.assertEqual(second[0], 200)
        self.assertEqual(run.call_count, 1)

    def test_running_plan_is_not_evicted(self) -> None:
        registry = web.PlanRegistry(max_plans=1)
        plan = web.PLANS.get(self._plan())
        registry.put(plan)
        registry.claim(plan.plan_id)
        other = web.PLANS.get(self._plan())
        with self.assertRaises(web.PlanConflictError):
            registry.put(other)
        self.assertIs(registry.get(plan.plan_id), plan)
        registry.finish(plan.plan_id, True)
        registry.put(other)
        with self.assertRaises(ValueError):
            registry.get(plan.plan_id)


if __name__ == "__main__":
    unittest.main()
