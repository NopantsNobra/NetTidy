"""
执行引擎模块
ExecutionEngine - 统一阶段编排（含真回收站路径）
"""

import json
import os
import time
import threading
import uuid
from datetime import datetime, timezone
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional

from .常量定义 import MAX_LOG_FILES, NodeAction, NodeStatus, VERSION
from .数据模型 import FileNode, OperationLog
from .安全检查 import ErrorCollector
from .工具函数 import _win_path, _fmt_size
from .配置管理 import runtime, make_progress
from .Win32底层IO import Win32IO, RecycleBin


# ═══════════════════════════════════════════════════════════
# ExecutionEngine 类
# ═══════════════════════════════════════════════════════════

class ExecutionEngine:
    """
    执行引擎
    负责执行所有文件操作，包括：
    - 文件移动
    - 文件删除（支持回收站）
    - 硬链接创建
    - 操作日志记录
    - 回滚脚本生成
    """
    
    def __init__(self, nodes: List[FileNode], root_path: str, log_dir: str, trash_dir: str):
        """
        初始化执行引擎
        
        Args:
            nodes: 文件节点列表
            root_path: 根目录路径
            log_dir: 日志目录
            trash_dir: 回收站目录
        """
        self.nodes = nodes
        self.root_path = root_path
        self.log_dir = log_dir
        self.trash_dir = trash_dir
        self.errors = ErrorCollector()
        self.op_logs: List[OperationLog] = []
        self._seq = 0
        self._scan_ts = 0.0
        self._lock = threading.Lock()
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
        self._throttle = 0
    
    def set_scan_timestamp(self, ts: float) -> None:
        """设置扫描时间戳"""
        self._scan_ts = ts
    
    def execute(self, dry_run: bool = True) -> Dict:
        """
        执行所有操作
        
        Args:
            dry_run: 是否为模拟运行
            
        Returns:
            统计结果
        """
        os.makedirs(self.log_dir, exist_ok=True)
        os.makedirs(self.trash_dir, exist_ok=True)
        
        moved = [n for n in self.nodes if n.action == NodeAction.MOVE]
        deleted = [n for n in self.nodes if n.action == NodeAction.DELETE]
        hardlinked = [n for n in self.nodes if n.action == NodeAction.HARDLINK]
        
        total = len(moved) + len(deleted) + len(hardlinked)
        
        if not total:
            print("✅ 无需执行操作")
            return self.stats
        
        print(f"\n⚙️ 执行操作: {total} 项")
        
        if dry_run:
            print("💡 模拟运行模式，不实际执行")
            return self.stats
        
        # 执行移动操作
        for n in make_progress(moved, desc="移动文件", unit="项"):
            self._execute_move(n)
        
        # 执行删除操作
        for n in make_progress(deleted, desc="删除文件", unit="项"):
            self._execute_delete(n)
        
        # 执行硬链接操作
        for n in make_progress(hardlinked, desc="创建硬链接", unit="项"):
            self._execute_hardlink(n)
        
        # 保存操作日志
        self._save_logs()
        
        return self.stats
    
    def _execute_move(self, node: FileNode) -> None:
        """
        执行移动操作
        
        Args:
            node: 文件节点
        """
        with self._lock:
            self._seq += 1
            seq = self._seq
        
        source = node.full_path
        target = node.target_path
        
        # 检查源文件是否存在
        if not os.path.exists(source):
            node.status = NodeStatus.VANISHED
            self.stats["vanished"] += 1
            self._log(seq, "MOVE", source, target, "VANISHED")
            return
        
        # 检查目标是否已存在
        if os.path.exists(target):
            node.status = NodeStatus.CONFLICT
            self.stats["conflict"] += 1
            self._log(seq, "MOVE", source, target, "CONFLICT")
            return
        
        try:
            # 创建目标目录
            os.makedirs(os.path.dirname(target), exist_ok=True)
            
            # 移动文件
            if Win32IO.move_file(source, target):
                node.status = NodeStatus.SUCCESS
                self.stats["success"] += 1
                self._log(seq, "MOVE", source, target, "SUCCESS")
            else:
                node.status = NodeStatus.FAILED
                self.stats["failed"] += 1
                self._log(seq, "MOVE", source, target, "FAILED")
        
        except OSError as e:
            node.status = NodeStatus.FAILED
            node.error_msg = str(e)
            self.stats["failed"] += 1
            self._log(seq, "MOVE", source, target, "FAILED")
    
    def _execute_delete(self, node: FileNode) -> None:
        """
        执行删除操作
        
        Args:
            node: 文件节点
        """
        with self._lock:
            self._seq += 1
            seq = self._seq
        
        source = node.full_path
        
        # 检查文件是否存在
        if not os.path.exists(source):
            node.status = NodeStatus.VANISHED
            self.stats["vanished"] += 1
            self._log(seq, "DELETE", source, "", "VANISHED")
            return
        
        try:
            # 尝试使用系统回收站
            if runtime.use_recycle_bin:
                success, msg = RecycleBin.move_to_recycle_bin(source)
                if success:
                    node.status = NodeStatus.RECYCLED
                    self.stats["recycled"] += 1
                    self._log(seq, "DELETE", source, "[系统回收站]", "RECYCLED")
                    return
                else:
                    # 降级到自定义回收站
                    print(f"  ⚠️ 回收站失败，降级: {msg}")
            
            # 使用自定义回收站
            trash_path = self._get_trash_path(source)
            
            if node.is_dir:
                os.makedirs(trash_path, exist_ok=True)
                if Win32IO.move_file(source, trash_path):
                    node.status = NodeStatus.TRASHED
                    node.target_path = trash_path
                    self.stats["trashed"] += 1
                    self._log(seq, "DELETE", source, trash_path, "TRASHED")
                else:
                    node.status = NodeStatus.FAILED
                    self.stats["failed"] += 1
                    self._log(seq, "DELETE", source, trash_path, "FAILED")
            else:
                if Win32IO.move_file(source, trash_path):
                    node.status = NodeStatus.TRASHED
                    node.target_path = trash_path
                    self.stats["trashed"] += 1
                    self._log(seq, "DELETE", source, trash_path, "TRASHED")
                else:
                    node.status = NodeStatus.FAILED
                    self.stats["failed"] += 1
                    self._log(seq, "DELETE", source, trash_path, "FAILED")
        
        except OSError as e:
            node.status = NodeStatus.FAILED
            node.error_msg = str(e)
            self.stats["failed"] += 1
            self._log(seq, "DELETE", source, "", "FAILED")
    
    def _execute_hardlink(self, node: FileNode) -> None:
        """
        执行硬链接操作
        
        Args:
            node: 文件节点
        """
        with self._lock:
            self._seq += 1
            seq = self._seq
        
        source = node.full_path
        target = node.target_path
        
        # 检查源文件是否存在
        if not os.path.exists(source):
            node.status = NodeStatus.VANISHED
            self.stats["vanished"] += 1
            self._log(seq, "HARDLINK", source, target, "VANISHED")
            return
        
        try:
            # 删除原文件
            if Win32IO.delete_file(source):
                # 创建硬链接
                if Win32IO.create_hard_link(target, source):
                    node.status = NodeStatus.HARDLINKED
                    self.stats["hardlinked"] += 1
                    self._log(seq, "HARDLINK", source, target, "HARDLINKED")
                else:
                    # 硬链接失败，尝试恢复
                    self.errors.add(source, "HardlinkFail", "无法创建硬链接")
                    node.status = NodeStatus.FAILED
                    self.stats["failed"] += 1
                    self._log(seq, "HARDLINK", source, target, "FAILED")
            else:
                node.status = NodeStatus.FAILED
                self.stats["failed"] += 1
                self._log(seq, "HARDLINK", source, target, "FAILED")
        
        except OSError as e:
            node.status = NodeStatus.FAILED
            node.error_msg = str(e)
            self.stats["failed"] += 1
            self._log(seq, "HARDLINK", source, target, "FAILED")
    
    def _get_trash_path(self, source: str) -> str:
        """
        获取回收站路径
        
        Args:
            source: 源文件路径
            
        Returns:
            回收站路径
        """
        name = os.path.basename(source)
        base, ext = os.path.splitext(name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = uuid.uuid4().hex[:8]
        new_name = f"{base}_{timestamp}_{unique_id}{ext}"
        return os.path.join(self.trash_dir, new_name)
    
    def _log(self, seq: int, action: str, source: str, target: str, status: str) -> None:
        """
        记录操作日志
        
        Args:
            seq: 序号
            action: 操作类型
            source: 源路径
            target: 目标路径
            status: 状态
        """
        log = OperationLog(
            seq=seq,
            action=action,
            source=source,
            target=target,
            timestamp=datetime.now().isoformat(),
            status=status,
        )
        
        with self._lock:
            self.op_logs.append(log)
    
    def _save_logs(self) -> None:
        """保存操作日志"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(self.log_dir, f"operations_{timestamp}.json")
        
        try:
            data = {
                "version": VERSION,
                "timestamp": datetime.now().isoformat(),
                "root_path": self.root_path,
                "stats": self.stats,
                "operations": [log.to_dict() for log in self.op_logs],
            }
            
            with open(log_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # 清理旧日志
            self._cleanup_old_logs()
        
        except OSError as e:
            print(f"⚠️ 无法保存日志: {e}")
    
    def _cleanup_old_logs(self) -> None:
        """清理旧日志文件"""
        try:
            log_files = sorted([
                f for f in os.listdir(self.log_dir)
                if f.startswith("operations_") and f.endswith(".json")
            ])
            
            while len(log_files) > MAX_LOG_FILES:
                old_file = os.path.join(self.log_dir, log_files.pop(0))
                os.remove(old_file)
        
        except OSError:
            pass
    
    def generate_rollback_script(self) -> str:
        """
        生成回滚脚本
        
        Returns:
            回滚脚本内容
        """
        if not self.op_logs:
            return "# 无需回滚\n"
        
        lines = [
            "#!/usr/bin/env python3",
            '"""回滚脚本 - 自动生成"""',
            "import os",
            "import shutil",
            "import sys",
            "",
            "def main():",
            "    ok = err = 0",
        ]
        
        for log in self.op_logs:
            if log.action == "MOVE":
                lines.append(f"    # {log.seq}: MOVE")
                lines.append(f"    try:")
                lines.append(f"        os.makedirs(os.path.dirname({json.dumps(log.source)}), exist_ok=True)")
                lines.append(f"        shutil.move({json.dumps(log.target)}, {json.dumps(log.source)})")
                lines.append(f"        ok += 1")
                lines.append(f"    except: err += 1")
            elif log.action == "DELETE" and log.status == "TRASHED":
                lines.append(f"    # {log.seq}: DELETE (from trash)")
                lines.append(f"    try:")
                lines.append(f"        shutil.move({json.dumps(log.target)}, {json.dumps(log.source)})")
                lines.append(f"        ok += 1")
                lines.append(f"    except: err += 1")
            elif log.action == "DELETE" and log.status == "RECYCLED":
                lines.append(f"    # {log.seq}: DELETE (recycled - 需手动恢复)")
                lines.append(f"    # {log.source} -> 系统回收站")
            elif log.action == "HARDLINK":
                lines.append(f"    # {log.seq}: HARDLINK")
                lines.append(f"    try:")
                lines.append(f"        os.remove({json.dumps(log.source)})")
                lines.append(f"        shutil.move({json.dumps(log.target)}, {json.dumps(log.source)})")
                lines.append(f"        ok += 1")
                lines.append(f"    except: err += 1")
        
        lines.append(f'    print(f"✅ 恢复 {{ok}} 项, 失败 {{err}} 项")')
        lines.append(f'    return 0 if err == 0 else 1')
        lines.append('')
        lines.append('if __name__ == "__main__": sys.exit(main())')
        
        return "\n".join(lines)
    
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
            f"  🗑️ 回收站: {self.stats['trashed']} 项",
        ]
        
        if self.stats["recycled"]:
            lines.append(f"  ♻️ 系统回收站: {self.stats['recycled']} 项")
        
        if self.stats["hardlinked"]:
            lines.append(f"  🔗 硬链接: {self.stats['hardlinked']} 项")
        
        for k, l in [
            ("vanished", "Vanished"),
            ("locked", "Locked"),
            ("changed", "Changed"),
            ("failed", "Failed"),
        ]:
            if self.stats[k]:
                lines.append(f"  - {l}: {self.stats[k]}")
        
        lines.append("=" * 55)
        
        return "\n".join(lines)
