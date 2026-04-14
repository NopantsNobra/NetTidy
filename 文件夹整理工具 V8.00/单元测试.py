"""
单元测试模块
包含所有模块的单元测试
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from 常量定义 import (
    VERSION, IS_WINDOWS, NodeAction, NodeStatus, ReparseType,
    SafetyLevel, PermissionAction, CACHE_DIR_PATTERNS, JUNK_FILE_PATTERNS,
)
from 工具函数 import (
    _win_path, _short_path, _sanitize_filename, _truncate_name,
    _fmt_size, _parse_size, _compute_quick_hash, _compute_full_hash,
)
from 数据模型 import FileNode, VirtualFileNode, OperationLog, ErrorEntry
from 安全检查 import SafetyChecker, LockManager, ErrorCollector


# ═══════════════════════════════════════════════════════════
# 常量测试
# ═══════════════════════════════════════════════════════════

class TestConstants(unittest.TestCase):
    """常量模块测试"""
    
    def test_version(self):
        """测试版本号格式"""
        parts = VERSION.split(".")
        self.assertEqual(len(parts), 3)
        for p in parts:
            self.assertTrue(p.isdigit())
    
    def test_is_windows(self):
        """测试平台检测"""
        self.assertIsInstance(IS_WINDOWS, bool)
    
    def test_node_action_values(self):
        """测试节点操作枚举值"""
        self.assertEqual(NodeAction.NONE.value, "None")
        self.assertEqual(NodeAction.DELETE.value, "DELETE")
        self.assertEqual(NodeAction.MOVE.value, "MOVE")
        self.assertEqual(NodeAction.HARDLINK.value, "HARDLINK")
    
    def test_node_status_values(self):
        """测试节点状态枚举值"""
        self.assertEqual(NodeStatus.PENDING.value, "Pending")
        self.assertEqual(NodeStatus.SUCCESS.value, "Success")
        self.assertEqual(NodeStatus.FAILED.value, "Failed")
    
    def test_cache_patterns_not_empty(self):
        """测试缓存模式列表非空"""
        self.assertTrue(len(CACHE_DIR_PATTERNS) > 0)
    
    def test_junk_patterns_not_empty(self):
        """测试垃圾文件模式列表非空"""
        self.assertTrue(len(JUNK_FILE_PATTERNS) > 0)


# ═══════════════════════════════════════════════════════════
# 工具函数测试
# ═══════════════════════════════════════════════════════════

class TestUtils(unittest.TestCase):
    """工具函数测试"""
    
    def test_sanitize_filename(self):
        """测试文件名清理"""
        # 正常文件名
        self.assertEqual(_sanitize_filename("test.txt"), "test.txt")
        
        # 非法字符
        self.assertEqual(_sanitize_filename("test<>file.txt"), "test__file.txt")
        
        # 空文件名
        self.assertEqual(_sanitize_filename(""), "_unnamed")
        
        # 点号开头
        self.assertEqual(_sanitize_filename(".hidden"), "_hidden")
    
    def test_truncate_name(self):
        """测试文件名截断"""
        long_name = "a" * 300 + ".txt"
        truncated = _truncate_name(long_name, 200)
        self.assertLessEqual(len(truncated), 200)
        self.assertTrue(truncated.endswith(".txt"))
    
    def test_fmt_size(self):
        """测试大小格式化"""
        self.assertEqual(_fmt_size(0), "0 B")
        self.assertEqual(_fmt_size(1024), "1.00 KB")
        self.assertEqual(_fmt_size(1048576), "1.00 MB")
        self.assertEqual(_fmt_size(1073741824), "1.00 GB")
    
    def test_parse_size(self):
        """测试大小解析"""
        self.assertEqual(_parse_size("100"), 100)
        self.assertEqual(_parse_size("1KB"), 1024)
        self.assertEqual(_parse_size("1MB"), 1048576)
        self.assertEqual(_parse_size("1GB"), 1073741824)
    
    def test_win_path(self):
        """测试 Windows 路径转换"""
        if IS_WINDOWS:
            path = "C:\\test\\path"
            result = _win_path(path)
            self.assertTrue(result.startswith("\\\\?\\"))
        else:
            # 非 Windows 平台应返回原路径
            path = "/test/path"
            self.assertEqual(_win_path(path), path)


# ═══════════════════════════════════════════════════════════
# 数据模型测试
# ═══════════════════════════════════════════════════════════

class TestModels(unittest.TestCase):
    """数据模型测试"""
    
    def test_file_node_creation(self):
        """测试文件节点创建"""
        node = FileNode(
            full_path="/test/file.txt",
            file_name="file.txt",
            extension=".txt",
            size_bytes=1024,
            depth=1,
        )
        
        self.assertEqual(node.full_path, "/test/file.txt")
        self.assertEqual(node.file_name, "file.txt")
        self.assertEqual(node.extension, ".txt")
        self.assertEqual(node.size_bytes, 1024)
        self.assertEqual(node.action, NodeAction.NONE)
        self.assertEqual(node.status, NodeStatus.PENDING)
    
    def test_file_node_to_dict(self):
        """测试文件节点序列化"""
        node = FileNode(
            full_path="/test/file.txt",
            file_name="file.txt",
            extension=".txt",
            size_bytes=1024,
            depth=1,
        )
        
        d = node.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["full_path"], "/test/file.txt")
    
    def test_operation_log(self):
        """测试操作日志"""
        log = OperationLog(
            seq=1,
            action="DELETE",
            source="/test/file.txt",
            target="",
            timestamp=datetime.now().isoformat(),
        )
        
        self.assertEqual(log.seq, 1)
        self.assertEqual(log.action, "DELETE")
    
    def test_error_entry(self):
        """测试错误条目"""
        entry = ErrorEntry(
            path="/test/file.txt",
            error_type="TestError",
            message="Test error message",
        )
        
        self.assertEqual(entry.path, "/test/file.txt")
        self.assertEqual(entry.error_type, "TestError")


# ═══════════════════════════════════════════════════════════
# 安全检查测试
# ═══════════════════════════════════════════════════════════

class TestSafety(unittest.TestCase):
    """安全检查测试"""
    
    def test_safety_level_check(self):
        """测试安全级别检查"""
        level, msg = SafetyChecker.check("C:\\Windows\\System32")
        self.assertEqual(level, SafetyLevel.BLOCKED)
    
    def test_protected_path(self):
        """测试受保护路径检测"""
        # Windows 系统目录
        if IS_WINDOWS:
            self.assertTrue(SafetyChecker.is_protected_path("C:\\Windows"))
    
    def test_error_collector(self):
        """测试错误收集器"""
        collector = ErrorCollector()
        
        self.assertFalse(collector.has_errors())
        self.assertEqual(collector.count(), 0)
        
        collector.add("/test/path", "TestError", "Test message")
        
        self.assertTrue(collector.has_errors())
        self.assertEqual(collector.count(), 1)
    
    def test_lock_manager(self):
        """测试锁管理器"""
        lock_path = LockManager.lock_path("/test")
        self.assertIn("/test", lock_path)


# ═══════════════════════════════════════════════════════════
# 运行测试
# ═══════════════════════════════════════════════════════════

def run_tests():
    """运行所有测试"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    suite.addTests(loader.loadTestsFromTestCase(TestConstants))
    suite.addTests(loader.loadTestsFromTestCase(TestUtils))
    suite.addTests(loader.loadTestsFromTestCase(TestModels))
    suite.addTests(loader.loadTestsFromTestCase(TestSafety))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
