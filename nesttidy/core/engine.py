"""重构引擎：串联 扫描→穿透判定→映射表生成 的主调度器。"""
from __future__ import annotations
from pathlib import Path
from typing import List, Tuple

from nesttidy.core.node import FileNode, MapEntry
from nesttidy.core.penetrator import penetrator
from nesttidy.core.scanner import Scanner
from nesttidy.core.sanitizer import sanitize_filename

from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)

# 合法的执行模式白名单
VALID_MODES = {"copy", "move"}


class RestructureEngine:
    """重构引擎：提供从源目录路径到操作映射表的完整流水线。"""

    def __init__(self, source: Path, target: Path, mode: str = "copy"):
        self.source = source.resolve()
        self.target = target.resolve()
        if mode not in VALID_MODES:
            raise ValueError(f"不支持的执行模式: {mode!r}，仅允许 {VALID_MODES}")
        self.mode = mode
        self._root: FileNode = None
        self._map: List[MapEntry] = []

    def scan(self) -> "RestructureEngine":
        """扫描阶段：遍历源目录，构建内存文件树。"""
        try:
            scanner = Scanner(self.source)
            self._root = scanner.scan()
        except Exception as exc:
            # ══════════════════════════════════════════════════
            # 【QC修复】捕获底层异常，附加阶段上下文后重新抛出
            # __cause__ 链保证原始异常堆栈不丢失
            # ══════════════════════════════════════════════════
            raise RuntimeError(
                f"重构引擎: 扫描阶段失败 — {exc}"
            ) from exc
        return self

    def analyze(self) -> "RestructureEngine":
        """分析阶段：对内存树执行穿透判定。"""
        if self._root is None:
            raise RuntimeError("请先调用 scan() 构建内存树。")
        try:
            penetrator.penetrate(self._root)
        except Exception as exc:
            # ══════════════════════════════════════════════════
            # 【QC修复】同上，附加阶段上下文
            # ══════════════════════════════════════════════════
            raise RuntimeError(
                f"重构引擎: 穿透分析阶段失败 — {exc}"
            ) from exc
        return self

    def build_map(self) -> "RestructureEngine":
        """映射阶段：将分析结果转换为可执行的操作映射表。"""
        if self._root is None:
            raise RuntimeError("请先调用 scan() 和 analyze()。")
        self._map = []

        # 显式栈迭代替代递归，彻底杜绝深树炸栈
        stack: List[Tuple[FileNode, Path]] = [(self._root, self.target)]

        while stack:
            node, current_target_dir = stack.pop()
            for child in node.children:
                raw_name = child.effective_name

                # 最后防线：强制清洗与路径穿越拦截
                safe_final_name = sanitize_filename(raw_name)
                target_path = current_target_dir / safe_final_name

                if not target_path.is_relative_to(current_target_dir):
                    logger.error(
                        f"[安全拦截] 检测到路径穿越尝试: {raw_name!r}，已强制重命名为安全名称"
                    )
                    safe_final_name = sanitize_filename(f"unsafe_{child.name}")
                    target_path = current_target_dir / safe_final_name

                if child.is_file:
                    self._map.append(MapEntry(
                        source_path=child.path,
                        target_path=target_path,
                        action=self.mode,
                        action_reason=self._describe_action(child),
                        rename_reason=(
                            f"冲突重命名: {child.name!r} → {child.safe_name!r}"
                            if child.safe_name else None
                        ),
                        file_size=child.file_size,
                    ))
                else:
                    stack.append((child, target_path))

        logger.info(f"映射表生成完毕，共 {len(self._map)} 条操作记录")
        return self

    def _describe_action(self, node: FileNode) -> str:
        """生成操作原因的人类可读描述。"""
        parts = []
        if node.tier == 3:
            parts.append("第三档单文件叶节点提取")
        if node.sw > 0:
            parts.append(f"穿透深度 sw={node.sw} 提升")
        if node.ancestry_chain:
            parts.append(f"血统链: {' → '.join(node.ancestry_chain)}")
        return "；".join(parts) if parts else "直接复制"

    @property
    def map(self) -> List[MapEntry]:
        return self._map

    @property
    def root(self) -> FileNode:
        return self._root
