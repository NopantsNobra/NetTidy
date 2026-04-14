"""
交互预览模块
InteractivePreview - 交互式预览与确认
"""

import os
from typing import List, Dict, Optional

from .常量定义 import NodeAction, NodeStatus
from .数据模型 import FileNode
from .工具函数 import _fmt_size


# ═══════════════════════════════════════════════════════════
# InteractivePreview 类
# ═══════════════════════════════════════════════════════════

class InteractivePreview:
    """
    交互式预览器
    提供操作预览和用户确认功能
    """
    
    PAGE_SIZE = 20
    
    def __init__(self, nodes: List[FileNode]):
        """
        初始化交互预览器
        
        Args:
            nodes: 文件节点列表
        """
        self.nodes = nodes
        self._filtered: List[FileNode] = []
    
    def preview(self, action_filter: Optional[NodeAction] = None) -> None:
        """
        显示操作预览
        
        Args:
            action_filter: 操作类型过滤器
        """
        # 过滤节点
        if action_filter:
            self._filtered = [n for n in self.nodes if n.action == action_filter]
        else:
            self._filtered = [n for n in self.nodes if n.action != NodeAction.NONE]
        
        if not self._filtered:
            print("✅ 无操作需要预览")
            return
        
        # 统计
        delete_count = sum(1 for n in self._filtered if n.action == NodeAction.DELETE)
        move_count = sum(1 for n in self._filtered if n.action == NodeAction.MOVE)
        hardlink_count = sum(1 for n in self._filtered if n.action == NodeAction.HARDLINK)
        
        delete_size = sum(n.size_bytes for n in self._filtered if n.action == NodeAction.DELETE)
        hardlink_size = sum(n.size_bytes for n in self._filtered if n.action == NodeAction.HARDLINK)
        
        print("\n" + "=" * 60)
        print("📋 操作预览")
        print("=" * 60)
        print(f"  🗑️ 删除: {delete_count} 项 ({_fmt_size(delete_size)})")
        print(f"  📦 移动: {move_count} 项")
        print(f"  🔗 硬链接: {hardlink_count} 项 ({_fmt_size(hardlink_size)})")
        print(f"  📊 总计: {len(self._filtered)} 项")
        print("=" * 60)
        
        # 分页显示
        self._paginate()
    
    def _paginate(self) -> None:
        """分页显示操作列表"""
        total_pages = (len(self._filtered) + self.PAGE_SIZE - 1) // self.PAGE_SIZE
        current_page = 0
        
        while True:
            start = current_page * self.PAGE_SIZE
            end = min(start + self.PAGE_SIZE, len(self._filtered))
            page_items = self._filtered[start:end]
            
            print(f"\n📄 第 {current_page + 1}/{total_pages} 页")
            print("-" * 60)
            
            for i, n in enumerate(page_items, start + 1):
                action_icon = {
                    NodeAction.DELETE: "🗑️",
                    NodeAction.MOVE: "📦",
                    NodeAction.HARDLINK: "🔗",
                }.get(n.action, "❓")
                
                print(f"{i:4d}. {action_icon} {n.file_name}")
                print(f"      路径: {n.full_path}")
                if n.target_path:
                    print(f"      目标: {n.target_path}")
                print(f"      大小: {_fmt_size(n.size_bytes)}")
                if n.reason:
                    print(f"      原因: {n.reason}")
                print()
            
            # 导航提示
            if total_pages > 1:
                nav = "按 Enter 继续"
                if current_page > 0:
                    nav += " | 'p' 上一页"
                if current_page < total_pages - 1:
                    nav += " | 'n' 下一页"
                nav += " | 'q' 退出"
                
                try:
                    cmd = input(f"\n{nav}: ").strip().lower()
                    if cmd == 'q':
                        break
                    elif cmd == 'p' and current_page > 0:
                        current_page -= 1
                    elif cmd == 'n' and current_page < total_pages - 1:
                        current_page += 1
                    elif cmd == '' and current_page < total_pages - 1:
                        current_page += 1
                    else:
                        # 无效输入，保持当前页
                        pass
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
            else:
                break
    
    def confirm(self, message: str = "确认执行以上操作？") -> bool:
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
                if response in ('y', 'yes', '是'):
                    return True
                elif response in ('n', 'no', '否', ''):
                    return False
                else:
                    print("请输入 y 或 n")
            except (EOFError, KeyboardInterrupt):
                print()
                return False
    
    def select_action(self) -> Optional[str]:
        """
        让用户选择操作
        
        Returns:
            用户选择的操作
        """
        print("\n可选操作:")
        print("  1. 执行所有操作")
        print("  2. 仅执行删除操作")
        print("  3. 仅执行移动操作")
        print("  4. 仅执行硬链接操作")
        print("  5. 取消")
        
        while True:
            try:
                choice = input("请选择 [1-5]: ").strip()
                if choice == '1':
                    return 'all'
                elif choice == '2':
                    return 'delete'
                elif choice == '3':
                    return 'move'
                elif choice == '4':
                    return 'hardlink'
                elif choice == '5':
                    return None
                else:
                    print("请输入 1-5")
            except (EOFError, KeyboardInterrupt):
                print()
                return None
    
    def filter_by_pattern(self, pattern: str) -> List[FileNode]:
        """
        按模式过滤节点
        
        Args:
            pattern: 过滤模式（支持通配符）
            
        Returns:
            匹配的节点列表
        """
        import fnmatch
        
        return [
            n for n in self.nodes
            if fnmatch.fnmatch(n.file_name.lower(), pattern.lower())
        ]
    
    def search(self, keyword: str) -> List[FileNode]:
        """
        搜索节点
        
        Args:
            keyword: 搜索关键词
            
        Returns:
            匹配的节点列表
        """
        keyword_lower = keyword.lower()
        
        return [
            n for n in self.nodes
            if keyword_lower in n.file_name.lower()
            or keyword_lower in n.full_path.lower()
        ]
    
    def show_summary(self) -> None:
        """显示操作摘要"""
        # 按操作类型分组
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
        
        for action, nodes in by_action.items():
            total_size = sum(n.size_bytes for n in nodes)
            action_name = {
                NodeAction.DELETE: "删除",
                NodeAction.MOVE: "移动",
                NodeAction.HARDLINK: "硬链接",
            }.get(action, action.value)
            
            print(f"  {action_name}: {len(nodes)} 项 ({_fmt_size(total_size)})")
        
        print("=" * 60)
    
    def show_duplicates(self, dup_groups: List[Dict]) -> None:
        """
        显示重复文件组
        
        Args:
            dup_groups: 重复文件组列表
        """
        if not dup_groups:
            print("✅ 无重复文件")
            return
        
        print(f"\n🔄 发现 {len(dup_groups)} 组重复文件")
        print("=" * 60)
        
        for g in dup_groups[:10]:
            print(f"\n组 #{g['group_id']} - {_fmt_size(g['size'])}")
            print(f"  保留: {g['keep']['path']}")
            print(f"  删除:")
            for d in g['delete']:
                print(f"    - {d['path']}")
        
        if len(dup_groups) > 10:
            print(f"\n... 还有 {len(dup_groups) - 10} 组未显示")
        
        print("=" * 60)
