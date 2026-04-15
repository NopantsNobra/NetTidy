"""核心引擎模块：提供目录重构的核心算法。

模块：
  - node: 核心数据结构（FileNode, MapEntry）
  - scanner: 目录扫描器
  - penetrator: 穿透判定器
  - co_extractor: 同生共死检查器
  - renamer: 冲突重命名器
  - engine: 重构引擎（主调度器）
"""
from nesttidy.core.node import FileNode, MapEntry
from nesttidy.core.scanner import Scanner, ScanAbortError
from nesttidy.core.penetrator import penetrator
from nesttidy.core.co_extractor import co_extractor
from nesttidy.core.renamer import renamer
from nesttidy.core.engine import RestructureEngine

__all__ = [
    "FileNode",
    "MapEntry",
    "Scanner",
    "ScanAbortError",
    "penetrator",
    "co_extractor",
    "renamer",
    "RestructureEngine",
]
