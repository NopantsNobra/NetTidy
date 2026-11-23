"""本地 Web 的请求来源防线：Host 白名单 + Origin 校验 + 只收 JSON。

理巢绑 127.0.0.1 不监听外网，但浏览器里任意网页都能向 127.0.0.1 发请求：

- DNS 重绑定：恶意域名解析到 127.0.0.1，页面就变成「同源」，能读响应——Host 校验拦住；
- 跨站伪造：恶意页面向本机接口 POST 执行 / 删除 / 撤销。去重删除的底线是
  「逐个点选 + 二次确认」，被网页代点就破了这条底线——Origin 校验 + 只收
  application/json（表单 / text/plain 是免预检的「简单请求」）拦住。

无 Origin 的请求（curl、命令行脚本）照常放行；页面自身一直是同源 fetch + JSON。
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

from nesttidy.web import create_server


class RequestSourceGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = Path(tempfile.mkdtemp(prefix="nesttidy_guard_"))
        cls._old_cwd = Path.cwd()
        os.chdir(cls.base)
        cls.server: ThreadingHTTPServer = create_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        os.chdir(cls._old_cwd)

    def _request(self, path: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
        payload = {"Content-Type": "application/json"}
        if headers:
            payload.update(headers)
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers=payload,
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_same_origin_json_request_passes(self) -> None:
        """防线不得误伤正常使用（页面同源、无 Origin 的本机请求）。"""
        status, data = self._request("/api/validate-root", {"path": str(self.base)})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_foreign_origin_is_rejected(self) -> None:
        status, data = self._request(
            "/api/duplicates/delete",
            {"selected_root": str(self.base), "mode": "purge", "confirm": True, "entries": [{"path": "x", "keep_path": "y"}]},
            headers={"Origin": "http://evil.example.com"},
        )
        self.assertEqual(status, 403)
        self.assertIn("跨站", data["error"])

    def test_null_origin_is_rejected(self) -> None:
        status, _ = self._request("/api/plan", {"selected_root": str(self.base)}, headers={"Origin": "null"})
        self.assertEqual(status, 403)

    def test_foreign_host_is_rejected(self) -> None:
        """DNS 重绑定形态：请求真到了 127.0.0.1，但 Host 是攻击者域名。"""
        status, data = self._request("/api/health", None, headers={"Host": "evil.example.com"})
        self.assertEqual(status, 403)
        self.assertIn("Host", data["error"])

    def test_foreign_host_on_get_index_is_rejected(self) -> None:
        status, _ = self._request("/", None, headers={"Host": "rebind.attacker"})
        self.assertEqual(status, 403)

    def test_origin_with_wrong_port_is_rejected(self) -> None:
        status, _ = self._request(
            "/api/validate-root",
            {"path": str(self.base)},
            headers={"Origin": f"http://127.0.0.1:{(self.port + 1) % 65535 or 1}"},
        )
        self.assertEqual(status, 403)

    def test_non_json_content_type_is_rejected(self) -> None:
        """text/plain 是免预检的简单请求，必须卡在 Content-Type 上。"""
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/validate-root",
            data=json.dumps({"path": str(self.base)}).encode("utf-8"),
            headers={"Content-Type": "text/plain"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=15)
        self.assertEqual(ctx.exception.code, 415)

    def test_localhost_host_is_accepted(self) -> None:
        status, data = self._request("/api/validate-root", {"path": str(self.base)}, headers={"Host": f"localhost:{self.port}"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])


if __name__ == "__main__":
    unittest.main()
