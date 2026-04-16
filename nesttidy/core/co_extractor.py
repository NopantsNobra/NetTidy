"""目录结构整理器：基于修订版规则实现穿透、同生共死、冻结与提级。"""
from __future__ import annotations
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from nesttidy.core.node import FileNode

class FolderCleaner:
    """目录结构整理器：彻底剥离无用嵌套，智能处理分叉与提级。"""

    def clean(self, node: "FileNode", is_root: bool = False):
        """
        核心递归清理函数（自底向上）
        :param node: 当前处理的节点
        :param is_root: 是否为根目录锚定点，根目录绝不被穿透剥离
        """
        # 1. 递归清理所有子节点（自底向上，保证处理当前节点时，子节点已是干净状态）
        for child in node.children[:]:  # 使用切片拷贝，防止迭代中修改导致问题
            if not child.is_file:
                self.clean(child, is_root=False)

        # 2. 穿透（剥洋葱）：如果子目录是空壳（0文件1子目录），用孙子目录替换它
        # 注意：根目录绝对不穿透
        if not is_root:
            self._penetrate(node)

        # 3. 同生共死检查（分叉点判断）
        frozen = self._check_co_extraction(node)

        # 4. 单文件提级（只有在未冻结的情况下才执行）
        if not frozen:
            self._extract_single_files(node)

    def _penetrate(self, node: "FileNode"):
        """穿透逻辑：撕掉 0文件1目录 的无用包装纸"""
        changed = True
        while changed:
            changed = False
            i = 0
            while i < len(node.children):
                child = node.children[i]
                if child.is_file:
                    i += 1
                    continue

                # 动态获取子节点的文件和目录数（因为递归可能已改变其内部结构）
                child_files = [c for c in child.children if c.is_file]
                child_dirs = [c for c in child.children if not c.is_file]

                # 符合穿透条件：0个文件，且只有1个子目录
                if len(child_files) == 0 and len(child_dirs) == 1:
                    grandchild = child_dirs[0]
                    # 记录被撕掉的壳，用于溯源防撞车
                    grandchild.ancestry_chain = child.ancestry_chain + [child.name]
                    # 替换掉子目录
                    node.children[i] = grandchild
                    changed = True
                    # 继续检查这个位置，因为 grandchild 可能也是个空壳（连续穿透）
                    continue 
                i += 1

    def _check_co_extraction(self, node: "FileNode") -> bool:
        """
        同生共死检查：处理分叉点
        :return: 是否触发冻结（全留情况返回 True）
        """
        current_files = [c for c in node.children if c.is_file]
        current_dirs = [c for c in node.children if not c.is_file]

        # 触发条件：当前目录无文件，且有两个及以上的子目录（分叉点）
        if len(current_files) == 0 and len(current_dirs) > 1:
            # 检查是否所有子目录都严格满足：只有1个文件，0个子目录
            all_single_file = all(
                len([c for c in d.children if c.is_file]) == 1 and 
                len([c for c in d.children if not c.is_file]) == 0
                for d in current_dirs
            )

            if all_single_file:
                # 情况A：全提。将所有子目录中的单文件扒出来，放到当前目录下
                for d in current_dirs:
                    file_node = next(c for c in d.children if c.is_file)
                    # 记录来源防撞车
                    file_node.ancestry_chain = d.ancestry_chain + [d.name]
                    node.children.append(file_node)
                
                # 删除所有子目录
                node.children = [c for c in node.children if c.is_file]
                return False  # 全提后不冻结
            else:
                # 情况B：全留。触发冻结，禁止后续的单文件提级，保护分类结构
                return True

        return False  # 非分叉点，不冻结

    def _extract_single_files(self, node: "FileNode"):
        """单文件提级：将只有1个文件的子目录壳子扔掉，文件提上来"""
        i = 0
        while i < len(node.children):
            child = node.children[i]
            if child.is_file:
                i += 1
                continue

            child_files = [c for c in child.children if c.is_file]
            child_dirs = [c for c in child.children if not c.is_file]

            # 严格判定：只有1个文件，且没有更深的子目录
            if len(child_files) == 1 and len(child_dirs) == 0:
                file_node = child_files[0]
                # 记录被扔掉的壳
                file_node.ancestry_chain = child.ancestry_chain + [child.name]
                # 提至当前目录（替换该子目录）
                node.children[i] = file_node
                # 不增加 i，因为列表原地修改了，当前位置已经是新提上来的 file_node
                continue 
            
            i += 1

# 模块级单例
folder_cleaner = FolderCleaner()