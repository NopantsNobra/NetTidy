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
    ancestry_chain: List[str] = field(default_factory=list) # 穿透血统链

    # --- 【QC修复】固化统计属性 ---
    _resolved_file_count: int = -1            # -1 表示尚未固化
    _resolved_dir_count: int = -1             # -1 表示尚未固化

    # --- 重复检测标记 ---
    is_duplicate: bool = False
    # 【QC修复】使用 None 表达“无值”语义，与 Optional 类型注解保持一致
    duplicate_of: Optional[str] = None        # 原件的绝对路径字符串

    # --- 重命名结果 ---
    safe_name: str = ""                       # 若非空，则代替 name 作为目标文件名

    def __post_init__(self):
        """确保 path 为 Path 类型。"""
        if isinstance(self.path, str):
            self.path = Path(self.path)

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
    schema_version 用于后续算法平滑升级时的版本兼容判断。
    """
    source_path: Path
    target_path: Path
    # 预留 "move" 用于未来大文件场景的原地重排优化（V1.0 执行层默认强制走 copy）
    action: str               # "copy" | "skip" | "move"(预留)
    action_reason: str        # 操作原因的人类可读描述
    schema_version: str = "1.1"
    rename_reason: Optional[str] = None       # 若发生重命名，记录原因
    file_size: int = 0

    def __post_init__(self):
        """确保路径为 Path 类型。"""
        if isinstance(self.source_path, str):
            self.source_path = Path(self.source_path)
        if isinstance(self.target_path, str):
            self.target_path = Path(self.target_path)
