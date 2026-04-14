"""
重组引擎模块
=============
职责：目录结构重组，包括解包、提级、重命名等

对外接口：
    - RestructureEngine  重组引擎类

依赖关系：
    - 数据模型 (FileNode)
    - 常量定义 (枚举)
    - 工具函数 (_sanitize_filename, _fmt_size)
    - 配置管理 (runtime)

使用示例：
    from 重组引擎 import RestructureEngine
    engine = RestructureEngine(nodes, "/project")
    engine.unpack_redundant()
    engine.lift_children("/target_dir")
    stats = engine.get_stats()

版本历史：
    9.0: 结构简化、类型注解完善
    8.0: 初始模块化版本
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import List, Dict, Optional, Set, Any

from .常量定义 import NodeAction
from .数据模型 import FileNode
from .工具函数 import _sanitize_filename, _fmt_size


# ═══════════════════════════════════════════════════════════════════
# RestructureEngine - 重组引擎
# ═══════════════════════════════════════════════════════════════════

class RestructureEngine:
    """
    重组引擎
    
    负责重新组织目录结构：
    - 冗余目录解包（A/A/file.txt → A/file.txt）
    - 子目录提级
    - 冲突解决
    
    使用示例：
        >>> engine = RestructureEngine(nodes, "/project")
        >>> unpacked = engine.unpack_redundant()
        >>> lifted = engine.lift_children("/target/subdir")
        >>> stats = engine.get_stats()
    """
    
    def __init__(
        self,
        nodes: List[FileNode],
        root_path: str,
        config: Optional[Dict] = None,
    ):
        """
        初始化重组引擎
        
        Args:
            nodes: 文件节点列表
            root_path: 根目录路径
            config: 配置字典
        """
        self.nodes = nodes
        self.root_path = os.path.abspath(root_path)
        self.config = config or {}
        
        # 统计
        self._stats = {
            "unpacked": 0,
            "lifted": 0,
            "renamed": 0,
            "conflicts_resolved": 0,
        }
    
    # ═══════════════════════════════════════════════════════════════
    # 冗余目录解包
    # ═══════════════════════════════════════════════════════════════
    
    def unpack_redundant(self) -> int:
        """
        解包冗余目录结构
        
        识别形如 A/A/file.txt 的冗余嵌套，自动合并为 A/file.txt。
        
        Returns:
            解包的目录数量
        """
        unpacked = 0
        
        # 构建目录树
        dir_children: Dict[str, List[FileNode]] = defaultdict(list)
        for node in self.nodes:
            if node.is_dir:
                parent = os.path.dirname(node.full_path)
                dir_children[parent].append(node)
        
        # 构建路径前缀映射
        node_by_path: Dict[str, FileNode] = {}
        for node in self.nodes:
            node_by_path[node.full_path.lower()] = node
        
        # 找出可解包的冗余结构
        # 条件：目录名 == 父目录名
        for parent_lower, children in list(dir_children.items()):
            if parent_lower.lower() == self.root_path.lower():
                continue
            
            parent_name = os.path.basename(parent_lower)
            active_children = [
                c for c in children
                if c.action == NodeAction.NONE and c.is_dir
            ]
            
            if len(active_children) != 1:
                continue
            
            child = active_children[0]
            
            # 检查目录名是否与父目录名相同
            if child.file_name.lower() != parent_name.lower():
                continue
            
            # 解包：提升子项，删除冗余目录
            child_prefix = child.full_path.lower() + os.sep
            
            for node in self.nodes:
                if node.full_path.lower().startswith(child_prefix):
                    if node.action == NodeAction.NONE or node.full_path == child.full_path:
                        continue
                    
                    # 计算新路径
                    rel = os.path.relpath(node.full_path, child.full_path)
                    new_path = os.path.join(parent_lower, rel)
                    
                    node.target_path = new_path
                    node.action = NodeAction.MOVE
                    node.reason = f"解包冗余: {child.file_name}"
                    unpacked += 1
            
            # 标记冗余目录删除
            child.action = NodeAction.DELETE
            child.reason = "冗余目录（子项已提级）"
            unpacked += 1
        
        self._stats["unpacked"] = unpacked
        return unpacked
    
    # ═══════════════════════════════════════════════════════════════
    # 子目录提级
    # ═══════════════════════════════════════════════════════════════
    
    def lift_children(self, target_dir: str) -> int:
        """
        提升子目录到父目录
        
        将目标目录的直属子项提升到父目录，然后删除目标目录。
        
        Args:
            target_dir: 目标目录路径
            
        Returns:
            提升的文件数量
        """
        lifted = 0
        target_lower = target_dir.lower()
        
        # 找到父目录
        parent_dir = os.path.dirname(target_dir)
        
        # 提升直属子项
        for node in self.nodes:
            if not node.full_path.lower().startswith(target_lower + os.sep):
                continue
            
            if node.full_path.lower() == target_lower:
                continue
            
            # 计算相对路径
            rel = os.path.relpath(node.full_path, target_dir)
            
            # 只提升直属子项（不包含子目录的内容）
            if os.sep in rel:
                continue
            
            # 新路径
            new_path = os.path.join(parent_dir, rel)
            
            node.target_path = new_path
            node.action = NodeAction.MOVE
            node.reason = f"提级: {os.path.basename(target_dir)}"
            lifted += 1
        
        # 标记目标目录删除
        for node in self.nodes:
            if node.full_path.lower() == target_lower:
                node.action = NodeAction.DELETE
                node.reason = "目录已提级"
                break
        
        self._stats["lifted"] = lifted
        return lifted
    
    # ═══════════════════════════════════════════════════════════════
    # 冲突解决
    # ═══════════════════════════════════════════════════════════════
    
    def resolve_conflicts(self) -> int:
        """
        解决目标路径冲突
        
        当多个文件有相同的目标路径时，自动添加序号后缀。
        
        Returns:
            解决的冲突数量
        """
        resolved = 0
        target_groups: Dict[str, List[FileNode]] = defaultdict(list)
        
        # 收集有目标路径的节点
        for node in self.nodes:
            if node.action == NodeAction.MOVE and node.target_path:
                key = node.target_path.lower()
                target_groups[key].append(node)
        
        # 解决冲突
        for path_lower, nodes in target_groups.items():
            if len(nodes) <= 1:
                continue
            
            # 第一个保持不变
            for i, node in enumerate(nodes[1:], start=2):
                base, ext = os.path.splitext(node.target_path)
                node.target_path = f"{base}_{i}{ext}"
                resolved += 1
        
        self._stats["conflicts_resolved"] = resolved
        return resolved
    
    def resolve_duplicate_names(self) -> int:
        """
        解决同名文件冲突
        
        在目标目录中，如果存在同名文件，自动添加目录名后缀。
        
        Returns:
            解决的数量
        """
        resolved = 0
        name_counts: Dict[str, List[FileNode]] = defaultdict(list)
        
        # 收集同名文件
        for node in self.nodes:
            if node.action == NodeAction.MOVE and node.target_path:
                target_name = os.path.basename(node.target_path).lower()
                name_counts[target_name].append(node)
        
        # 处理同名
        for name_lower, nodes in name_counts.items():
            if len(nodes) <= 1:
                continue
            
            # 按来源目录分组
            by_dir: Dict[str, List[FileNode]] = defaultdict(list)
            for node in nodes:
                src_dir = os.path.basename(os.path.dirname(node.full_path))
                if not src_dir:
                    src_dir = "root"
                by_dir[src_dir].append(node)
            
            # 第一个保持不变
            first = True
            for dir_name, dir_nodes in by_dir.items():
                for node in dir_nodes:
                    if first:
                        first = False
                        continue
                    
                    base, ext = os.path.splitext(node.target_path)
                    node.target_path = f"{base}_from_{dir_name}{ext}"
                    resolved += 1
        
        self._stats["renamed"] = resolved
        return resolved
    
    # ═══════════════════════════════════════════════════════════════
    # 统计
    # ═══════════════════════════════════════════════════════════════
    
    def get_stats(self) -> Dict[str, Any]:
        """
        获取统计信息
        
        Returns:
            统计字典
        """
        return {
            "unpacked": self._stats["unpacked"],
            "lifted": self._stats["lifted"],
            "renamed": self._stats["renamed"],
            "conflicts_resolved": self._stats["conflicts_resolved"],
            "total": (
                self._stats["unpacked"] +
                self._stats["lifted"] +
                self._stats["renamed"]
            ),
        }
    
    def run(
        self,
        mode: str = "unpack",
        target_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        运行重组流程
        
        Args:
            mode: 重组模式 (unpack/lift/all)
            target_dir: 目标目录（用于 lift 模式）
            
        Returns:
            统计结果
        """
        print(f"\n🔄 重组处理 ({mode}):")
        
        if mode in ("unpack", "all"):
            unpacked = self.unpack_redundant()
            print(f"  解包: {unpacked} 个")
        
        if mode in ("lift", "all") and target_dir:
            lifted = self.lift_children(target_dir)
            print(f"  提级: {lifted} 个")
        
        if mode in ("all",):
            resolved = self.resolve_conflicts()
            print(f"  冲突解决: {resolved} 个")
        
        stats = self.get_stats()
        print(f"\n📊 重组统计:")
        print(f"  总操作: {stats['total']} 项")
        
        return stats
