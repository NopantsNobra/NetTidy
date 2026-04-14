"""
扁平化引擎模块
FlattenEngine - 目录结构扁平化处理
"""

import os
from typing import List, Dict, Tuple

from .常量定义 import NodeAction
from .数据模型 import FileNode
from .工具函数 import _sanitize_filename, _truncate_name


# ═══════════════════════════════════════════════════════════
# FlattenEngine 类
# ═══════════════════════════════════════════════════════════

class FlattenEngine:
    """
    扁平化引擎
    负责将深层嵌套的目录结构扁平化，包括：
    - 单子目录解包
    - 冗余目录合并
    - 文件提升
    """
    
    def __init__(self, nodes: List[FileNode], root_path: str):
        """
        初始化扁平化引擎
        
        Args:
            nodes: 文件节点列表
            root_path: 根目录路径
        """
        self.nodes = nodes
        self.root_path = os.path.abspath(root_path)
        self._stats = {
            "unpacked": 0,
            "lifted": 0,
            "merged": 0,
        }
    
    def unpack_single_child_dirs(self) -> int:
        """
        解包单子目录结构
        例如: a/b/c/file.txt -> a/file.txt (如果 b 和 c 都是单子目录)
        
        Returns:
            解包的目录数量
        """
        unpacked = 0
        
        # 构建目录树
        dir_children: Dict[str, List[FileNode]] = {}
        for n in self.nodes:
            if n.is_dir:
                parent = os.path.dirname(n.full_path)
                if parent not in dir_children:
                    dir_children[parent] = []
                dir_children[parent].append(n)
        
        # 找出单子目录
        changed = True
        while changed:
            changed = False
            
            for parent, children in list(dir_children.items()):
                # 过滤掉已标记删除的
                active_children = [c for c in children if c.action == NodeAction.NONE]
                
                if len(active_children) == 1:
                    child = active_children[0]
                    
                    # 检查是否为单子目录（只有一个子目录）
                    if child.is_dir and child.full_path in dir_children:
                        grandchildren = [
                            gc for gc in dir_children[child.full_path]
                            if gc.action == NodeAction.NONE
                        ]
                        
                        if len(grandchildren) == 1 and grandchildren[0].is_dir:
                            # 标记中间目录删除
                            child.action = NodeAction.DELETE
                            child.reason = "冗余中间目录"
                            unpacked += 1
                            changed = True
        
        self._stats["unpacked"] = unpacked
        return unpacked
    
    def lift_children(self, target_dir: str) -> int:
        """
        提升子文件到父目录
        
        Args:
            target_dir: 目标目录路径
            
        Returns:
            提升的文件数量
        """
        lifted = 0
        parent_dir = os.path.dirname(target_dir)
        prefix = target_dir.lower() + os.sep
        
        for n in self.nodes:
            if n.full_path.lower().startswith(prefix) and n.action == NodeAction.NONE:
                rel = os.path.relpath(n.full_path, target_dir)
                if os.sep not in rel:  # 直接子项
                    n.target_path = os.path.join(parent_dir, rel)
                    n.action = NodeAction.MOVE
                    lifted += 1
        
        # 标记目标目录删除
        for n in self.nodes:
            if n.full_path.lower() == target_dir.lower():
                n.action = NodeAction.DELETE
                n.reason = "目录已提升"
                break
        
        self._stats["lifted"] = lifted
        return lifted
    
    def merge_empty_dirs(self) -> int:
        """
        合并空目录
        
        Returns:
            合并的目录数量
        """
        merged = 0
        
        # 找出空目录
        dir_files: Dict[str, int] = {}
        for n in self.nodes:
            if not n.is_dir:
                parent = os.path.dirname(n.full_path)
                while parent and parent.startswith(self.root_path):
                    dir_files[parent] = dir_files.get(parent, 0) + 1
                    parent = os.path.dirname(parent)
        
        for n in self.nodes:
            if n.is_dir and n.action == NodeAction.NONE:
                file_count = dir_files.get(n.full_path, 0)
                if file_count == 0:
                    n.action = NodeAction.DELETE
                    n.reason = "空目录"
                    merged += 1
        
        self._stats["merged"] = merged
        return merged
    
    def flatten_to_depth(self, max_depth: int) -> int:
        """
        扁平化到指定深度
        
        Args:
            max_depth: 最大深度
            
        Returns:
            移动的文件数量
        """
        moved = 0
        root_depth = self.root_path.count(os.sep)
        
        for n in self.nodes:
            if n.is_dir or n.action != NodeAction.NONE:
                continue
            
            current_depth = n.depth
            if current_depth > max_depth:
                # 生成扁平化文件名
                rel_path = os.path.relpath(n.full_path, self.root_path)
                flat_name = self._generate_flat_name(rel_path)
                
                n.target_path = os.path.join(self.root_path, flat_name)
                n.action = NodeAction.MOVE
                n.reason = f"扁平化: 深度 {current_depth} -> {max_depth}"
                moved += 1
        
        return moved
    
    def _generate_flat_name(self, rel_path: str) -> str:
        """
        生成扁平化文件名
        
        Args:
            rel_path: 相对路径
            
        Returns:
            扁平化文件名
        """
        # 将路径分隔符替换为下划线
        parts = rel_path.split(os.sep)
        if len(parts) == 1:
            return parts[0]
        
        # 保留扩展名
        name, ext = os.path.splitext(parts[-1])
        
        # 组合路径部分
        prefix = "_".join(parts[:-1])
        flat_name = f"{prefix}_{name}{ext}"
        
        # 清理文件名
        flat_name = _sanitize_filename(flat_name)
        
        # 截断过长的文件名
        flat_name = _truncate_name(flat_name, 200)
        
        return flat_name
    
    def get_stats(self) -> Dict:
        """
        获取统计信息
        
        Returns:
            统计字典
        """
        return self._stats.copy()
    
    def run(self, unpack: bool = True, merge_empty: bool = True) -> Dict:
        """
        运行完整的扁平化流程
        
        Args:
            unpack: 是否解包单子目录
            merge_empty: 是否合并空目录
            
        Returns:
            统计结果
        """
        if unpack:
            unpacked = self.unpack_single_child_dirs()
            print(f"  📦 解包单子目录: {unpacked} 个")
        
        if merge_empty:
            merged = self.merge_empty_dirs()
            print(f"  📁 合并空目录: {merged} 个")
        
        return self.get_stats()
