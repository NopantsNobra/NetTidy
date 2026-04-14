"""
文件夹整理工具 V9.0
====================
模块化文件夹结构智能重组引擎

功能特性:
- 目录诊断与分析
- 缓存文件清理
- 重复文件检测与去重
- 冗余目录结构解包
- 文件扁平化重组
- Windows 系统回收站集成
- 安全检查与权限修复

适用平台: Windows 7+ / 离线分析模式支持所有平台
运行要求: Python 3.9+
"""

__version__ = "9.0.0"
__author__ = "FolderRestructure Team"

# 公开 API
__all__ = [
    # 核心引擎
    "DiagnosticEngine",
    "EliminationEngine",
    "FlattenEngine",
    "RestructureEngine",
    "ExecutionEngine",
    "PermissionEngine",
    # 安全与工具
    "SafetyChecker",
    "LockManager",
    "ErrorCollector",
    "Win32IO",
    "RecycleBin",
    "ZoneIdentifierHandler",
    # 报告与交互
    "HTMLReportGenerator",
    "InteractivePreview",
    "OfflineAnalyzer",
    # 主编排器
    "FolderRestructureEngine",
    # 入口
    "main",
]

# 导入核心组件
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
from .离线分析 import OfflineAnalyzer

from .命令行接口 import FolderRestructureEngine, main
