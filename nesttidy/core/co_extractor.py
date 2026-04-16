"""同生共死检查器：分支型终点（第二档）下的子项提取判定。 """
from __future__ import annotations
from typing import List, TYPE_CHECKING
from nesttidy.runtime.logger import get_logger

if TYPE_CHECKING:
    from nesttidy.core.node import FileNode

logger = get_logger(__name__)

class CoExtractor:
    """同生共死检查器：决定是否提取分支下的所有单文件叶子节点。"""

    def check(self, parent_node: "FileNode", children: List["FileNode"]) -> List["FileNode"]:
        """检查并决定是否提取子项。"""
        # 筛选出 sw>0 且第四档的节点（这些是"向上抛出"的黑盒子）
        extract_pool = [
            c for c in children 
            if not c.is_file and c.sw > 0 and c.tier == 4
        ]

        if not extract_pool:
            return children

        # ══════════════════════════════════════════════════
        # 【致命修复】必须同时校验文件数和目录数
        # 防止 f=1, d=1 的第四档黑盒被错误提取，导致子目录被静默丢弃
        # ══════════════════════════════════════════════════
        can_extract = all(
            c._resolved_file_count == 1 and c._resolved_dir_count == 0 
            for c in extract_pool
        )

        if not can_extract:
            logger.debug(f"[同生共死] {parent_node.name!r} 存在非单文件子项或黑盒，不提取")
            return children

        logger.debug(f"[同生共死] {parent_node.name!r} 全部子项为单文件叶，提取 {len(extract_pool)} 个文件")
        
        extracted_files: List["FileNode"] = []
        kept_children: List["FileNode"] = []

        # 使用 id() 进行精确的内存对象匹配，防止极低概率的 __eq__ 重载问题
        pool_ids = {id(c) for c in extract_pool}

        for child in children:
            if id(child) in pool_ids:
                file_node = self._extract_single_file(child)
                if file_node:
                    extracted_files.append(file_node)
                    logger.debug(f"[同生共死] 提取: {file_node.name!r} (来自 {child.name!r})")
            else:
                kept_children.append(child)

        return kept_children + extracted_files

    def _extract_single_file(self, node: "FileNode") -> "FileNode | None":
        """从第四档节点中提取唯一的文件节点。"""
        for child in node.children:
            if child.is_file:
                # 继承父节点的血统链（加上父节点名）
                child.ancestry_chain = node.ancestry_chain + [node.name]
                child.sw = node.sw
                return child
        return None

# 模块级单例
co_extractor = CoExtractor()
