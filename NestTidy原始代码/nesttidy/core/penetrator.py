"""穿透判定器：基于穿透深度的节点判定与重组核心算法。

完整实现《精确规则.md》"算法总纲"：

    算法入口：Penetrator.penetrate(execution_dir)
      ↓ 对每个直接子文件夹以 sw=0 独立调用 _resolve()
      ↓ _resolve() 递归执行节点四档分类与处理
      ↓ 第一档：空壳穿透（sw+1，节点消失）
      ↓ 第二档（sw=0）：原地保留 → 收集子结果 → 同生共死检查 → 冲突重命名
      ↓ 第三档：单文件提取（文件夹消失，文件上移）
      ↓ 第四档（sw=0）：原地保留 → 收集子结果 → 冲突重命名
      ↓ 第二/四档（sw>0）：节点标记后向上抛出

【QC修复】：_resolved_file_count / _resolved_dir_count 在判定瞬间固化，
后续 co_extractor 严禁重新遍历 children 计算。
"""
from __future__ import annotations

from typing import List, TYPE_CHECKING

from nesttidy.core.co_extractor import co_extractor
from nesttidy.core.renamer import renamer
from nesttidy.enhance.system_filter import system_filter
from nesttidy.runtime.logger import get_logger

if TYPE_CHECKING:
    from nesttidy.core.node import FileNode

logger = get_logger(__name__)


class Penetrator:
    """目录重组穿透判定器（核心算法实现）。"""

    def penetrate(self, execution_dir: "FileNode") -> "FileNode":
        """对执行目录执行穿透判定，直接修改内存树并返回。

        执行目录的每个直接子文件夹是绝对锚点，以 sw=0 独立启动，互不干扰。
        """
        valid_children = system_filter.filter(execution_dir.children)
        new_children: List["FileNode"] = []

        for child in valid_children:
            if child.is_file:
                # 执行目录下的直接文件，原样保留
                new_children.append(child)
                continue
            # 绝对锚点规则：sw=0，chain=[] 独立启动
            resolved = self._resolve(child, sw=0, chain=[])
            new_children.extend(resolved)

        # 执行目录层级的冲突重命名
        new_children = renamer.rename_conflicts(new_children)
        execution_dir.children = new_children
        return execution_dir

    # ─────────────────────────────────────────────────────────────────
    # 核心递归函数
    # ─────────────────────────────────────────────────────────────────

    def _resolve(
        self, node: "FileNode", sw: int, chain: List[str]
    ) -> List["FileNode"]:
        """对节点执行穿透判定，返回应放置在该节点位置的项目列表。

        返回值语义：
        - [node] with node.sw==0  → 节点原位保留（已递归处理其子树）
        - [node] with node.sw>0   → 节点向上抛出（由 sw=0 祖先的 co_extractor 处理）
        - [file_node]              → 第三档提取的文件（文件夹已消失）
        - 第一档：直接透传子节点的返回值（当前节点消失）
        """
        valid_children = system_filter.filter(node.children)
        f_count = sum(1 for c in valid_children if c.is_file)
        d_count = sum(1 for c in valid_children if not c.is_file)

        # ── [QC修复] 判定瞬间固化属性 ────────────────────────────────
        node._resolved_file_count = f_count
        node._resolved_dir_count = d_count
        node.sw = sw
        node.ancestry_chain = list(chain)

        dirs = [c for c in valid_children if not c.is_file]
        files = [c for c in valid_children if c.is_file]

        # ══════════════════════════════════════════════════════════════
        # 第一档：纯空壳节点（F=0, D=1）
        # ══════════════════════════════════════════════════════════════
        if f_count == 0 and d_count == 1:
            node.tier = 1
            logger.debug(f"[第一档] {node.name!r} sw={sw} → 穿透至子节点")
            # 当前节点消失，将唯一子文件夹作为替身，sw+1
            return self._resolve(dirs[0], sw=sw + 1, chain=chain + [node.name])

        # ══════════════════════════════════════════════════════════════
        # 第三档：单文件叶节点（F=1, D=0）
        # 优先于第四档判定（F=1,D=0 同时满足第四档条件 F≥1）
        # ══════════════════════════════════════════════════════════════
        if f_count == 1 and d_count == 0:
            node.tier = 3
            file_node = files[0]
            # 提取文件，继承血统链（含当前文件夹名，用于重命名回溯）
            file_node.ancestry_chain = chain + [node.name]
            logger.debug(
                f"[第三档] {node.name!r} sw={sw} → 提取文件 {file_node.name!r}"
            )
            return [file_node]

        # ══════════════════════════════════════════════════════════════
        # 第二档：分支型终点（F=0, D≥2）
        # ══════════════════════════════════════════════════════════════
        if f_count == 0 and d_count >= 2:
            node.tier = 2
            if sw > 0:
                logger.debug(f"[第二档↑] {node.name!r} sw={sw} → 向上抛出")
                return [node]
            # sw=0：原地保留，处理子节点，执行同生共死
            logger.debug(f"[第二档✓] {node.name!r} → 原地保留，处理 {d_count} 个子文件夹")
            node.children = self._process_anchor_children(
                node, valid_children, is_tier2=True
            )
            return [node]

        # ══════════════════════════════════════════════════════════════
        # 第四档：文件型终点（F≥2，或 F≥1 且 D≥1）
        # ══════════════════════════════════════════════════════════════
        node.tier = 4
        if sw > 0:
            logger.debug(f"[第四档↑] {node.name!r} sw={sw} → 向上抛出")
            return [node]
        # sw=0：原地保留，处理子节点
        logger.debug(f"[第四档✓] {node.name!r} → 原地保留")
        node.children = self._process_anchor_children(
            node, valid_children, is_tier2=False
        )
        return [node]

    # ─────────────────────────────────────────────────────────────────
    # 辅助：处理 sw=0 节点（第二/四档）的子项
    # ─────────────────────────────────────────────────────────────────

    def _process_anchor_children(
        self,
        node: "FileNode",
        valid_children: List["FileNode"],
        is_tier2: bool,
    ) -> List["FileNode"]:
        """处理一个 sw=0 锚点节点的所有子项。

        流程：
        1. 直接文件原样保留
        2. 每个子文件夹以 sw=0、chain=[] 独立启动 _resolve
        3. 收集所有返回的项目（包括 sw>0 的抛出项）
        4. 若为第二档，执行同生共死检查（co_extractor）
        5. 对同一层级执行冲突重命名
        """
        result: List["FileNode"] = []
        dirs = [c for c in valid_children if not c.is_file]
        files = [c for c in valid_children if c.is_file]

        # 直接文件原样保留
        result.extend(files)

        # 处理每个子文件夹（各自独立，互不干扰）
        for d in dirs:
            # 注意：chain=[] 表示从当前层重新开始血统追踪
            resolved = self._resolve(d, sw=0, chain=[])
            result.extend(resolved)

        # 第二档：同生共死检查
        if is_tier2:
            result = co_extractor.check(node, result)

        # 冲突重命名
        result = renamer.rename_conflicts(result)

        return result


# 模块级单例
penetrator = Penetrator()