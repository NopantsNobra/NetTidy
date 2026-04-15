"""冲突重命名器：统一溯源前缀法。

《精确规则.md》第5节规则摘要：
  触发条件：同一层级出现 ≥2 个同名的项（文件或文件夹）
  解决逻辑：沿穿透血统链向上回溯，找到第一个与其他冲突项名称不同的祖先，
            用其名称作前缀：<祖先名>-<原名>
  注意：发生冲突的双方均须执行重命名，不保留原始未加前缀的名称。

【QC致命修复】：处理 ancestry_chain 完全一致的极端情况，
使用随机 UUID 短前缀兜底，防止静默文件覆盖。
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import List, TYPE_CHECKING

from nesttidy.utils.path import names_equal
from nesttidy.runtime.logger import get_logger

if TYPE_CHECKING:
    from nesttidy.core.node import FileNode

logger = get_logger(__name__)


class Renamer:
    """统一溯源前缀法冲突重命名器。"""

    def rename_conflicts(self, items: List["FileNode"]) -> List["FileNode"]:
        """检测同一层级的命名冲突，并为每个冲突项生成带前缀的 safe_name。

        使用 effective_name（优先 safe_name，否则 name）进行比较，
        以正确处理已被先前轮次重命名的项目。
        """
        # 按小写 effective_name 分组
        groups: defaultdict[str, List["FileNode"]] = defaultdict(list)
        for item in items:
            groups[item.effective_name.lower()].append(item)

        for name_lower, group in groups.items():
            if len(group) < 2:
                continue
            logger.debug(
                f"[重命名] 检测到 {len(group)} 个同名冲突: {name_lower!r}"
            )
            for item in group:
                prefix = self._find_first_different_ancestor(item, group)
                # 冲突双方均须重命名，不保留原始名称
                item.safe_name = f"{prefix}-{item.name}"
                logger.debug(
                    f"[重命名] {item.name!r} → {item.safe_name!r} (前缀: {prefix!r})"
                )

        return items

    def _find_first_different_ancestor(
        self, item: "FileNode", conflict_items: List["FileNode"]
    ) -> str:
        """在血统链中找到第一个能区分当前项与所有其他冲突项的祖先名称。

        逐位比较 ancestry_chain：若某一位上，当前项与至少一个其他项的祖先名不同，
        则该祖先名可用作前缀。
        """
        for i, ancestor_name in enumerate(item.ancestry_chain):
            for other in conflict_items:
                if other is item:
                    continue
                # 若 other 在此位置没有祖先，或祖先名不同 → 此祖先可区分
                if i >= len(other.ancestry_chain) or not names_equal(
                    ancestor_name, other.ancestry_chain[i]
                ):
                    return ancestor_name

        # ── [QC致命修复] 极端兜底 ────────────────────────────────────
        # 所有冲突项的 ancestry_chain 完全一致（或均为空）时，无法找到区分祖先。
        # 使用随机 UUID 短前缀防止静默文件覆盖。
        fallback = f"unnamed_{uuid.uuid4().hex[:4]}"
        logger.error(
            f"[重命名兜底] 冲突项 {item.name!r} 的血统链与其他项完全一致，"
            f"无法找到区分祖先。触发随机前缀兜底: {fallback!r}"
        )
        return fallback


# 模块级单例
renamer = Renamer()