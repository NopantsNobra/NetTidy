"""v1.4.0 界面修订的后端契约测试。

覆盖三件事：
1. 空/空白源目录在所有需要源目录的接口上必须显式报错
   （历史 bug：Path('').resolve() 落到进程 CWD，空路径曾被静默当成
   「扫描理巢程序自己的目录」并生成可执行计划）；
2. repair 与 duplicates/delete 要求目录真实存在；
3. repair 未勾选任何修复动作时拒绝空转（否则全树遍历后谎报「全部正常」）；
4. /api/validate-root 失焦预检：存在性、文件/目录区分、相对路径、去引号。
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
from nesttidy.web import create_server


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
    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix="nesttidy_v14_"))
        # 试运行成功会把「上次的源目录」写进启动目录的 理巢配置.json，测试必须与真实启动一样落在临时目录里。
        cls._old_cwd = Path.cwd()
        os.chdir(cls.base)
        cls.server: ThreadingHTTPServer = create_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        os.chdir(cls._old_cwd)

    def _mkdir(self, name: str) -> str:
        path = self.base / name
        path.mkdir()
        return str(path)


class EmptyRootRejectionTests(_ServerMixin):
    """空/空白 selected_root 不得静默落到进程 CWD（v1.4.0 修复）。"""

    def test_plan_rejects_empty_root(self):
        for body in ({"selected_root": "", "mode": "batch"}, {"selected_root": "   ", "mode": "batch"}, {"mode": "batch"}):
            with self.subTest(body=body):
                status, data = _post(self.port, "/api/plan", body)
                self.assertEqual(status, 400)
                self.assertIn("源目录", data["error"])

    def test_duplicates_rejects_empty_root(self):
        status, data = _post(self.port, "/api/duplicates", {"selected_root": "", "level": "standard"})
        self.assertEqual(status, 400)
        self.assertIn("源目录", data["error"])

    def test_repair_rejects_empty_root(self):
        status, data = _post(
            self.port,
            "/api/repair",
            {"selected_root": " ", "clear_readonly": True},
        )
        self.assertEqual(status, 400)
        self.assertIn("源目录", data["error"])

    def test_duplicates_delete_rejects_empty_root(self):
        status, data = _post(
            self.port,
            "/api/duplicates/delete",
            {"selected_root": "", "mode": "recycle", "confirm": True, "entries": []},
        )
        self.assertEqual(status, 400)
        self.assertIn("源目录", data["error"])

    def test_plan_still_works_with_real_root(self):
        root = self._mkdir("real_root")
        status, data = _post(self.port, "/api/plan", {"selected_root": root, "mode": "batch"})
        self.assertEqual(status, 200)
        self.assertEqual(data["selected_root"], str(Path(root).resolve()))


class RootExistenceTests(_ServerMixin):
    """repair / duplicates / duplicates/delete 要求目录真实存在。"""

    def test_repair_rejects_missing_root(self):
        status, data = _post(
            self.port,
            "/api/repair",
            {"selected_root": str(self.base / "no_such_dir"), "clear_readonly": True},
        )
        self.assertEqual(status, 400)
        self.assertIn("不存在", data["error"])

    def test_duplicates_rejects_missing_root(self):
        status, data = _post(
            self.port,
            "/api/duplicates",
            {"selected_root": str(self.base / "no_such_dir"), "level": "standard"},
        )
        self.assertEqual(status, 400)
        self.assertIn("不存在", data["error"])


class RepairNoopGuardTests(_ServerMixin):
    """不勾任何修复动作时必须拒绝，不得全树遍历后谎报「全部正常」。"""

    def test_repair_rejects_no_action_selected(self):
        root = self._mkdir("repair_noop")
        status, data = _post(self.port, "/api/repair", {"selected_root": root})
        self.assertEqual(status, 400)
        self.assertIn("至少一项", data["error"])


class RepairActionForwardingTests(_ServerMixin):
    """校验放行的五个修复动作都必须真正执行：旧版丢弃后两项，勾了静默不生效。"""

    def test_repair_forwards_grant_and_inheritance_flags(self):
        from unittest import mock

        root = self._mkdir("repair_flags")
        seen: dict = {}

        def fake_repair(selected_root, **kwargs):
            seen.update(kwargs)
            return []

        with mock.patch("nesttidy.web.repair_permissions", side_effect=fake_repair):
            status, _ = _post(
                self.port,
                "/api/repair",
                {"selected_root": root, "grant_current_user": True, "fix_inheritance": True},
            )
        self.assertEqual(status, 200)
        self.assertTrue(seen.get("grant_current_user"), "grant_current_user 被校验放行后丢弃")
        self.assertTrue(seen.get("fix_inheritance"), "fix_inheritance 被校验放行后丢弃")


class ValidateRootTests(_ServerMixin):
    """/api/validate-root：输入框失焦预检的契约。"""

    def test_validate_ok(self):
        status, data = _post(self.port, "/api/validate-root", {"path": str(self.base)})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["resolved"], str(self.base.resolve()))

    def test_validate_strips_quotes(self):
        status, data = _post(self.port, "/api/validate-root", {"path": '"' + str(self.base) + '"'})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_validate_missing_dir(self):
        status, data = _post(self.port, "/api/validate-root", {"path": str(self.base / "nope")})
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "目录不存在")

    def test_validate_file_not_dir(self):
        file_path = self.base / "afile.txt"
        file_path.write_text("x", encoding="utf-8")
        status, data = _post(self.port, "/api/validate-root", {"path": str(file_path)})
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("文件", data["error"])

    def test_validate_relative_path(self):
        status, data = _post(self.port, "/api/validate-root", {"path": "relative/path"})
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("完整路径", data["error"])

    def test_validate_empty_path(self):
        status, data = _post(self.port, "/api/validate-root", {"path": "  "})
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "路径为空")


class UiShellContractTests(unittest.TestCase):
    """前端外壳的结构性契约：全局状态条必须放在所有面板之外。"""

    def setUp(self):
        from nesttidy.ui import INDEX_HTML

        self.html = INDEX_HTML

    def test_global_msg_bar_outside_any_panel(self):
        """#msgbar 必须出现在第一个 .panel 之前（否则其他页签看不见错误）。"""
        msgbar = self.html.index('id="msgbar"')
        first_panel = self.html.index('class="panel"')
        self.assertLess(msgbar, first_panel)

    def test_no_confirm_or_alert_left(self):
        """原生 confirm/alert 已全部替换为页内模态/全局状态条。"""
        for banned in ("confirm(", "alert("):
            self.assertNotIn(banned, self.html)

    def test_modal_and_busy_guard_present(self):
        self.assertIn("showModal", self.html)
        self.assertIn("function guard(", self.html)
        self.assertIn("planExecuted", self.html)
        self.assertIn("invalidatePlan", self.html)

    def test_responsive_breakpoints_present(self):
        self.assertIn("@media (max-width:900px)", self.html)
        self.assertIn("@media (max-width:700px)", self.html)

    def test_tabs_are_keyboard_accessible(self):
        self.assertIn('role="tab"', self.html)
        self.assertIn("ArrowRight", self.html)
        # 容器 role 由脚本补挂（保持 HTML 上 tabs 容器字符串的历史形态不变）
        self.assertIn("setAttribute('role','tablist')", self.html)

    def test_validate_root_endpoint_wired(self):
        self.assertIn("/api/validate-root", self.html)

    def test_short_select_labels(self):
        """下拉显示值必须是短语，长说明交给提示行。"""
        self.assertIn('<option value="move">原地整理</option>', self.html)
        self.assertIn('<option value="recycle">移入回收站</option>', self.html)
        self.assertIn('<option value="standard">标准（推荐）</option>', self.html)

    def test_copy_mode_requires_output_before_plan(self):
        """复制方式没填输出目录就试运行：旧版会生成 move 计划却挂着复制按钮。

        执行侧本来就有一句「复制方式要先填输出目录」，所以断言必须落在试运行这一步
        独有的措辞上，否则用例在任何版本上都绿。
        """
        self.assertIn("复制方式要先填输出目录，再点试运行", self.html)


if __name__ == "__main__":
    unittest.main()
