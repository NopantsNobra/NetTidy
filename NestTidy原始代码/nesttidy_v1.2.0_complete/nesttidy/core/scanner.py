"""目录扫描器：将磁盘目录结构镜像到内存 FileNode 树。 """
from __future__ import annotations
import os
import errno
from pathlib import Path
from typing import Optional, Set

from nesttidy.core.node import FileNode
from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)

class ScanAbortError(RuntimeError):
    """扫描因熔断触发而强制中止。"""

class Scanner:
    """将磁盘目录结构镜像为内存 FileNode 树。"""

    MAX_NODES = 5_000_000  # 硬保护
    # ══════════════════════════════════════════════════
    # 【QC修复2】构建深度防爆栈阈值
    # ══════════════════════════════════════════════════
    MAX_SAFE_DEPTH = 500    

    def __init__(self, source_path: Path):
        self.source = source_path.resolve()
        self.node_count: int = 0
        self.visited_real_paths: Set[Path] = set()

    def scan(self) -> FileNode:
        self.node_count = 0
        self.visited_real_paths = set()
        # 【QC修复1】入口日志脱敏，只打印用户指定的路径，不打印内部解析
        logger.info(f"开始扫描: {self.source}")
        root = self._scan_node(self.source, depth=0)
        
        if root is None:
            raise RuntimeError(f"源目录无法访问或不存在: {self.source.name}")
            
        logger.info(f"扫描完成，共 {self.node_count:,} 个节点")
        return root

    def _get_safe_log_path(self, path: Path) -> str:
        """【QC修复1】生成安全的相对路径用于日志打印，防止泄露绝对路径。"""
        try:
            # 尝试计算相对路径，如果失败（跨盘符等）则只返回文件名
            return str(path.relative_to(self.source))
        except ValueError:
            return f".../{path.name}"

    def _scan_node(self, path: Path, depth: int) -> Optional[FileNode]:
 