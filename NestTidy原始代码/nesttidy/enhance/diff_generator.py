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
  h1   { color: #333; }
  .meta { color: #666; margin-bottom: 16px; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  td, th { border: 1px solid #ddd; padding: 8px 12px; text-align: left; }
  th { background: #f4f4f4; font-weight: 600; }
  tr:nth-child(even) { background: #fafafa; }
  .copy   { color: #1976D2; font-weight: 600; }
  .move   { color: #388E3C; font-weight: 600; }
  .skip   { color: #9E9E9E; }
  .renamed { font-style: italic; color: #E65100; font-size: 12px; }
  .ok     { color: #388E3C; }
</style>
</head>
<body>
<h1>🪺 理巢重构报告</h1>
<div class="meta">
  <p>源目录：<code>{{ source }}</code></p>
  <p>目标目录：<code>{{ target }}</code></p>
  <p>操作数量：<strong>{{ entries|length }}</strong></p>
</div>
<table>
  <tr>
    <th>#</th><th>操作</th><th>源路径</th><th>目标路径</th><th>备注</th>
  </tr>
  {% for entry in entries %}
  <tr>
    <td>{{ loop.index }}</td>
    <td class="{{ entry.action }}">{{ entry.action }}</td>
    <td>{{ entry.source_path }}</td>
    <td>{{ entry.target_path }}</td>
    <td class="renamed">{{ entry.rename_reason or '' }}</td>
  </tr>
  {% endfor %}
</table>
</body>
</html>
"""


class DiffGenerator:
    """生成重构前后的差异对比报告。"""

    def generate_text(
        self, entries: List[MapEntry], source: Path, target: Path
    ) -> str:
        """生成纯文本差异报告。"""
        lines = [
            "=" * 68,
            "理巢 — 重构差异报告",
            f"源目录:   {source}",
            f"目标目录: {target}",
            f"操作数量: {len(entries)}",
            "=" * 68,
        ]
        for i, entry in enumerate(entries, 1):
            label = {"copy": "复制", "move": "移动", "skip": "跳过"}.get(
                entry.action, entry.action
            )
            src_rel = self._safe_rel(entry.source_path, source)
            tgt_rel = self._safe_rel(entry.target_path, target)
            lines.append(f"[{i:04d}] [{label}] {src_rel}")
            if str(src_rel) != str(tgt_rel):
                lines.append(f"         → {tgt_rel}")
            if entry.rename_reason:
                lines.append(f"         ({entry.rename_reason})")
        return "\n".join(lines)

    def generate_html(
        self, entries: List[MapEntry], source: Path, target: Path
    ) -> str:
        """生成 HTML 格式差异报告（依赖 Jinja2）。"""
        try:
            from jinja2 import Environment, BaseLoader
        except ImportError:
            logger.warning("jinja2 未安装，回退到纯文本报告。")
            return self.generate_text(entries, source, target)

        env = Environment(loader=BaseLoader(), autoescape=True)
        tmpl = env.from_string(_HTML_TEMPLATE)
        return tmpl.render(entries=entries, source=source, target=target)

    def _safe_rel(self, path: Path, base: Path) -> Path:
        try:
            return path.relative_to(base)
        except ValueError:
            return path