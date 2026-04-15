"""重复文件检测器：基于文件大小 + MD5 哈希的两阶段检测，避免全量哈希计算。"""
from __future__ import annotations
from collections import defaultdict
from typing import Dict, List, TYPE_CHECKING

from nesttidy.utils.hash import file_md5
from nesttidy.runtime.logger import get_logger

if TYPE_CHECKING:
    from nesttidy.core.node import FileNode

logger = get_logger(__name__)


class DupDetector:
    """两阶段重复文件检测器。"""

    def detect(self, root: "FileNode") -> Dict[str, List["FileNode"]]:
        """检测整棵文件树中的重复文件。"""
        # 阶段一：按文件大小分组（O(n) 无 IO）
        size_groups: defaultdict[int, List["FileNode"]] = defaultdict(list)
        self._collect_files(root, size_groups)

        # 阶段二：对同大小文件计算 MD5 确认
        dup_groups: Dict[str, List["FileNode"]] = {}
        for _size, nodes in size_groups.items():
            if len(nodes) < 2:
                continue
                
            md5_groups: defaultdict[str, List["FileNode"]] = defaultdict(list)
            for node in nodes:
                try:
                    h = file_md5(node.path)
                    md5_groups[h].append(node)
                except OSError as exc:
                    # ══════════════════════════════════════════════════
                    # 【QC修复】日志脱敏：异常信息中抹除绝对路径前缀
                    # ══════════════════════════════════════════════════
                    logger.warning(f"无法计算哈希，已跳过: {node.path.name} — {exc}")

            for h, group in md5_groups.items():
                if len(group) >= 2:
                    dup_groups[h] = group
                    # 第一个视为"原件"，其余标记为重复
                    for node in group[1:]:
                        node.is_duplicate = True
                        # ⚠️ 安全提示：此处存储了绝对路径用于底层执行器定位文件。
                        # 若此字段后续被用于生成人类可读报告，必须在报告层（如 diff_generator）进行相对化脱敏！
                        node.duplicate_of = str(group[0].path)

        logger.info(f"重复检测完成，发现 {len(dup_groups)} 组重复文件")
        return dup_groups

    def _collect_files(
        self,
        node: "FileNode",
        groups: "defaultdict[int, List[FileNode]]",
    ) -> None:
        """递归收集所有文件节点，按大小分组。"""
        if node.is_file:
            groups[node.file_size].append(node)
        else:
            for child in node.children:
                self._collect_files(child, groups)
