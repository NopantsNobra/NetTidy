# -*- coding: utf-8 -*-
"""
文件夹重组工具 V7.0 — 单元测试模块
"""

import os
import tempfile
import unittest

from .公共定义 import _fmt_size, _sanitize_filename, _truncate_name, _win_path, _short_path
from .数据模型 import VirtualFileNode
from .执行引擎 import FileListParser, ExecutionEngine

# ============================================================
# 单元测试
# ============================================================

def run_tests() -> bool:
    """运行内置单元测试"""
    import unittest
    import tempfile

    class TestSanitizeFilename(unittest.TestCase):
        def test_normal(self):
            self.assertEqual(_sanitize_filename("hello.txt"), "hello.txt")
        def test_illegal_chars(self):
            result = _sanitize_filename('file<>:"|?*.txt')
            for c in '<>:"|?*\x00':
                self.assertNotIn(c, result)
        def test_reserved_name(self):
            self.assertTrue(_sanitize_filename("CON").startswith("_"))
        def test_empty(self):
            self.assertEqual(_sanitize_filename(""), "_unnamed")
        def test_dots_spaces(self):
            result = _sanitize_filename("  ...  ")
            self.assertNotEqual(result, "")
            self.assertFalse(result.startswith(" "))

    class TestTruncateName(unittest.TestCase):
        def test_short_name_unchanged(self):
            self.assertEqual(_truncate_name("short.txt", 100), "short.txt")
        def test_long_name_truncated(self):
            result = _truncate_name("a" * 300 + ".txt", 50)
            self.assertLessEqual(len(result.encode("utf-8")), 50)
            self.assertTrue(result.endswith(".txt"))
        def test_hash_appended(self):
            result = _truncate_name("x" * 200 + ".dat", 40)
            self.assertIn("_", result)

    class TestFmtSize(unittest.TestCase):
        def test_bytes(self): self.assertIn("B", _fmt_size(500))
        def test_kb(self): self.assertIn("KB", _fmt_size(2048))
        def test_mb(self): self.assertIn("MB", _fmt_size(5 * 1024 * 1024))
        def test_gb(self): self.assertIn("GB", _fmt_size(3 * 1024 * 1024 * 1024))
        def test_zero(self): self.assertIn("B", _fmt_size(0))
        def test_input_unchanged(self):
            original = 5 * 1024 * 1024
            _ = _fmt_size(original)
            self.assertEqual(original, 5 * 1024 * 1024)

    class TestWinPath(unittest.TestCase):
        def test_roundtrip_normal(self):
            path = r"C:\Users\test\file.txt"
            self.assertEqual(_short_path(_win_path(path)), path)
        def test_unc_path(self):
            path = r"\\server\share\file.txt"
            wp = _win_path(path)
            if IS_WINDOWS:
                self.assertTrue(wp.startswith("\\\\?\\UNC\\"))

    class TestVirtualFileNode(unittest.TestCase):
        def test_creation(self):
            node = VirtualFileNode(r"C:\test\file.txt", "file.txt", ".txt", 1024, 1)
            self.assertEqual(node.file_name, "file.txt")
            self.assertEqual(node.size_bytes, 1024)
            self.assertFalse(node.is_dir)

    class TestOfflineDedupByName(unittest.TestCase):
        def test_size_aware_dedup(self):
            nodes = [
                VirtualFileNode(r"C:\a\video.mp4", "video.mp4", ".mp4", 1024 * 1024, 1),
                VirtualFileNode(r"C:\b\video.mp4", "video.mp4", ".mp4", 1024 * 1024, 2),
                VirtualFileNode(r"C:\c\video.mp4", "video.mp4", ".mp4", 100, 1),
            ]
            engine = OfflineEliminationEngine(nodes)
            count = engine.deduplicate_by_name()
            self.assertEqual(count, 1)
            self.assertEqual(nodes[0].action, NodeAction.NONE)
            self.assertEqual(nodes[1].action, NodeAction.DELETE)
            self.assertEqual(nodes[2].action, NodeAction.NONE)

        def test_no_size_data_marks_review(self):
            nodes = [
                VirtualFileNode(r"C:\a\doc.txt", "doc.txt", ".txt", 0, 1),
                VirtualFileNode(r"C:\b\doc.txt", "doc.txt", ".txt", 0, 2),
            ]
            engine = OfflineEliminationEngine(nodes)
            count = engine.deduplicate_by_name()
            self.assertEqual(count, 0)
            self.assertIn("手动确认", nodes[0].reason)

    class TestFileListParser(unittest.TestCase):
        def test_pure_path_list(self):
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
                f.write("C:\\Projects\\src\\main.py\nC:\\Projects\\README.md\n")
                tmppath = f.name
            parser = FileListParser()
            lines = parser.load_files([tmppath])
            os.unlink(tmppath)
            self.assertEqual(len(lines), 2)
            self.assertFalse(parser._has_custom_format)

        def test_truncated_line_detection(self):
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
                f.write("C:\\Projects\\normal.txt\nC:\\Projects\\long_path...\nC:\\Projects\\another.txt\n")
                tmppath = f.name
            parser = FileListParser()
            lines = parser.load_files([tmppath])
            os.unlink(tmppath)
            self.assertEqual(len(lines), 2)
            self.assertEqual(parser.total_truncated, 1)

    class TestRollbackScript(unittest.TestCase):
        def test_rollback_generates_python(self):
            engine = ExecutionEngine([], "/tmp/test", "/tmp/log", "/tmp/trash")
            engine.op_logs.append(OperationLog(1, "MOVE", r"C:\file.txt", r"C:\trash\file.txt", "2026-01-01T00:00:00"))
            script = engine.generate_rollback_script()
            self.assertIn("shutil.move", script)
            self.assertIn("import shutil", script)

        def test_empty_rollback(self):
            engine = ExecutionEngine([], "/tmp/test", "/tmp/log", "/tmp/trash")
            script = engine.generate_rollback_script()
            self.assertIn("无需回滚", script)

        def test_rollback_path_injection_protection(self):
            engine = ExecutionEngine([], "/tmp/test", "/tmp/log", "/tmp/trash")
            engine.op_logs.append(OperationLog(1, "MOVE", r'C:\test"file.txt', r'C:\trash"file.txt', "2026-01-01T00:00:00"))
            script = engine.generate_rollback_script()
            self.assertIn('\\"', script)
            self.assertNotIn('r"C:\\test', script)

    class TestTrashPathConflict(unittest.TestCase):
        def test_trash_path_always_unique(self):
            with tempfile.TemporaryDirectory() as tmpdir:
                trash_dir = os.path.join(tmpdir, ".trash")
                os.makedirs(trash_dir)
                engine = ExecutionEngine([], tmpdir, os.path.join(tmpdir, "log"), trash_dir)
                paths = [engine._get_trash_path(os.path.join(tmpdir, "subdir", "test.txt")) for _ in range(10)]
                self.assertEqual(len(paths), len(set(paths)))
                for path in paths:
                    self.assertRegex(os.path.basename(path), r'.*_\d{8}_\d{6}_[a-f0-9]{8}\.txt$')

    class TestOfflineDedupScopeFix(unittest.TestCase):
        def test_dedup_no_size_single_group(self):
            nodes = [
                VirtualFileNode(r"C:\a\doc.txt", "doc.txt", ".txt", 0, 1),
                VirtualFileNode(r"C:\b\doc.txt", "doc.txt", ".txt", 0, 2),
                VirtualFileNode(r"C:\c\doc.txt", "doc.txt", ".txt", 0, 3),
            ]
            engine = OfflineEliminationEngine(nodes)
            count = engine.deduplicate_by_name()
            self.assertEqual(count, 0)
            self.assertEqual(len(engine.duplicate_groups), 1)
            self.assertEqual(engine.duplicate_groups[0]["keep"], r"C:\a\doc.txt")
            self.assertEqual(len(engine.duplicate_groups[0]["delete"]), 2)

    class TestNodeAttrsStr(unittest.TestCase):
        def test_no_attrs(self):
            node = VirtualFileNode(r"C:\test.txt", "test.txt", ".txt")
            self.assertEqual(_node_attrs_str(node), "-")
        def test_hidden_readonly(self):
            node = FileNode(r"C:\test.txt", "test.txt", ".txt", is_hidden=True, is_readonly=True)
            self.assertEqual(_node_attrs_str(node), "HR")
        def test_reparse_symlink(self):
            node = FileNode(r"C:\link", "link", "", reparse_type=ReparseType.SYMLINK)
            self.assertIn("S", _node_attrs_str(node))

    # 运行测试
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for test_class in [
        TestSanitizeFilename, TestTruncateName, TestFmtSize,
        TestWinPath, TestVirtualFileNode, TestOfflineDedupByName,
        TestFileListParser, TestRollbackScript, TestTrashPathConflict,
        TestOfflineDedupScopeFix, TestNodeAttrsStr,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(test_class))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    main()
