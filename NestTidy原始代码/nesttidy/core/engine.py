"""重构引擎：串联 扫描→穿透判定→映射表生成 的主调度器。

算法与 IO 彻底分离（开发原则 1）：
  - scan()    → 构建内存树（只读磁盘）
  - analyze() → 穿透判定（纯内存计算，零磁盘 IO）
  - build_map() → 生成映射表（纯内存遍历，零磁盘 IO）
  实际文件操作由 safety/file_executor.py 单独负责。
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from nesttidy.core.node import FileNode, MapEntry
from nesttidy.core.penetrator import penetrator
from nesttidy.core.scanner import Scanner
from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)


class RestructureEngine:
    """重构引擎：提供从源目录路径到操作映射表的完整流水线。

    使用示例::

        engine = (
            RestructureEngine(source=Path("C:/Messy"), target=Path("C:/Clean"), mode="copy")
            .scan()
            .analyze()
            .build_map()
        )
        entries = engine.map  # List[MapEntry]
    """

    def __init__(self, source: Path, target: Path, mode: str = "copy"):
        """
        Args:
            source: 执行目录（待重构的源目录）
            target: 输出目录（重构结果的目标目录）
            mode:   "copy"（保留源文件）| "move"（移动后删除源）
        """
        self.source = source.resolve()
        self.target = target.resolve()
        self.mode = mode
        self._root: FileNode = None
        self._map: List[MapEntry] = []

    # ── 阶段一：扫描 ─────────────────────────────────────────────────

    def scan(self) -> "RestructureEngine":
        """扫描源目录，构建内存树。支持链式调用。"""
        scanner = Scanner(self.source)
        self._root = scanner.scan()
        return self

    # ── 阶段二：穿透判定 ─────────────────────────────────────────────

    def analyze(self) -> "RestructureEngine":
        """对内存树执行穿透判定算法，确定重组结构。支持链式调用。"""
        if self._root is None:
            raise RuntimeError("请先调用 scan() 构建内存树。")
        penetrator.penetrate(self._root)
        return self

    # ── 阶段三：生成映射表 ───────────────────────────────────────────

    def build_map(self) -> "RestructureEngine":
        """将穿透判定结果转换为操作映射表。支持链式调用。"""
        if self._root is None:
            raise RuntimeError("请先调用 scan() 和 analyze()。")
        self._map = []
        self._build_map_recursive(self._root, self.target)
        logger.info(f"映射表生成完毕，共 {len(self._map)} 条操作记录")
        return self

    def _build_map_recursive(
        self, node: FileNode, current_target_dir: Path
    ) -> None:
        """递归遍历处理后的内存树，为每个文件生成 MapEntry。"""
        for child in node.children:
            effective_name = child.effective_name
            target_path = current_target_dir / effective_name

            if child.is_file:
                self._map.append(MapEntry(
                    source_path=child.path,
                    target_path=target_path,
                    action=self.mode,
                    action_reason=self._describe_action(child),
                    rename_reason=(
                        f"冲突重命名: {child.name!r} → {child.safe_name!r}"
                        if child.safe_name else None
                    ),
                    file_size=child.file_size,
                ))
            else:
                # 目录节点：递归进入，不生成 MapEntry（目录按需创建）
                self._build_map_recursive(child, target_path)

    def _describe_action(self, node: FileNode) -> str:
        """生成操作原因的人类可读描述。"""
        parts = []
        if node.tier == 3:
            parts.append("第三档单文件叶节点提取")
        if node.sw > 0:
            parts.append(f"穿透深度 sw={node.sw} 提升")
        if node.ancestry_chain:
            parts.append(f"血统链: {' → '.join(node.ancestry_chain)}")
        return "；".join(parts) if parts else "直接复制"

    # ── 属性访问 ─────────────────────────────────────────────────────

    @property
    def map(self) -> List[MapEntry]:
        """只读访问映射表。"""
        return self._map

    @property
    def root(self) -> FileNode:
        """只读访问内存树根节点。"""
        return self._root