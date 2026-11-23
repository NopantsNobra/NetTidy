"""P0-3 · resolve() 单次规划内记忆化的行为一致性回归。

改造只把同一路径字符串的重复 resolve() 收敛为一次，resolved 语义（含
符号链接）不变，且缓存只活在一次规划内。这里用 mock 计数证明：
  - 同一路径被重复引用时只解析一次；
  - 两次独立规划的缓存互不复用；
  - 缓存版与逐次解析版的计划逐条一致（符号链接/keep 判定不漂移）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nesttidy.model import OperationPlan
from nesttidy.planner import _path_resolver, build_plan


def touch(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def normalized(plan: OperationPlan) -> dict:
    data = plan.to_dict()
    data["plan_id"] = data["created_at"] = "<volatile>"
    for key, field in (("operations", "op_id"), ("mappings", "entry_id"), ("issues", "issue_id")):
        for row in data[key]:
            row[field] = "<volatile>"
            if "created_at" in row:
                row["created_at"] = "<volatile>"
    return data


class PathResolverTests(unittest.TestCase):
    def test_same_path_resolved_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "实目录"
            target.mkdir()
            link = Path(tmp) / "链接"
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("本机无法创建符号链接")

            original_resolve = Path.resolve
            resolve = _path_resolver()
            with mock.patch.object(Path, "resolve", autospec=True, side_effect=original_resolve) as spy:
                a = resolve(Path(link))
                b = resolve(Path(link))

            self.assertEqual(a, b, "同路径两次解析结果不同")
            self.assertEqual(a, original_resolve(target), "符号链接解析语义被改变")
            # link 同字符串两次引用只解析一次（第二次命中缓存，不进 spy）。
            self.assertEqual(spy.call_count, 1, f"同路径重复解析未被缓存，实际 {spy.call_count} 次")
            self.assertIs(a, b, "缓存应返回同一 Path 对象")

    def test_cache_is_not_reused_across_plans(self) -> None:
        """两个 resolver 实例互不共享缓存，避免跨规划陈旧判定。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x"
            p.write_text("x")
            original_resolve = Path.resolve
            r1, r2 = _path_resolver(), _path_resolver()
            with mock.patch.object(Path, "resolve", autospec=True, side_effect=original_resolve) as spy:
                r1(Path(p))
                r1(Path(p))
                r2(Path(p))
            self.assertEqual(spy.call_count, 2, "跨 resolver 实例错误复用了缓存")

    def test_build_plan_matches_uncached_resolution(self) -> None:
        """把 resolver 换成逐次 Path.resolve，计划必须逐条一致。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "资源" / "包A"
            touch(root / "说明.txt", "a")
            touch(root / "视频" / "01.mkv", "b")
            touch(root / "视频" / "02.mkv", "c")
            try:
                (Path(tmp) / "资源" / "包A_链接").symlink_to(root, target_is_directory=True)
            except (OSError, NotImplementedError):
                pass

            memo_plan = build_plan(str(root), "single")
            # 用无缓存 resolver 替换闭包，模拟改造前每次都 resolve。
            with mock.patch(
                "nesttidy.planner._path_resolver",
                side_effect=lambda: (lambda path: path.resolve()),
            ):
                uncached_plan = build_plan(str(root), "single")

            self.assertEqual(normalized(memo_plan), normalized(uncached_plan),
                             "记忆化改变了 keep/move 或目标路径判定")
            self.assertEqual(memo_plan.source_snapshot_digest, uncached_plan.source_snapshot_digest)


if __name__ == "__main__":
    unittest.main()
