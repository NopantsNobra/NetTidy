"""
文件夹整理工具 V8.0
模块化文件夹结构智能重组引擎

功能特性:
- 目录诊断与分析
- 缓存文件清理
- 重复文件检测与去重
- 冗余目录结构解包
- 文件扁平化重组
- Windows 系统回收站集成
- 安全检查与权限修复
"""

__version__ = "8.0.0"
__author__ = "FolderRestructure Team"
__all__ = [
    # 常量与枚举
    "VERSION", "IS_WINDOWS", "NodeAction", "NodeStatus", "ReparseType",
    "SafetyLevel", "PermissionAction",
    # 数据模型
    "FileNode", "VirtualFileNode", "OperationLog", "ErrorEntry",
    # 核心引擎
    "DiagnosticEngine", "EliminationEngine", "FlattenEngine",
    "RestructureEngine", "ExecutionEngine", "PermissionEngine",
    # 工具类
    "SafetyChecker", "LockManager", "ErrorCollector",
    "Win32IO", "RecycleBin", "ZoneIdentifierHandler",
    # 报告与交互
    "HTMLReportGenerator", "InteractivePreview",
    # 主入口
    "FolderRestructureEngine", "main",
]

from .常量定义 import (
    VERSION, IS_WINDOWS, NodeAction, NodeStatus, ReparseType,
    SafetyLevel, PermissionAction,
)
from .数据模型 import FileNode, VirtualFileNode, OperationLog, ErrorEntry
from .诊断引擎 import DiagnosticEngine
from .剔除引擎 import EliminationEngine
from .扁平化引擎 import FlattenEngine
from .重组引擎 import RestructureEngine
from .执行引擎 import ExecutionEngine
from .权限引擎 import PermissionEngine
from .安全检查 import SafetyChecker, LockManager, ErrorCollector
from .Win32底层IO import Win32IO, RecycleBin
from .区域标识处理 import ZoneIdentifierHandler
from .报告生成 import HTMLReportGenerator
from .交互预览 import InteractivePreview
from .命令行接口 import FolderRestructureEngine, main
