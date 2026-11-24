"""P0-2 · os.scandir 元数据复用的行为一致性回归。

改造把 build_tree / resource_roots / snapshot_digest 的遍历从
iterdir + is_dir + stat（每文件多次系统调用）改为 os.scandir 的
DirEntry 缓存元数据。这里把三个改造前的实现原样内联作对照，
跑同一份真实目录树生成两份计划，逐字段比对全部语义输出。

digest 是 sha256 字节序列，任何遍历顺序/字段的偏差都会改变哈希，
因此 digest 相等即证明字节级一致，不只是一个计数。
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nesttidy import planner, rules
from nesttidy.model import OperationPlan


def touch(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def legacy_build_tree(root: Path, should_skip, progress=None, **_ignored):
    """改造前实现：Path.iterdir + 逐项 is_dir/stat。仅用于对照，不要在生产线复活。"""
    issues: list[dict] = []

    def walk(path: Path):
        node = rules.VDir(name=path.name, original_path=path)
        try:
            children = sorted(path.iterdir(), key=lambda p: rules.stable_key(p, "dir" if p.is_dir() else "file"))
        except OSError as exc:
            issues.append({"path": str(path), "code": "PERMISSION_DENIED", "message": str(exc)})
            return node
        for child in children:
            skip = should_skip(child)
            if skip:
                issues.append({"path": str(child), "code": skip, "message": "已按扫描排除规则跳过"})
                continue
            try:
                if child.is_dir():
                    node.dirs.append(walk(child))
                elif child.is_file():
                    stat = child.stat()
                    cleaned, clean_trace = rules.clean_name(child.name)
                    node.files.append(
                        rules.VFile(
                            name=cleaned,
                            original_path=child,
                            size=stat.st_size,
                            mtime_ns=stat.st_mtime_ns,
                            name_cleaning_trace=clean_trace,
                        )
                    )
                else:
                    issues.append({"path": str(child), "code": "SKIPPED_SPECIAL", "message": "特殊文件系统条目"})
            except OSError as exc:
                issues.append({"path": str(child), "code": "PERMISSION_DENIED", "message": str(exc)})
        return node

    tree = walk(root)
    tree.sort()
    return tree, issues


def legacy_snapshot_digest(paths: list[Path], progress=None) -> str:
    """改造前实现：os.walk + 逐文件 path.stat。仅用于对照。"""
    h = hashlib.sha256()
    done = 0
    for root in sorted(paths, key=lambda p: str(p).casefold()):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort(key=str.casefold)
            filenames.sort(key=str.casefold)
            h.update(str(Path(dirpath)).encode("utf-8", "surrogatepass"))
            for name in filenames:
                path = Path(dirpath) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                done += 1
                if progress:
                    progress(done, 0, name, "计划")
                h.update(str(path).encode("utf-8", "surrogatepass"))
                h.update(str(stat.st_size).encode())
                h.update(str(stat.st_mtime_ns).encode())
    return h.hexdigest()


def legacy_resource_roots(selected_root: Path, mode: str):
    """改造前实现：selected_root.iterdir + 逐项 is_dir。仅用于对照。"""
    from nesttidy.planner import issue, should_skip_path

    selected_root = selected_root.resolve()
    issues: list = []
    if not selected_root.exists() or not selected_root.is_dir():
        raise ValueError(f"目录不存在或不是文件夹: {selected_root}")
    if mode == "single":
        return [selected_root], issues
    roots: list[Path] = []
    for child in sorted(selected_root.iterdir(), key=lambda p: (str(p).casefold(), str(p))):
        skip = should_skip_path(child)
        if skip:
            issues.append(issue(str(child), "scan", skip.split(":", 1)[0], skip))
            continue
        if child.is_dir():
            roots.append(child.resolve())
        elif child.is_file():
            issues.append(issue(str(child), "scan", "IGNORED_ROOT_FILE", "批量模式下运行根目录文件原地保留"))
    return roots, issues


def normalized(plan: OperationPlan) -> dict:
    """抹掉每次生成都不同的 id/时间戳，其余字段全量参与比对。"""
    data = plan.to_dict()
    data["plan_id"] = data["created_at"] = "<volatile>"
    for key, field in (("operations", "op_id"), ("mappings", "entry_id"), ("issues", "issue_id")):
        for row in data[key]:
            row[field] = "<volatile>"
            if "created_at" in row:
                row["created_at"] = "<volatile>"
    return data


class ScandirEquivalenceTests(unittest.TestCase):
    """scandir 三处改造必须与改造前逐条、逐字节一致。"""

    @staticmethod
    def build_tree_fixture(base: Path) -> Path:
        root = base / "资源库" / "专题A"
        files = (
            "总览.txt",
            "电影/下载源/解压目录/第一季/第01集.mkv", "电影/下载源/解压目录/第一季/第02集.mkv",
            "电影/下载源/解压目录/海报/封面.jpg",
            "合集/甲辑/a.txt", "合集/乙辑/b.txt",
            "保留分区/正文/保留1.txt", "保留分区/正文/保留2.txt", "保留分区/附录/保留3.txt",
            "Z_顶层/rootfile.dat", "Z_顶层/中文 命名/测试 文件.txt",
        )
        for item in files:
            touch(root / Path(item), item)
        (root / "电影" / "下载源" / "空缓存").mkdir(parents=True, exist_ok=True)
        # 关键边界：符号链接必须位于“被遍历目录的子层”，才会真正走到
        # snapshot_digest 的 `if not os.path.islink(new_path)` 分支。
        # 若只放在 root.parent，resource_roots 会先把它当资源根跳过，覆盖不到该分支。
        target = base / "链接目标"
        target.mkdir(parents=True, exist_ok=True)
        touch(target / "外部.txt", "outside")
        try:
            (root / "Z_顶层" / "链接目录").symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            pass
        return root

    def patch_legacy(self):
        """返回同时替换三处遍历为旧实现的上下文管理器栈。"""
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(mock.patch.object(planner, "build_tree", legacy_build_tree))
        stack.enter_context(mock.patch.object(planner, "snapshot_digest", legacy_snapshot_digest))
        stack.enter_context(mock.patch.object(planner, "resource_roots", legacy_resource_roots))
        return stack

    def test_batch_plan_matches_legacy_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.build_tree_fixture(Path(tmp))
            # 符号链接目录：os.walk 与 scandir 版都不进入；应作为 issue 跳过。
            try:
                (root.parent / "专题A_链接").symlink_to(root, target_is_directory=True)
            except (OSError, NotImplementedError):
                pass

            with self.patch_legacy():
                legacy = planner.build_plan(str(root.parent), "batch")
            current = planner.build_plan(str(root.parent), "batch")

            self.assertEqual(normalized(current), normalized(legacy),
                             "scandir 改造改变了 mappings/operations/issues/目标路径")
            self.assertEqual(current.source_snapshot_digest, legacy.source_snapshot_digest,
                             "snapshot_digest 字节序列改变")

    def test_digest_matches_legacy_for_multi_root_and_reversed_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.build_tree_fixture(Path(tmp))
            dirs = [p for p in root.rglob("*") if p.is_dir()] + [root]
            for order in (dirs, list(reversed(dirs))):
                self.assertEqual(planner.snapshot_digest(order), legacy_snapshot_digest(order),
                                 "多根/乱序下 digest 与改造前不一致")

    def test_mutant_is_detected(self) -> None:
        """变异守卫：若把 stat size 改错，对照必须失败，证明上面的断言有效。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = self.build_tree_fixture(Path(tmp))
            baseline = planner.snapshot_digest([root])

            def mutant(paths, progress=None):
                h = hashlib.sha256()
                for p in paths:
                    for dirpath, dirnames, filenames in os.walk(p):
                        dirnames.sort(key=str.casefold)
                        h.update(str(Path(dirpath)).encode("utf-8", "surrogatepass"))
                        for name in sorted(filenames, key=str.casefold):
                            fp = Path(dirpath) / name
                            h.update(str(fp).encode("utf-8", "surrogatepass"))
                            h.update(str(fp.stat().st_size + 1).encode())
                return h.hexdigest()

            self.assertNotEqual(mutant([root]), baseline, "变异未被检出，说明 digest 断言是假绿")


if __name__ == "__main__":
    unittest.main()
