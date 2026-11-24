"""repair 的行为接缝：Windows 专有动作的接线与「不跟随链接」的边界。

两处历史缺陷都藏在 `os.name == "nt"` 分支里，非 Windows 全绿也照样带病出厂：

1. grant_current_user 的 run_icacls 调用把 action 写成位置参数，被 *args 吞掉后
   run_icacls 缺关键字参数直接 TypeError——Windows 上勾「授权当前用户」必崩；
2. 符号链接 / junction 不在跳过名单里，属性与 ACL 会写到链接目标上，
   目标可能落在选定目录之外（页面承诺「符号链接，不跟随不处理」）。

这里不测 icacls / attrib 本身（那是 Windows 的事），只测接线与边界。
"""
from __future__ import annotations

import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from nesttidy.repair import repair_permissions, run_attrib, run_icacls


def touch(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def windows_repair_module(getlogin) -> SimpleNamespace:
    """只替换 repair 命名空间里的 os：walk 用真的，name=nt 才能走进 Windows 分支。"""
    return SimpleNamespace(name="nt", walk=os.walk, getlogin=getlogin)


class GrantAndInheritanceWiringTests(unittest.TestCase):
    """grant_current_user / fix_inheritance 必须真的走到 run_icacls，且不许抛异常。"""

    def test_grant_and_inheritance_reach_icacls_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "R"
            touch(root / "a.txt")
            with mock.patch("nesttidy.repair.os", windows_repair_module(lambda: "tester")):
                rows = repair_permissions(str(root), grant_current_user=True, fix_inheritance=True)
        actions = [row.action for row in rows]
        # 历史缺陷：action 写成位置参数 → TypeError，一行都到不了这里。
        self.assertIn("grant_current_user", actions)
        self.assertIn("fix_inheritance", actions)
        self.assertTrue(all(row.status in {"ok", "failed", "skipped"} for row in rows))

    def test_unknown_account_records_failure_rows_instead_of_crashing(self) -> None:
        def no_login() -> str:
            raise OSError("no controlling terminal")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "R"
            touch(root / "a.txt")
            with mock.patch("nesttidy.repair.os", windows_repair_module(no_login)):
                rows = repair_permissions(str(root), grant_current_user=True)
        grant_rows = [row for row in rows if row.action == "grant_current_user"]
        self.assertTrue(grant_rows)
        # 目录自身与文件逐项各一行，根因相同：每行都必须说清「未执行」，不许异常中断整轮。
        for row in grant_rows:
            self.assertEqual(row.status, "failed")
            self.assertIn("无法确定当前登录用户名", row.message)


class SubprocessOutputDecodingTests(unittest.TestCase):
    """attrib / icacls 的输出只用于判定成功与否，不解析内容——解码必须容错。

    历史缺陷：两处 `subprocess.run(text=True)` 未指定 encoding/errors。中文 Windows 上
    Python 3.13 默认按 UTF-8 解码，而 attrib / icacls 输出 GBK 中文路径，读取线程抛
    UnicodeDecodeError。异常不传播到主流程（check=True 只看 returncode），产品行为正常，
    但每次权限修复都在 stderr 刷一堆栈。用接线断言锁住契约，跨平台可跑。
    """

    def test_run_attrib_uses_error_tolerant_decoding(self) -> None:
        with mock.patch("nesttidy.repair.subprocess.run") as run:
            run_attrib(Path("whatever.txt"), "+R", "clear_readonly")
        kwargs = run.call_args.kwargs
        self.assertTrue(kwargs.get("text"), "输出按文本读取的契约不能丢")
        self.assertEqual(kwargs.get("errors"), "replace", "text=True 必须配容错解码，否则 GBK 输出抛异常")

    def test_run_icacls_uses_error_tolerant_decoding(self) -> None:
        with mock.patch("nesttidy.repair.subprocess.run") as run:
            run_icacls(Path("whatever.txt"), "/grant", "*S-1-1-0:F", action="grant_current_user")
        kwargs = run.call_args.kwargs
        self.assertTrue(kwargs.get("text"), "输出按文本读取的契约不能丢")
        self.assertEqual(kwargs.get("errors"), "replace", "text=True 必须配容错解码，否则 GBK 输出抛异常")

    @unittest.skipUnless(os.name == "nt", "icacls 中文 GBK 输出是 Windows 专有场景")
    def test_chinese_path_gbk_output_produces_no_thread_exception(self) -> None:
        """行为级：真实 icacls 对中文路径输出 GBK，解码不得在读取线程抛异常。"""
        caught: list[BaseException] = []
        original_hook = threading.excepthook

        def capture(args: threading.ExceptHookArgs) -> None:
            caught.append(args.exc_value)
            original_hook(args)

        threading.excepthook = capture
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "中文文件.txt"
                touch(path)
                result = run_icacls(path, "/grant", "*S-1-1-0:F", action="grant_current_user")
        finally:
            threading.excepthook = original_hook
        self.assertEqual(result.status, "ok")
        self.assertEqual(caught, [], f"子进程输出解码异常仍在读取线程抛出：{caught}")


class SymlinkBoundaryTests(unittest.TestCase):
    """符号链接不跟随不处理：属性修复不得穿过链接写到外面的目标上。"""

    def test_repair_skips_symlink_and_leaves_target_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()  # macOS 的 /var 是指向 /private/var 的链接，两侧必须同源才能比路径
            root = base / "R"
            root.mkdir()
            outside = base / "outside.txt"
            touch(outside)
            outside.chmod(stat.S_IREAD)  # 只读目标：被误修会加上写位，可断言
            link = root / "link.txt"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("本机无法创建符号链接，跳过此边界场景")
            before = outside.stat().st_mode

            rows = repair_permissions(str(root), clear_readonly=True)

            link_rows = [row for row in rows if row.path == str(link)]
            self.assertEqual([row.status for row in link_rows], ["skipped"])
            self.assertIn("SYMLINK", link_rows[0].message)
            self.assertEqual(outside.stat().st_mode, before, "修复穿过符号链接改了链接目标")
            self.assertEqual(oct(outside.stat().st_mode & 0o222), "0o0", "只读目标被误写")


if __name__ == "__main__":
    unittest.main()
