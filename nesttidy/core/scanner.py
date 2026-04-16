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
        safe_log_path = self._get_safe_log_path(path)

        # ── 熔断检查 ────────────────────────────────────────────
        self.node_count += 1
        if self.node_count > self.MAX_NODES:
            raise ScanAbortError(f"节点数超过 {self.MAX_NODES:,} 安全阈值，强制中止。")

        # ══════════════════════════════════════════════════
        # 【QC修复2】深度防爆栈拦截
        # ══════════════════════════════════════════════════
        if depth > self.MAX_SAFE_DEPTH:
            logger.error(f"[安全拦截] 目录嵌套超过 {self.MAX_SAFE_DEPTH} 层，停止展开: {safe_log_path}")
            # 返回一个空的目录节点，防止上层树结构断裂
            return FileNode(name=path.name, path=path, is_file=False, file_size=0)

        # ── 符号链接循环检测 ────────────────────────────────────
        try:
            real_path = path.resolve(strict=False)
            if real_path in self.visited_real_paths:
                logger.warning(f"检测到符号链接循环，已跳过: {safe_log_path}")
                return None
            self.visited_real_paths.add(real_path)
        except Exception as exc:
            logger.warning(f"无法解析真实路径，已跳过: {safe_log_path} — {exc}")
            return None

        # ── 构建当前节点 ──────────────────────────────────────
        try:
            stat = path.stat()
            is_file = path.is_file()
        except PermissionError:
            logger.warning(f"权限不足，已跳过: {safe_log_path}")
            return None
        except OSError as exc:
            # ══════════════════════════════════════════════════
            # 【QC修复3】特判 Windows 超长路径错误
            # ══════════════════════════════════════════════════
            if hasattr(exc, 'winerror') and exc.winerror == 123:
                logger.error(f"路径过长(>260字符)被系统拒绝，已跳过: {safe_log_path}")
            else:
                logger.warning(f"无法访问路径，已跳过: {safe_log_path} — {exc}")
            return None

        node = FileNode(
            name=path.name,
            path=path,
            is_file=is_file,
            file_size=stat.st_size if is_file else 0,
        )

        if is_file:
            return node

        # ── 目录：递归扫描子项 ────────────────────────────────
        try:
            entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
        except PermissionError:
            logger.warning(f"无权读取目录内容，已跳过内部文件: {safe_log_path}")
            return node
        except OSError as exc:
            logger.warning(f"读取目录失败，已跳过内部文件: {safe_log_path} — {exc}")
            return node

        for entry in entries:
            child = self._scan_node(Path(entry.path), depth + 1)
            if child is not None:
                node.children.append(child)

        return node
