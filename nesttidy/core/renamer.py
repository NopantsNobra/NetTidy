"""冲突重命名器：统一溯源前缀法。 """
from __future__ import annotations
import uuid
import hashlib
from collections import defaultdict
from typing import List, TYPE_CHECKING

from nesttidy.utils.path import names_equal
from nesttidy.core.sanitizer import sanitize_filename  # 引入清洗器
from nesttidy.runtime.logger import get_logger

if TYPE_CHECKING:
    from nesttidy.core.node import FileNode

logger = get_logger(__name__)

class Renamer:
    """统一溯源前缀法冲突重命名器。"""

    def rename_conflicts(self, items: List["FileNode"]) -> List["FileNode"]:
        """检测同一层级的命名冲突，并为每个冲突项生成带前缀的 safe_name。 """
        groups: defaultdict[str, List["FileNode"]] = defaultdict(list)
        for item in items:
            # 兼容安全：确保 effective_name 存在，且链表可迭代
            eff_name = getattr(item, 'effective_name', None) or item.name
            groups[eff_name.lower()].append(item)

        for name_lower, group in groups.items():
            if len(group) < 2:
                continue
                
            logger.debug(f"[重命名] 检测到 {len(group)} 个同名冲突: {name_lower!r}")
            
            for item in group:
                prefix = self._find_first_different_ancestor(item, group)
                
                # ══════════════════════════════════════════════════
                # 【QC修复1】前缀必须先清洗，防止非法字符污染拼接结果
                # ══════════════════════════════════════════════════
                safe_prefix = sanitize_filename(prefix)
                
                # 拼接原始名称
                raw_new_name = f"{safe_prefix}-{item.name}"
                
                # ══════════════════════════════════════════════════
                # 【QC修复1续】拼接后的整体必须再次清洗（处理截断、保留名、尾部空格等）
                # ══════════════════════════════════════════════════
                item.safe_name = sanitize_filename(raw_new_name)
                
                logger.debug(f"[重命名] {item.name!r} → {item.safe_name!r} (前缀: {safe_prefix!r})")

        return items

    def _find_first_different_ancestor(
        self, item: "FileNode", conflict_items: List["FileNode"]
    ) -> str:
        """在血统链中找到第一个能区分当前项与所有其他冲突项的祖先名称。 """
        # ══════════════════════════════════════════════════
        # 【QC修复3】防空链表防御
        # ══════════════════════════════════════════════════
        my_chain = item.ancestry_chain or []

        for i, ancestor_name in enumerate(my_chain):
            for other in conflict_items:
                if other is item:
                    continue
                other_chain = other.ancestry_chain or []
                
                if i >= len(other_chain) or not names_equal(ancestor_name, other_chain[i]):
                    return ancestor_name

        # ══════════════════════════════════════════════════════════════
        # 【QC修复2】绝对确定性兜底：优先用物理路径哈希，绝对不用纯随机 UUID
        # ══════════════════════════════════════════════════════════════
        abs_path_str = str(getattr(item, 'absolute_path', ''))
        if abs_path_str:
            # 使用绝对路径的 MD5 前 4 位作为确定性区分码
            path_hash = hashlib.md5(abs_path_str.encode('utf-8')).hexdigest()[:4]
            fallback = f"dup_{path_hash}"
            logger.warning(
                f"[重命名兜底] 冲突项 {item.name!r} 的血统链完全一致，"
                f"使用路径哈希前缀: {fallback!r}"
            )
            return fallback

        # 极端中的极端：连绝对路径都没有（理论上不可能发生在真实文件上）
        fallback = f"unnamed_{uuid.uuid4().hex[:4]}"
        logger.error(
            f"[重命名兜底] 冲突项 {item.name!r} 无血统且无绝对路径，触发随机前缀: {fallback!r}"
        )
        return fallback

# 模块级单例
renamer = Renamer()
