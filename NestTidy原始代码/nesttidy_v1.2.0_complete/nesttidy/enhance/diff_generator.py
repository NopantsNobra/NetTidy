"""差异报告生成器：生成重构前后的人类可读对比报告（纯文本 / HTML）。"""
from __future__ import annotations
from pathlib import Path
from typing import List

from nesttidy.core.node import MapEntry
from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)

# Jinja2 HTML 报告模板
_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>理巢重构报告</title>
    <style>
        body { font-family: 'Segoe UI', monospace; padding: 24px; color: #222; }
        h1 { color: #333; }
        .meta { color: #666; margin-bottom: 16px; }
        table { border-collapse: collapse; width: 100%; font-size: 13px; }
        td, th { border: 1px solid #ddd; padding: 8px 12px; text-align: left; }
        th { background: #f4f4f4; font-weight: 600; }
        tr:nth-child(even) { background: #fafafa; }
        .copy { color: #1976D2; font-weight: 600; }
        .move { color: #388E3C; font-weight: 600; }
        .cross_vol_move { color: #7B1FA2; font-weight: 600; }
        .skip { color: #9E9E9E; }
        .renamed { font-style: italic; color: #E65100; font-size: 12px; }
        .ok { color: #388E3C; }
    </style>
</head>
<body>
    <h1>🪺 理巢重构报告</h1>
    <div class="meta">
        <p>源目录：<code>{{ source_name }}</code></p>
        <p>目标目录：<code>{{ target_name }}</code></p>
        <p>操作数量：<strong>{{ entries|length }}</strong></p>
    </div>
    <table>
        <tr>
            <th>#</th><th>操作</th><th>源路径</th><th>目标路径</th><th>备注</th>
        </tr>
        {% for entry in entries %}
        <tr>
            <td>{{ loop.index }}</td>
            <td class="{{ entry.action }}">{{ entry.action_label }}</td>
            <td>{{ entry.safe_source }}</td>
            <td>{{ entry.safe_target }}</td>
            <td class="renamed">{{ entry.rename_reason or '' }}</td>
        </tr>
        {% endfor %}
    </table>
</body>
</html>
"""

class DiffGenerator:
    """生成重构前后的差异对比报告。"""

    # ══════════════════════════════════════════════════
    # 【QC修复3】补全操作类型的中文映射表
    # ══════════════════════════════════════════════════
    ACTION_LABELS = {
        "copy": "复制",
        "move": "同卷移动",
        "cross_vol_move": "跨卷移动",
        "skip": "跳过",
    }

    def generate_text(
        self,
        entries: List[MapEntry],
        source: Path,
        target: Path,
    ) -> str:
        """生成纯文本差异报告。"""
        # ══════════════════════════════════════════════════
        # 【QC修复2】报告头部脱敏：只显示目录名称
        # ══════════════════════════════════════════════════
        lines = [
            "=" * 68,
            "理巢 — 重构差异报告",
            f"源目录: {source.name}",
            f"目标目录: {target.name}",
            f"操作数量: {len(entries)}",
            "=" * 68,
        ]

        for i, entry in enumerate(entries, 1):
            label = self.ACTION_LABELS.get(entry.action, entry.action)
            src_rel = self._safe_rel(entry.source_path, source)
            tgt_rel = self._safe_rel(entry.target_path, target)
            
            lines.append(f"[{i:04d}] [{label}] {src_rel}")
            if str(src_rel) != str(tgt_rel):
                lines.append(f"      → {tgt_rel}")
            if entry.rename_reason:
                lines.append(f"      ({entry.rename_reason})")

        return "\n".join(lines)

    def generate_html(
        self,
        entries: List[MapEntry],
        source: Path,
        target: Path,
    ) -> str:
        """生成 HTML 格式差异报告（依赖 Jinja2）。"""
        try:
            from jinja2 import Environment, BaseLoader
        except ImportError:
            logger.warning("jinja2 未安装，回退到纯文本报告。")
            return self.generate_text(entries, source, target)

        env = Environment(loader=BaseLoader(), autoescape=True)
        tmpl = env.from_string(_HTML_TEMPLATE)
        
        # 为模板准备安全的上下文数据
        safe_entries = []
        for entry in entries:
            safe_entries.append({
                "action": entry.action,
                "action_label": self.ACTION_LABELS.get(entry.action, entry.action),
                "safe_source": str(self._safe_rel(entry.source_path, source)),
                "safe_target": str(self._safe_rel(entry.target_path, target)),
                "rename_reason": entry.rename_reason,
            })

        return tmpl.render(
            entries=safe_entries,
            source_name=source.name,  # 【QC修复2】
            target_name=target.name,  # 【QC修复2】
        )

    def _safe_rel(self, path: Path, base: Path) -> Path | str:
        """安全的相对路径转换。"""
        try:
            return path.relative_to(base)
        except ValueError:
            # ══════════════════════════════════════════════════
            # 【QC修复1】封死越狱漏洞：外部路径绝对不能原样输出
            # 只暴露文件名，加上外部标记防止混淆
            # ══════════════════════════════════════════════════
            return f"[外部路径] {path.name}"
