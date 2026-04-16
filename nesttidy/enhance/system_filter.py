"""系统文件过滤器：在所有判定中将系统文件视为不存在。

《精确规则.md》基础定义：
- 系统文件：.DS_Store、desktop.ini、Thumbs.db
- 有效内容：剔除系统文件后，目录下剩余的文件与子文件夹
- 有效子文件夹：内部（含任意深层级）存在至少 1 个非系统文件
"""
from __future__ import annotations

from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from nesttidy.core.node import FileNode

# 系统文件名称集合（统一小写匹配）
_SYSTEM_NAMES: frozenset = frozenset({
    ".ds_store",
    "desktop.ini",
    "thumbs.db",
})


class SystemFilter:
    """将系统文件从有效内容中剔除。"""

    def is_system_file(self, name: str) -> bool:
        """判断一个文件名是否为系统文件。"""
        return name.lower() in _SYSTEM_NAMES

    def filter(self, nodes: List["FileNode"]) -> List["FileNode"]:
        """过滤掉列表中的系统文件节点，返回有效内容列表。"""
        return [n for n in nodes if not (n.is_file and self.is_system_file(n.name))]

    def has_real_content(self, node: "FileNode") -> bool:
        """判断一个目录节点（含任意深层级）是否存在至少 1 个非系统文件。

        这是"有效子文件夹"($D_{valid}$) 的判定依据。
        """
        if node.is_file:
            return not self.is_system_file(node.name)
        for child in node.children:
            if self.has_real_content(child):
                return True
        return False


# 模块级单例（全局共享，避免重复实例化）
system_filter = SystemFilter()