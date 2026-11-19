"""最小本地配置（路线图 P1：仅目标路径与端口）的单元与接缝测试。

覆盖：
1. 文件不存在回落默认；读取时去引号、忽略未知键；
2. JSON 损坏 / 非对象 / 字段类型错 / 端口越界一律显式报错（不静默回落）；
3. 保存-读取回环（含中文路径）；remember_selected_root 只改源目录、保留端口、拒绝空路径、并发安全；
4. GET /api/config 预填契约；试运行/诊断成功后把解析后的源目录写回配置；
5. 启动端口优先级：--port > 理巢配置.json > 默认 8766；配置损坏时启动显式失败。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from nesttidy import app, web
from nesttidy.config import (
    CONFIG_FILE_NAME,
    DEFAULT_PORT,
    LocalConfig,
    config_path,
    load_local_config,
    remember_selected_root,
    save_local_config,
)


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


def _get(port: int, path: str) -> tuple[int, dict]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


class LocalConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, text: str) -> Path:
        path = config_path(self.base)
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_file_returns_defaults(self):
        config = load_local_config(self.base)
        self.assertEqual(config, LocalConfig(selected_root="", port=DEFAULT_PORT))

    def test_roundtrip_keeps_chinese_root(self):
        save_local_config(LocalConfig(selected_root=str(self.base / "下载整理"), port=9100), self.base)
        config = load_local_config(self.base)
        self.assertEqual(config.selected_root, str(self.base / "下载整理"))
        self.assertEqual(config.port, 9100)

    def test_load_cleans_quotes_and_spaces(self):
        self._write(json.dumps({"selected_root": '  "D:\\下载\\资源包" ', "port": 9001}, ensure_ascii=False))
        config = load_local_config(self.base)
        self.assertEqual(config.selected_root, "D:\\下载\\资源包")
        self.assertEqual(config.port, 9001)

    def test_unknown_keys_are_ignored(self):
        self._write(json.dumps({"selected_root": "D:\\下载", "port": 9002, "exclude": "*.tmp", "rule": "hack"}))
        config = load_local_config(self.base)
        self.assertEqual((config.selected_root, config.port), ("D:\\下载", 9002))

    def test_broken_json_raises_with_path(self):
        path = self._write("{oops")
        with self.assertRaises(ValueError) as ctx:
            load_local_config(self.base)
        self.assertIn(str(path), str(ctx.exception))

    def test_non_object_json_rejected(self):
        self._write("[]")
        with self.assertRaises(ValueError):
            load_local_config(self.base)

    def test_field_type_errors_rejected(self):
        cases = [
            {"selected_root": 123},
            {"port": "8766"},
            {"port": True},
            {"port": 0},
            {"port": 65536},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                self._write(json.dumps(payload))
                with self.assertRaises(ValueError):
                    load_local_config(self.base)

    def test_port_boundaries_accepted(self):
        for port in (1, 65535):
            with self.subTest(port=port):
                self._write(json.dumps({"port": port}))
                self.assertEqual(load_local_config(self.base).port, port)

    def test_remember_updates_root_and_keeps_port(self):
        save_local_config(LocalConfig(selected_root="D:\\旧的", port=9555), self.base)
        path = remember_selected_root("D:\\新的", self.base)
        self.assertEqual(path, config_path(self.base))
        config = load_local_config(self.base)
        self.assertEqual((config.selected_root, config.port), ("D:\\新的", 9555))

    def test_remember_blank_root_rejected(self):
        with self.assertRaises(ValueError):
            remember_selected_root("   ", self.base)

    def test_remember_is_safe_under_concurrency(self):
        threads = [
            threading.Thread(target=remember_selected_root, args=(f"D:\\下载\\{index}", self.base))
            for index in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        roots = {load_local_config(self.base).selected_root for _ in range(4)}
        self.assertEqual(len(roots), 1)
        self.assertRegex(roots.pop(), r"D:\\下载\\\d")


class ConfigEndpointTests(unittest.TestCase):
    """/api/config 预填契约与「成功操作后记住源目录」的接缝。"""

    def setUp(self) -> None:
        web.PLANS.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        (self.base / "资源包").mkdir()
        (self.base / "资源包" / "a.txt").write_text("same", encoding="utf-8")
        (self.base / "资源包" / "b.txt").write_text("same", encoding="utf-8")
        self._old_cwd = Path.cwd()
        os.chdir(self.base)
        self.server = web.create_server("127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def test_config_endpoint_empty_without_file(self):
        status, data = _get(self.port, "/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(data["selected_root"], "")

    def test_plan_success_persists_resolved_root(self):
        status, _ = _post(self.port, "/api/plan", {"selected_root": str(self.base / "资源包"), "mode": "batch"})
        self.assertEqual(status, 200)
        status, data = _get(self.port, "/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(data["selected_root"], str(self.base / "资源包"))
        on_disk = json.loads(config_path(self.base).read_text(encoding="utf-8"))
        self.assertEqual(on_disk["port"], DEFAULT_PORT)

    def test_duplicates_success_persists_root(self):
        status, _ = _post(self.port, "/api/duplicates", {"selected_root": str(self.base / "资源包"), "level": "fast"})
        self.assertEqual(status, 200)
        config = load_local_config(self.base)
        self.assertEqual(config.selected_root, str(self.base / "资源包"))

    def test_config_endpoint_reports_broken_file(self):
        config_path(self.base).write_text("{oops", encoding="utf-8")
        status, data = _get(self.port, "/api/config")
        self.assertEqual(status, 400)
        self.assertEqual(data["selected_root"], "")
        self.assertIn("配置文件", data["error"])

    def test_ui_prefills_from_config(self):
        self.assertIn("/api/config", web.INDEX_HTML)
        self.assertIn("已从本地配置载入上次的源目录", web.INDEX_HTML)


class StartupPortTests(unittest.TestCase):
    """端口优先级 --port > 理巢配置.json > 默认；配置损坏时启动显式失败。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self._old_cwd = Path.cwd()
        os.chdir(self.base)

    def tearDown(self) -> None:
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def _run_main(self, argv: list[str]):
        with mock.patch("nesttidy.app.serve") as serve_mock, mock.patch("sys.argv", ["nesttidy"] + argv):
            app.main()
        return serve_mock.call_args

    def test_port_from_config_file(self):
        save_local_config(LocalConfig(selected_root="", port=9123), self.base)
        call = self._run_main([])
        self.assertEqual(call.args, ("127.0.0.1", 9123))

    def test_cli_port_overrides_config(self):
        save_local_config(LocalConfig(selected_root="", port=9123), self.base)
        call = self._run_main(["--port", "7000"])
        self.assertEqual(call.args, ("127.0.0.1", 7000))

    def test_default_port_without_any_source(self):
        call = self._run_main([])
        self.assertEqual(call.args, ("127.0.0.1", DEFAULT_PORT))

    def test_broken_config_blocks_startup(self):
        config_path(self.base).write_text("{oops", encoding="utf-8")
        with self.assertRaises(SystemExit) as ctx:
            self._run_main([])
        self.assertIn(CONFIG_FILE_NAME, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
