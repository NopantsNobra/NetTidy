"""目录扫描器：将磁盘目录结构镜像到内存 FileNode 树。

修复清单（TM / QC 审查）：
- [TM] 500万节点熔断器（MAX_NODES 硬保护，不可配置）
- [QC] 符号链接循环检测（visited_real_paths 集合）
- 防御性处理：权限不足、路径超长、文件占用均有明确日志与跳过逻辑
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Set

from nesttidy.core.node import FileNode
from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)


class ScanAbortError(RuntimeError):
    """扫描因熔断触发而强制中止。"""


class Scanner:
    """将磁盘目录结构镜像为内存 FileNode 树。

    使用方式::

        scanner = Scanner(Path("C:/MyFolder"))
        root = scanner.scan()
    """

    MAX_NODES = 5_000_000  # TM审查要求：绝对安全阈值，不可配置（硬保护）

    def __init__(self, source_path: Path):
        self.source = source_path
        self.node_count: int = 0
        self.visited_real_paths: Set[Path] = set()  # QC修复：防符号链接死循环

    def scan(self) -> FileNode:
        """扫描 source_path，返回根 FileNode 树。"""
        self.node_count = 0
        self.visited_real_paths = set()
        logger.info(f"开始扫描: {self.source}")
        root = self._scan_node(self.source, depth=0)
        logger.info(f"扫描完成，共 {self.node_count:,} 个节点")
        return root

    def _scan_node(self, path: Path, depth: int) -> Optional[FileNode]:
        """递归扫描单个节点，返回 FileNode 或 None（跳过时）。"""

        # ── [TM修复] 熔断检查 ─────────────────────────────────────────
        self.node_count += 1
        if self.node_count > self.MAX_NODES:
            raise ScanAbortError(
                f"目录节点数超过 {self.MAX_NODES:,}（500万）安全阈值，"
                "为防止内存溢出已强制中止扫描。"
            )

        # ── [QC修复] 符号链接循环检测 ─────────────────────────────────
        try:
            real_path = path.resolve(strict=False)
            if real_path in self.visited_real_paths:
                logger.warning(f"检测到符号链接循环或重复访问，已跳过: {path}")
                return None
            self.visited_real_paths.add(real_path)
        except Exception as exc:
            logger.warning(f"无法解析真实路径，已跳过: {path} — {exc}")
            return None

        # ── 构建当前节点 ──────────────────────────────────────────────
        try:
            stat = path.stat()
            is_file = path.is_file()
        except PermissionError:
            logger.warning(f"权限不足，已跳过: {path}")
            return None
        except OSError as exc:
            logger.warning(f"无法访问路径，已跳过: {path} — {exc}")
            return None

        node = FileNode(
            name=path.name,
            path=path,
            is_file=is_file,
            file_size=stat.st_size if is_file else 0,
        )

        if is_file:
            return node

        # ── 目录：递归扫描子项 ────────────────────────────────────────
        try:
            entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
        except PermissionError:
            logger.warning(f"无权读取目录内容，已跳过: {path}")
            return node
        except OSError as exc:
            logger.warning(f"读取目录失败，已跳过: {path} — {exc}")
            return node

        for entry in entries:
            child = self._scan_node(Path(entry.path), depth + 1)
            if child is not None:
                node.children.append(child)

        return node