from __future__ import annotations

import errno
import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from nesttidy import web
from nesttidy.planner import build_plan


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


def _make_run_root(base: Path, tag: str) -> Path:
    resource_root = base / f"src{tag}" / f"R{tag}"
    (resource_root / f"shell{tag}").mkdir(parents=True, exist_ok=True)
    (resource_root / f"shell{tag}" / f"{tag.lower()}.txt").write_text(tag, encoding="utf-8")
    return base / f"src{tag}"


def _plan(run_root: Path):
    return build_plan(str(run_root), "batch", str(run_root.parent / f"out_{run_root.name}"))


class PlanRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        web.PLANS.clear()

    def test_two_plans_are_retrieved_by_their_own_id(self) -> None:
        """两个标签页各生成一次计划，各自按 plan_id 取回，不得互相覆盖。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            plan_a = _plan(_make_run_root(base, "A"))
            plan_b = _plan(_make_run_root(base, "B"))
            id_a = web.PLANS.put(plan_a)
            id_b = web.PLANS.put(plan_b)

            self.assertNotEqual(id_a, id_b)
            self.assertIs(web.PLANS.get(id_a), plan_a)
            self.assertIs(web.PLANS.get(id_b), plan_b)

    def test_unknown_plan_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            web.PLANS.get("not-a-real-plan-id")

    def test_registry_evicts_oldest_plan(self) -> None:
        """内存缓存必须有上限，否则长期运行的本地服务会无限累积计划。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            run_root = _make_run_root(base, "A")
            registry = web.PlanRegistry(max_plans=2)
            ids = [registry.put(_plan(run_root)) for _ in range(3)]
            with self.assertRaises(ValueError):
                registry.get(ids[0])
            self.assertIsNotNone(registry.get(ids[1]))
            self.assertIsNotNone(registry.get(ids[2]))


    def test_indexed_pages_match_legacy_and_reflect_live_status(self) -> None:
        from unittest.mock import patch
        from nesttidy.reports import readable_group_rows
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "src"
            for group in ("A", "B"):
                for n in range(7):
                    p = root / group / "wrapper" / f"{n}.txt"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(group, encoding="utf-8")
            plan = build_plan(str(root), "batch")
            registry = web.PlanRegistry(max_plans=1)
            registry.put(plan)
            with patch("nesttidy.web.build_mapping_index", wraps=web.build_mapping_index) as build:
                for group in [*plan.resource_roots, "", "missing"]:
                    for offset, limit in [(0, 3), (3, 3), (6, 5), (20, 2), (-2, 4), (0, 0)]:
                        self.assertEqual(registry.page(plan.plan_id, group, offset, limit),
                                         readable_group_rows(plan, group, offset, limit))
                plan.mappings[0].status = "completed"
                for group in plan.resource_roots:
                    self.assertEqual(registry.page(plan.plan_id, group, 0, 20),
                                     readable_group_rows(plan, group, 0, 20))
                self.assertEqual(build.call_count, 1)
            other = build_plan(str(root), "batch")
            registry.put(other)
            self.assertNotIn(plan.plan_id, registry._pages)
            with self.assertRaises(ValueError):
                registry.page(plan.plan_id, plan.resource_roots[0], 0, 10)

    def test_parallel_pages_build_index_once(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            plan = _plan(_make_run_root(base, "A"))
            registry = web.PlanRegistry()
            registry.put(plan)
            with patch("nesttidy.web.build_mapping_index", wraps=web.build_mapping_index) as build:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    rows = list(pool.map(lambda _: registry.page(plan.plan_id, plan.resource_roots[0], 0, 1), range(8)))
                self.assertEqual(build.call_count, 1)
                self.assertTrue(all(row == rows[0] for row in rows))
            registry.clear()
            self.assertFalse(registry._pages)


class PortBindingTests(unittest.TestCase):
    def test_occupied_port_is_classified_with_port_number(self) -> None:
        """真实占用一个临时端口，再让服务绑定它：必须得到可判定的占用错误。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            port = int(blocker.getsockname()[1])
            with self.assertRaises(web.PortInUseError) as ctx:
                web.create_server("127.0.0.1", port)
        self.assertEqual(ctx.exception.port, port)
        self.assertIn(str(port), str(ctx.exception))
        self.assertIsInstance(ctx.exception.cause, OSError)

    def test_reuse_occupied_port_is_classified_without_silent_coexistence(self) -> None:
        """占用方也设 SO_REUSEADDR：服务必须报错，不得静默绑到同一端口。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            port = int(blocker.getsockname()[1])
            with self.assertRaises(web.PortInUseError) as ctx:
                web.create_server("127.0.0.1", port)
        self.assertEqual(ctx.exception.port, port)
        self.assertIn(str(port), str(ctx.exception))

    def test_non_local_host_is_one_line_error_not_traceback(self) -> None:
        """--host 非 127.0.0.1：拒绝语义不变，但报错形态必须是一行 SystemExit，不是 traceback。"""
        import sys
        import tempfile
        from unittest import mock

        from nesttidy import app

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sys, "argv", ["nesttidy", "--host", "0.0.0.0"]):
            old_cwd = os.getcwd()
            os.chdir(tmp)  # 隔离 理巢配置.json，保证 load_local_config 走默认值
            try:
                with self.assertRaises(SystemExit) as ctx:
                    app.main()
            finally:
                os.chdir(old_cwd)
        self.assertIn("理巢启动失败", str(ctx.exception))
        self.assertIn("127.0.0.1", str(ctx.exception))

    def test_unroutable_bind_error_is_not_classified_as_port_in_use(self) -> None:
        """203.0.113.1 本机实测 winerror=10049，属非占用 bind 错误，必须原样抛。"""
        with self.assertRaises(OSError) as ctx:
            web.create_server("203.0.113.1", 0)
        self.assertNotIsInstance(ctx.exception, web.PortInUseError)
        self.assertNotIn(getattr(ctx.exception, "winerror", None), (10048, 10013))

    def test_unrelated_oserror_is_not_classified_as_port_in_use(self) -> None:
        """getaddrinfo 失败是真实的 OSError，但不属于占用，必须原样抛。"""
        with self.assertRaises(OSError) as ctx:
            web.create_server("999.999.999.999", 0)
        self.assertNotIsInstance(ctx.exception, web.PortInUseError)
        self.assertNotIn(ctx.exception.errno, (errno.EADDRINUSE, 10048, 10013))

    def test_launcher_distinguishes_port_in_use_from_other_failures(self) -> None:
        """bat 必须对退出码 23 单独提示端口占用，并保留原 Python 安装提示。"""
        from nesttidy.app import EXIT_PORT_IN_USE

        bat = (Path(__file__).resolve().parents[1] / "start_nesttidy.bat").read_text(encoding="utf-8")
        self.assertEqual(EXIT_PORT_IN_USE, 23)
        self.assertIn(f'"{EXIT_PORT_IN_USE}"', bat)
        self.assertIn("已被占用", bat)
        self.assertIn("Python 3.11", bat)


class WebEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        web.PLANS.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        _make_run_root(self.base, "A")
        _make_run_root(self.base, "B")
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

    def _files(self, name: str) -> list[str]:
        folder = self.base / name
        return sorted(p.name for p in folder.rglob("*") if p.is_file()) if folder.exists() else []

    def test_two_tabs_execute_their_own_plan(self) -> None:
        """端到端：A/B 两个预览，分别执行时各写各的输出目录，不串台。"""
        status_a, plan_a = _post(self.port, "/api/plan", {"selected_root": str(self.base / "srcA"), "mode": "batch", "output_root": str(self.base / "outA")})
        status_b, plan_b = _post(self.port, "/api/plan", {"selected_root": str(self.base / "srcB"), "mode": "batch", "output_root": str(self.base / "outB")})
        self.assertEqual((status_a, status_b), (200, 200))
        self.assertNotEqual(plan_a["plan_id"], plan_b["plan_id"])

        status_1, res_1 = _post(self.port, "/api/execute-copy", {"plan_id": plan_a["plan_id"]})
        status_2, res_2 = _post(self.port, "/api/execute-copy", {"plan_id": plan_b["plan_id"]})
        self.assertEqual((status_1, status_2), (200, 200))
        self.assertEqual((res_1["completed"], res_1["failed"]), (1, 0))
        self.assertEqual((res_2["completed"], res_2["failed"]), (1, 0))
        self.assertEqual(self._files("outA"), ["a.txt"])
        self.assertEqual(self._files("outB"), ["b.txt"])

    def test_execute_without_plan_id_is_rejected(self) -> None:
        _post(self.port, "/api/plan", {"selected_root": str(self.base / "srcA"), "mode": "batch", "output_root": str(self.base / "outA")})
        status, body = _post(self.port, "/api/execute-copy", {})
        self.assertEqual(status, 400)
        self.assertIn("plan_id", body["error"])
        self.assertEqual(self._files("outA"), [])

    def test_report_uses_the_requested_plan(self) -> None:
        status_a, plan_a = _post(self.port, "/api/plan", {"selected_root": str(self.base / "srcA"), "mode": "batch"})
        status_b, plan_b = _post(self.port, "/api/plan", {"selected_root": str(self.base / "srcB"), "mode": "batch"})
        self.assertEqual((status_a, status_b), (200, 200))
        self.assertNotEqual(plan_a["plan_id"], plan_b["plan_id"])
        status, body = _post(self.port, "/api/report", {"plan_id": plan_a["plan_id"]})
        self.assertEqual(status, 200)
        summary = json.loads((Path(body["report_dir"]) / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["plan_id"], plan_a["plan_id"])
        self.assertEqual(summary["selected_root"], str(self.base / "srcA"))

    def test_plan_response_carries_display_stats(self) -> None:
        """预览页的规则计数/R5 样例/边界检查三块内容完全依赖 /api/plan 的 display 载荷，接缝丢失时页面直接失效。"""
        resource = self.base / "srcC" / "R"
        for rel in ("分叉一/cover.jpg", "分叉二/cover.jpg", "合集/外壳/内层/deep.txt", "说明.txt"):
            target = resource / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x", encoding="utf-8")
        status, body = _post(self.port, "/api/plan", {"selected_root": str(self.base / "srcC"), "mode": "batch"})
        self.assertEqual(status, 200)
        display = body["display"]
        self.assertEqual([row["rule"] for row in display["rule_rows"]], ["R0", "R1", "R2", "R3", "R4", "R5"])
        counts = {row["rule"]: row["count"] for row in display["rule_rows"]}
        self.assertEqual((counts["R0"], counts["R2"], counts["R4"], counts["R5"]), (5, 1, 3, 2))
        samples = display["r5_samples"]
        self.assertEqual({sample["original_name"] for sample in samples}, {"cover.jpg"})
        self.assertEqual({sample["final_name"] for sample in samples}, {"分叉一-cover.jpg", "分叉二-cover.jpg"})
        boundary = display["boundary"]
        self.assertEqual((boundary["checked"], boundary["out_of_bound"]), (4, 0))


if __name__ == "__main__":
    unittest.main()
