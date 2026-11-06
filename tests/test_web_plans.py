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


if __name__ == "__main__":
    unittest.main()
