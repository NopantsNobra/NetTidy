# -*- coding: utf-8 -*-
"""
文件夹重组工具 V7.0 — 执行引擎模块
包含：ExecutionEngine、FileListParser、Offline 离线引擎组、
      FolderRestructureEngine（主编排器）、print_offline_report
"""

import json
import os
import re
import shutil
import stat
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .公共定义 import (
    _fmt_size, _sanitize_filename, _truncate_name, _win_path,
    IS_WINDOWS, Win32IO,
    MAX_WORKERS, TRASH_DIR,
    JUNK_FILE_PATTERNS, CACHE_DIR_PATTERNS, VERSION,
)
from .数据模型 import (
    ErrorCollector, FileNode, VirtualFileNode,
    NodeAction, NodeStatus, OperationLog, ReparseType,
    SafetyLevel, PermissionAction,
)
from .扫描引擎 import DiagnosticEngine, PermissionEngine, SafetyChecker, ZoneIdentifierHandler
from .智能处理 import EliminationEngine, FlattenEngine, RestructureEngine

# ============================================================
# 执行引擎 — 统一阶段编排
# ============================================================

class ExecutionEngine:
    def __init__(self, nodes: List[FileNode], root_path: str,
                 log_dir: str, trash_dir: str):
        self.nodes = nodes
        self.root_path = root_path
        self.log_dir = log_dir
        self.trash_dir = trash_dir
        self.errors = ErrorCollector()
        self.op_logs: List[OperationLog] = []
        self._seq = 0
        self._scan_timestamp: float = 0
        self._lock = threading.Lock()
        self.stats: Dict[str, int] = {
            "success": 0, "vanished": 0, "changed": 0,
            "locked": 0, "skipped": 0, "conflict": 0,
            "failed": 0, "trashed": 0,
        }
        self._throttle = 0

    def set_scan_timestamp(self, ts: float) -> None:
        self._scan_timestamp = ts

    def execute(self, dry_run: bool = True) -> Dict[str, int]:
        """执行所有待处理操作"""
        os.makedirs(self.log_dir, exist_ok=True)
        os.makedirs(self.trash_dir, exist_ok=True)

        moved = [n for n in self.nodes if n.action == NodeAction.MOVE]
        deleted = [n for n in self.nodes if n.action == NodeAction.DELETE]
        total = len(moved) + len(deleted)

        if total == 0:
            print("✅ 无需执行任何操作")
            return self.stats

        mode_label = "🔍 预览模式" if dry_run else "⚡ 执行模式"
        print(f"\n{mode_label}: 共 {total} 项操作 (移动 {len(moved)} / 删除 {len(deleted)})")

        if not dry_run and total > 100:
            print(f"  使用 {MAX_WORKERS} 线程并行处理...\n")

        # Phase 1: 权限/属性修复（由外部 fix_permissions() 单独调用，此处跳过）
        # Phase 2: 移动操作（并行）
        print("  Phase 2: 移动操作...")
        self._parallel_process(moved, self._process_move, dry_run, "移动")

        # Phase 3: 删除操作（并行）
        print("  Phase 3: 删除操作...")
        self._parallel_process(deleted, self._process_delete, dry_run, "删除")

        if not dry_run:
            print("  Phase 4: 空目录清理...")
            self._cleanup_empty_dirs()
            self._write_log()
            self._rotate_logs()

        return self.stats

    def _parallel_process(self, nodes: List[FileNode], handler, dry_run: bool, label: str) -> None:
        """并行处理一组节点（移动或删除）"""
        if not nodes:
            return

        # dry-run 或小批量：直接串行处理，避免线程开销
        if dry_run or len(nodes) <= 20:
            for node in nodes:
                handler(node, dry_run)
            return

        # 并行处理
        completed = [0]
        progress_lock = threading.Lock()

        def _safe_handler(node: FileNode) -> None:
            try:
                handler(node, dry_run)
                self._maybe_throttle()
                with progress_lock:
                    completed[0] += 1
                    if completed[0] % 200 == 0:
                        print(f"    {label}进度: {completed[0]}/{len(nodes)}")
            except Exception as e:
                with self._lock:
                    self.errors.add(node.full_path, f"{label}Failed", str(e))
                    self.stats["failed"] += 1

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(_safe_handler, node) for node in nodes]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    pass  # 已在 _safe_handler 中处理

    def _process_move(self, node: FileNode, dry_run: bool) -> None:
        """处理单个移动操作"""
        if not Win32IO.exists(node.full_path):
            with self._lock:
                node.status = NodeStatus.VANISHED
                self.stats["vanished"] += 1
            return

        if self._scan_timestamp:
            try:
                st = Win32IO.safe_stat(node.full_path)
                if st and st.st_mtime > self._scan_timestamp:
                    with self._lock:
                        node.status = NodeStatus.CHANGED
                        self.stats["changed"] += 1
                    return
            except OSError:
                pass

        target = node.target_path
        if not target:
            return

        if dry_run:
            with self._lock:
                node.status = NodeStatus.SUCCESS
                self.stats["success"] += 1
            return

        target_dir = os.path.dirname(target)
        os.makedirs(target_dir, exist_ok=True)

        for attempt in range(3):
            try:
                if Win32IO.exists(target):
                    target = self._resolve_conflict(target)
                    node.target_path = target

                if not Win32IO.move(node.full_path, target):
                    src_wp = _win_path(node.full_path) if Win32IO.exists(node.full_path) else node.full_path
                    dst_wp = _win_path(target)
                    shutil.copy2(src_wp, dst_wp)
                    Win32IO.delete_file(node.full_path)

                with self._lock:
                    self._log_op("MOVE", node.full_path, target)
                    node.status = NodeStatus.SUCCESS
                    self.stats["success"] += 1
                return
            except OSError:
                if attempt < 2:
                    time.sleep(0.5)  # 重试间隔 500ms
                    continue
                with self._lock:
                    node.status = NodeStatus.LOCKED
                    node.error_msg = "移动失败"
                    self.stats["locked"] += 1
                    self.errors.add(node.full_path, "MoveFailed", "IO错误")

    def _process_delete(self, node: FileNode, dry_run: bool) -> None:
        """处理单个删除操作"""
        if not Win32IO.exists(node.full_path):
            with self._lock:
                node.status = NodeStatus.VANISHED
                self.stats["vanished"] += 1
            return

        if dry_run:
            with self._lock:
                node.status = NodeStatus.SUCCESS
                self.stats["success"] += 1
            return

        for attempt in range(3):
            try:
                # 缩小 TOCTOU 竞态窗口：移动前再次确认文件存在
                if not Win32IO.exists(node.full_path):
                    with self._lock:
                        node.status = NodeStatus.VANISHED
                        self.stats["vanished"] += 1
                    return

                trash_target = self._get_trash_path(node.full_path)
                os.makedirs(os.path.dirname(trash_target), exist_ok=True)

                if not Win32IO.move(node.full_path, trash_target):
                    src_wp = _win_path(node.full_path) if Win32IO.exists(node.full_path) else node.full_path
                    dst_wp = _win_path(trash_target)
                    shutil.copy2(src_wp, dst_wp)
                    Win32IO.delete_file(node.full_path)

                with self._lock:
                    self._log_op("DELETE", node.full_path, trash_target, status="TRASHED")
                    node.status = NodeStatus.TRASHED
                    self.stats["success"] += 1
                    self.stats["trashed"] += 1
                return
            except OSError:
                if attempt < 2:
                    time.sleep(0.5)  # 重试间隔 500ms
                    continue
                with self._lock:
                    node.status = NodeStatus.LOCKED
                    node.error_msg = "删除失败"
                    self.stats["locked"] += 1
                    self.errors.add(node.full_path, "DeleteFailed", "IO错误")

    def _cleanup_empty_dirs(self) -> None:
        """清理空目录"""
        dirs = sorted(
            [n for n in self.nodes if n.is_dir],
            key=lambda n: n.depth, reverse=True
        )
        for d in dirs:
            if d.action == NodeAction.DELETE:
                continue
            try:
                if Win32IO.is_dir(d.full_path) and not Win32IO.safe_listdir(d.full_path):
                    if Win32IO.remove_directory(d.full_path):
                        with self._lock:
                            self._log_op("RMDIR", d.full_path, "")
            except OSError:
                pass

    def _resolve_conflict(self, target_path: str) -> str:
        """解决目标路径冲突"""
        dir_name = os.path.dirname(target_path)
        base, ext = os.path.splitext(os.path.basename(target_path))
        counter = 1
        while True:
            new_name = f"{base}_{counter}{ext}"
            new_path = os.path.join(dir_name, new_name)
            if not Win32IO.exists(new_path):
                return new_path
            counter += 1

    def _get_trash_path(self, original_path: str) -> str:
        """
        获取回收站目标路径 (V6.6: 原子操作)
        
        使用时间戳+UUID组合直接生成唯一路径，避免TOCTOU竞态条件。
        """
        try:
            rel = os.path.relpath(original_path, self.root_path)
        except ValueError:
            rel = os.path.basename(original_path)
        target = os.path.join(self.trash_dir, rel)
        
        # V6.6 安全修复: 原子操作
        # 直接生成唯一路径，不做预先检查
        base, ext = os.path.splitext(target)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_suffix = uuid.uuid4().hex[:8]
        return f"{base}_{ts}_{unique_suffix}{ext}"

    def _log_op(self, action: str, source: str, target: str, status: str = "SUCCESS") -> None:
        """记录操作日志（调用方须已持有 self._lock，不可重入）"""
        self._seq += 1
        self.op_logs.append(OperationLog(
            seq=self._seq, action=action, source=source, target=target,
            timestamp=datetime.now(timezone.utc).isoformat(), status=status
        ))

    def _maybe_throttle(self) -> None:
        """节流控制"""
        with self._lock:
            self._throttle += 1
            do_sleep = (self._throttle % THROTTLE_BATCH == 0)
        if do_sleep:
            time.sleep(THROTTLE_SLEEP)

    def _write_log(self) -> None:
        """写入操作日志"""
        if not self.op_logs:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(self.log_dir, f"operations_{ts}.jsonl")
        with open(log_path, "w", encoding="utf-8") as f:
            for entry in self.op_logs:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        print(f"📝 操作日志: {log_path}")

    def _rotate_logs(self) -> None:
        """日志轮转"""
        try:
            if not os.path.isdir(self.log_dir):
                return
            log_files = sorted([
                f for f in os.listdir(self.log_dir)
                if f.startswith("operations_") and f.endswith(".jsonl")
            ])
            if len(log_files) > MAX_LOG_FILES:
                for old_file in log_files[:len(log_files) - MAX_LOG_FILES]:
                    try:
                        os.remove(os.path.join(self.log_dir, old_file))
                    except OSError:
                        pass
        except OSError:
            pass

    def generate_rollback_script(self) -> str:
        """
        生成回滚脚本 (V6.6: 使用json.dumps防止路径注入)
        
        Returns:
            回滚脚本的完整 Python 代码
        """
        if not self.op_logs:
            return "#!/usr/bin/env python3\n# 无操作记录，无需回滚\nimport sys\nsys.exit(0)\n"

        lines = [
            '#!/usr/bin/env python3',
            '"""回滚脚本 - 自动生成"""',
            f'# 生成时间: {datetime.now().isoformat()}',
            f'# 共 {len(self.op_logs)} 条操作记录',
            '',
            'import os',
            'import shutil',
            'import sys',
            '',
            'def main():',
            '    restored = 0',
            '    errors = 0',
            '',
        ]

        # 收集所有需要创建的目录
        mkdir_dirs: Set[str] = set()
        for entry in reversed(self.op_logs):
            if entry.action in ("MOVE", "DELETE"):
                src_dir = os.path.dirname(entry.source)
                if src_dir:
                    mkdir_dirs.add(src_dir)
            elif entry.action == "RMDIR":
                mkdir_dirs.add(entry.source)

        # 在循环外一次性创建所有目录
        for d in sorted(mkdir_dirs):
            lines.append(f'    os.makedirs({json.dumps(d)}, exist_ok=True)')

        lines.append('')
        lines.append('    # --- 操作回滚 ---')

        for entry in reversed(self.op_logs):
            if entry.action in ("MOVE", "DELETE"):
                lines.append(f'    try:')
                lines.append(f'        shutil.move({json.dumps(entry.target)}, {json.dumps(entry.source)})')
                lines.append(f'        restored += 1')
                lines.append(f'    except Exception as e:')
                lines.append(f'        print(f"❌ 回滚失败: {entry.source} — {{e}}")')
                lines.append(f'        errors += 1')
                lines.append('')
            elif entry.action == "RMDIR":
                lines.append(f'    try:')
                lines.append(f'        os.makedirs({json.dumps(entry.source)}, exist_ok=True)')
                lines.append(f'        restored += 1')
                lines.append(f'    except Exception as e:')
                lines.append(f'        print(f"❌ 目录重建失败: {entry.source} — {{e}}")')
                lines.append(f'        errors += 1')
                lines.append('')

        lines.append('    print(f"✅ 回滚完成: 恢复 {restored} 项, 失败 {errors} 项")')
        lines.append('    return 0 if errors == 0 else 1')
        lines.append('')
        lines.append('if __name__ == "__main__":')
        lines.append('    sys.exit(main())')
        return '\n'.join(lines)

    def generate_report(self) -> str:
        """生成操作报告"""
        lines = [
            "=" * 50,
            "📊 操作报告",
            "=" * 50,
            f"  ✅ 成功处理: {self.stats['success']} 项",
            f"  🗑️ 移入回收站: {self.stats['trashed']} 项",
        ]
        for key, label in [
            ("vanished", "Vanished (扫描后已消失)"),
            ("locked", "Locked (文件被占用/权限不足)"),
            ("changed", "Changed (扫描后被修改)"),
            ("conflict", "Conflict (命名冲突)"),
            ("failed", "Failed (其他失败)"),
        ]:
            if self.stats[key]:
                lines.append(f"    - {label}: {self.stats[key]} 项")
        lines.append("=" * 50)
        return "\n".join(lines)


# ============================================================
# 离线文件列表解析器
# ============================================================

class FileListParser:
    """
    解析多种格式的文件列表。

    支持格式:
      1. PowerShell Format-Table 输出 (含 FullName 表头)
      2. 纯路径列表 (每行一个路径)
      3. 带深度和类型的自定义格式: [{depth}] path #DIR / [{depth}] path #FILE

    核心能力:
      - 自动跳过 PowerShell 表头和分隔线
      - 检测被控制台宽度截断的行 (以 '...' 结尾)
      - 去除列对齐产生的尾部空白
      - 基于前缀关系推断文件/目录
      - 支持多文件合并 + 跨文件去重
    """

    _CUSTOM_FMT_RE = re.compile(r'^\[\{?(\d+)\}?\]\s+(.+?)\s+#(DIR|FILE)\s*$')

    def __init__(self):
        self.total_truncated = 0
        self.total_skipped = 0
        self._type_map: Dict[str, bool] = {}
        self._depth_map: Dict[str, int] = {}
        self._has_custom_format = False

    def load_files(self, filepaths: List[str]) -> List[str]:
        """加载文件列表"""
        all_lines = []
        self.total_truncated = 0
        self.total_skipped = 0
        self._type_map = {}
        self._depth_map = {}
        self._has_custom_format = False

        for filepath in filepaths:
            if not os.path.isfile(filepath):
                print(f"❌ 文件不存在: {filepath}")
                continue

            print(f"📂 加载文件列表: {filepath}")
            lines, truncated, skipped = self._load_single(filepath)
            all_lines.extend(lines)
            self.total_truncated += truncated
            self.total_skipped += skipped
            print(f"   有效路径: {len(lines)} 行, 截断跳过: {truncated}, 表头/空行跳过: {skipped}")

        # 跨文件去重
        before = len(all_lines)
        all_lines = list(dict.fromkeys(all_lines))
        if len(all_lines) < before:
            print(f"   跨文件去重: {before} → {len(all_lines)} 行")

        return all_lines

    def _load_single(self, filepath: str) -> Tuple[List[str], int, int]:
        """加载单个文件"""
        with open(filepath, "r", encoding="utf-8-sig") as f:
            raw_lines = f.readlines()

        valid_lines = []
        truncated_count = 0
        skipped_count = 0
        in_header = True
        found_fullname = False

        for raw in raw_lines:
            line = raw.rstrip("\r\n")
            if not line.strip():
                skipped_count += 1
                continue

            # 自定义格式检测
            m = self._CUSTOM_FMT_RE.match(line)
            if m:
                self._has_custom_format = True
                depth = int(m.group(1))
                path_str = m.group(2).strip()
                is_dir = m.group(3) == "DIR"
                self._type_map[path_str] = is_dir
                self._depth_map[path_str] = depth
                valid_lines.append(path_str)
                continue

            # PowerShell 表头处理
            if in_header:
                stripped = line.strip()
                if stripped == "FullName" or "FullName" in stripped:
                    found_fullname = True
                    skipped_count += 1
                    continue
                if stripped.startswith("-" * 8):
                    in_header = False
                    skipped_count += 1
                    continue
                if not found_fullname:
                    in_header = False
                else:
                    in_header = False

            # 截断检测
            line = line.rstrip()
            if TRUNCATED_LINE_RE.search(line):
                truncated_count += 1
                continue

            valid_lines.append(line)

        return valid_lines, truncated_count, skipped_count

    def parse_to_virtual_nodes(self, lines: List[str]) -> Tuple[List[VirtualFileNode], str, Dict]:
        """将路径列表解析为虚拟节点"""
        SEP_WIN = "\\"
        use_custom = self._has_custom_format and bool(self._type_map)

        # 推断公共根
        root_parts = None
        for line in lines:
            parts = line.split(SEP_WIN)
            if root_parts is None:
                root_parts = list(parts)
            else:
                new_root = []
                for a, b in zip(root_parts, parts):
                    if a == b:
                        new_root.append(a)
                    else:
                        break
                root_parts = new_root

        root = SEP_WIN.join(root_parts) if root_parts else ""
        if re.match(r'^[A-Z]:$', root, re.IGNORECASE):
            root = root + SEP_WIN
        root_depth = len(root_parts) if root_parts else 1

        # 确定 is_dir
        all_intermediate_dirs: Set[str] = set()
        for line in lines:
            parts = line.split(SEP_WIN)
            for i in range(root_depth, len(parts) - 1):
                all_intermediate_dirs.add(SEP_WIN.join(parts[:i + 1]))

        all_known = set(lines) | all_intermediate_dirs
        sorted_paths = sorted(all_known, key=lambda p: p.lower())

        def has_children(path: str) -> bool:
            prefix = path.lower() + SEP_WIN
            lo = bisect.bisect_left(sorted_paths, prefix, key=lambda p: p.lower())
            if lo < len(sorted_paths):
                return sorted_paths[lo].lower().startswith(prefix)
            return False

        is_dir_map = {}
        for line in lines:
            if line in all_intermediate_dirs:
                is_dir_map[line] = True
            elif use_custom and line in self._type_map:
                is_dir_map[line] = self._type_map[line]
            elif has_children(line):
                is_dir_map[line] = True
            else:
                is_dir_map[line] = False

        # 创建节点
        nodes = []
        existing_paths = set()

        for line in lines:
            parts = line.split(SEP_WIN)
            rel_depth = self._depth_map.get(line, len(parts) - root_depth) if use_custom else len(parts) - root_depth
            fname = parts[-1] if parts else ""
            is_dir = is_dir_map.get(line, False)
            ext = "" if is_dir else ("." + fname.rsplit(".", 1)[1].lower() if "." in fname else "")

            nodes.append(VirtualFileNode(
                full_path=line, file_name=fname, extension=ext,
                depth=rel_depth, is_dir=is_dir,
            ))
            existing_paths.add(line)

        # 补充隐含目录
        implicit_count = 0
        for dir_path in all_intermediate_dirs:
            if dir_path not in existing_paths:
                dir_parts = dir_path.split(SEP_WIN)
                nodes.append(VirtualFileNode(
                    full_path=dir_path, file_name=dir_parts[-1],
                    extension="", depth=len(dir_parts) - root_depth, is_dir=True,
                ))
                implicit_count += 1

        parse_stats = {
            "root": root,
            "total_input_lines": len(lines),
            "dirs_detected": sum(1 for v in is_dir_map.values() if v),
            "files_detected": sum(1 for v in is_dir_map.values() if not v),
            "implicit_dirs": implicit_count,
            "custom_format": use_custom,
        }

        return nodes, root, parse_stats


# ============================================================
# 离线分析引擎
# ============================================================

class OfflineDiagnosticEngine:
    """离线诊断：基于虚拟节点统计"""

    def __init__(self, nodes: List[VirtualFileNode], root: str):
        self.nodes = nodes
        self.root = root

    def get_stats(self, top_n: int = 10) -> Dict:
        files = [n for n in self.nodes if not n.is_dir]
        dirs = [n for n in self.nodes if n.is_dir]
        deepest = sorted(files, key=lambda n: n.depth, reverse=True)[:top_n]

        ext_count: Dict[str, int] = defaultdict(int)
        for n in files:
            ext_count[n.extension or "(无扩展名)"] += 1

        long_names = []
        for n in self.nodes:
            try:
                if len(n.full_path.encode("utf-8")) > MAX_PATH_LEGACY:
                    long_names.append(n.full_path)
            except (UnicodeEncodeError, UnicodeDecodeError):
                long_names.append(n.full_path)

        return {
            "root": self.root,
            "total_items": len(self.nodes),
            "total_files": len(files),
            "total_dirs": len(dirs),
            "max_depth": max((n.depth for n in self.nodes), default=0),
            "long_path_files": len(long_names),
            "long_path_examples": long_names[:5],
            "deepest_files": [(n.full_path, n.depth) for n in deepest[:5]],
            "extension_stats": dict(sorted(ext_count.items(), key=lambda x: x[1], reverse=True)[:15]),
        }


class OfflineEliminationEngine:
    """离线剔除：基于虚拟节点的缓存标记和去重"""

    def __init__(self, nodes: List[VirtualFileNode]):
        self.nodes = nodes
        self.duplicate_groups: List[Dict] = []

    def mark_cache_dirs(self, extra_patterns: List[str] = None) -> None:
        """标记缓存目录和垃圾文件"""
        patterns = list(CACHE_DIR_PATTERNS)
        if extra_patterns:
            patterns.extend(extra_patterns)

        for node in self.nodes:
            if node.action != NodeAction.NONE:
                continue
            if not node.is_dir:
                for pattern in JUNK_FILE_PATTERNS:
                    if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                        node.action = NodeAction.DELETE
                        node.reason = f"临时文件: {pattern}"
                        break

        cache_roots: List[VirtualFileNode] = []
        for node in self.nodes:
            if node.action != NodeAction.NONE:
                continue
            for pattern in patterns:
                if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                    cache_roots.append(node)
                    node.action = NodeAction.DELETE
                    node.reason = f"缓存目录/文件: {pattern}"
                    break

        dir_roots = [n for n in cache_roots if n.is_dir]
        if dir_roots:
            sorted_nodes = sorted(self.nodes, key=lambda n: n.full_path.lower())
            for root in dir_roots:
                prefix = root.full_path.lower() + "\\"
                lo = bisect.bisect_left(sorted_nodes, prefix, key=lambda n: n.full_path.lower())
                for j in range(lo, len(sorted_nodes)):
                    if sorted_nodes[j].full_path.lower().startswith(prefix):
                        if sorted_nodes[j].action == NodeAction.NONE:
                            sorted_nodes[j].action = NodeAction.DELETE
                            sorted_nodes[j].reason = f"缓存子树: {root.file_name}"
                    else:
                        break

    def deduplicate_by_name(self) -> int:
        """基于文件名的离线去重"""
        files = [n for n in self.nodes if not n.is_dir and n.action == NodeAction.NONE]

        name_groups: Dict[str, List[VirtualFileNode]] = defaultdict(list)
        for f in files:
            name_groups[f.file_name.lower()].append(f)

        dup_count = 0
        for name, group in name_groups.items():
            if len(group) < 2:
                continue

            has_size_data = any(n.size_bytes > 0 for n in group)

            if has_size_data:
                size_subgroups: Dict[int, List[VirtualFileNode]] = defaultdict(list)
                for n in group:
                    size_subgroups[n.size_bytes].append(n)

                for size, subgroup in size_subgroups.items():
                    if len(subgroup) < 2:
                        continue
                    keep = min(subgroup, key=lambda n: n.depth)
                    self.duplicate_groups.append({
                        "name": name, "size": size,
                        "keep": keep.full_path,
                        "delete": [n.full_path for n in subgroup if n.full_path != keep.full_path],
                    })
                    for n in subgroup:
                        if n.full_path != keep.full_path:
                            n.action = NodeAction.DELETE
                            n.reason = f"同名同大小重复 (保留: 深度{keep.depth})"
                            dup_count += 1
            else:
                keep_candidate = min(group, key=lambda n: n.depth)
                for n in group:
                    if n.action == NodeAction.NONE:
                        n.reason = "同名疑似重复，请手动确认"
                self.duplicate_groups.append({
                    "name": name, "size": None,
                    "keep": keep_candidate.full_path,
                    "delete": [m.full_path for m in group if m.full_path != keep_candidate.full_path],
                })

        return dup_count


class OfflineRestructureEngine:
    """离线重组：基于虚拟节点的冗余解包"""

    def __init__(self, nodes: List[VirtualFileNode], root: str):
        self.nodes = nodes
        self.root = root

    def unpack_redundant(self) -> int:
        """解包冗余目录结构"""
        unpacked = 0
        SEP_WIN = "\\"
        dirs = [n for n in self.nodes if n.is_dir and n.action == NodeAction.NONE]
        dir_children = defaultdict(list)
        for d in dirs:
            parent = SEP_WIN.join(d.full_path.split(SEP_WIN)[:-1])
            dir_children[parent.lower()].append(d)

        for parent_lower, children in dir_children.items():
            if len(children) != 1:
                continue
            child = children[0]
            parent_name = parent_lower.split(SEP_WIN)[-1] if parent_lower != self.root.lower() else ""
            if not parent_name or parent_name != child.file_name.lower():
                continue

            actual_parent = SEP_WIN.join(child.full_path.split(SEP_WIN)[:-1])
            child_prefix = child.full_path.lower() + SEP_WIN
            for node in self.nodes:
                if node.full_path.lower().startswith(child_prefix) and node.action == NodeAction.NONE:
                    rel = node.full_path[len(child.full_path):].lstrip(SEP_WIN)
                    node.target_path = SEP_WIN.join([actual_parent, rel]) if rel else actual_parent
                    node.reason = f"解包: {child.file_name}"
                    node.action = NodeAction.MOVE

            child.action = NodeAction.DELETE
            child.reason = "冗余目录（子项已提级）"
            unpacked += 1

        return unpacked


# ============================================================
# 主引擎 — 统一编排器
# ============================================================

class FolderRestructureEngine:
    def __init__(self, target_path: str):
        self.target_path = os.path.abspath(target_path)
        self.log_dir = os.path.join(self.target_path, LOG_DIR)
        self.trash_dir = os.path.join(self.target_path, TRASH_DIR)
        self.errors = ErrorCollector()
        self.diagnostic: Optional[DiagnosticEngine] = None
        self.perm_engine: Optional[PermissionEngine] = None
        self.elim_engine: Optional[EliminationEngine] = None
        self.nodes: List[FileNode] = []
        self._scan_ts: float = 0

    def run_safety_check(self) -> Tuple[bool, str]:
        """运行前置安全检查"""
        level, msg = SafetyChecker.check(self.target_path)
        icons = {SafetyLevel.FULL: "🟢", SafetyLevel.RESTRICTED: "🟡", SafetyLevel.BLOCKED: "🔴"}
        print(f"\n🔒 安全检查: {icons.get(level, '?')} Level {level.value} — {msg}")
        if level == SafetyLevel.BLOCKED:
            return False, msg
        elif level == SafetyLevel.RESTRICTED:
            print("   ⚠️  受限模式：仅允许预览和诊断操作")
        return True, msg

    def diagnose(self, detect_zone: bool = False, verbose: bool = False) -> Dict:
        """执行目录诊断扫描"""
        print(f"\n🔍 扫描目录: {self.target_path}")
        self.diagnostic = DiagnosticEngine(self.target_path)
        self.nodes = self.diagnostic.scan(detect_zone=detect_zone, verbose=verbose)
        self._scan_ts = self.diagnostic._scan_timestamp
        stats = self.diagnostic.get_stats()
        self._print_stats(stats)
        return stats

    def analyze(self, cache_patterns: List[str] = None,
                dedup_strategy: str = "shallowest") -> Dict:
        """分析文件结构并标记待处理项"""
        result: Dict[str, int] = {}

        self.elim_engine = EliminationEngine(self.nodes, self.errors)
        self.elim_engine.mark_cache_dirs(cache_patterns)
        result["cache_marked"] = sum(1 for n in self.nodes if n.action == NodeAction.DELETE)

        result["duplicates_marked"] = self.elim_engine.deduplicate(dedup_strategy)

        restruct = RestructureEngine(self.nodes, self.target_path, self.errors)
        result["redundant_unpacked"] = restruct.unpack_redundant()

        return result

    def fix_permissions(self, actions: Set[PermissionAction],
                        dry_run: bool = True) -> Dict[str, int]:
        """修复文件权限和属性"""
        self.perm_engine = PermissionEngine(self.nodes)
        return self.perm_engine.apply_all(actions, dry_run=dry_run)

    def preview(self) -> List[Dict]:
        """预览待处理的操作清单"""
        actions = []
        for n in self.nodes:
            if n.action != NodeAction.NONE:
                actions.append({
                    "action": n.action.value,
                    "source": n.full_path,
                    "target": getattr(n, "target_path", ""),
                    "size": _fmt_size(n.size_bytes) if not n.is_dir else "[目录]",
                    "depth": n.depth,
                    "attrs": _node_attrs_str(n),
                })
        return actions

    def get_duplicate_preview(self) -> List[Dict]:
        """获取重复文件预览"""
        if self.elim_engine:
            return self.elim_engine.get_duplicate_groups()
        return []

    def execute(self, dry_run: bool = True) -> Dict[str, int]:
        """执行待处理的操作"""
        engine = ExecutionEngine(self.nodes, self.target_path,
                                 self.log_dir, self.trash_dir)
        engine.set_scan_timestamp(self._scan_ts)
        stats = engine.execute(dry_run=dry_run)

        if not dry_run:
            print(engine.generate_report())

            rollback = engine.generate_rollback_script()
            rollback_path = os.path.join(self.log_dir, "rollback.py")
            with open(rollback_path, "w", encoding="utf-8") as f:
                f.write(rollback)
            print(f"🔄 回滚脚本: {rollback_path}")

            if self.errors.has_errors():
                error_path = os.path.join(self.log_dir, "errors.json")
                with open(error_path, "w", encoding="utf-8") as f:
                    json.dump([asdict(e) for e in self.errors.errors],
                             f, indent=2, ensure_ascii=False)
                print(f"⚠️  错误报告: {error_path}")

        return stats

    def _print_stats(self, stats: Dict) -> None:
        """打印诊断统计信息"""
        print(f"\n{'=' * 55}")
        print(f"📊 目录诊断报告")
        print(f"{'=' * 55}")
        print(f"  路径: {stats['root']}")
        print(f"  总项数: {stats['total_items']} (文件 {stats['total_files']}, 目录 {stats['total_dirs']})")
        print(f"  总大小: {stats['total_size_mb']} MB")
        print(f"  最大深度: {stats['max_depth']}")
        print(f"  符号链接: {stats['symlinks']}  |  Junction: {stats['junctions']}  |  硬链接: {stats['hard_links']}")

        perm_parts = []
        if stats["readonly_files"]:
            perm_parts.append(f"只读 {stats['readonly_files']}")
        if stats["hidden_files"]:
            perm_parts.append(f"隐藏 {stats['hidden_files']}")
        if stats["system_files"]:
            perm_parts.append(f"系统 {stats['system_files']}")
        if stats["zone_identifier_files"]:
            perm_parts.append(f"Zone标识 {stats['zone_identifier_files']}")
        if perm_parts:
            print(f"  🔑 属性: {' / '.join(perm_parts)}")

        if stats["long_path_files"]:
            print(f"  ⚠️  超长路径 (>260字节): {stats['long_path_files']} 个文件")

        if stats["deepest_files"]:
            print(f"\n  📁 层级最深 (Top 5):")
            for path, depth in stats["deepest_files"][:5]:
                print(f"    深度 {depth}: {path}")

        if stats["largest_dirs"]:
            print(f"\n  📦 体积最大目录 (Top 5):")
            for path, size in stats["largest_dirs"][:5]:
                print(f"    {size}: {path}")

        if stats["extension_stats"]:
            print(f"\n  📋 扩展名分布 (Top 10):")
            for ext, count in sorted(stats["extension_stats"].items(),
                                     key=lambda x: x[1], reverse=True)[:10]:
                print(f"    {ext}: {count} 个文件")
        print(f"{'=' * 55}\n")


# ============================================================
# 离线分析报告输出
# ============================================================

def print_offline_report(stats: Dict, parse_stats: Dict,
                         cache_count: int, junk_count: int,
                         dup_count: int, dup_groups: List[Dict],
                         unpacked: int, nodes: List[VirtualFileNode],
                         truncated_count: int, skipped_count: int,
                         flatten_count: int = 0) -> None:
    """打印离线诊断报告"""
    sep_line = "=" * 65
    print(f"\n{sep_line}")
    print(f"📊 离线诊断报告 — 文件夹重组引擎 V{VERSION}")
    print(sep_line)
    print(f"  路径: {stats['root']}")
    print(f"  总项数: {stats['total_items']} (文件 {stats['total_files']}, 目录 {stats['total_dirs']})")
    print(f"  最大深度: {stats['max_depth']}")

    if truncated_count > 0:
        print(f"\n  ⛔ 截断警告: {truncated_count} 行被截断，已跳过")
        print(f"     解决: (Get-ChildItem -Path 'F:\\' -Recurse).FullName | Out-File 'list.txt' -Encoding UTF8")

    print(f"\n  📂 路径解析: {parse_stats['files_detected']} 文件 + {parse_stats['dirs_detected']} 目录")

    if stats["long_path_files"]:
        print(f"  ⚠️  超长路径: {stats['long_path_files']} 个")

    print(f"\n📋 扩展名分布:")
    for ext, count in sorted(stats["extension_stats"].items(), key=lambda x: x[1], reverse=True)[:10]:
        bar = "█" * min(40, count // 500)
        print(f"  {ext:15s} {count:6d}  {bar}")

    if stats["deepest_files"]:
        print(f"\n📁 层级最深:")
        for path, depth in stats["deepest_files"][:5]:
            print(f"  深度 {depth}: {path[:100]}")

    total_delete = junk_count + cache_count + dup_count
    print(f"\n🔧 推荐操作:")
    print(f"  🗑️ 垃圾文件: {junk_count} 项")
    print(f"  🗑️ 缓存目录: {cache_count} 项")
    print(f"  🔄 冗余目录解包: {unpacked} 项")
    print(f"  🗑️ 同名重复文件: {dup_count} 项")
    print(f"\n  📊 总计可清理: {total_delete} 项")

    if dup_groups:
        print(f"\n📋 重复文件详情 (前 15 组):")
        for i, g in enumerate(dup_groups[:15], 1):
            print(f"  组 {i}: {g['name'][:60]}")
            print(f"    ✅ 保留: {g['keep'][:90]}")
            for d in g["delete"][:3]:
                print(f"    🗑️ 删除: {d[:90]}")
            if len(g["delete"]) > 3:
                print(f"    ... +{len(g['delete'])-3} 个")

    deleted = [n for n in nodes if n.action == NodeAction.DELETE]
    if deleted:
        print(f"\n🗑️ 删除预览 (前 30 项):")
        for i, n in enumerate(deleted[:30], 1):
            print(f"  {i:4d}. {n.file_name[:50]:50s} ← {n.reason}")

    print(f"\n{sep_line}")
    print(f"✅ 诊断完成")
    print(f"   可删除: {total_delete} 项")
    print(f"   可移动: {sum(1 for n in nodes if n.action == NodeAction.MOVE)} 项")
    print(f"   无操作: {sum(1 for n in nodes if n.action == NodeAction.NONE)} 项")
    print(sep_line)

