"""变更摘要写入器：生成人类可读的变更摘要文件。

输出格式：
  - changes.txt: 纯文本格式，便于快速查看
  - 包含操作类型、源路径、目标路径、重命名原因等信息
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from nesttidy.core.node import MapEntry
from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)


class ChangesWriter:
    """变更摘要写入器。"""

    def __init__(
        self,
        include_timestamp: bool = True,
        include_summary: bool = True,
        max_path_display: int = 80,
    ):
        """
        Args:
            include_timestamp: 是否包含时间戳
            include_summary: 是否包含统计摘要
            max_path_display: 路径显示最大长度（超出截断）
        """
        self.include_timestamp = include_timestamp
        self.include_summary = include_summary
        self.max_path_display = max_path_display

    def write(
        self,
        entries: List[MapEntry],
        source: Path,
        target: Path,
        output_path: Path,
    ) -> None:
        """写入变更摘要文件。

        Args:
            entries: 映射表条目列表
            source: 源目录路径
            target: 目标目录路径
            output_path: 输出文件路径
        """
        lines: List[str] = []

        # 文件头
        lines.append("=" * 60)
        lines.append("理巢 — 目录重构变更摘要")
        lines.append("=" * 60)
        lines.append("")

        if self.include_timestamp:
            lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append("")

        lines.append(f"源目录: {source}")
        lines.append(f"目标目录: {target}")
        lines.append("")

        # 统计摘要
        if self.include_summary:
            summary = self._generate_summary(entries)
            lines.extend(summary)
            lines.append("")

        # 详细变更列表
        lines.append("-" * 60)
        lines.append("变更详情")
        lines.append("-" * 60)
        lines.append("")

        for i, entry in enumerate(entries, 1):
            entry_lines = self._format_entry(i, entry, source, target)
            lines.extend(entry_lines)

        # 写入文件
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        logger.info(f"变更摘要已写入: {output_path}")

    def _generate_summary(self, entries: List[MapEntry]) -> List[str]:
        """生成统计摘要。"""
        total_files = len(entries)
        total_size = sum(e.file_size for e in entries)

        copy_count = sum(1 for e in entries if e.action == "copy")
        move_count = sum(1 for e in entries if e.action == "move")

        renamed_count = sum(1 for e in entries if e.rename_reason)

        lines = [
            "统计摘要:",
            f"  总文件数: {total_files:,}",
            f"  总大小: {self._format_size(total_size)}",
            f"  复制操作: {copy_count:,}",
            f"  移动操作: {move_count:,}",
            f"  重命名文件: {renamed_count:,}",
        ]

        return lines

    def _format_entry(
        self,
        index: int,
        entry: MapEntry,
        source: Path,
        target: Path,
    ) -> List[str]:
        """格式化单个条目。"""
        lines = []

        # 操作类型标签
        action_label = {
            "copy": "[复制]",
            "move": "[移动]",
            "cross_vol_move": "[跨卷移动]",
        }.get(entry.action, f"[{entry.action}]")

        # 源路径（相对路径）
        try:
            src_rel = entry.source_path.relative_to(source)
        except ValueError:
            src_rel = entry.source_path

        # 目标路径（相对路径）
        try:
            tgt_rel = entry.target_path.relative_to(target)
        except ValueError:
            tgt_rel = entry.target_path

        # 格式化输出
        lines.append(f"{index:04d}. {action_label} {self._truncate_path(str(src_rel))}")

        # 如果路径有变化，显示目标路径
        if str(src_rel) != str(tgt_rel):
            lines.append(f"      → {self._truncate_path(str(tgt_rel))}")

        # 重命名原因
        if entry.rename_reason:
            lines.append(f"      ({entry.rename_reason})")

        # 操作原因
        if entry.action_reason:
            lines.append(f"      原因: {entry.action_reason}")

        lines.append("")

        return lines

    def _truncate_path(self, path: str) -> str:
        """截断过长的路径。"""
        if len(path) <= self.max_path_display:
            return path

        # 保留文件名，截断中间路径
        parts = path.split("/")
        if len(parts) <= 2:
            return path[:self.max_path_display - 3] + "..."

        filename = parts[-1]
        available = self.max_path_display - len(filename) - 4  # 留出 ".../" 的空间

        if available > 0:
            return path[:available] + ".../" + filename
        else:
            return "..." + filename[-(self.max_path_display - 3):]

    @staticmethod
    def _format_size(size: int) -> str:
        """格式化文件大小。"""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"


# 模块级单例
changes_writer = ChangesWriter()
