"""理巢 — 智能目录重构工具 · Streamlit Web UI
启动方式:
    cd user_project
    streamlit run app.py
"""
from __future__ import annotations

import io
import sys
import logging
import traceback
from pathlib import Path
from datetime import datetime

import streamlit as st
import pandas as pd

# ── 确保 nesttidy 包可导入 ─────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from nesttidy.runtime.config import Config, load_config
from nesttidy.runtime.scheduler import Scheduler, TaskResult
from nesttidy.runtime.logger import setup_logging

# ══════════════════════════════════════════════════════════════
# 页面配置
# ══════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="理巢 — 智能目录重构工具",
    page_icon="🪺",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 自定义 CSS ──────────────────────────────────────────────
st.markdown(
    """
<style>
/* 主标题区 */
.title-block {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    padding: 1.5rem 2rem;
    border-radius: 12px;
    margin-bottom: 1.5rem;
    color: white;
}
.title-block h1 {
    margin: 0;
    font-size: 1.8rem;
}
.title-block p {
    margin: 0.3rem 0 0 0;
    opacity: 0.85;
    font-size: 0.95rem;
}

/* 摘要卡片 */
.metric-card {
    background: #f8f9fa;
    border-radius: 10px;
    padding: 1.2rem;
    text-align: center;
    border: 1px solid #e9ecef;
    height: 100%;
}
.metric-card .label {
    font-size: 0.8rem;
    color: #6c757d;
    margin-bottom: 0.3rem;
}
.metric-card .value {
    font-size: 1.6rem;
    font-weight: 700;
    color: #212529;
}
.metric-card .sub {
    font-size: 0.75rem;
    color: #adb5bd;
    margin-top: 0.2rem;
}

/* 成功 / 失败横幅 */
.result-banner {
    padding: 1rem 1.5rem;
    border-radius: 10px;
    margin-bottom: 1rem;
    font-size: 1.1rem;
    font-weight: 600;
}
.result-ok {
    background: #d4edda;
    color: #155724;
    border: 1px solid #c3e6cb;
}
.result-fail {
    background: #f8d7da;
    color: #721c24;
    border: 1px solid #f5c6cb;
}

/* 阶段耗时 */
.phase-bar {
    display: flex;
    gap: 8px;
    margin: 0.8rem 0;
}
.phase-item {
    flex: 1;
    text-align: center;
    padding: 0.6rem 0.4rem;
    border-radius: 8px;
    background: #e9ecef;
}
.phase-item .phase-name {
    font-size: 0.75rem;
    color: #6c757d;
}
.phase-item .phase-time {
    font-size: 1.0rem;
    font-weight: 600;
    color: #495057;
}
</style>
""",
    unsafe_allow_html=True,
)

# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════
def format_size(size: int | float) -> str:
    """格式化文件大小。"""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def format_time(ms: float) -> str:
    """格式化耗时。"""
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.2f}s"


def safe_rel_str(path: Path, base: Path) -> str:
    """安全的相对路径字符串。"""
    try:
        return str(path.relative_to(base))
    except ValueError:
        return f"…/{path.name}"


# ══════════════════════════════════════════════════════════════
# 标题
# ══════════════════════════════════════════════════════════════
st.markdown(
    """
<div class="title-block">
    <h1>🪺 理巢</h1>
    <p>智能目录重构工具 — 让杂乱无章的文件夹变得井井有条</p>
</div>
""",
    unsafe_allow_html=True,
)

# ══════════════════════════════════════════════════════════════
# 侧边栏：配置区
# ══════════════════════════════════════════════════════════════
with st.sidebar:
    st.header("⚙️ 参数配置")

    st.subheader("📂 目录路径")
    source_dir = st.text_input(
        "源目录（待整理）",
        placeholder="例：/home/user/messy_folder",
        help="需要整理的原始文件夹路径",
    )
    target_dir = st.text_input(
        "目标目录（输出位置）",
        placeholder="例：/home/user/clean_output",
        help="整理后文件输出的目标路径",
    )

    st.divider()
    st.subheader("🔧 执行选项")

    mode = st.radio(
        "执行模式",
        options=["copy", "move"],
        format_func=lambda x: {"copy": "📋 复制（保留源文件）", "move": "✂️ 移动（节省空间）"}[x],
        index=0,
        help="复制模式更安全，移动模式更快（同卷时）",
    )

    col_opt1, col_opt2 = st.columns(2)
    with col_opt1:
        dry_run = st.toggle(
            "演练模式",
            value=True,
            help="只预览变更，不实际执行",
        )
    with col_opt2:
        detect_dup = st.toggle(
            "重复检测",
            value=True,
            help="识别并跳过内容相同的文件",
        )

    jobs = st.slider(
        "并发线程数",
        min_value=1,
        max_value=16,
        value=4,
        help="增大线程数可加速处理大量小文件",
    )

    # ── 界面显示配置 ──
    st.divider()
    st.subheader("🖥️ 界面配置")
    
    preview_limit = st.slider(
        "预览行数限制",
        min_value=100,
        max_value=10000,
        value=2000,
        step=100,
        help="映射表预览的最大行数，设置过大可能导致浏览器卡顿",
    )

    st.divider()

    # ── 运行按钮 ──
    run_clicked = st.button(
        "🚀 开始整理",
        type="primary",
        use_container_width=True,
        disabled=not (source_dir and target_dir),
    )
    st.caption("💡 建议先开启「演练模式」预览变更")

# ══════════════════════════════════════════════════════════════
# 主区域
# ══════════════════════════════════════════════════════════════

# ── 无操作时的引导 ──
if not run_clicked and "task_result" not in st.session_state:
    st.info(
        "👈 请在左侧配置源目录和目标目录，然后点击 **「开始整理」**。\n\n"
        "建议先开启 **演练模式** 预览变更，确认无误后再关闭演练执行。"
    )

    with st.expander("📖 使用说明", expanded=False):
        st.markdown(
            """
### 快速上手
1. **源目录**：选择需要整理的文件夹（支持嵌套目录）
2. **目标目录**：指定整理结果的输出位置
3. **演练模式**：开启后只预览不执行，强烈建议首次使用
4. 点击「开始整理」，等待分析完成

### 执行模式
| 模式 | 说明 | 适用场景 |
|------|------|---------|
| 复制 | 保留源文件不变 | 首次使用、重要数据 |
| 移动 | 直接移动文件 | 同磁盘、节省空间 |

### 安全机制
- ✅ 路径穿越拦截
- ✅ 符号链接循环检测
- ✅ 深度 / 节点数熔断保护
- ✅ 文件名自动清洗（Windows 兼容）
- ✅ 冲突自动重命名
"""
        )

# ── 执行任务 ──
if run_clicked:
    src = Path(source_dir).expanduser().resolve()
    tgt = Path(target_dir).expanduser().resolve()

    # ── 前端校验 ──
    errors = []
    if not src.exists():
        errors.append(f"源目录不存在：`{source_dir}`")
    elif not src.is_dir():
        errors.append(f"源路径不是目录：`{source_dir}`")

    if tgt.exists() and not tgt.is_dir():
        errors.append(f"目标路径已存在但不是目录：`{target_dir}`")

    try:
        if src.exists() and src.resolve() == tgt.resolve():
            errors.append("源目录和目标目录不能相同")
    except OSError:
        pass

    if errors:
        for e in errors:
            st.error(e)
    else:
        # ── 执行 ──
        spinner_text = "🔍 正在演练预览…" if dry_run else "⚙️ 正在执行整理…"
        with st.spinner(spinner_text):
            try:
                setup_logging(level=logging.INFO, log_file=None)

                config = load_config(
                    mode=mode,
                    max_workers=jobs,
                    detect_duplicates=detect_dup,
                    dry_run=dry_run,
                    force_cross_volume=False,
                    show_progress=False,
                    log_level="INFO",
                )

                scheduler = Scheduler(
                    mode=config.mode,
                    max_workers=config.max_workers,
                    detect_duplicates=config.detect_duplicates,
                    dry_run=config.dry_run,
                    force_cross_volume=config.force_cross_volume,
                )

                result = scheduler.run(
                    source=src,
                    target=tgt,
                    output_dir=tgt,
                )

                st.session_state["task_result"] = result
                
            except AttributeError as attr_err:
                traceback.print_exc()
                st.error(f"内部数据结构解析失败：{attr_err}")
                st.warning(
                    "⚠️ 发生此错误通常是因为源目录中存在**极度畸形的文件名**（如纯空格、特殊控制字符等）。\n\n"
                    "👉 请查看运行此网页的**终端黑窗口**，里面会有详细的 `[安全拦截]` 或 `清洗` 日志，"
                    "找到那个出问题的文件并将其手动重命名后重试。"
                )
            except Exception as exc:
                traceback.print_exc()
                st.error(f"执行异常：{exc}")

# ── 展示结果 ──
if "task_result" in st.session_state:
    result: TaskResult = st.session_state["task_result"]

    # ── 成功 / 失败横幅 ──
    if result.success:
        mode_label = "演练完成（未实际执行）" if dry_run else "执行成功"
        st.markdown(
            f'<div class="result-banner result-ok">✅ {mode_label}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="result-banner result-fail">❌ 执行失败</div>',
            unsafe_allow_html=True,
        )

    # ── 指标卡片 ──
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            f"""<div class="metric-card">
            <div class="label">总文件数</div>
            <div class="value">{result.file_count:,}</div>
        </div>""",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"""<div class="metric-card">
            <div class="label">总大小</div>
            <div class="value">{format_size(result.total_bytes)}</div>
        </div>""",
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f"""<div class="metric-card">
            <div class="label">重复文件组</div>
            <div class="value">{result.duplicate_groups}</div>
        </div>""",
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            f"""<div class="metric-card">
            <div class="label">总耗时</div>
            <div class="value">{format_time(result.total_time_ms)}</div>
        </div>""",
            unsafe_allow_html=True,
        )

    # ── 阶段耗时 ──
    st.markdown("#### ⏱️ 各阶段耗时")
    phases = [
        ("🔍 扫描", result.scan_time_ms),
        ("🧠 分析", result.analyze_time_ms),
        ("📦 执行", result.execute_time_ms),
    ]
    ph_cols = st.columns(len(phases))
    for col, (name, ms) in zip(ph_cols, phases):
        with col:
            st.metric(name, format_time(ms))

    # ── 执行详情（仅非演练模式） ──
    if result.execution_summary:
        summary = result.execution_summary
        st.markdown("#### 📊 执行详情")

        d1, d2, d3, d4 = st.columns(4)
        d1.metric("✅ 成功", f"{summary.success_count:,}")
        d2.metric("❌ 失败", f"{summary.failure_count:,}")
        d3.metric("⏭️ 跳过", f"{summary.skipped_count:,}")
        if summary.throughput_mbps > 0:
            d4.metric("🚀 吞吐量", f"{summary.throughput_mbps:.1f} MB/s")

    # ── 错误 & 警告 ──
    if result.errors:
        with st.expander(f"🚨 错误信息（{len(result.errors)} 条）", expanded=True):
            for err in result.errors:
                st.error(err)

    if result.warnings:
        with st.expander(f"⚠️ 警告信息（{len(result.warnings)} 条）"):
            for warn in result.warnings:
                st.warning(warn)

    # ── 预检详情 ──
    if result.pre_check:
        pc = result.pre_check
        with st.expander("🔎 预检结果"):
            pc_cols = st.columns(3)
            pc_cols[0].metric("源目录可读", "✅" if pc.source_readable else "❌")
            pc_cols[1].metric("目标目录可写", "✅" if pc.target_writable else "❌")
            pc_cols[2].metric("同卷", "✅" if pc.same_volume else "❌ 跨卷")

    # ── 映射表预览 ──
    with st.expander("📋 映射表预览（文件操作明细）", expanded=False):
        if hasattr(result, "_engine_map") and result._engine_map:
            entries = result._engine_map
        else:
            entries = []

        if entries:
            # 构建 DataFrame
            rows = []
            for i, e in enumerate(entries[:preview_limit], 1):
                src_rel = safe_rel_str(e.source_path, result.source)
                tgt_rel = safe_rel_str(e.target_path, result.target)
                rows.append(
                    {
                        "#": i,
                        "操作": e.action,
                        "源路径": src_rel,
                        "目标路径": tgt_rel,
                        "大小": format_size(e.file_size),
                        "重命名原因": e.rename_reason or "",
                    }
                )
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, height=400)

            if len(entries) > preview_limit:
                st.caption(f"仅展示前 {preview_limit} 条，共 {len(entries):,} 条")
        else:
            st.info(
                "映射表数据不可用（演练模式下完整映射表保存在目标目录的 report.html / changes.txt 中）"
            )

# ── 页脚 ──
st.divider()
st.caption(
    f"🪺 理巢 v1.2 · Streamlit UI · "
    f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
)
