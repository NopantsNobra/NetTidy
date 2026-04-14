"""
离线分析模块
离线分析功能 - 从保存的扫描结果进行分析
"""

import json
import os
from datetime import datetime
from typing import List, Dict, Optional

from .常量定义 import VERSION, NodeAction
from .数据模型 import FileNode
from .剔除引擎 import EliminationEngine
from .扁平化引擎 import FlattenEngine
from .重组引擎 import RestructureEngine
from .工具函数 import _fmt_size


# ═══════════════════════════════════════════════════════════
# OfflineAnalyzer 类
# ═══════════════════════════════════════════════════════════

class OfflineAnalyzer:
    """
    离线分析器
    从保存的扫描结果进行分析，无需重新扫描
    """
    
    def __init__(self, scan_file: str):
        """
        初始化离线分析器
        
        Args:
            scan_file: 扫描结果文件路径
        """
        self.scan_file = scan_file
        self.data: Dict = {}
        self.nodes: List[FileNode] = []
        self.root_path: str = ""
        self._load()
    
    def _load(self) -> None:
        """加载扫描结果"""
        try:
            with open(self.scan_file, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            
            self.root_path = self.data.get("root_path", "")
            
            # 重建节点列表
            for n in self.data.get("nodes", []):
                node = FileNode(
                    full_path=n["full_path"],
                    file_name=n["file_name"],
                    extension=n["extension"],
                    size_bytes=n["size_bytes"],
                    depth=n["depth"],
                    last_modified=n["last_modified"],
                    is_dir=n["is_dir"],
                    is_hidden=n["is_hidden"],
                    is_readonly=n["is_readonly"],
                    is_system=n["is_system"],
                )
                self.nodes.append(node)
            
            print(f"✅ 加载扫描结果: {len(self.nodes)} 个节点")
        
        except (OSError, json.JSONDecodeError) as e:
            print(f"❌ 无法加载扫描结果: {e}")
    
    def analyze(self, mode: str = "all") -> Dict:
        """
        执行离线分析
        
        Args:
            mode: 分析模式 (cache/junk/dup/flatten/restructure/all)
            
        Returns:
            分析结果
        """
        if not self.nodes:
            print("❌ 无数据可分析")
            return {}
        
        results = {}
        
        if mode in ("cache", "all"):
            results["cache"] = self._analyze_cache()
        
        if mode in ("junk", "all"):
            results["junk"] = self._analyze_junk()
        
        if mode in ("dup", "all"):
            results["dup"] = self._analyze_duplicates()
        
        if mode in ("flatten", "all"):
            results["flatten"] = self._analyze_flatten()
        
        if mode in ("restructure", "all"):
            results["restructure"] = self._analyze_restructure()
        
        return results
    
    def _analyze_cache(self) -> Dict:
        """分析缓存目录"""
        from .常量定义 import CACHE_DIR_PATTERNS
        import fnmatch
        
        cache_nodes = []
        for n in self.nodes:
            for p in CACHE_DIR_PATTERNS:
                if fnmatch.fnmatch(n.file_name.lower(), p.lower()):
                    cache_nodes.append(n)
                    break
        
        total_size = sum(n.size_bytes for n in cache_nodes)
        
        print(f"\n📦 缓存目录分析")
        print(f"  发现: {len(cache_nodes)} 个缓存目录")
        print(f"  大小: {_fmt_size(total_size)}")
        
        return {
            "count": len(cache_nodes),
            "size": total_size,
            "nodes": cache_nodes,
        }
    
    def _analyze_junk(self) -> Dict:
        """分析垃圾文件"""
        from .常量定义 import JUNK_FILE_PATTERNS
        import fnmatch
        
        junk_nodes = []
        for n in self.nodes:
            if n.is_dir:
                continue
            for p in JUNK_FILE_PATTERNS:
                if fnmatch.fnmatch(n.file_name.lower(), p.lower()):
                    junk_nodes.append(n)
                    break
        
        total_size = sum(n.size_bytes for n in junk_nodes)
        
        print(f"\n🗑️ 垃圾文件分析")
        print(f"  发现: {len(junk_nodes)} 个垃圾文件")
        print(f"  大小: {_fmt_size(total_size)}")
        
        return {
            "count": len(junk_nodes),
            "size": total_size,
            "nodes": junk_nodes,
        }
    
    def _analyze_duplicates(self) -> Dict:
        """分析重复文件"""
        # 按大小分组
        size_groups: Dict[int, List[FileNode]] = {}
        for n in self.nodes:
            if n.is_dir or n.size_bytes == 0:
                continue
            if n.size_bytes not in size_groups:
                size_groups[n.size_bytes] = []
            size_groups[n.size_bytes].append(n)
        
        # 找出潜在重复
        potential_dups = {
            k: v for k, v in size_groups.items()
            if len(v) > 1
        }
        
        dup_count = sum(len(v) - 1 for v in potential_dups.values())
        dup_size = sum(k * (len(v) - 1) for k, v in potential_dups.items())
        
        print(f"\n🔄 重复文件分析")
        print(f"  潜在重复: {dup_count} 个文件")
        print(f"  可释放: {_fmt_size(dup_size)}")
        
        return {
            "count": dup_count,
            "size": dup_size,
            "groups": len(potential_dups),
        }
    
    def _analyze_flatten(self) -> Dict:
        """分析扁平化机会"""
        # 统计深度分布
        depth_dist: Dict[int, int] = {}
        for n in self.nodes:
            depth = n.depth
            if depth not in depth_dist:
                depth_dist[depth] = 0
            depth_dist[depth] += 1
        
        max_depth = max(depth_dist.keys()) if depth_dist else 0
        deep_files = sum(c for d, c in depth_dist.items() if d > 5)
        
        print(f"\n📊 扁平化分析")
        print(f"  最大深度: {max_depth}")
        print(f"  深层文件 (>5): {deep_files}")
        
        return {
            "max_depth": max_depth,
            "deep_files": deep_files,
            "depth_distribution": depth_dist,
        }
    
    def _analyze_restructure(self) -> Dict:
        """分析重组机会"""
        # 统计扩展名分布
        ext_dist: Dict[str, int] = {}
        for n in self.nodes:
            if n.is_dir:
                continue
            ext = n.extension or "no_extension"
            if ext not in ext_dist:
                ext_dist[ext] = 0
            ext_dist[ext] += 1
        
        # 按数量排序
        sorted_ext = sorted(ext_dist.items(), key=lambda x: x[1], reverse=True)
        
        print(f"\n📁 重组分析")
        print(f"  扩展名种类: {len(ext_dist)}")
        print(f"  主要扩展名:")
        for ext, count in sorted_ext[:5]:
            print(f"    .{ext}: {count} 个")
        
        return {
            "extension_count": len(ext_dist),
            "top_extensions": sorted_ext[:10],
            "distribution": ext_dist,
        }
    
    def generate_report(self, output_path: str) -> bool:
        """
        生成离线分析报告
        
        Args:
            output_path: 输出文件路径
            
        Returns:
            是否成功
        """
        results = self.analyze()
        
        if not results:
            return False
        
        try:
            report = {
                "version": VERSION,
                "scan_file": self.scan_file,
                "root_path": self.root_path,
                "timestamp": datetime.now().isoformat(),
                "total_nodes": len(self.nodes),
                "analysis": {
                    "cache": {
                        "count": results.get("cache", {}).get("count", 0),
                        "size": results.get("cache", {}).get("size", 0),
                    },
                    "junk": {
                        "count": results.get("junk", {}).get("count", 0),
                        "size": results.get("junk", {}).get("size", 0),
                    },
                    "duplicates": {
                        "count": results.get("dup", {}).get("count", 0),
                        "size": results.get("dup", {}).get("size", 0),
                    },
                    "flatten": {
                        "max_depth": results.get("flatten", {}).get("max_depth", 0),
                        "deep_files": results.get("flatten", {}).get("deep_files", 0),
                    },
                    "restructure": {
                        "extension_count": results.get("restructure", {}).get("extension_count", 0),
                    },
                },
            }
            
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            
            print(f"\n✅ 报告已保存: {output_path}")
            return True
        
        except OSError as e:
            print(f"❌ 无法保存报告: {e}")
            return False


# ═══════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════

def analyze_offline(scan_file: str, output_report: str = None) -> Dict:
    """
    离线分析便捷函数
    
    Args:
        scan_file: 扫描结果文件路径
        output_report: 输出报告路径（可选）
        
    Returns:
        分析结果
    """
    analyzer = OfflineAnalyzer(scan_file)
    results = analyzer.analyze()
    
    if output_report:
        analyzer.generate_report(output_report)
    
    return results
