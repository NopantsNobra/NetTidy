"""穿透判定器：基于穿透深度的节点判定与重组核心算法。 """
from __future__ import annotations
from typing import List, TYPE_CHECKING
from nesttidy.core.co_extractor import co_extractor
from nesttidy.core.renamer import renamer
from nesttidy.enhance.system_filter import system_filter
from nesttidy.runtime.logger import get_logger

if TYPE_CHECKING:
    from nesttidy.core.node import FileNode

# 安全阈值：防止恶意或极端的套娃目录导致 Python C 栈溢出
MAX_SAFE_DEPTH = 500

logger = get_logger(__name__)

class Penetrator:
    """目录重组穿透判定器（核心算法实现）。"""

    def penetrate(self, execution_dir: "FileNode") -> "FileNode":
        """对执行目录执行穿透判定，直接修改内存树并返回。"""
        valid_children = system_filter.filter(execution_dir.children)
        new_children: List["FileNode"] = []
        
        for child in valid_children:
            if child.is_file:
                new_children.append(child)
                continue
            # 绝对锚点规则：sw=0，chain=[] 独立启动
            resolved = self._resolve(child, sw=0, chain=[])
            new_children.extend(resolved)
            
        # 执行目录层级的冲突重命名
        new_children = renamer.rename_conflicts(new_children)
        execution_dir.children = new_children
        return execution_dir

    def _resolve(
        self, node: "FileNode", sw: int, chain: List[str]
    ) -> List["FileNode"]:
        """对节点执行穿透判定，返回应放置在该节点位置的项目列表。"""
        valid_children = system_filter.filter(node.children)
        f_count = sum(1 for c in valid_children if c.is_file)
        d_count = sum(1 for c in valid_children if not c.is_file)

        # ── 判定瞬间固化属性 ────────────────────────────────
        node._resolved_file_count = f_count
        node._resolved_dir_count = d_count
        node.sw = sw
        node.ancestry_chain = list(chain)

        dirs = [c for c in valid_children if not c.is_file]
        files = [c for c in valid_children if c.is_file]

        # ══════════════════════════════════════════════════
        # 【QC修复0】防爆栈拦截
        # ══════════════════════════════════════════════════
        if sw > MAX_SAFE_DEPTH:
            logger.warning(f"[安全拦截] 检测到超过 {MAX_SAFE_DEPTH} 层深嵌套，强制保留: {node.name!r}")
            node.tier = 4 # 强制作为终点
            if sw > 0: return [node]
            node.children = self._process_anchor_children(node, valid_children, is_tier2=False)
            return [node]

        # ══════════════════════════════════════════════════
        # 【QC修复1】空节点拦截
        # ══════════════════════════════════════════════════
        if f_count == 0 and d_count == 0:
            node.tier = 0
            logger.debug(f"[空节点] {node.name!r} → 返回空")
            return []

        # ══════════════════════════════════════════════════
        # 第一档：纯空壳节点（F=0, D=1）
        # ══════════════════════════════════════════════════
        if f_count == 0 and d_count == 1:
            node.tier = 1
            logger.debug(f"[第一档] {node.name!r} sw={sw} → 穿透至子节点")
            return self._resolve(dirs[0], sw=sw + 1, chain=chain + [node.name])

        # ══════════════════════════════════════════════════
        # 第三档：单文件叶节点（F=1, D=0）
        # ══════════════════════════════════════════════════
        if f_count == 1 and d_count == 0:
            node.tier = 3
            file_node = files[0]
            file_node.ancestry_chain = chain + [node.name]
            logger.debug(f"[第三档] {node.name!r} sw={sw} → 提取文件 {file_node.name!r}")
            return [file_node]

        # ══════════════════════════════════════════════════
        # 第二档：分支型终点（F=0, D≥2）
        # ══════════════════════════════════════════════════
        if f_count == 0 and d_count >= 2:
            node.tier = 2
            if sw > 0:
                logger.debug(f"[第二档↑] {node.name!r} sw={sw} → 向上抛出")
                return [node]
            logger.debug(f"[第二档✓] {node.name!r} → 原地保留，处理 {d_count} 个子文件夹")
            node.children = self._process_anchor_children(node, valid_children, is_tier2=True)
            return [node]

        # ══════════════════════════════════════════════════
        # 第四档：文件型终点（F≥2，或 F≥1 且 D≥1）
        # ══════════════════════════════════════════════════
        node.tier = 4
        if sw > 0:
            logger.debug(f"[第四档↑] {node.name!r} sw={sw} → 向上抛出")
            return [node]
        logger.debug(f"[第四档✓] {node.name!r} → 原地保留")
        node.children = self._process_anchor_children(node, valid_children, is_tier2=False)
        return [node]

    def _process_anchor_children(
        self, node: "FileNode", valid_children: List["FileNode"], is_tier2: bool,
    ) -> List["FileNode"]:
        """处理一个 sw=0 锚点节点的所有子项。"""
        result: List["FileNode"] = []
        dirs = [c for c in valid_children if not c.is_file]
        files = [c for c in valid_children if c.is_file]

        result.extend(files)

        for d in dirs:
            resolved = self._resolve(d, sw=0, chain=[])
            result.extend(resolved)

        if is_tier2:
            result = co_extractor.check(node, result)

        result = renamer.rename_conflicts(result)
        return result

# 模块级单例
penetrator = Penetrator()
