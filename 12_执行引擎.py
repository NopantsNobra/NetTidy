"""
执行引擎模块
=============
职责：操作执行、日志记录、回滚脚本生成

对外接口：
    - ExecutionEngine  执行引擎类
    - generate_rollback_script()  生成回滚脚本函数

依赖关系：
    - 数据模型 (FileNode, OperationLog)
    - 常量定义 (VERSION, MAX_LOG_FILES, 枚举)
    - 工具函数 (_win_path, _fmt_size)
    - 安全检查 (ErrorCollector)
    - Win32底层IO (Win32IO, RecycleBin)
    - 配置管理 (runtime, make_progress)

使用示例：
    from 执行引擎 import ExecutionEngine
    engine = ExecutionEngine(nodes, "/root", "/logs", "/trash")
    engine.execute(dry_run=True)
    engine.generate_rollback_script()

版本历史：
    9.0: 回收站支持、多线程执行、类型注解完善
    8.0: 初始模块化版本
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any, Tuple

from .常量定义 import (
    VERSION,
    MAX_LOG_FILES,
    NodeAction,
    NodeStatus,
)
from .数据模型 import FileNode, OperationLog
from .安全检查 import ErrorCollector
from .工具函数 import _win_path, _fmt_size
from .配置管理 import runtime, make_progress
from .Win32底层IO import Win32IO, RecycleBin


# ═══════════════════════════════════════════════════════════════════
# ExecutionEngine - 执行引擎
# ═══════════════════════════════════════════════════════════════════

class ExecutionEngine:
    """
    执行引擎
    
    负责执行所有文件操作：
    - 文件移动
    - 文件删除（支持回收站）
    - 硬链接创建
    - 操作日志
    - 回滚脚本生成
    
    使用示例：
        >>> engine = ExecutionEngine(nodes, "/root", "/logs", "/trash")
        >>> stats = engine.execute(dry_run=False)
        >>> print(f"成功: {stats['success']}")
    """
    
    def __init__(
        self,
        nodes: List[FileNode],
        root_path: str,
        log_dir: str,
        trash_dir: str,
    ):
        """
        初始化执行引擎
        
        Args:
            nodes: 文件节点列表
            root_path: 根目录路径
            log_dir: 日志目录
            trash_dir: 回收站目录
        """
        self.nodes = nodes
        self.root_path = os.path.abspath(root_path)
        self.log_dir = log_dir
        self.trash_dir = trash_dir
        
        # 错误收集
        self.errors = ErrorCollector()
        
        # 操作日志
        self.op_logs: List[OperationLog] = []
        self._seq = 0
        self._lock = threading.Lock()
        
        # 扫描时间戳
        self._scan_ts: float = 0
        
        # 节流
        self._throttle_counter = 0
        
        # 统计
        self.stats = {
            "success": 0,
            "vanished": 0,
            "changed": 0,
            "locked": 0,
            "skipped": 0,
            "conflict": 0,
            "failed": 0,
            "trashed": 0,
            "recycled": 0,
            "hardlinked": 0,
        }
    
    def set_scan_timestamp(self, ts: float) -> None:
        """设置扫描时间戳"""
        self._scan_ts = ts
    
    def execute(self, dry_run: bool = True) -> Dict[str, int]:
        """
        执行所有操作
        
        Args:
            dry_run: 是否模拟运行
            
        Returns:
            统计字典
        """
        # 创建目录
        os.makedirs(self.log_dir, exist_ok=True)
        os.makedirs(self.trash_dir, exist_ok=True)
        
        # 分类节点
        move_nodes = [n for n in self.nodes if n.action == NodeAction.MOVE]
        delete_nodes = [n for n in self.nodes if n.action == NodeAction.DELETE]
        hardlink_nodes = [n for n in self.nodes if n.action == NodeAction.HARDLINK]
        
        total = len(move_nodes) + len(delete_nodes) + len(hardlink_nodes)
        
        if total == 0:
            print("✅ 无需执行操作")
            return self.stats
        
        # 打印摘要
        print(f"\n{'🔍 预览' if dry_run else '⚡ 执行'}模式:")
        print(f"  📦 移动: {len(move_nodes)} 项")
        print(f"  🗑️ 删除: {len(delete_nodes)} 项")
        print(f"  🔗 硬链接: {len(hardlink_nodes)} 项")
        print(f"  总计: {total} 项")
        
        if dry_run:
            print("💡 模拟运行，不实际修改文件")
        else:
            print(f"💾 使用 {runtime.max_workers} 线程并行处理...")
        
        # 执行移动
        if move_nodes:
            print("\n📦 Phase 1: 移动操作")
            self._process_batch(move_nodes, self._process_move, dry_run)
        
        # 执行删除
        if delete_nodes:
            print("\n🗑️ Phase 2: 删除操作")
            self._process_batch(delete_nodes, self._process_delete, dry_run)
        
        # 执行硬链接
        if hardlink_nodes:
            print("\n🔗 Phase 3: 硬链接操作")
            self._process_batch(hardlink_nodes, self._process_hardlink, dry_run)
        
        # 保存日志
        if not dry_run:
            self._save_logs()
            self._cleanup_logs()
        
        return self.stats
    
    def _process_batch(
        self,
        nodes: List[FileNode],
        handler: callable,
        dry_run: bool,
    ) -> None:
        """批量处理节点"""
        if dry_run or len(nodes) <= 20:
            for node in make_progress(nodes, desc="处理", unit="项", disable=dry_run):
                handler(node, dry_run)
            return
        
        # 多线程处理
        completed = [0]
        progress_lock = threading.Lock()
        
        def safe_handler(node: FileNode) -> None:
            try:
                handler(node, dry_run)
                self._maybe_throttle()
                
                with progress_lock:
                    completed[0] += 1
                    if completed[0] % 200 == 0:
                        print(f"  进度: {completed[0]}/{len(nodes)}")
            except Exception as e:
                self.errors.add_from_exception(
                    node.full_path, "ExecutionError", e
                )
                with self._lock:
                    self.stats["failed"] += 1
        
        workers = runtime.max_workers
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(safe_handler, n) for n in nodes
            ]
            
            for future in make_progress(
                as_completed(futures), total=len(futures),
                desc="并行处理", unit="项"
            ):
                try:
                    future.result()
                except Exception:
                    pass
    
    def _process_move(self, node: FileNode, dry_run: bool) -> None:
        """处理移动操作"""
        with self._lock:
            self._seq += 1
            seq = self._seq
        
        source = node.full_path
        target = node.target_path
        
        # 检查源文件
        if not Win32IO.exists(source):
            node.status = NodeStatus.VANISHED
            self.stats["vanished"] += 1
            self._log(seq, "MOVE", source, target, "VANISHED")
            return
        
        # 检查修改时间
        if self._scan_ts > 0:
            try:
                st = Win32IO.safe_stat(source)
                if st and st.st_mtime > self._scan_ts:
                    node.status = NodeStatus.CHANGED
                    self.stats["changed"] += 1
                    self._log(seq, "MOVE", source, target, "CHANGED")
                    return
            except OSError:
                pass
        
        if dry_run:
            node.status = NodeStatus.SUCCESS
            self.stats["success"] += 1
            self._log(seq, "MOVE", source, target, "SUCCESS")
            return
        
        # 创建目标目录
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
        except OSError as e:
            node.status = NodeStatus.FAILED
            node.error_msg = str(e)
            self.stats["failed"] += 1
            self._log(seq, "MOVE", source, target, "FAILED")
            return
        
        # 移动文件
        if Win32IO.move_file(source, target):
            node.status = NodeStatus.SUCCESS
            self.stats["success"] += 1
            self._log(seq, "MOVE", source, target, "SUCCESS")
        else:
            node.status = NodeStatus.LOCKED
            node.error_msg = "移动失败"
            self.stats["locked"] += 1
            self._log(seq, "MOVE", source, target, "LOCKED")
    
    def _process_delete(self, node: FileNode, dry_run: bool) -> None:
        """处理删除操作"""
        with self._lock:
            self._seq += 1
            seq = self._seq
        
        source = node.full_path
        
        if not Win32IO.exists(source):
            node.status = NodeStatus.VANISHED
            self.stats["vanished"] += 1
            self._log(seq, "DELETE", source, "", "VANISHED")
            return
        
        if dry_run:
            node.status = NodeStatus.SUCCESS
            self.stats["success"] += 1
            self._log(seq, "DELETE", source, "", "SUCCESS")
            return
        
        # 尝试系统回收站
        if runtime.use_recycle_bin:
            ok, msg = RecycleBin.move_to_recycle_bin(source)
            if ok:
                node.status = NodeStatus.RECYCLED
                self.stats["recycled"] += 1
                self.stats["success"] += 1
                self._log(seq, "DELETE", source, "[系统回收站]", "RECYCLED")
                return
            
            # 降级：打印警告，继续使用自定义回收站
            if runtime.verbose:
                print(f"  ⚠️ 系统回收站失败 ({msg})，使用自定义回收站")
        
        # 自定义回收站
        trash_path = self._get_trash_path(source)
        
        # 移动到回收站
        if Win32IO.move_file(source, trash_path):
            node.status = NodeStatus.TRASHED
            node.target_path = trash_path
            self.stats["trashed"] += 1
            self.stats["success"] += 1
            self._log(seq, "DELETE", source, trash_path, "TRASHED")
        else:
            node.status = NodeStatus.LOCKED
            node.error_msg = "删除失败"
            self.stats["locked"] += 1
            self._log(seq, "DELETE", source, "", "LOCKED")
    
    def _process_hardlink(self, node: FileNode, dry_run: bool) -> None:
        """
        处理硬链接操作
        
        将被标记的重复文件（source）替换为指向保留文件（target）的硬链接。
        流程：source 移入回收站 → 在 source 原位置创建指向 target 的硬链接。
        """
        with self._lock:
            self._seq += 1
            seq = self._seq
        
        source = node.full_path    # 被标记的重复文件（要被替换为硬链接）
        target = node.target_path  # 保留文件（不应被改动）
        
        if not Win32IO.exists(source):
            node.status = NodeStatus.VANISHED
            self.stats["vanished"] += 1
            self._log(seq, "HARDLINK", source, target, "VANISHED")
            return
        
        if not target or not Win32IO.exists(target):
            node.status = NodeStatus.FAILED
            node.error_msg = "保留文件不存在"
            self.stats["failed"] += 1
            self._log(seq, "HARDLINK", source, target, "FAILED")
            return
        
        if dry_run:
            node.status = NodeStatus.SUCCESS
            self.stats["success"] += 1
            self._log(seq, "HARDLINK", source, target, "SUCCESS")
            return
        
        # 安全操作：先将 source 移入回收站备份
        trash_path = self._get_trash_path(source)
        
        try:
            if not Win32IO.move_file(source, trash_path):
                raise OSError("移动到回收站失败")
            
            # 在 source 原位置创建指向 target 的硬链接
            if Win32IO.create_hard_link(source, target):
                node.status = NodeStatus.HARDLINKED
                self.stats["hardlinked"] += 1
                self.stats["success"] += 1
                self._log(seq, "HARDLINK", source, target, "HARDLINKED")
            else:
                # 硬链接失败，恢复原文件
                Win32IO.move_file(trash_path, source)
                node.status = NodeStatus.FAILED
                node.error_msg = "硬链接创建失败"
                self.stats["failed"] += 1
                self._log(seq, "HARDLINK", source, target, "FAILED")
        
        except OSError as e:
            # 尝试恢复
            try:
                if Win32IO.exists(trash_path) and not Win32IO.exists(source):
                    Win32IO.move_file(trash_path, source)
            except OSError:
                pass
            node.status = NodeStatus.FAILED
            node.error_msg = str(e)
            self.stats["failed"] += 1
            self._log(seq, "HARDLINK", source, target, "FAILED")
    
    def _get_trash_path(self, source: str) -> str:
        """生成回收站路径"""
        name = os.path.basename(source)
        base, ext = os.path.splitext(name)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique = uuid.uuid4().hex[:8]
        
        new_name = f"{base}_{timestamp}_{unique}{ext}"
        
        # 保留目录结构
        try:
            rel = os.path.relpath(source, self.root_path)
            parts = rel.split(os.sep)
            if len(parts) > 1:
                subdir = os.path.join(*parts[:-1])
                return os.path.join(self.trash_dir, subdir, new_name)
        except ValueError:
            pass
        
        return os.path.join(self.trash_dir, new_name)
    
    def _log(
        self,
        seq: int,
        action: str,
        source: str,
        target: str,
        status: str,
    ) -> None:
        """记录操作日志"""
        with self._lock:
            log = OperationLog(
                seq=seq,
                action=action,
                source=source,
                target=target,
                timestamp=datetime.now(timezone.utc).isoformat(),
                status=status,
            )
            self.op_logs.append(log)
    
    def _maybe_throttle(self) -> None:
        """节流"""
        with self._lock:
            self._throttle_counter += 1
            if self._throttle_counter % 200 == 0:
                time.sleep(runtime.throttle_sleep)
    
    def _save_logs(self) -> None:
        """保存操作日志"""
        if not self.op_logs:
            return
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(self.log_dir, f"operations_{timestamp}.json")
        
        data = {
            "version": VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "root_path": self.root_path,
            "stats": self.stats,
            "operations": [log.to_dict() for log in self.op_logs],
        }
        
        try:
            with open(log_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"📝 操作日志: {log_file}")
        except OSError as e:
            print(f"⚠️ 保存日志失败: {e}")
        
        # 保存错误报告
        if self.errors:
            error_file = os.path.join(self.log_dir, "errors.json")
            try:
                with open(error_file, "w", encoding="utf-8") as f:
                    json.dump(self.errors.to_dict(), f, ensure_ascii=False, indent=2)
                print(f"⚠️ 错误报告: {error_file}")
            except OSError:
                pass
    
    def _cleanup_logs(self) -> None:
        """清理旧日志"""
        try:
            if not os.path.isdir(self.log_dir):
                return
            
            log_files = sorted([
                f for f in os.listdir(self.log_dir)
                if f.startswith("operations_") and f.endswith(".json")
            ])
            
            while len(log_files) > MAX_LOG_FILES:
                old_file = os.path.join(self.log_dir, log_files.pop(0))
                try:
                    os.remove(old_file)
                except OSError:
                    pass
        
        except OSError:
            pass
    
    def generate_rollback_script(self) -> str:
        """
        生成回滚脚本
        
        Returns:
            回滚脚本内容
        """
        if not self.op_logs:
            return "#!/usr/bin/env python3\n# 无操作记录，无需回滚\nimport sys\nsys.exit(0)\n"
        
        lines = [
            "#!/usr/bin/env python3",
            f'"""回滚脚本 - 自动生成"""',
            f"# 生成时间: {datetime.now().isoformat()}",
            f"# 操作记录数: {len(self.op_logs)}",
            "",
            "import os",
            "import shutil",
            "import sys",
            "import json",
            "",
            "def main():",
            "    restored = 0",
            "    errors = 0",
            "",
        ]
        
        # 收集需要创建的目录
        needed_dirs: Set[str] = set()
        for log in self.op_logs:
            if log.action == "MOVE":
                needed_dirs.add(os.path.dirname(log.source))
            elif log.action in ("DELETE", "HARDLINK"):
                needed_dirs.add(os.path.dirname(log.source))
        
        for d in sorted(needed_dirs):
            lines.append(f"    os.makedirs({json.dumps(d)}, exist_ok=True)")
        
        lines.append("")
        lines.append("    # === 回滚操作 ===")
        
        for log in self.op_logs:
            if log.action == "MOVE":
                lines.append(f"    # [{log.seq}] MOVE: {log.source}")
                lines.append(f"    try:")
                lines.append(f"        if os.path.exists({json.dumps(log.target)}):")
                lines.append(f"            shutil.move({json.dumps(log.target)}, {json.dumps(log.source)})")
                lines.append(f"        restored += 1")
                lines.append(f"    except Exception as e:")
                lines.append(f'        print(f"❌ 回滚失败: {log.source} - {{e}}")')
                lines.append(f"        errors += 1")
                lines.append("")
            
            elif log.action == "DELETE" and log.status == "TRASHED":
                lines.append(f"    # [{log.seq}] DELETE (from trash): {log.source}")
                lines.append(f"    try:")
                lines.append(f"        if os.path.exists({json.dumps(log.target)}):")
                lines.append(f"            os.makedirs(os.path.dirname({json.dumps(log.source)}), exist_ok=True)")
                lines.append(f"            shutil.move({json.dumps(log.target)}, {json.dumps(log.source)})")
                lines.append(f"        restored += 1")
                lines.append(f"    except Exception as e:")
                lines.append(f'        print(f"❌ 回滚失败: {log.source} - {{e}}")')
                lines.append(f"        errors += 1")
                lines.append("")
            
            elif log.action == "DELETE" and log.status == "RECYCLED":
                lines.append(f"    # [{log.seq}] ⚠️ 系统回收站: {log.source}")
                lines.append(f"    # 需要从系统回收站手动恢复")
                lines.append("")
            
            elif log.action == "HARDLINK":
                lines.append(f"    # [{log.seq}] HARDLINK: {log.source}")
                lines.append(f"    try:")
                lines.append(f"        if os.path.exists({json.dumps(log.target)}):")
                lines.append(f"            os.remove({json.dumps(log.target)})")
                lines.append(f"        if os.path.exists({json.dumps(log.source)}):")
                lines.append(f"            shutil.move({json.dumps(log.source)}, {json.dumps(log.target)})")
                lines.append(f"        restored += 1")
                lines.append(f"    except Exception as e:")
                lines.append(f'        print(f"❌ 回滚失败: {log.target} - {{e}}")')
                lines.append(f"        errors += 1")
                lines.append("")
        
        lines.append(f'    print(f"✅ 回滚完成: 恢复 {{restored}} 项, 失败 {{errors}} 项")')
        lines.append("    return 0 if errors == 0 else 1")
        lines.append("")
        lines.append("if __name__ == '__main__':")
        lines.append("    sys.exit(main())")
        
        return "\n".join(lines)
    
    def save_rollback_script(self, output_path: Optional[str] = None) -> str:
        """
        保存回滚脚本
        
        Args:
            output_path: 输出路径
            
        Returns:
            保存的路径
        """
        if output_path is None:
            output_path = os.path.join(self.log_dir, "rollback.py")
        
        content = self.generate_rollback_script()
        
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"🔄 回滚脚本: {output_path}")
            return output_path
        except OSError as e:
            print(f"❌ 保存回滚脚本失败: {e}")
            return ""
    
    def generate_report(self) -> str:
        """
        生成操作报告
        
        Returns:
            报告内容
        """
        lines = [
            "=" * 55,
            "📊 操作报告",
            "=" * 55,
            f"  ✅ 成功: {self.stats['success']} 项",
        ]
        
        if self.stats["trashed"] > 0:
            lines.append(f"  🗑️ 回收站: {self.stats['trashed']} 项")
        
        if self.stats["recycled"] > 0:
            lines.append(f"  ♻️ 系统回收站: {self.stats['recycled']} 项")
        
        if self.stats["hardlinked"] > 0:
            lines.append(f"  🔗 硬链接: {self.stats['hardlinked']} 项")
        
        for key, label in [
            ("vanished", "已消失"),
            ("locked", "被锁定"),
            ("changed", "已变更"),
            ("conflict", "冲突"),
            ("failed", "失败"),
        ]:
            if self.stats[key] > 0:
                lines.append(f"  ⚠️ {label}: {self.stats[key]} 项")
        
        lines.append("=" * 55)
        
        return "\n".join(lines)
