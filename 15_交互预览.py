"""
交互预览模块
=============
职责：交互式操作预览与确认

对外接口：
    - InteractivePreview  交互式预览器

依赖关系：
    - 数据模型 (FileNode)
    - 常量定义 (枚举)
    - 工具函数 (_fmt_size)

使用示例：
    from 交互预览 import InteractivePreview
    preview = InteractivePreview(nodes)
    preview.preview()
    if preview.confirm("确认执行?"):
        ...

版本历史：
    9.0: 分页显示、搜索过滤
    8.0: 初始模块化版本
"""

from __future__ import annotations

import fnmatch
from typing import List, Dict, Optional, Set, Callable

from .常量定义 import NodeAction, NodeStatus
from .数据模型 import FileNode
from .工具函数 import _fmt_size


# ═══════════════════════════════════════════════════════════════════
# InteractivePreview
# ═══════════════════════════════════════════════════════════════════

class InteractivePreview:
    """
    交互式预览器
    
    提供操作预览、用户确认、分类选择等功能。
    
    使用示例：
        >>> preview = InteractivePreview(nodes)
        >>> preview.preview()
        >>> preview.show_summary()
        >>> if preview.confirm():
        ...     return True
    """
    
    PAGE_SIZE = 20
    """每页显示数量"""
    
    def __init__(self, nodes: List[FileNode]):
        """
        初始化交互预览器
        
        Args:
            nodes: 文件节点列表
        """
        self.nodes = nodes
        self._filtered: List[FileNode] = []
        self._categories: Dict[str, Dict] = {}
    
    def preview(
        self,
        action_filter: Optional[NodeAction] = None,
    ) -> None:
        """
        显示操作预览
        
        Args:
            action_filter: 操作类型过滤器
        """
        self._filtered = self._get_filtered_nodes(action_filter)
        
        if not self._filtered:
            print("✅ 无操作需要预览")
            return
        
        self._print_summary()
        self._paginate()
    
    def _get_filtered_nodes(
        self,
        action_filter: Optional[NodeAction] = None,
    ) -> List[FileNode]:
        """获取过滤后的节点"""
        if action_filter:
            return [n for n in self.nodes if n.action == action_filter]
        return [n for n in self.nodes if n.action != NodeAction.NONE]
    
    def _print_summary(self) -> None:
        """打印摘要"""
        delete_nodes = [n for n in self._filtered if n.action == NodeAction.DELETE]
        move_nodes = [n for n in self._filtered if n.action == NodeAction.MOVE]
        hardlink_nodes = [n for n in self._filtered if n.action == NodeAction.HARDLINK]
        
        delete_size = sum(n.size_bytes for n in delete_nodes)
        hardlink_size = sum(n.size_bytes for n in hardlink_nodes)
        
        print("\n" + "=" * 60)
        print("📋 操作预览")
        print("=" * 60)
        print(f"  🗑️ 删除: {len(delete_nodes)} 项 ({_fmt_size(delete_size)})")
        print(f"  📦 移动: {len(move_nodes)} 项")
        print(f"  🔗 硬链接: {len(hardlink_nodes)} 项")
        print(f"  📊 总计: {len(self._filtered)} 项")
        print("=" * 60)
    
    def _paginate(self) -> None:
        """分页显示"""
        total_pages = max(1, (len(self._filtered) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        current = 0
        
        while True:
            start = current * self.PAGE_SIZE
            end = min(start + self.PAGE_SIZE, len(self._filtered))
            page_items = self._filtered[start:end]
            
            print(f"\n📄 第 {current + 1}/{total_pages} 页")
            print("-" * 60)
            
            for i, n in enumerate(page_items, start + 1):
                icon = self._get_action_icon(n.action)
                print(f"{i:4d}. {icon} {n.file_name}")
                print(f"      路径: {n.full_path}")
                if n.target_path:
                    print(f"      目标: {n.target_path}")
                print(f"      大小: {_fmt_size(n.size_bytes)}")
                if n.reason:
                    print(f"      原因: {n.reason[:50]}")
                print()
            
            # 导航
            if total_pages <= 1:
                break
            
            nav_parts = []
            if current > 0:
                nav_parts.append("'p' 上一页")
            if current < total_pages - 1:
                nav_parts.append("'n' 下一页")
            nav_parts.append("'q' 退出")
            
            try:
                cmd = input(f"  {' | '.join(nav_parts)}: ").strip().lower()
                
                if cmd == 'q':
                    break
                elif cmd == 'p' and current > 0:
                    current -= 1
                elif cmd == 'n' and current < total_pages - 1:
                    current += 1
                elif cmd == '' and current < total_pages - 1:
                    current += 1
            except (EOFError, KeyboardInterrupt):
                print()
                break
    
    def _get_action_icon(self, action: NodeAction) -> str:
        """获取操作图标"""
        icons = {
            NodeAction.DELETE: "🗑️",
            NodeAction.MOVE: "📦",
            NodeAction.HARDLINK: "🔗",
            NodeAction.RENAME: "✏️",
            NodeAction.COPY: "📋",
        }
        return icons.get(action, "❓")
    
    def show_summary(self) -> None:
        """显示操作摘要"""
        by_action: Dict[NodeAction, List[FileNode]] = {}
        for n in self.nodes:
            if n.action == NodeAction.NONE:
                continue
            if n.action not in by_action:
                by_action[n.action] = []
            by_action[n.action].append(n)
        
        if not by_action:
            print("✅ 无操作需要执行")
            return
        
        print("\n📊 操作摘要")
        print("=" * 60)
        
        action_names = {
            NodeAction.DELETE: "删除",
            NodeAction.MOVE: "移动",
            NodeAction.HARDLINK: "硬链接",
            NodeAction.RENAME: "重命名",
        }
        
        for action, nodes in by_action.items():
            total_size = sum(n.size_bytes for n in nodes)
            name = action_names.get(action, action.value)
            print(f"  {name}: {len(nodes)} 项 ({_fmt_size(total_size)})")
        
        print("=" * 60)
    
    def confirm(self, message: str = "确认执行以上操作?") -> bool:
        """
        请求用户确认
        
        Args:
            message: 确认消息
            
        Returns:
            是否确认
        """
        print()
        while True:
            try:
                response = input(f"❓ {message} [y/N]: ").strip().lower()
                
                if response in ('y', 'yes', '是', 'y'):
                    return True
                elif response in ('n', 'no', '否', '', 'n'):
                    return False
                else:
                    print("请输入 y 或 n")
            except (EOFError, KeyboardInterrupt):
                print()
                return False
    
    def select_categories(self) -> Dict[str, bool]:
        """
        让用户选择操作类别
        
        Returns:
            类别选择字典
        """
        categories = self._categorize()
        
        if not categories:
            return {}
        
        print("\n" + "=" * 60)
        print("🎯 交互式操作选择")
        print("=" * 60)
        print("输入要包含的操作编号 (逗号分隔, 如 1,3)")
        print("  all → 全部  |  none → 全部禁用  |  回车 → 默认全选")
        print("-" * 60)
        
        keys = list(categories.keys())
        for i, key in enumerate(keys, 1):
            cat = categories[key]
            status = "✅" if cat["enabled"] else "❌"
            print(f"  [{i}] {status} {cat['label']}")
        
        print("-" * 60)
        
        while True:
            try:
                choice = input("\n请选择: ").strip().lower()
                
                if not choice:
                    return {k: True for k in keys}
                
                if choice == "all":
                    return {k: True for k in keys}
                
                if choice == "none":
                    return {k: False for k in keys}
                
                try:
                    selected = [int(x.strip()) for x in choice.split(",")]
                    result = {}
                    for k in keys:
                        result[k] = False
                    for s in selected:
                        if 1 <= s <= len(keys):
                            result[keys[s - 1]] = True
                    return result
                except ValueError:
                    print("⚠️ 输入无效，请重试")
            except (EOFError, KeyboardInterrupt):
                return {}
    
    def _categorize(self) -> Dict[str, Dict]:
        """将操作分类"""
        categories: Dict[str, Dict] = {}
        
        # 缓存目录
        cache_nodes = [
            n for n in self.nodes
            if n.action == NodeAction.DELETE and "缓存" in n.reason
        ]
        if cache_nodes:
            cache_size = sum(n.size_bytes for n in cache_nodes)
            categories["cache"] = {
                "label": f"缓存目录清理 ({len(cache_nodes)} 项, {_fmt_size(cache_size)})",
                "nodes": cache_nodes,
                "enabled": True,
            }
        
        # 垃圾文件
        junk_nodes = [
            n for n in self.nodes
            if n.action == NodeAction.DELETE and "垃圾" in n.reason
        ]
        if junk_nodes:
            junk_size = sum(n.size_bytes for n in junk_nodes)
            categories["junk"] = {
                "label": f"垃圾文件清理 ({len(junk_nodes)} 项, {_fmt_size(junk_size)})",
                "nodes": junk_nodes,
                "enabled": True,
            }
        
        # 重复文件
        dup_nodes = [
            n for n in self.nodes
            if n.action in (NodeAction.DELETE, NodeAction.HARDLINK) and "重复" in n.reason
        ]
        if dup_nodes:
            dup_size = sum(n.size_bytes for n in dup_nodes if n.action == NodeAction.DELETE)
            categories["dup"] = {
                "label": f"重复文件处理 ({len(dup_nodes)} 项, {_fmt_size(dup_size)})",
                "nodes": dup_nodes,
                "enabled": True,
            }
        
        # 扁平化
        flatten_nodes = [
            n for n in self.nodes
            if n.action == NodeAction.MOVE and "扁平化" in n.reason
        ]
        if flatten_nodes:
            categories["flatten"] = {
                "label": f"扁平化移动 ({len(flatten_nodes)} 项)",
                "nodes": flatten_nodes,
                "enabled": True,
            }
        
        return categories
    
    def apply_selection(self, selection: Dict[str, bool]) -> None:
        """根据用户选择调整节点"""
        categories = self._categorize()
        enabled_paths: Set[str] = set()
        
        for key, enabled in selection.items():
            if enabled and key in categories:
                for n in categories[key]["nodes"]:
                    enabled_paths.add(n.full_path)
        
        for n in self.nodes:
            if n.action != NodeAction.NONE and n.full_path not in enabled_paths:
                n.action = NodeAction.NONE
                n.target_path = ""
                n.reason = ""
    
    def search(self, keyword: str) -> List[FileNode]:
        """
        搜索节点
        
        Args:
            keyword: 搜索关键词
            
        Returns:
            匹配的节点
        """
        keyword_lower = keyword.lower()
        return [
            n for n in self.nodes
            if keyword_lower in n.file_name.lower()
            or keyword_lower in n.full_path.lower()
            or keyword_lower in n.reason.lower()
        ]
    
    def filter_by_pattern(self, pattern: str) -> List[FileNode]:
        """
        按模式过滤
        
        Args:
            pattern: 文件名模式
            
        Returns:
            匹配的节点
        """
        return [
            n for n in self.nodes
            if fnmatch.fnmatch(n.file_name.lower(), pattern.lower())
        ]


# ═══════════════════════════════════════════════════════════════════
# 导出
# ═══════════════════════════════════════════════════════════════════

__all__ = [
    "InteractivePreview",
]
