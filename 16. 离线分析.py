"""
离线分析模块
=============
职责：从文件列表进行离线分析和诊断

对外接口：
    - OfflineAnalyzer  离线分析器

依赖关系：
    - 数据模型 (VirtualFileNode)
    - 常量定义 (VERSION, CACHE_DIR_PATTERNS, JUNK_FILE_PATTERNS)
    - 工具函数 (_fmt_size)

使用示例：
    from 离线分析 import OfflineAnalyzer
    analyzer = OfflineAnalyzer("scan_result.json")
    analyzer.analyze()

版本历史：
    9.0: 支持 JSON 格式
    8.0: 初始模块化版本
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from datetime import datetime
from typing import List, Dict, Optional, Any

from .常量定义 import (
    VERSION,
    CACHE_DIR_PATTERNS,
    JUNK_FILE_PATTERNS,
)
from .数据模型 import VirtualFileNode
from .工具函数 import _fmt_size


# ═══════════════════════════════════════════════════════════════════
# OfflineAnalyzer
# ═══════════════════════════════════════════════════════════════════

class OfflineAnalyzer:
    """
    离线分析器
    
    从文件列表或扫描结果 JSON 进行分析，无需重新扫描。
    
    使用示例：
        >>> analyzer = OfflineAnalyzer()
        >>> analyzer.load_from_json("scan.json")
        >>> analyzer.analyze()
        >>> analyzer.generate_report("report.json")
    """
    
    def __init__(self):
        """初始化离线分析器"""
        self.nodes: List[VirtualFileNode] = []
        self.root_path = ""
        self.scan_timestamp = ""
        self.results: Dict[str, Any] = {}
    
    def load_from_json(self, json_path: str) -> bool:
        """
        从 JSON 文件加载扫描结果
        
        Args:
            json_path: JSON 文件路径
            
        Returns:
            是否成功
        """
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            self.root_path = data.get("root_path", "")
            self.scan_timestamp = data.get("scan_timestamp", "")
            
            # 重建节点
            for node_data in data.get("nodes", []):
                node = VirtualFileNode(
                    full_path=node_data["full_path"],
                    file_name=node_data["file_name"],
                    extension=node_data.get("extension", ""),
                    size_bytes=node_data.get("size_bytes", 0),
                    depth=node_data.get("depth", 0),
                    is_dir=node_data.get("is_dir", False),
                )
                self.nodes.append(node)
            
            print(f"✅ 加载扫描结果: {len(self.nodes)} 个节点")
            return True
        
        except (OSError, json.JSONDecodeError) as e:
            print(f"❌ 加载失败: {e}")
            return False
    
    def load_from_list(self, file_path: str) -> int:
        """
        从文件列表加载
        
        Args:
            file_path: 文件路径
            
        Returns:
            加载的节点数量
        """
        # 检测截断行
        truncated_re = re.compile(r'\.\.\.\s*$')
        
        valid_lines = []
        truncated_count = 0
        skipped_count = 0
        
        with open(file_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.rstrip()
                
                if not line.strip():
                    skipped_count += 1
                    continue
                
                if truncated_re.search(line):
                    truncated_count += 1
                    continue
                
                valid_lines.append(line)
        
        # 去重
        seen = set()
        unique_lines = []
        for line in valid_lines:
            if line not in seen:
                seen.add(line)
                unique_lines.append(line)
        
        if len(unique_lines) < len(valid_lines):
            print(f"  去重: {len(valid_lines)} → {len(unique_lines)} 行")
        
        # 解析根目录
        root_parts = None
        for line in unique_lines:
            if os.sep not in line and ":" not in line:
                continue
            parts = line.split(os.sep if os.sep in line else "\\")
            if root_parts is None:
                root_parts = list(parts)
            else:
                for i, (a, b) in enumerate(zip(root_parts, parts)):
                    if a == b:
                        continue
                    root_parts = root_parts[:i]
                    break
        
        self.root_path = os.sep.join(root_parts) if root_parts else ""
        
        # 解析节点
        for line in unique_lines:
            parts = line.split(os.sep if os.sep in line else "\\")
            depth = len(parts) - len(root_parts) if root_parts else 0
            
            node = VirtualFileNode(
                full_path=line,
                file_name=parts[-1] if parts else "",
                extension=self._get_extension(parts[-1] if parts else ""),
                depth=max(0, depth),
            )
            self.nodes.append(node)
        
        if truncated_count > 0:
            print(f"⚠️ 跳过 {truncated_count} 行截断数据")
        
        print(f"✅ 加载文件列表: {len(self.nodes)} 个节点")
        return len(self.nodes)
    
    def _get_extension(self, filename: str) -> str:
        """获取扩展名"""
        if "." not in filename:
            return ""
        return "." + filename.rsplit(".", 1)[1].lower()
    
    def analyze(self) -> Dict[str, Any]:
        """
        执行分析
        
        Returns:
            分析结果
        """
        print("\n📊 离线分析:")
        
        results = {
            "cache": self._analyze_cache(),
            "junk": self._analyze_junk(),
            "dup": self._analyze_duplicates(),
            "stats": self._get_stats(),
        }
        
        self.results = results
        return results
    
    def _analyze_cache(self) -> Dict[str, Any]:
        """分析缓存目录"""
        cache_nodes = []
        
        for node in self.nodes:
            if node.is_dir:
                for pattern in CACHE_DIR_PATTERNS:
                    if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                        cache_nodes.append(node)
                        break
        
        print(f"\n📦 缓存目录: {len(cache_nodes)} 个")
        return {"count": len(cache_nodes), "nodes": cache_nodes}
    
    def _analyze_junk(self) -> Dict[str, Any]:
        """分析垃圾文件"""
        junk_nodes = []
        
        for node in self.nodes:
            if node.is_dir:
                continue
            for pattern in JUNK_FILE_PATTERNS:
                if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                    junk_nodes.append(node)
                    break
        
        print(f"🗑️ 垃圾文件: {len(junk_nodes)} 个")
        return {"count": len(junk_nodes), "nodes": junk_nodes}
    
    def _analyze_duplicates(self) -> Dict[str, Any]:
        """分析重复文件（按名称）"""
        # 按名称分组
        name_groups: Dict[str, List[VirtualFileNode]] = {}
        for node in self.nodes:
            if node.is_dir:
                continue
            name = node.file_name.lower()
            if name not in name_groups:
                name_groups[name] = []
            name_groups[name].append(node)
        
        # 按大小筛选真正的重复
        true_dups = []
        for name, nodes in name_groups.items():
            if len(nodes) < 2:
                continue
            
            # 检查是否有相同大小
            size_groups = {}
            for n in nodes:
                if n.size_bytes not in size_groups:
                    size_groups[n.size_bytes] = []
                size_groups[n.size_bytes].append(n)
            
            for size, same_size_nodes in size_groups.items():
                if len(same_size_nodes) > 1:
                    true_dups.append({
                        "name": name,
                        "size": size,
                        "count": len(same_size_nodes),
                    })
        
        print(f"🔄 重复文件: {len(true_dups)} 组")
        return {"count": sum(d["count"] - 1 for d in true_dups), "groups": len(true_dups)}
    
    def _get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        files = [n for n in self.nodes if not n.is_dir]
        dirs = [n for n in self.nodes if n.is_dir]
        
        # 扩展名统计
        ext_count = {}
        for n in files:
            ext = n.extension or "(无扩展名)"
            ext_count[ext] = ext_count.get(ext, 0) + 1
        
        # 深度统计
        depth_count = {}
        for n in self.nodes:
            depth_count[n.depth] = depth_count.get(n.depth, 0) + 1
        
        return {
            "total_files": len(files),
            "total_dirs": len(dirs),
            "total_nodes": len(self.nodes),
            "max_depth": max((n.depth for n in self.nodes), default=0),
            "extension_stats": dict(sorted(ext_count.items(), key=lambda x: x[1], reverse=True)[:20]),
            "depth_stats": dict(sorted(depth_count.items())),
        }
    
    def generate_report(self, output_path: str) -> bool:
        """
        生成分析报告
        
        Args:
            output_path: 输出文件路径
            
        Returns:
            是否成功
        """
        if not self.results:
            self.analyze()
        
        report = {
            "version": VERSION,
            "root_path": self.root_path,
            "timestamp": datetime.now().isoformat(),
            "scan_timestamp": self.scan_timestamp,
            "stats": self.results.get("stats", {}),
            "analysis": {
                "cache_count": self.results.get("cache", {}).get("count", 0),
                "junk_count": self.results.get("junk", {}).get("count", 0),
                "dup_count": self.results.get("dup", {}).get("count", 0),
            },
        }
        
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"✅ 报告已保存: {output_path}")
            return True
        except OSError as e:
            print(f"❌ 保存失败: {e}")
            return False


# ═══════════════════════════════════════════════════════════════════
# 导出
# ═══════════════════════════════════════════════════════════════════

__all__ = [
    "OfflineAnalyzer",
]
