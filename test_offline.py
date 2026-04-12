#!/usr/bin/env python3
"""
文件夹重组引擎 V5.1 — 离线模拟测试器 (V2)
在非 Windows 环境下读取文件列表，构建虚拟文件树，运行分析逻辑并输出诊断报告。

支持的输入格式:
  - PowerShell Get-ChildItem 输出 (含 FullName 表头)
  - 纯路径列表 (每行一个路径)

用法:
  python test_offline.py <文件列表.txt> [--all-files file2.txt ...]
"""

import os
import sys
import json
import hashlib
import fnmatch
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Set, Tuple
import bisect
import re
import time
import argparse

# ============================================================
# 常量
# ============================================================

CACHE_DIR_PATTERNS = [
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".tox", ".eggs", "*.egg-info", ".gradle", ".mvn",
    "target", "dist", ".next", ".nuxt",
    "vendor", ".terraform", ".serverless",
    ".cache", ".parcel-cache", ".webpack",
    "Thumbs.db", "desktop.ini", ".DS_Store",
    "ehthumbs.db", "System Volume Information",
    "$RECYCLE.BIN", "RECYCLER",
]

# 额外的垃圾文件模式（针对下载场景）
JUNK_FILE_PATTERNS = [
    "*.bc!",           # BitComet 未完成下载
    "*.td",            # Thunder 临时文件
    "*.xltd",          # 迅雷临时文件
    "*.!ut",           # uTorrent 临时文件
    "*.part",          # 部分下载
    "*.crdownload",    # Chrome 下载临时文件
]

SEP = "\\"

# 2048.cc 网站前缀（应清理）
SITE_PREFIX_PATTERN = re.compile(r"^2048\.cc-")

MAX_PATH_LEGACY = 260


class NodeAction(Enum):
    NONE = "NONE"
    DELETE = "DELETE"
    MOVE = "MOVE"


@dataclass
class VirtualFileNode:
    """轻量级文件节点，不依赖真实文件系统"""
    full_path: str
    file_name: str
    extension: str
    size_bytes: int = 0
    depth: int = 0
    is_dir: bool = False
    is_truncated: bool = False     # 是否为被截断的路径
    action: NodeAction = NodeAction.NONE
    reason: str = ""               # 标记原因

    # 去重用
    _quick_hash: Optional[str] = field(default=None, repr=False)
    _full_hash: Optional[str] = field(default=None, repr=False)

    def quick_hash(self) -> str:
        return self._quick_hash or ""

    def full_hash(self) -> str:
        return self._full_hash or ""


# ============================================================
# 文件列表加载器 — 支持 PowerShell 格式 + 截断检测
# ============================================================

def load_file_list(filepath: str) -> Tuple[List[str], int]:
    """
    加载文件列表，返回 (有效路径行列表, 截断行数)。

    处理 PowerShell Get-ChildItem | Format-Table 输出格式:
      - 自动跳过 'FullName' 表头和 '--------' 分隔线
      - 去除 Format-Table 列对齐产生的尾部空白
      - 检测被 PowerShell 控制台宽度截断的行 (以 '...' 结尾)
    """
    with open(filepath, "r", encoding="utf-8-sig") as f:
        raw_lines = f.readlines()

    valid_lines = []
    truncated_count = 0
    skipped_count = 0
    in_header = True
    found_separator = False

    for raw in raw_lines:
        line = raw.rstrip("\r\n")

        # 跳过空行
        if not line.strip():
            skipped_count += 1
            continue

        # PowerShell 表头区域：跳过 FullName 行和 --- 分隔线
        if in_header:
            stripped = line.strip()
            if stripped == "FullName":
                found_separator = True
                skipped_count += 1
                continue
            if stripped.startswith("-" * 8):
                in_header = False
                skipped_count += 1
                continue
            # 如果之前找到了 FullName，任何分隔线后面的行才是数据
            if found_separator:
                # 已经在分隔线处理中
                pass
            else:
                # 可能是 BOM 或其他前缀，继续等待
                if "FullName" in stripped:
                    found_separator = True
                    skipped_count += 1
                    continue
                # 没有找到 FullName 表头，可能是纯路径列表
                # 回退：从这一行开始当作数据
                in_header = False

        # 去除 Format-Table 列对齐产生的尾部空白
        line = line.rstrip()

        # 检测截断行：以 '...' 结尾
        if line.endswith("..."):
            truncated_count += 1
            continue  # 跳过截断行，不作为有效路径

        valid_lines.append(line)

    return valid_lines, truncated_count, skipped_count


def parse_path_to_nodes(lines: List[str]) -> Tuple[List[VirtualFileNode], str, Dict]:
    """
    将文件路径列表解析为虚拟节点列表。

    核心改进:
    1. 文件/目录推断基于前缀关系而非扩展名
    2. 提供解析统计信息
    """
    SEP = "\\"

    # ===== Phase 1: 收集所有路径，推断根 =====
    root_parts = None
    for line in lines:
        parts = line.split(SEP)
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

    root = SEP.join(root_parts) if root_parts else "F:" + SEP
    # 确保驱动器根目录有尾部反斜杠
    if re.match(r'^[A-Z]:$', root, re.IGNORECASE):
        root = root + SEP
    root_depth = len(root_parts) if root_parts else 1

    # ===== Phase 2: 基于前缀关系判断文件/目录 =====
    # 构建所有路径的排序列表 + 中间目录集合，用于二分前缀查找
    all_intermediate_dirs: Set[str] = set()

    # 先收集所有中间目录（从路径中推导）
    for line in lines:
        parts = line.split(SEP)
        for i in range(root_depth, len(parts) - 1):
            all_intermediate_dirs.add(SEP.join(parts[:i + 1]))

    # 合并：排序列表包含所有路径 + 已知的中间目录
    all_known = set(lines) | all_intermediate_dirs
    sorted_paths = sorted(all_known, key=lambda p: p.lower())

    # 快速前缀查找：判断 path 是否为其他路径的前缀
    def has_children(path: str) -> bool:
        """检查路径是否是其他路径的前缀（即它是一个目录）"""
        prefix = path.lower() + SEP
        lo = bisect.bisect_left(sorted_paths, prefix, key=lambda p: p.lower())
        if lo < len(sorted_paths):
            return sorted_paths[lo].lower().startswith(prefix)
        return False

    # 对每个路径判断：如果它是其他路径的前缀 → 目录，否则 → 文件
    is_dir_map: Dict[str, bool] = {}

    for line in lines:
        # 任何出现在中间目录集合中的路径必定是目录
        if line in all_intermediate_dirs:
            is_dir_map[line] = True
        elif has_children(line):
            is_dir_map[line] = True
        else:
            is_dir_map[line] = False

    # ===== Phase 3: 创建节点 =====
    nodes = []
    existing_paths = set()

    for line in lines:
        parts = line.split(SEP)
        rel_depth = len(parts) - root_depth
        fname = parts[-1] if parts else ""
        is_dir = is_dir_map.get(line, False)

        if is_dir:
            ext = ""
        else:
            # 有扩展名 → 提取
            if "." in fname:
                ext = "." + fname.rsplit(".", 1)[1].lower()
            else:
                ext = ""

        node = VirtualFileNode(
            full_path=line,
            file_name=fname,
            extension=ext,
            depth=rel_depth,
            is_dir=is_dir,
        )
        nodes.append(node)
        existing_paths.add(line)

        # 中间目录已在 Phase 2 收集

    # ===== Phase 4: 创建隐含目录节点 =====
    for dir_path in all_intermediate_dirs:
        if dir_path not in existing_paths:
            dir_parts = dir_path.split(SEP)
            dir_depth = len(dir_parts) - root_depth
            dir_name = dir_parts[-1]
            dir_node = VirtualFileNode(
                full_path=dir_path,
                file_name=dir_name,
                extension="",
                depth=dir_depth,
                is_dir=True,
            )
            nodes.append(dir_node)

    parse_stats = {
        "root": root,
        "total_input_lines": len(lines),
        "dirs_detected_by_prefix": sum(1 for v in is_dir_map.values() if v),
        "files_detected": sum(1 for v in is_dir_map.values() if not v),
        "implicit_dirs": len(all_intermediate_dirs) - sum(1 for v in is_dir_map.values() if v),
    }

    return nodes, root, parse_stats


# ============================================================
# 分析引擎（V5.1 逻辑的离线版）
# ============================================================

class OfflineDiagnosticEngine:
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

        # 长路径检测
        long_names = []
        for n in self.nodes:
            try:
                full_bytes = len(n.full_path.encode("utf-8"))
                if full_bytes > MAX_PATH_LEGACY:
                    long_names.append((n.full_path, full_bytes))
            except (UnicodeEncodeError, UnicodeDecodeError):
                long_names.append((n.full_path, -1))

        max_depth = max((n.depth for n in self.nodes), default=0)

        return {
            "root": self.root,
            "total_items": len(self.nodes),
            "total_files": len(files),
            "total_dirs": len(dirs),
            "max_depth": max_depth,
            "long_path_files": len(long_names),
            "long_path_examples": long_names[:5],
            "deepest_files": [(n.full_path, n.depth) for n in deepest[:5]],
            "extension_stats": dict(sorted(ext_count.items(), key=lambda x: x[1], reverse=True)[:15]),
        }


class OfflineEliminationEngine:
    def __init__(self, nodes: List[VirtualFileNode]):
        self.nodes = nodes
        self.duplicate_groups: List[Dict] = []

    def mark_cache_dirs(self, extra_patterns: List[str] = None):
        """标记缓存/临时目录和垃圾文件"""
        patterns = list(CACHE_DIR_PATTERNS)
        if extra_patterns:
            patterns.extend(extra_patterns)

        # 垃圾文件单独标记（不作为目录递归）
        for node in self.nodes:
            if node.action != NodeAction.NONE:
                continue
            if not node.is_dir:
                for pattern in JUNK_FILE_PATTERNS:
                    if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                        node.action = NodeAction.DELETE
                        node.reason = f"临时文件: {pattern}"
                        break

        # 缓存目录标记（排序 + 二分优化）
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

        # 对缓存目录标记子树
        dir_roots = [n for n in cache_roots if n.is_dir]
        if dir_roots:
            sorted_nodes = sorted(self.nodes, key=lambda n: n.full_path.lower())
            for root in dir_roots:
                prefix = root.full_path.lower() + SEP
                lo = bisect.bisect_left(sorted_nodes, prefix, key=lambda n: n.full_path.lower())
                for j in range(lo, len(sorted_nodes)):
                    if sorted_nodes[j].full_path.lower().startswith(prefix):
                        if sorted_nodes[j].action == NodeAction.NONE:
                            sorted_nodes[j].action = NodeAction.DELETE
                            sorted_nodes[j].reason = f"缓存子树: {root.file_name}"
                    else:
                        break

    def detect_site_prefixes(self):
        """检测 2048.cc- 等网站前缀文件"""
        for node in self.nodes:
            if node.is_dir or node.action != NodeAction.NONE:
                continue
            if SITE_PREFIX_PATTERN.match(node.file_name):
                node.reason = f"网站前缀: {node.file_name[:30]}..."

    def deduplicate_by_name_inference(self) -> int:
        """
        基于文件名推断去重：检测相同文件名（忽略路径）的重复。
        这是离线模式的简化版——没有真实文件内容，仅按文件名推断。
        """
        files = [n for n in self.nodes
                 if not n.is_dir and n.action == NodeAction.NONE]

        # 按文件名分组（忽略路径）
        name_groups: Dict[str, List[VirtualFileNode]] = defaultdict(list)
        for f in files:
            name_groups[f.file_name.lower()].append(f)

        dup_count = 0
        for name, group in name_groups.items():
            if len(group) < 2:
                continue

            # 保留最浅的，其余标记删除
            keep = min(group, key=lambda n: n.depth)
            group.sort(key=lambda n: n.depth)

            self.duplicate_groups.append({
                "name": name,
                "keep": keep.full_path,
                "delete": [n.full_path for n in group if n.full_path != keep.full_path],
            })

            for n in group:
                if n.full_path != keep.full_path:
                    n.action = NodeAction.DELETE
                    n.reason = f"同名重复 (保留: 深度{keep.depth})"
                    dup_count += 1

        return dup_count


class OfflineRestructureEngine:
    def __init__(self, nodes: List[VirtualFileNode], root: str):
        self.nodes = nodes
        self.root = root

    def unpack_redundant(self) -> int:
        r"""解包冗余目录 (A\A\ → A\)"""
        unpacked = 0
        dirs = [n for n in self.nodes if n.is_dir and n.action == NodeAction.NONE]
        dir_children = defaultdict(list)
        for d in dirs:
            parent = SEP.join(d.full_path.split(SEP)[:-1])
            dir_children[parent.lower()].append(d)

        for parent_lower, children in dir_children.items():
            if len(children) != 1:
                continue
            child = children[0]
            parent_name = parent_lower.split(SEP)[-1] if parent_lower != self.root.lower() else ""
            if not parent_name or parent_name != child.file_name.lower():
                continue

            actual_parent = SEP.join(child.full_path.split(SEP)[:-1])
            child_prefix = child.full_path.lower() + SEP
            for node in self.nodes:
                if node.full_path.lower().startswith(child_prefix) and \
                   node.action == NodeAction.NONE:
                    rel = node.full_path[len(child.full_path):].lstrip(SEP)
                    node.full_path = SEP.join([actual_parent, rel]) if rel else actual_parent
                    node.reason = f"解包: {child.file_name}"
                    node.action = NodeAction.MOVE

            child.action = NodeAction.DELETE
            child.reason = "冗余目录（子项已提级）"
            unpacked += 1

        return unpacked


# ============================================================
# 报告生成
# ============================================================

def print_report(stats: Dict, parse_stats: Dict,
                 cache_count: int, junk_count: int,
                 dup_count: int, dup_groups: List[Dict],
                 site_prefix_count: int, unpacked: int,
                 nodes: List[VirtualFileNode],
                 truncated_count: int, skipped_count: int):
    sep_line = "=" * 65
    print(f"\n{sep_line}")
    print("📊 离线诊断报告 — 文件夹重组引擎 V5.1 模拟测试")
    print(sep_line)
    print(f"  路径: {stats['root']}")
    print(f"  总项数: {stats['total_items']} (文件 {stats['total_files']}, 目录 {stats['total_dirs']})")
    print(f"  最大深度: {stats['max_depth']}")

    # ===== 截断警告 =====
    if truncated_count > 0:
        total_input = parse_stats["total_input_lines"]
        pct = truncated_count / (total_input + truncated_count) * 100
        print(f"\n  ⛔ 截断警告: {truncated_count} 行 ({pct:.1f}%) 被 PowerShell 截断，已跳过")
        print(f"     原因: PowerShell 默认控制台宽度 (~119字符) 截断了长路径")
        print(f"     解决: 使用以下命令重新导出（无截断）:")
        print(f"       (Get-ChildItem -Path 'F:\\' -Recurse).FullName | Out-File 'list.txt' -Encoding UTF8")
        print(f"       或者: dir /s /b > list.txt")

    if skipped_count > 0:
        print(f"  ℹ️  跳过行: {skipped_count} (表头/空行)")

    # 文件/目录检测统计
    print(f"\n  📂 路径解析: {parse_stats['files_detected']} 文件 + "
          f"{parse_stats['dirs_detected_by_prefix']} 目录 "
          f"(基于前缀关系推断)")
    if parse_stats["implicit_dirs"] > 0:
        print(f"     补充隐含目录: {parse_stats['implicit_dirs']} 个")

    if stats["long_path_files"]:
        print(f"  ⚠️  超长路径 (>260字节): {stats['long_path_files']} 个")

    # 扩展名
    print(f"\n📋 扩展名分布 (Top 10):")
    for ext, count in sorted(stats["extension_stats"].items(),
                             key=lambda x: x[1], reverse=True)[:10]:
        bar = "█" * min(40, count // 500)
        print(f"  {ext:15s} {count:6d}  {bar}")

    # 最深路径
    if stats["deepest_files"]:
        print(f"\n📁 层级最深:")
        for path, depth in stats["deepest_files"]:
            print(f"  深度 {depth}: {path[:100]}")

    # 操作统计
    print(f"\n🔧 推荐操作:")
    print(f"  🗑️ 垃圾文件 (.bc!/.td 等):  {junk_count} 项")
    print(f"  🗑️ 缓存目录/文件:            {cache_count} 项")
    print(f"  🔄 冗余目录解包:             {unpacked} 项")
    print(f"  🗑️ 同名重复文件:             {dup_count} 项")
    print(f"  ✏️ 网站前缀清理:             {site_prefix_count} 项")

    total_delete = junk_count + cache_count + dup_count
    print(f"\n  📊 总计可清理: {total_delete} 项 (不含前缀重命名)")

    # 去重详情
    if dup_groups:
        print(f"\n📋 重复文件详情 (前 15 组):")
        for i, g in enumerate(dup_groups[:15], 1):
            print(f"  组 {i}: {g['name'][:60]}")
            print(f"    ✅ 保留: {g['keep'][:90]}")
            for d in g["delete"][:3]:
                print(f"    🗑️ 删除: {d[:90]}")
            if len(g["delete"]) > 3:
                print(f"    ... +{len(g['delete'])-3} 个重复")

    # 清理预览
    deleted = [n for n in nodes if n.action == NodeAction.DELETE]
    if deleted:
        print(f"\n🗑️ 删除操作预览 (前 30 项):")
        for i, n in enumerate(deleted[:30], 1):
            print(f"  {i:4d}. {n.file_name[:50]:50s} ← {n.reason}")
        if len(deleted) > 30:
            print(f"  ... 还有 {len(deleted) - 30} 项")

    # 网站前缀清理预览
    site_nodes = [n for n in nodes if "网站前缀" in n.reason]
    if site_nodes:
        print(f"\n✏️ 网站前缀清理预览 (前 15 项):")
        for i, n in enumerate(site_nodes[:15], 1):
            cleaned = SITE_PREFIX_PATTERN.sub("", n.file_name)
            print(f"  {i:4d}. {n.file_name[:40]} → {cleaned[:40]}")
        if len(site_nodes) > 15:
            print(f"  ... 还有 {len(site_nodes) - 15} 项")

    # 路径超长检测
    long_paths = [(n, len(n.full_path.encode("utf-8"))) for n in nodes
                  if len(n.full_path.encode("utf-8")) > MAX_PATH_LEGACY]
    if long_paths:
        print(f"\n⚠️  超长路径详情:")
        for n, blen in long_paths[:5]:
            print(f"  {blen} 字节: {n.full_path[:100]}")

    # 深度统计
    depth_counter = Counter(n.depth for n in nodes)
    print(f"\n📏 深度分布:")
    for depth in sorted(depth_counter.keys()):
        cnt = depth_counter[depth]
        bar = "█" * min(50, cnt // 200)
        print(f"  深度 {depth:2d}: {cnt:6d}  {bar}")

    # 总结
    moved = sum(1 for n in nodes if n.action == NodeAction.MOVE)
    print(f"\n{sep_line}")
    print(f"✅ 诊断完成")
    print(f"   可删除: {total_delete} 项")
    print(f"   可重命名(去前缀): {site_prefix_count} 项")
    print(f"   可移动(解包): {moved} 项")
    print(f"   无操作: {sum(1 for n in nodes if n.action == NodeAction.NONE)} 项")
    print(sep_line)


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="文件夹重组引擎 V5.1 — 离线模拟测试器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python test_offline.py list.txt
  python test_offline.py list.txt --extra-patterns .svn .git
  python test_offline.py list_part_1.txt list_part_2.txt

导出无截断文件列表的 PowerShell 命令:
  (Get-ChildItem -Path 'F:\\' -Recurse).FullName | Out-File 'list.txt' -Encoding UTF8
  dir /s /b F:\\ > list.txt
        """
    )
    parser.add_argument("files", nargs="+", help="文件列表 (支持多个分片文件)")
    parser.add_argument("--extra-patterns", nargs="+", help="额外的缓存匹配模式")
    parser.add_argument("--export-json", metavar="FILE", help="导出诊断报告为 JSON")

    args = parser.parse_args()

    all_lines = []
    total_truncated = 0
    total_skipped = 0

    for filepath in args.files:
        if not os.path.isfile(filepath):
            print(f"❌ 文件不存在: {filepath}")
            sys.exit(1)

        print(f"📂 加载文件列表: {filepath}")
        lines, truncated, skipped = load_file_list(filepath)
        all_lines.extend(lines)
        total_truncated += truncated
        total_skipped += skipped
        print(f"   有效路径: {len(lines)} 行, 截断跳过: {truncated}, 表头/空行跳过: {skipped}")

    if not all_lines:
        print("❌ 没有有效的路径数据")
        sys.exit(1)

    # 去重
    before_dedup = len(all_lines)
    all_lines = list(dict.fromkeys(all_lines))  # 保序去重
    if len(all_lines) < before_dedup:
        print(f"   跨文件去重: {before_dedup} → {len(all_lines)} 行 (去除 {before_dedup - len(all_lines)} 重复)")

    print(f"\n   共 {len(all_lines)} 条唯一路径")
    if total_truncated > 0:
        pct = total_truncated / (len(all_lines) + total_truncated) * 100
        print(f"   ⛔ 警告: {total_truncated} 行 ({pct:.1f}%) 被截断，数据不完整！")

    t0 = time.time()
    nodes, root, parse_stats = parse_path_to_nodes(all_lines)
    print(f"   构建 {len(nodes)} 个节点 ({parse_stats['files_detected']} 文件 + "
          f"{parse_stats['dirs_detected_by_prefix']} 目录 + "
          f"{parse_stats['implicit_dirs']} 隐含目录)")

    # 诊断
    diag = OfflineDiagnosticEngine(nodes, root)
    stats = diag.get_stats()

    # 分析
    print("🔧 分析文件结构...")
    elim = OfflineEliminationEngine(nodes)

    # 缓存/垃圾文件标记
    elim.mark_cache_dirs(extra_patterns=args.extra_patterns)
    cache_count = sum(1 for n in nodes if n.action == NodeAction.DELETE and "缓存" in n.reason)
    junk_count = sum(1 for n in nodes if n.action == NodeAction.DELETE and "临时文件" in n.reason)

    # 网站前缀检测
    elim.detect_site_prefixes()
    site_prefix_count = sum(1 for n in nodes if "网站前缀" in n.reason)

    # 去重
    dup_count = elim.deduplicate_by_name_inference()
    dup_groups = elim.duplicate_groups

    # 结构重组
    restruct = OfflineRestructureEngine(nodes, root)
    unpacked = restruct.unpack_redundant()

    elapsed = time.time() - t0
    print(f"   分析完成 ({elapsed:.2f}s)")

    # 输出报告
    print_report(stats, parse_stats, cache_count, junk_count, dup_count,
                 dup_groups, site_prefix_count, unpacked, nodes,
                 total_truncated, total_skipped)

    # JSON 导出
    if args.export_json:
        report = {
            "stats": stats,
            "parse_stats": parse_stats,
            "truncated_lines": total_truncated,
            "operations": {
                "cache_cleanup": cache_count,
                "junk_cleanup": junk_count,
                "duplicates": dup_count,
                "redundant_unpack": unpacked,
                "site_prefix_cleanup": site_prefix_count,
            },
            "duplicate_groups": dup_groups[:50],
        }
        with open(args.export_json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n📄 JSON 报告已导出: {args.export_json}")


if __name__ == "__main__":
    main()
