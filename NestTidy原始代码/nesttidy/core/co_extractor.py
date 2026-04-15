"""同生共死检查器：分支型终点（第二档）下的子项提取判定。

《精确规则.md》第4节规则摘要：
  触发条件：sw=0 的第二档节点（分支型终点）
  判定逻辑：
    - 若所有子文件夹均为"单文件叶"（第四档且sw>0），则全部提取到当前层级
    - 若存在任一子文件夹含≥2个文件，则全部保留，不提取
  提取后：若文件撞名，立即执行溯源重命名

【QC修复】：严格依赖 _resolved_file_count 固化属性，杜绝二次遍历 children。
"""
from __future__ import annotations

from typing import List, TYPE_CHECKING

from nesttidy.runtime.logger import get_logger

if TYPE_CHECKING:
    from nesttidy.core.node import FileNode

logger = get_logger(__name__)


class CoExtractor:
    """同生共死检查器：决定是否提取分支下的所有单文件叶子节点。"""

    def check(self, parent_node: "FileNode", children: List["FileNode"]) -> List["FileNode"]:
        """检查并决定是否提取子项。

        Args:
            parent_node: 第二档分支型终点节点（sw=0）
            children: 当前层级所有子项（包括直接文件和已处理的子文件夹结果）

        Returns:
            处理后的子项列表（可能包含提取上来的文件）
        """
        # 筛选出 sw>0 且第四档的节点（这些是"向上抛出"的候选）
        extract_pool = [
            c for c in children
            if not c.is_file and c.sw > 0 and c.tier == 4
        ]

        if not extract_pool:
            # 没有需要提取的节点，直接返回
            return children

        # ══════════════════════════════════════════════════════════════
        # 【QC致命修复】必须使用判定时固化的属性，绝不能用 len([ch for ch in c.children...])
        # ══════════════════════════════════════════════════════════════
        can_extract = all(
            c._resolved_file_count == 1 for c in extract_pool
        )

        if not can_extract:
            # 存在非单文件的子项，全部保留
            logger.debug(
                f"[同生共死] {parent_node.name!r} 存在非单文件子项，不提取"
            )
            return children

        # ══════════════════════════════════════════════════════════════
        # 执行提取：将每个第四档节点中的唯一文件提取到当前层级
        # ══════════════════════════════════════════════════════════════
        logger.debug(
            f"[同生共死] {parent_node.name!r} 全部子项为单文件叶，提取 {len(extract_pool)} 个文件"
        )

        extracted_files: List["FileNode"] = []
        kept_children: List["FileNode"] = []

        for child in children:
            if child in extract_pool:
                # 找到该节点中的唯一文件
                file_node = self._extract_single_file(child)
                if file_node:
                    extracted_files.append(file_node)
                    logger.debug(
                        f"[同生共死] 提取: {file_node.name!r} (来自 {child.name!r})"
                    )
            else:
                kept_children.append(child)

        # 返回：保留的子项 + 提取的文件
        return kept_children + extracted_files

    def _extract_single_file(self, node: "FileNode") -> "FileNode | None":
        """从第四档节点中提取唯一的文件节点。

        文件继承父节点的血统链（用于后续冲突重命名）。
        """
        for child in node.children:
            if child.is_file:
                # 继承父节点的血统链（加上父节点名）
                child.ancestry_chain = node.ancestry_chain + [node.name]
                child.sw = node.sw
                return child
        return None


# 模块级单例
co_extractor = CoExtractor()
