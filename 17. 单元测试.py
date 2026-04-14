"""
单元测试模块
=============
职责：各模块的单元测试

运行方式：
    python -m folder_restructure_v8.单元测试
    python -m unittest folder_restructure_v8.单元测试 -v

依赖模块：
    - 所有业务模块
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from typing import List

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from 常量定义 import (
    VERSION, IS_WINDOWS, NodeAction, NodeStatus, ReparseType,
    SafetyLevel, PermissionAction, DedupStrategy, FlattenMode,
    CACHE_DIR_PATTERNS, JUNK_FILE_PATTERNS, EXTENSION_GROUPS,
    DEFAULT_CONFIG, WINDOWS_RESERVED, ILLEGAL_CHARS,
)
from 工具函数 import (
    _sanitize_filename, _truncate_name, _fmt_size, _parse_size,
    _win_path, _short_path, _unique_filename, _escape_html,
)
from 数据模型 import FileNode, VirtualFileNode, OperationLog, ErrorEntry, DuplicateGroup
from 安全检查 import SafetyChecker, LockManager, ErrorCollector
from 配置管理 import RuntimeConfig, load_config, validate_config


# ═══════════════════════════════════════════════════════════════════
# 常量测试
# ═══════════════════════════════════════════════════════════════════

class TestConstants(unittest.TestCase):
    """常量模块测试"""
    
    def test_version_format(self):
        """测试版本号格式"""
        parts = VERSION.split(".")
        self.assertEqual(len(parts), 3)
        for p in parts:
            self.assertTrue(p.isdigit(), f"版本号部分应为数字: {p}")
    
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
    
    def test_dedup_strategy_from_string(self):
        """测试去重策略解析"""
        self.assertEqual(DedupStrategy.from_string("shallowest"), DedupStrategy.SHALLOWEST)
        self.assertEqual(DedupStrategy.from_string("newest"), DedupStrategy.NEWEST)
        self.assertEqual(DedupStrategy.from_string("largest"), DedupStrategy.LARGEST)
        self.assertEqual(DedupStrategy.from_string("hardlink"), DedupStrategy.HARDLINK)
        self.assertEqual(DedupStrategy.from_string("invalid"), DedupStrategy.SHALLOWEST)
    
    def test_flatten_mode_from_string(self):
        """测试扁平化模式解析"""
        self.assertEqual(FlattenMode.from_string("single"), FlattenMode.SINGLE)
        self.assertEqual(FlattenMode.from_string("ext"), FlattenMode.EXTENSION)
        self.assertEqual(FlattenMode.from_string("date"), FlattenMode.DATE)
        self.assertEqual(FlattenMode.from_string("invalid"), FlattenMode.SINGLE)
    
    def test_permission_action_from_string(self):
        """测试权限操作解析"""
        result = PermissionAction.from_string("readonly,hidden")
        self.assertIn(PermissionAction.REMOVE_READONLY, result)
        self.assertIn(PermissionAction.REMOVE_HIDDEN, result)
        
        result = PermissionAction.from_string("all")
        self.assertIn(PermissionAction.ALL, result)
    
    def test_cache_patterns_not_empty(self):
        """测试缓存模式列表"""
        self.assertTrue(len(CACHE_DIR_PATTERNS) > 0)
        self.assertIn("node_modules", CACHE_DIR_PATTERNS)
    
    def test_extension_groups_complete(self):
        """测试扩展名分组完整性"""
        self.assertIn("images", EXTENSION_GROUPS)
        self.assertIn("videos", EXTENSION_GROUPS)
        self.assertIn("documents", EXTENSION_GROUPS)
        self.assertIn("code", EXTENSION_GROUPS)


# ═══════════════════════════════════════════════════════════════════
# 工具函数测试
# ═══════════════════════════════════════════════════════════════════

class TestUtils(unittest.TestCase):
    """工具函数测试"""
    
    def test_sanitize_filename_normal(self):
        """测试正常文件名清理"""
        self.assertEqual(_sanitize_filename("test.txt"), "test.txt")
        self.assertEqual(_sanitize_filename("file name.txt"), "file name.txt")
    
    def test_sanitize_filename_illegal_chars(self):
        """测试非法字符清理"""
        result = _sanitize_filename('test<>file:"txt')
        for char in '<>:"|?*':
            self.assertNotIn(char, result)
    
    def test_sanitize_filename_reserved(self):
        """测试保留设备名处理"""
        self.assertTrue(_sanitize_filename("CON").startswith("_"))
        self.assertTrue(_sanitize_filename("PRN").startswith("_"))
        self.assertTrue(_sanitize_filename("AUX").startswith("_"))
    
    def test_sanitize_filename_empty(self):
        """测试空文件名"""
        self.assertEqual(_sanitize_filename(""), "_unnamed")
        self.assertEqual(_sanitize_filename("   "), "_unnamed")
    
    def test_truncate_name_short(self):
        """测试短文件名截断"""
        result = _truncate_name("short.txt", 100)
        self.assertEqual(result, "short.txt")
    
    def test_truncate_name_long(self):
        """测试长文件名截断"""
        long_name = "a" * 300 + ".txt"
        result = _truncate_name(long_name, 50)
        self.assertLessEqual(len(result.encode("utf-8")), 50)
        self.assertTrue(result.endswith(".txt"))
    
    def test_fmt_size(self):
        """测试大小格式化"""
        self.assertEqual(_fmt_size(0), "0 B")
        self.assertIn("KB", _fmt_size(1024))
        self.assertIn("MB", _fmt_size(1048576))
        self.assertIn("GB", _fmt_size(1073741824))
    
    def test_parse_size(self):
        """测试大小解析"""
        self.assertEqual(_parse_size("100"), 100)
        self.assertEqual(_parse_size("1KB"), 1024)
        self.assertEqual(_parse_size("1MB"), 1048576)
        self.assertEqual(_parse_size("1.5GB"), int(1.5 * 1024**3))
    
    def test_parse_size_invalid(self):
        """测试无效大小解析"""
        with self.assertRaises(ValueError):
            _parse_size("invalid")
    
    def test_win_path_conversion(self):
        """测试 Windows 路径转换"""
        if IS_WINDOWS:
            path = "C:\\test\\path"
            result = _win_path(path)
            self.assertTrue(result.startswith("\\\\?\\"))
            
            # 测试还原
            short = _short_path(result)
            self.assertEqual(short.lower(), path.lower())


# ═══════════════════════════════════════════════════════════════════
# 数据模型测试
# ═══════════════════════════════════════════════════════════════════

class TestModels(unittest.TestCase):
    """数据模型测试"""
    
    def test_file_node_creation(self):
        """测试 FileNode 创建"""
        node = FileNode(
            full_path="/test/file.txt",
            file_name="file.txt",
            extension=".txt",
            size_bytes=1024,
            depth=1,
        )
        
        self.assertEqual(node.full_path, "/test/file.txt")
        self.assertEqual(node.file_name, "file.txt")
        self.assertEqual(node.action, NodeAction.NONE)
        self.assertEqual(node.status, NodeStatus.PENDING)
    
    def test_file_node_to_dict(self):
        """测试 FileNode 序列化"""
        node = FileNode(
            full_path="/test/file.txt",
            file_name="file.txt",
            extension=".txt",
        )
        
        d = node.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["full_path"], "/test/file.txt")
    
    def test_file_node_attrs_str(self):
        """测试属性字符串"""
        node = FileNode(
            full_path="/test/file.txt",
            file_name="file.txt",
            extension=".txt",
            is_hidden=True,
            is_readonly=True,
        )
        
        attrs = node.attrs_str
        self.assertIn("H", attrs)
        self.assertIn("R", attrs)
    
    def test_virtual_file_node(self):
        """测试 VirtualFileNode"""
        node = VirtualFileNode(
            full_path="/test/file.txt",
            file_name="file.txt",
            extension=".txt",
        )
        
        self.assertFalse(node.is_marked)
        node.action = NodeAction.DELETE
        self.assertTrue(node.is_marked)
    
    def test_operation_log(self):
        """测试操作日志"""
        log = OperationLog(
            seq=1,
            action="DELETE",
            source="/test/file.txt",
            target="/trash/file.txt",
            timestamp=datetime.now().isoformat(),
        )
        
        self.assertEqual(log.seq, 1)
        self.assertEqual(log.action, "DELETE")
        
        d = log.to_dict()
        self.assertIsInstance(d, dict)
    
    def test_error_entry(self):
        """测试错误条目"""
        entry = ErrorEntry(
            seq=1,
            path="/test/file.txt",
            error_type="ReadError",
            message="无法读取文件",
        )
        
        self.assertEqual(entry.path, "/test/file.txt")
        
        d = entry.to_dict()
        self.assertIsInstance(d, dict)
    
    def test_duplicate_group(self):
        """测试重复文件组"""
        group = DuplicateGroup(
            group_id=1,
            size_bytes=1024,
            file_hash="abc123",
            keep_path="/test/file1.txt",
            delete_paths=["/test/file2.txt", "/test/file3.txt"],
        )
        
        self.assertEqual(group.delete_count, 2)
        self.assertEqual(group.save_bytes, 2048)
        
        d = group.to_dict()
        self.assertIsInstance(d, dict)


# ═══════════════════════════════════════════════════════════════════
# 安全检查测试
# ═══════════════════════════════════════════════════════════════════

class TestSafety(unittest.TestCase):
    """安全检查测试"""
    
    def test_error_collector(self):
        """测试错误收集器"""
        collector = ErrorCollector()
        
        self.assertFalse(collector.has_errors())
        self.assertEqual(collector.count(), 0)
        
        collector.add("/test/path", "TestError", "测试消息")
        
        self.assertTrue(collector.has_errors())
        self.assertEqual(collector.count(), 1)
        
        errors = collector.get_by_type("TestError")
        self.assertEqual(len(errors), 1)
        
        collector.clear()
        self.assertFalse(collector.has_errors())
    
    def test_lock_manager_create(self):
        """测试锁管理器创建"""
        with tempfile.TemporaryDirectory() as tmpdir:
            lock = LockManager(tmpdir)
            
            self.assertTrue(lock.acquire())
            self.assertTrue(lock._acquired)
            self.assertTrue(os.path.exists(lock.lock_path))
            
            lock.release()
            self.assertFalse(os.path.exists(lock.lock_path))
    
    def test_lock_manager_context(self):
        """测试锁管理器上下文"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with LockManager(tmpdir) as lock:
                self.assertTrue(lock._acquired)
            
            self.assertFalse(os.path.exists(lock.lock_path))
    
    def test_safety_level_check(self):
        """测试安全级别检查"""
        if IS_WINDOWS:
            # Windows 系统目录应被阻止
            level, _ = SafetyChecker.check("C:\\Windows\\System32")
            self.assertEqual(level, SafetyLevel.BLOCKED)
    
    def test_safety_is_protected_path(self):
        """测试受保护路径检测"""
        if IS_WINDOWS:
            self.assertTrue(SafetyChecker.is_protected_path("C:\\Windows"))
            self.assertTrue(SafetyChecker.is_protected_path("C:\\Windows\\System32"))


# ═══════════════════════════════════════════════════════════════════
# 配置管理测试
# ═══════════════════════════════════════════════════════════════════

class TestConfig(unittest.TestCase):
    """配置管理测试"""
    
    def test_runtime_config_defaults(self):
        """测试运行时配置默认值"""
        config = RuntimeConfig()
        
        self.assertEqual(config.max_workers, 8)
        self.assertTrue(config.dry_run)
        self.assertFalse(config.use_recycle_bin)
    
    def test_runtime_config_modify(self):
        """测试运行时配置修改"""
        config = RuntimeConfig()
        config.max_workers = 4
        config.verbose = True
        
        self.assertEqual(config.max_workers, 4)
        self.assertTrue(config.verbose)
    
    def test_validate_config_valid(self):
        """测试有效配置验证"""
        config = {
            "dedup_strategy": "shallowest",
            "flatten_mode": "ext",
            "threads": 8,
        }
        
        valid, errors = validate_config(config)
        self.assertTrue(valid)
        self.assertEqual(len(errors), 0)
    
    def test_validate_config_invalid_strategy(self):
        """测试无效策略验证"""
        config = {
            "dedup_strategy": "invalid_strategy",
        }
        
        valid, errors = validate_config(config)
        self.assertFalse(valid)
        self.assertTrue(any("去重策略" in e for e in errors))
    
    def test_validate_config_invalid_threads(self):
        """测试无效线程数验证"""
        config = {
            "threads": -1,
        }
        
        valid, errors = validate_config(config)
        self.assertFalse(valid)


# ═══════════════════════════════════════════════════════════════════
# 扁平化引擎测试
# ═══════════════════════════════════════════════════════════════════

class TestFlatten(unittest.TestCase):
    """扁平化引擎测试"""
    
    def test_size_category(self):
        """测试大小分类"""
        from 扁平化引擎 import FlattenEngine
        
        engine = FlattenEngine([], "/tmp")
        
        self.assertEqual(engine._get_size_category(500), "小于1KB")
        self.assertEqual(engine._get_size_category(1024), "1KB-1MB")
        self.assertEqual(engine._get_size_category(1024 * 1024), "1MB-100MB")
        self.assertEqual(engine._get_size_category(1024 * 1024 * 1024), "100MB-1GB")
    
    def test_ext_classification(self):
        """测试扩展名分类"""
        from 扁平化引擎 import FlattenEngine
        
        engine = FlattenEngine([], "/tmp")
        
        self.assertEqual(engine.ext_group_map.get(".jpg"), "images")
        self.assertEqual(engine.ext_group_map.get(".py"), "code")
        self.assertEqual(engine.ext_group_map.get(".pdf"), "documents")


# ═══════════════════════════════════════════════════════════════════
# 剔除引擎测试
# ═══════════════════════════════════════════════════════════════════

class TestElimination(unittest.TestCase):
    """剔除引擎测试"""
    
    def test_mark_junk_files(self):
        """测试垃圾文件标记"""
        from 剔除引擎 import EliminationEngine
        
        nodes = [
            FileNode(full_path="/test/file.txt", file_name="file.txt", extension=".txt"),
            FileNode(full_path="/test/temp.tmp", file_name="temp.tmp", extension=".tmp"),
            FileNode(full_path="/test/log.log", file_name="log.log", extension=".log"),
        ]
        
        engine = EliminationEngine(nodes)
        marked = engine.mark_junk_files()
        
        self.assertEqual(marked, 2)  # .tmp 和 .log 应该被标记
        
        self.assertEqual(nodes[1].action, NodeAction.DELETE)
        self.assertEqual(nodes[2].action, NodeAction.DELETE)


# ═══════════════════════════════════════════════════════════════════
# 执行引擎测试
# ═══════════════════════════════════════════════════════════════════

class TestExecution(unittest.TestCase):
    """执行引擎测试"""
    
    def test_get_trash_path_format(self):
        """测试回收站路径格式"""
        from 执行引擎 import ExecutionEngine
        
        nodes = [FileNode(full_path="/test/file.txt", file_name="file.txt", extension=".txt")]
        engine = ExecutionEngine(nodes, "/test", "/logs", "/trash")
        
        path = engine._get_trash_path("/test/file.txt")
        
        self.assertTrue(path.startswith("/trash"))
        self.assertIn("file_", path)
        self.assertTrue(path.endswith(".txt"))
    
    def test_generate_rollback_script_empty(self):
        """测试空回滚脚本"""
        from 执行引擎 import ExecutionEngine
        
        nodes = []
        engine = ExecutionEngine(nodes, "/test", "/logs", "/trash")
        
        script = engine.generate_rollback_script()
        
        self.assertIn("无需回滚", script)


# ═══════════════════════════════════════════════════════════════════
# 运行测试
# ═══════════════════════════════════════════════════════════════════

def run_tests():
    """运行所有测试"""
    print(f"\n{'='*60}")
    print(f"  文件夹整理工具 V{VERSION} - 单元测试")
    print(f"{'='*60}\n")
    
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # 添加测试类
    test_classes = [
        TestConstants,
        TestUtils,
        TestModels,
        TestSafety,
        TestConfig,
        TestFlatten,
        TestElimination,
        TestExecution,
    ]
    
    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    
    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # 打印摘要
    print(f"\n{'='*60}")
    if result.wasSuccessful():
        print(f"  ✅ 全部测试通过 ({result.testsRun} 个测试)")
    else:
        print(f"  ❌ {len(result.failures)} 个失败, {len(result.errors)} 个错误")
    print(f"{'='*60}\n")
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
