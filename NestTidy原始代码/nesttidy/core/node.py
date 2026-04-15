"""核心数据结构：FileNode（内存镜像节点）与 MapEntry（映射表条目）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class FileNode:
    """内存中的文件树节点（磁盘目录结构的完整镜像）。

    设计原则（参见开发手册 1.2）：
    - 算法与 IO 彻底分离：所有重组逻辑只操作此内存结构，不直接操作磁盘。
    - _resolved_* 属性由 Penetrator 在判定瞬间固化，后续任何模块严禁修改。
    """
    name: str
    path: Path
    is_file: bool
    file_size: int = 0
    children: List["FileNode"] = field(default_factory=list)

    # --- 算法运行时动态字段 ---
    sw: int = 0                              # 穿透深度（shells_walked）
    tier: int = 0                            # 节点档位 1-4（0 表示尚未判定）
    ancestry_chain: List[str] = field(default_factory=list)  # 穿透血统链（用于冲突重命名）

    # --- 【QC修复】固化统计属性 ---
    # 在判定出档位的瞬间赋值，后续同生共死机制严禁再次遍历 children 计算
    _resolved_file_count: int = -1   # -1 表示尚未固化
    _resolved_dir_count: int = -1    # -1 表示尚未固化

    # --- 重复检测标记 ---
    is_duplicate: bool = False
    duplicate_of: str = ""           # 原件的绝对路径字符串

    # --- 重命名结果 ---
    safe_name: str = ""              # 若非空，则代替 name 作为目标文件名

    @property
    def effective_name(self) -> str:
        """实际使用的名称（优先 safe_name）。"""
        return self.safe_name if self.safe_name else self.name

    def __repr__(self) -> str:
        kind = "F" if self.is_file else "D"
        return f"FileNode({kind}, {self.name!r}, tier={self.tier}, sw={self.sw})"


@dataclass
class MapEntry:
    """映射表条目，描述一次文件操作的完整信息。

    schema_version 用于后续算法平滑升级时的版本兼容判断（TM修复）。
    """
    source_path: Path
    target_path: Path
    action: str              # "copy" | "move"
    action_reason: str       # 操作原因的人类可读描述
    schema_version: str = "1.1"
    rename_reason: Optional[str] = None  # 若发生重命名，记录原因
    file_size: int = 0