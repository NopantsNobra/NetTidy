"""数据模型定义"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TreeNode:
    """目录树节点"""
    name: str
    path: str  # 相对于扫描根目录的路径，根节点为 ""
    is_dir: bool
    children: list = field(default_factory=list)
    size: int = 0
    is_frozen: bool = False
    is_empty: bool = False
    source_path: str = ""  # 原始路径（R2/R3 移动前），用于 R5 祖先追溯

    @property
    def file_count(self) -> int:
        """直接子文件数"""
        return sum(1 for c in self.children if not c.is_dir)

    @property
    def dir_children(self) -> list:
        """直接子目录列表"""
        return [c for c in self.children if c.is_dir]

    @property
    def file_children(self) -> list:
        """直接子文件列表"""
        return [c for c in self.children if not c.is_dir]

    @property
    def child_count(self) -> int:
        """直接子目录数"""
        return len(self.dir_children)

    @property
    def is_shell(self) -> bool:
        """壳 = 0 个文件 + 恰好 1 个子文件夹"""
        return self.is_dir and self.file_count == 0 and self.child_count == 1

    @property
    def is_branching(self) -> bool:
        """分叉口 = 0 个文件 + 2 个及以上子文件夹"""
        return self.is_dir and self.file_count == 0 and self.child_count >= 2

    @property
    def is_single_file_dir(self) -> bool:
        """恰好只有 1 个文件、没有子文件夹"""
        return self.is_dir and self.file_count == 1 and self.child_count == 0

    @property
    def total_items(self) -> int:
        """递归统计全部文件数"""
        count = 0
        for c in self.children:
            if c.is_dir:
                count += c.total_items
            else:
                count += 1
        return count

    @property
    def total_dirs(self) -> int:
        """递归统计全部目录数"""
        count = 0
        for c in self.children:
            if c.is_dir:
                count += 1 + c.total_dirs
        return count
