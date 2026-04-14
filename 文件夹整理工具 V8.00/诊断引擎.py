"""
诊断引擎模块
DiagnosticEngine - 目录诊断与分析
"""

import csv
import json
import os
import stat
from collections import defaultdict
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import threading
from typing import List, Dict, Optional

from .常量定义 import (
    IS_WINDOWS, FILE_ATTRIBUTE_HIDDEN, FILE_ATTRIBUTE_READONLY, FILE_ATTRIBUTE_SYSTEM,
    FILE_ATTRIBUTE_REPARSE, IO_REPARSE_TAG_SYMLINK, IO_REPARSE_TAG_MOUNT_POINT,
    MAX_PATH_LEGACY, ReparseType, VERSION, NodeAction,
)
from .数据模型 import FileNode
from .安全检查 import ErrorCollector
from .工具函数 import _win_path, _fmt_size
from .配置管理 import runtime, make_progress
from .Win32底层IO import Win32IO
from .区域标识处理 import ZoneIdentifierHandler


# ═══════════════════════════════════════════════════════════
# DiagnosticEngine 类
# ═══════════════════════════════════════════════════════════

class DiagnosticEngine:
    """
    诊断引擎
    负责扫描目录结构、收集文件信息、生成诊断报告
    """
    
    def __init__(self, root_path: str):
        """
        初始化诊断引擎
        
        Args:
            root_path: 根目录路径
        """
        self.root_path = os.path.abspath(root_path)
        self.nodes: List[FileNode] = []
        self._scan_timestamp: float = 0
        self._errors = ErrorCollector()
    
    def scan(self, detect_zone: bool = False, verbose: bool = False, config: dict = None) -> List[FileNode]:
        """
        扫描目录结构
        
        Args:
            detect_zone: 是否检测 Zone.Identifier
            verbose: 是否显示详细信息
            config: 配置字典
            
        Returns:
            文件节点列表
        """
        self._scan_timestamp = __import__('time').time()
        self.nodes = []
        
        root_depth = self.root_path.count(os.sep)
        print(f"\n📂 扫描目录: {self.root_path}")
        
        # 遍历目录
        for root, dirs, files in os.walk(self.root_path):
            # 处理目录
            for d in dirs:
                path = os.path.join(root, d)
                depth = path.count(os.sep) - root_depth
                node = self._mk(path, depth, is_dir=True)
                self.nodes.append(node)
            
            # 处理文件
            for f in files:
                path = os.path.join(root, f)
                depth = path.count(os.sep) - root_depth
                node = self._mk(path, depth, is_dir=False)
                self.nodes.append(node)
        
        # Zone.Identifier 检测
        if detect_zone and IS_WINDOWS:
            self._detect_zone_identifiers()
        
        # 打印统计
        self._print_stats(verbose)
        
        return self.nodes
    
    def _mk(self, path: str, depth: int, is_dir: bool = False) -> FileNode:
        """
        创建文件节点
        
        Args:
            path: 文件路径
            depth: 深度
            is_dir: 是否为目录
            
        Returns:
            文件节点
        """
        st = Win32IO.safe_stat(path) if IS_WINDOWS else os.lstat(path)
        name = os.path.basename(path) if path != self.root_path else os.path.basename(self.root_path)
        ext = "" if is_dir else os.path.splitext(name)[1].lower()
        
        rp = ReparseType.NONE
        hl = 1
        ih, ir, isys = False, False, False
        
        if st:
            if stat.S_ISLNK(st.st_mode):
                rp = ReparseType.SYMLINK
            elif not stat.S_ISDIR(st.st_mode) and st.st_nlink > 1:
                rp = ReparseType.HARDLINK
                hl = st.st_nlink
            
            if IS_WINDOWS:
                try:
                    attrs = Win32IO.get_file_attributes(path)
                    if attrs is not None:
                        ih = bool(attrs & FILE_ATTRIBUTE_HIDDEN)
                        ir = bool(attrs & FILE_ATTRIBUTE_READONLY)
                        isys = bool(attrs & FILE_ATTRIBUTE_SYSTEM)
                        
                        if attrs & FILE_ATTRIBUTE_REPARSE and rp == ReparseType.NONE:
                            tag = Win32IO.get_reparse_tag(path)
                            if tag == IO_REPARSE_TAG_SYMLINK:
                                rp = ReparseType.SYMLINK
                            elif tag == IO_REPARSE_TAG_MOUNT_POINT:
                                rp = ReparseType.JUNCTION
                            else:
                                rp = ReparseType.SYMLINK
                except (AttributeError, OSError):
                    if st:
                        ir = not bool(st.st_mode & stat.S_IWRITE)
        
        return FileNode(
            full_path=path,
            file_name=name,
            extension=ext,
            size_bytes=st.st_size if st else 0,
            depth=depth,
            last_modified=st.st_mtime if st else 0.0,
            reparse_type=rp,
            is_hidden=ih,
            is_readonly=ir,
            is_system=isys,
            hard_link_count=hl,
            is_dir=is_dir,
        )
    
    def _detect_zone_identifiers(self) -> None:
        """检测 Zone.Identifier"""
        fnodes = [n for n in self.nodes if not n.is_dir]
        if not fnodes:
            return
        
        zc = [0]
        lock = threading.Lock()
        
        def _cz(nd):
            try:
                cp = _win_path(nd.full_path)
                check_path = cp if os.path.exists(cp) else nd.full_path
                if ZoneIdentifierHandler.has_zone_identifier(check_path):
                    with lock:
                        nd.has_zone_identifier = True
                        zc[0] += 1
            except (OSError, IOError):
                pass
        
        with ThreadPoolExecutor(max_workers=runtime.max_workers) as ex:
            list(ex.map(_cz, make_progress(fnodes, desc="Zone检测", unit="项")))
        
        if zc[0]:
            print(f"  发现 {zc[0]} 个含 Zone.Identifier")
    
    def _print_stats(self, verbose: bool = False) -> None:
        """打印统计信息"""
        total_files = sum(1 for n in self.nodes if not n.is_dir)
        total_dirs = sum(1 for n in self.nodes if n.is_dir)
        total_size = sum(n.size_bytes for n in self.nodes if not n.is_dir)
        
        print(f"  📄 文件: {total_files}")
        print(f"  📁 目录: {total_dirs}")
        print(f"  💾 大小: {_fmt_size(total_size)}")
        
        if verbose:
            hidden = sum(1 for n in self.nodes if n.is_hidden)
            readonly = sum(1 for n in self.nodes if n.is_readonly)
            system = sum(1 for n in self.nodes if n.is_system)
            symlinks = sum(1 for n in self.nodes if n.reparse_type == ReparseType.SYMLINK)
            
            if hidden:
                print(f"  👁️ 隐藏: {hidden}")
            if readonly:
                print(f"  🔒 只读: {readonly}")
            if system:
                print(f"  ⚙️ 系统: {system}")
            if symlinks:
                print(f"  🔗 符号链接: {symlinks}")
    
    def get_stats(self) -> Dict:
        """
        获取统计信息
        
        Returns:
            统计字典
        """
        files = [n for n in self.nodes if not n.is_dir]
        dirs = [n for n in self.nodes if n.is_dir]
        
        # 扩展名统计
        ext_stats = defaultdict(lambda: {"count": 0, "size": 0})
        for n in files:
            ext = n.extension or "(无扩展名)"
            ext_stats[ext]["count"] += 1
            ext_stats[ext]["size"] += n.size_bytes
        
        # 深度统计
        depth_stats = defaultdict(int)
        for n in self.nodes:
            depth_stats[n.depth] += 1
        
        # 大小分布
        size_ranges = {
            "< 1KB": 0,
            "1KB - 1MB": 0,
            "1MB - 100MB": 0,
            "100MB - 1GB": 0,
            "> 1GB": 0,
        }
        for n in files:
            size = n.size_bytes
            if size < 1024:
                size_ranges["< 1KB"] += 1
            elif size < 1024 * 1024:
                size_ranges["1KB - 1MB"] += 1
            elif size < 100 * 1024 * 1024:
                size_ranges["1MB - 100MB"] += 1
            elif size < 1024 * 1024 * 1024:
                size_ranges["100MB - 1GB"] += 1
            else:
                size_ranges["> 1GB"] += 1
        
        return {
            "total_files": len(files),
            "total_dirs": len(dirs),
            "total_size": sum(n.size_bytes for n in files),
            "extension_stats": dict(ext_stats),
            "depth_stats": dict(depth_stats),
            "size_distribution": size_ranges,
            "hidden_count": sum(1 for n in self.nodes if n.is_hidden),
            "readonly_count": sum(1 for n in self.nodes if n.is_readonly),
            "system_count": sum(1 for n in self.nodes if n.is_system),
        }
    
    def export_csv(self, output_path: str) -> bool:
        """
        导出扫描结果到 CSV
        
        Args:
            output_path: 输出文件路径
            
        Returns:
            是否成功
        """
        try:
            with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "路径", "文件名", "扩展名", "大小(字节)", "深度",
                    "是否目录", "是否隐藏", "是否只读", "是否系统",
                    "操作", "原因"
                ])
                
                for n in self.nodes:
                    writer.writerow([
                        n.full_path,
                        n.file_name,
                        n.extension,
                        n.size_bytes,
                        n.depth,
                        n.is_dir,
                        n.is_hidden,
                        n.is_readonly,
                        n.is_system,
                        n.action.value,
                        n.reason,
                    ])
            
            return True
        except OSError:
            return False
    
    def export_json(self, output_path: str) -> bool:
        """
        导出扫描结果到 JSON
        
        Args:
            output_path: 输出文件路径
            
        Returns:
            是否成功
        """
        try:
            data = {
                "version": VERSION,
                "root_path": self.root_path,
                "scan_timestamp": datetime.fromtimestamp(self._scan_timestamp).isoformat(),
                "stats": self.get_stats(),
                "nodes": [n.to_dict() for n in self.nodes],
            }
            
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            return True
        except OSError:
            return False
