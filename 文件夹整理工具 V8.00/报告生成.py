"""
报告生成模块
HTMLReportGenerator - HTML 可视化报告生成器
"""

import html as html_mod
import os
from typing import List, Dict, Any

from .常量定义 import VERSION
from .数据模型 import FileNode
from .工具函数 import _fmt_size


# ═══════════════════════════════════════════════════════════
# HTMLReportGenerator 类
# ═══════════════════════════════════════════════════════════

class HTMLReportGenerator:
    """
    HTML 报告生成器
    生成可视化的 HTML 格式操作报告
    """
    
    def generate(
        self,
        stats: Dict,
        nodes: List[FileNode],
        dup_groups: List[Dict],
        freed_bytes: int,
        output_path: str,
    ) -> bool:
        """
        生成 HTML 报告
        
        Args:
            stats: 统计信息
            nodes: 文件节点列表
            dup_groups: 重复文件组
            freed_bytes: 释放的字节数
            output_path: 输出文件路径
            
        Returns:
            是否成功
        """
        try:
            # 计算统计数据
            freed_mb = freed_bytes / 1048576
            delete_nodes = [n for n in nodes if n.action.value == "DELETE"]
            move_nodes = [n for n in nodes if n.action.value == "MOVE"]
            hardlink_nodes = [n for n in nodes if n.action.value == "HARDLINK"]
            
            delete_size = sum(n.size_bytes for n in delete_nodes)
            hardlink_size = sum(n.size_bytes for n in hardlink_nodes)
            
            # 扩展名统计
            ext_top = list(stats.get("extension_stats", {}).items())[:8]
            max_ext = ext_top[0][1] if ext_top else 1
            
            # 颜色配置
            colors = ["green", "blue", "orange", "purple"] * 2
            
            def bar_percent(value: int, total: int) -> float:
                return max(3, min(100, value / total * 100)) if total else 3
            
            # 生成 HTML
            html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>重组报告 V{VERSION}</title>
<style>
:root {{
    --bg: #0d1117;
    --card: #161b22;
    --bd: #30363d;
    --tx: #c9d1d9;
    --dm: #8b949e;
    --ac: #58a6ff;
    --gn: #3fb950;
    --rd: #f85149;
    --yw: #d29922;
    --or: #db6d28;
    --pp: #bc8cff;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--tx);
    line-height: 1.6;
    padding: 20px;
}}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ font-size: 2em; margin-bottom: 20px; color: var(--ac); }}
h2 {{ font-size: 1.5em; margin: 20px 0 10px; color: var(--gn); }}
.card {{
    background: var(--card);
    border: 1px solid var(--bd);
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 20px;
}}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }}
.stat-item {{
    background: rgba(255,255,255,0.05);
    padding: 15px;
    border-radius: 6px;
    text-align: center;
}}
.stat-value {{ font-size: 2em; font-weight: bold; color: var(--ac); }}
.stat-label {{ color: var(--dm); font-size: 0.9em; }}
.progress-bar {{
    background: var(--bd);
    border-radius: 4px;
    height: 20px;
    overflow: hidden;
    margin: 5px 0;
}}
.progress-fill {{
    height: 100%;
    border-radius: 4px;
    transition: width 0.3s;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 10px;
}}
th, td {{
    padding: 10px;
    text-align: left;
    border-bottom: 1px solid var(--bd);
}}
th {{ color: var(--dm); font-weight: 500; }}
tr:hover {{ background: rgba(255,255,255,0.05); }}
.badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.85em;
}}
.badge-delete {{ background: var(--rd); }}
.badge-move {{ background: var(--yw); }}
.badge-hardlink {{ background: var(--pp); }}
</style>
</head>
<body>
<div class="container">
<h1>📊 文件夹重组报告</h1>

<div class="card">
<h2>📈 总览</h2>
<div class="stats">
    <div class="stat-item">
        <div class="stat-value">{freed_mb:.1f} MB</div>
        <div class="stat-label">释放空间</div>
    </div>
    <div class="stat-item">
        <div class="stat-value">{len(delete_nodes)}</div>
        <div class="stat-label">删除文件</div>
    </div>
    <div class="stat-item">
        <div class="stat-value">{len(move_nodes)}</div>
        <div class="stat-label">移动文件</div>
    </div>
    <div class="stat-item">
        <div class="stat-value">{len(hardlink_nodes)}</div>
        <div class="stat-label">硬链接</div>
    </div>
</div>
</div>

<div class="card">
<h2>📁 扩展名分布</h2>
<table>
<tr><th>扩展名</th><th>数量</th><th>占比</th></tr>
"""
            
            # 添加扩展名统计
            for ext, count in ext_top:
                percent = bar_percent(count, max_ext)
                color = colors[ext_top.index((ext, count)) % len(colors)]
                html_content += f"""
<tr>
<td>.{html_mod.escape(ext)}</td>
<td>{count}</td>
<td>
<div class="progress-bar">
<div class="progress-fill" style="width: {percent}%; background: var(--{color});"></div>
</div>
</td>
</tr>
"""
            
            html_content += """
</table>
</div>
"""
            
            # 添加重复文件组
            if dup_groups:
                html_content += """
<div class="card">
<h2>🔄 重复文件组</h2>
<table>
<tr><th>组ID</th><th>大小</th><th>保留</th><th>删除</th></tr>
"""
                for g in dup_groups[:20]:
                    html_content += f"""
<tr>
<td>#{g['group_id']}</td>
<td>{_fmt_size(g['size'])}</td>
<td>{html_mod.escape(g['keep']['path'])}</td>
<td><span class="badge badge-delete">{len(g['delete'])} 个</span></td>
</tr>
"""
                html_content += """
</table>
</div>
"""
            
            # 添加操作详情
            html_content += """
<div class="card">
<h2>📋 操作详情</h2>
<table>
<tr><th>路径</th><th>操作</th><th>大小</th><th>原因</th></tr>
"""
            
            for n in nodes[:100]:
                if n.action.value == "DELETE":
                    badge = '<span class="badge badge-delete">删除</span>'
                elif n.action.value == "MOVE":
                    badge = '<span class="badge badge-move">移动</span>'
                elif n.action.value == "HARDLINK":
                    badge = '<span class="badge badge-hardlink">硬链接</span>'
                else:
                    continue
                
                html_content += f"""
<tr>
<td>{html_mod.escape(n.full_path)}</td>
<td>{badge}</td>
<td>{_fmt_size(n.size_bytes)}</td>
<td>{html_mod.escape(n.reason)}</td>
</tr>
"""
            
            html_content += """
</table>
</div>

<div class="card">
<p style="color: var(--dm); text-align: center;">
由 文件夹整理工具 V""" + VERSION + """ 生成
</p>
</div>

</div>
</body>
</html>
"""
            
            # 写入文件
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            
            return True
        
        except OSError as e:
            print(f"⚠️ 无法生成报告: {e}")
            return False
