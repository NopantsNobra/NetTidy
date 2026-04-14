"""
报告生成模块
=============
职责：生成 HTML/JSON 格式的操作报告

对外接口：
    - HTMLReportGenerator  HTML 报告生成器

依赖关系：
    - 数据模型 (FileNode, ScanResult)
    - 常量定义 (VERSION)
    - 工具函数 (_fmt_size, _escape_html)

使用示例：
    from 报告生成 import HTMLReportGenerator
    gen = HTMLReportGenerator()
    gen.generate(stats, nodes, dup_groups, freed_bytes, "report.html")

版本历史：
    10.0: 最终审计版本，修正逻辑错误与文档一致性
    9.0: 现代化 UI、响应式设计
    8.0: 初始模块化版本
"""

from __future__ import annotations

import html as html_mod
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

from .常量定义 import VERSION
from .数据模型 import FileNode, ScanResult, DuplicateGroup
from .工具函数 import _fmt_size, _escape_html


# ═══════════════════════════════════════════════════════════════════
# HTMLReportGenerator
# ═══════════════════════════════════════════════════════════════════

class HTMLReportGenerator:
    """
    HTML 报告生成器
    
    生成自包含的 HTML 格式操作报告。
    支持暗色主题、响应式设计。
    
    使用示例：
        >>> gen = HTMLReportGenerator()
        >>> path = gen.generate(stats, nodes, dup_groups, freed, "report.html")
    """
    
    def __init__(self):
        """初始化报告生成器"""
        self.output_path = ""
    
    def generate(
        self,
        stats: Dict[str, Any],
        nodes: List[FileNode],
        dup_groups: List[DuplicateGroup],
        freed_bytes: int,
        output_path: str,
    ) -> str:
        """
        生成 HTML 报告
        
        Args:
            stats: 统计信息
            nodes: 文件节点列表
            dup_groups: 重复文件组
            freed_bytes: 释放的字节数
            output_path: 输出文件路径
            
        Returns:
            输出文件路径
        """
        self.output_path = output_path
        
        # 准备数据
        freed_mb = freed_bytes / (1024 * 1024)
        
        delete_nodes = [n for n in nodes if n.action.value == "DELETE"]
        move_nodes = [n for n in nodes if n.action.value == "MOVE"]
        hardlink_nodes = [n for n in nodes if n.action.value == "HARDLINK"]
        
        delete_size = sum(n.size_bytes for n in delete_nodes)
        hardlink_size = sum(n.size_bytes for n in hardlink_nodes)
        
        # 扩展名统计
        ext_data = stats.get("extension_stats", {})
        ext_top = list(ext_data.items())[:10]
        
        # 颜色列表
        colors = ["#3fb950", "#58a6ff", "#d29922", "#bc8cff", "#f85149"]
        
        # 生成 HTML
        html = self._generate_html(
            stats=stats,
            freed_mb=freed_mb,
            delete_count=len(delete_nodes),
            delete_size=delete_size,
            move_count=len(move_nodes),
            hardlink_count=len(hardlink_nodes),
            hardlink_size=hardlink_size,
            dup_groups=dup_groups,
            ext_top=ext_top,
            colors=colors,
        )
        
        # 写入文件
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        
        return output_path
    
    def _generate_html(
        self,
        stats: Dict,
        freed_mb: float,
        delete_count: int,
        delete_size: int,
        move_count: int,
        hardlink_count: int,
        hardlink_size: int,
        dup_groups: List[DuplicateGroup],
        ext_top: List,
        colors: List,
    ) -> str:
        """生成 HTML 内容"""
        
        # 格式化当前时间
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 按原因分组
        reason_groups = {}
        for n in dup_groups:
            reason = n.action
            if reason not in reason_groups:
                reason_groups[reason] = []
            reason_groups[reason].append(n)
        
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>文件夹整理报告 V{VERSION}</title>
<style>
:root {{
    --bg: #0d1117;
    --card: #161b22;
    --border: #30363d;
    --text: #c9d1d9;
    --text-dim: #8b949e;
    --accent: #58a6ff;
    --green: #3fb950;
    --red: #f85149;
    --yellow: #d29922;
    --purple: #bc8cff;
    --orange: #db6d28;
}}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    padding: 20px;
    max-width: 1200px;
    margin: 0 auto;
}}

h1 {{ color: var(--accent); margin-bottom: 0.5rem; }}
h2 {{ color: var(--accent); margin: 1.5rem 0 1rem; font-size: 1.2rem; border-bottom: 1px solid var(--border); padding-bottom: 0.5rem; }}
h3 {{ color: var(--text); margin: 1rem 0 0.5rem; font-size: 1rem; }}

.subtitle {{ color: var(--text-dim); margin-bottom: 2rem; font-size: 0.9rem; }}

.summary {{
    background: linear-gradient(135deg, #161b22, #1c2333);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 2rem;
    text-align: center;
    margin: 2rem 0;
}}

.summary .big {{ font-size: 3rem; font-weight: 700; color: var(--green); }}
.summary .sub {{ color: var(--text-dim); margin-top: 0.5rem; }}

.cards {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 1rem;
    margin: 1.5rem 0;
}}

.card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem;
    text-align: center;
}}

.card .value {{ font-size: 1.8rem; font-weight: 700; color: var(--accent); }}
.card.green .value {{ color: var(--green); }}
.card.red .value {{ color: var(--red); }}
.card.yellow .value {{ color: var(--yellow); }}
.card.purple .value {{ color: var(--purple); }}
.card .label {{ color: var(--text-dim); font-size: 0.85rem; margin-top: 0.3rem; }}

.bar-chart {{ margin: 1rem 0; }}
.bar-row {{ display: flex; align-items: center; margin: 0.4rem 0; }}
.bar-label {{ width: 100px; font-size: 0.85rem; color: var(--text-dim); text-align: right; padding-right: 1rem; flex-shrink: 0; }}
.bar-track {{ flex: 1; background: var(--card); border-radius: 4px; height: 24px; overflow: hidden; }}
.bar-fill {{ height: 100%; border-radius: 4px; display: flex; align-items: center; padding-left: 0.5rem; font-size: 0.8rem; transition: width 0.5s ease; min-width: 30px; }}

table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.9rem; }}
th {{ background: var(--card); color: var(--text-dim); text-align: left; padding: 0.6rem 0.8rem; border-bottom: 2px solid var(--border); position: sticky; top: 0; }}
td {{ padding: 0.5rem 0.8rem; border-bottom: 1px solid var(--border); word-break: break-all; }}
tr:hover {{ background: rgba(88, 166, 255, 0.05); }}

.badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.8em;
}}
.badge-delete {{ background: var(--red); }}
.badge-move {{ background: var(--accent); }}
.badge-hardlink {{ background: var(--purple); }}

.tree {{
    font-family: 'Cascadia Code', Consolas, monospace;
    font-size: 0.85rem;
    padding: 1rem;
    background: var(--card);
    border-radius: 8px;
    max-height: 300px;
    overflow-y: auto;
    white-space: pre;
}}

.footer {{ margin-top: 3rem; text-align: center; color: var(--text-dim); font-size: 0.8rem; }}

@media (max-width: 768px) {{
    body {{ padding: 1rem; }}
    .cards {{ grid-template-columns: repeat(2, 1fr); }}
    h1 {{ font-size: 1.5rem; }}
}}
</style>
</head>
<body>

<h1>📊 文件夹整理报告</h1>
<p class="subtitle">引擎版本 V{VERSION} | 生成时间: {now}</p>

<div class="summary">
    <div class="big">{freed_mb:.1f} MB</div>
    <div class="sub">预计释放空间</div>
</div>

<div class="cards">
    <div class="card">
        <div class="value">{stats.get('total_files', 0):,}</div>
        <div class="label">文件数</div>
    </div>
    <div class="card">
        <div class="value">{stats.get('total_dirs', 0):,}</div>
        <div class="label">目录数</div>
    </div>
    <div class="card green">
        <div class="value">{delete_count}</div>
        <div class="label">待删除</div>
    </div>
    <div class="card">
        <div class="value">{move_count}</div>
        <div class="label">待移动</div>
    </div>
    <div class="card purple">
        <div class="value">{hardlink_count}</div>
        <div class="label">硬链接</div>
    </div>
    <div class="card yellow">
        <div class="value">{len(dup_groups)}</div>
        <div class="label">重复组</div>
    </div>
</div>

<h2>📦 操作分布</h2>
<div class="bar-chart">
    <div class="bar-row">
        <div class="bar-label">删除</div>
        <div class="bar-track">
            <div class="bar-fill" style="width:{self._bar_pct(delete_count, max(delete_count, move_count, hardlink_count, 1))}%; background: var(--red);">{delete_count}</div>
        </div>
    </div>
    <div class="bar-row">
        <div class="bar-label">移动</div>
        <div class="bar-track">
            <div class="bar-fill" style="width:{self._bar_pct(move_count, max(delete_count, move_count, hardlink_count, 1))}%; background: var(--accent);">{move_count}</div>
        </div>
    </div>
    <div class="bar-row">
        <div class="bar-label">硬链接</div>
        <div class="bar-track">
            <div class="bar-fill" style="width:{self._bar_pct(hardlink_count, max(delete_count, move_count, hardlink_count, 1))}%; background: var(--purple);">{hardlink_count}</div>
        </div>
    </div>
</div>

<h2>📈 空间释放</h2>
<div class="bar-chart">
    <div class="bar-row">
        <div class="bar-label">删除释放</div>
        <div class="bar-track">
            <div class="bar-fill" style="width:{self._bar_pct(delete_size, max(delete_size, hardlink_size, 1))}%; background: var(--red);">{_fmt_size(delete_size)}</div>
        </div>
    </div>
    <div class="bar-row">
        <div class="bar-label">硬链接节省</div>
        <div class="bar-track">
            <div class="bar-fill" style="width:{self._bar_pct(hardlink_size, max(delete_size, hardlink_size, 1))}%; background: var(--purple);">{_fmt_size(hardlink_size)}</div>
        </div>
    </div>
</div>

<h2>📋 扩展名分布</h2>
<div style="max-height: 300px; overflow-y: auto;">
<table>
<tr><th>扩展名</th><th>数量</th><th>占比</th></tr>
"""
        
        max_ext = ext_top[0][1] if ext_top else 1
        for i, (ext, count) in enumerate(ext_top):
            pct = count / max_ext * 100
            color = colors[i % len(colors)]
            html += f"""
<tr>
<td>{html_mod.escape(ext)}</td>
<td>{count}</td>
<td>
<div class="bar-track" style="height: 16px;">
<div class="bar-fill" style="width:{pct:.0f}%; background: {color};">{count}</div>
</div>
</td>
</tr>
"""
        
        html += """
</table>
</div>
"""
        
        # 重复文件组
        if dup_groups:
            html += f"""
<h2>🔄 重复文件组 (前 20 组)</h2>
<div style="max-height: 400px; overflow-y: auto;">
<table>
<tr><th>组</th><th>大小</th><th>操作</th><th>保留</th><th>删除</th></tr>
"""
            
            for g in dup_groups[:20]:
                action = g.action
                action_class = "badge-delete" if action == "DELETE" else "badge-hardlink"
                del_paths = "<br>".join(html_mod.escape(p[-60:]) for p in g.delete_paths[:3])
                if len(g.delete_paths) > 3:
                    del_paths += f"<br>+{len(g.delete_paths) - 3} 项"
                
                html += f"""
<tr>
<td>#{g.group_id}</td>
<td>{_fmt_size(g.size_bytes)}</td>
<td><span class="badge {action_class}">{action}</span></td>
<td>{html_mod.escape(g.keep_path[-60:])}</td>
<td>{del_paths}</td>
</tr>
"""
            
            html += """
</table>
</div>
"""
        
        # 待删除预览
        if delete_nodes:
            html += f"""
<h2>🗑️ 待删除文件 (前 50 项)</h2>
<div style="max-height: 400px; overflow-y: auto;">
<table>
<tr><th>#</th><th>文件名</th><th>大小</th><th>原因</th></tr>
"""
            
            for i, n in enumerate(delete_nodes[:50], 1):
                html += f"""
<tr>
<td>{i}</td>
<td>{html_mod.escape(n.file_name[:50])}</td>
<td>{_fmt_size(n.size_bytes)}</td>
<td>{html_mod.escape(n.reason[:50])}</td>
</tr>
"""
            
            if len(delete_nodes) > 50:
                html += f"""
<tr><td colspan="4" style="text-align:center; color: var(--text-dim);">... 还有 {len(delete_nodes) - 50} 项</td></tr>
"""
            
            html += """
</table>
</div>
"""
        
        html += f"""
<div class="footer">
    <p>文件夹整理工具 V{VERSION} | 所有删除操作会移入回收站</p>
</div>

</body>
</html>
"""
        
        return html
    
    @staticmethod
    def _bar_pct(value: int, total: int) -> float:
        """计算百分比"""
        if total <= 0:
            return 5
        pct = value / total * 100
        return max(5, min(100, pct))


# ═══════════════════════════════════════════════════════════════════
# 导出
# ═══════════════════════════════════════════════════════════════════

__all__ = [
    "HTMLReportGenerator",
]
