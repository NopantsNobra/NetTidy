"""理巢 NestTidy — Streamlit Web UI 入口"""
import os
import json
import time
import streamlit as st

from nest_tidy.models import TreeNode
from nest_tidy.scanner import scan_directory, tree_to_dict
from nest_tidy.engine import run_engine, format_tree
from nest_tidy.diff_renderer import (
    compute_diff, render_tree_with_diff, render_summary,
)
from nest_tidy.executor import execute_mapping
from nest_tidy.permission_fixer import fix_permissions


# ── 页面配置 ─────────────────────────────────────────────

st.set_page_config(
    page_title="理巢 NestTidy",
    page_icon="🪺",
    layout="wide",
)

st.title("🪺 理巢 NestTidy — 智能目录清理工具")

# ── Session State 初始化 ─────────────────────────────────

_defaults = {
    "step": 1,
    "source_dir": "",
    "target_dir": "",
    "strategy": "auto",
    "fix_perm": False,
    "fix_readonly": False,
    "fix_hidden": False,
    "fix_zone": False,
    "scan_tree": None,
    "result_tree": None,
    "mapping": {},
    "diff_report": None,
    "execution_log": None,
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ── Step 1: 配置 ─────────────────────────────────────────

def step_config():
    st.header("Step 1: 配置")

    col1, col2 = st.columns(2)
    with col1:
        source = st.text_input(
            "📂 源目录路径",
            value=st.session_state.source_dir,
            placeholder="D:\\Downloads\\剧集合集",
        )
    with col2:
        target = st.text_input(
            "📁 目标目录路径",
            value=st.session_state.target_dir,
            placeholder="留空则自动生成（源目录_tidy）",
        )

    st.subheader("执行策略")
    strategy = st.radio(
        "选择文件操作方式",
        options=["auto", "copy", "move"],
        format_func=lambda x: {
            "auto": "🔄 自动(auto) — 同卷移动，跨卷复制",
            "copy": "📋 复制(copy) — 纯复制，不触碰源文件",
            "move": "✂️ 移动(move) — 同卷极速移动",
        }[x],
        horizontal=True,
    )

    st.subheader("权限修复（可选，默认全部关闭）")
    col3, col4, col5, col6 = st.columns(4)
    with col3:
        fix_perm = st.checkbox("🔐 授予完全控制权", value=st.session_state.fix_perm)
    with col4:
        fix_readonly = st.checkbox("📖 去除只读属性", value=st.session_state.fix_readonly)
    with col5:
        fix_hidden = st.checkbox("👁️ 去除隐藏属性", value=st.session_state.fix_hidden)
    with col6:
        fix_zone = st.checkbox("🌐 清除网络标识", value=st.session_state.fix_zone)

    if st.button("▶ 开始扫描", type="primary", use_container_width=True):
        if not source or not os.path.isdir(source):
            st.error("❌ 源目录不存在，请检查路径")
            return
        if not target:
            target = source.rstrip("/\\") + "_tidied"

        st.session_state.source_dir = source
        st.session_state.target_dir = target
        st.session_state.strategy = strategy
        st.session_state.fix_perm = fix_perm
        st.session_state.fix_readonly = fix_readonly
        st.session_state.fix_hidden = fix_hidden
        st.session_state.fix_zone = fix_zone
        st.session_state.step = 2
        st.rerun()


# ── Step 2: 扫描 & 算法 ──────────────────────────────────

def step_scan():
    st.header("Step 2: 扫描 & 算法")

    source = st.session_state.source_dir

    # 扫描
    if st.session_state.scan_tree is None:
        with st.spinner("正在扫描目录..."):
            tree = scan_directory(source)
            st.session_state.scan_tree = tree
        st.success(f"✅ 扫描完成：共 {tree.total_items} 个文件，{tree.total_dirs} 个目录")

    tree = st.session_state.scan_tree

    # 展示源目录树
    st.subheader("📂 源目录树")
    st.code(format_tree(tree), language=None)

    # 运行算法
    if st.button("▶ 运行整理算法", type="primary", use_container_width=True):
        with st.spinner("正在运行 R0-R5 算法..."):
            result_tree, mapping = run_engine(tree, case_sensitive=False)
            st.session_state.result_tree = result_tree
            st.session_state.mapping = mapping

            # 生成差异报告
            diff_report = compute_diff(tree, result_tree, mapping)
            st.session_state.diff_report = diff_report

        st.success(f"✅ 算法完成：映射表包含 {len(mapping)} 个文件")
        st.session_state.step = 3
        st.rerun()

    if st.button("◀ 返回修改", use_container_width=True):
        st.session_state.step = 1
        st.rerun()


# ── Step 3: 预览对比 ─────────────────────────────────────

def step_preview():
    st.header("Step 3: 预览对比")

    result_tree = st.session_state.result_tree
    diff_report = st.session_state.diff_report
    source_tree = st.session_state.scan_tree

    if result_tree is None:
        st.error("请先运行算法")
        return

    # 左右两栏对比
    col_src, col_tgt = st.columns(2)
    with col_src:
        st.subheader("📂 原目录结构")
        src_text = render_tree_with_diff(source_tree, diff_report, side="source")
        st.code(src_text, language=None)

    with col_tgt:
        st.subheader("📁 整理后目录结构")
        tgt_text = render_tree_with_diff(result_tree, diff_report, side="result")
        st.code(tgt_text, language=None)

    # 统计摘要
    st.info(render_summary(diff_report))

    # 权限修复预览
    any_fix = any([
        st.session_state.fix_perm,
        st.session_state.fix_readonly,
        st.session_state.fix_hidden,
        st.session_state.fix_zone,
    ])
    if any_fix:
        fixes = []
        if st.session_state.fix_perm:
            fixes.append("授予完全控制权")
        if st.session_state.fix_readonly:
            fixes.append("去除只读属性")
        if st.session_state.fix_hidden:
            fixes.append("去除隐藏属性")
        if st.session_state.fix_zone:
            fixes.append("清除网络标识")
        st.warning(f"🔧 权限修复：已开启 {len(fixes)} 项 — {', '.join(fixes)}")

    # 操作按钮
    col_back, col_exec = st.columns(2)
    with col_back:
        if st.button("◀ 返回修改", use_container_width=True):
            st.session_state.step = 1
            st.rerun()
    with col_exec:
        if st.button("✅ 确认并执行", type="primary", use_container_width=True):
            st.session_state.step = 4
            st.rerun()


# ── Step 4: 执行 ─────────────────────────────────────────

def step_execute():
    st.header("Step 4: 执行结果")

    mapping = st.session_state.mapping
    source = st.session_state.source_dir
    target = st.session_state.target_dir
    strategy = st.session_state.strategy

    if not mapping:
        st.error("映射表为空，请返回重新操作")
        return

    # 执行文件操作
    if st.session_state.execution_log is None:
        with st.spinner("正在执行文件操作..."):
            start_time = time.time()
            exec_result = execute_mapping(mapping, source, target, strategy)
            elapsed = time.time() - start_time

            # 权限修复
            perm_report = None
            any_fix = any([
                st.session_state.fix_perm,
                st.session_state.fix_readonly,
                st.session_state.fix_hidden,
                st.session_state.fix_zone,
            ])
            if any_fix and exec_result.success:
                with st.spinner("正在修复权限..."):
                    perm_report = fix_permissions(
                        target,
                        fix_perm=st.session_state.fix_perm,
                        fix_readonly=st.session_state.fix_readonly,
                        fix_hidden=st.session_state.fix_hidden,
                        fix_zone=st.session_state.fix_zone,
                    )

            st.session_state.execution_log = {
                "exec_result": exec_result,
                "perm_report": perm_report,
                "elapsed": elapsed,
            }

    log = st.session_state.execution_log
    exec_result = log["exec_result"]
    perm_report = log["perm_report"]
    elapsed = log["elapsed"]

    # 目录整理结果
    if exec_result.success:
        action = "移动" if st.session_state.strategy == "move" else "复制"
        if st.session_state.strategy == "auto":
            action = "移动" if exec_result.files_moved > 0 else "复制"
        st.success(
            f"✅ 目录整理完成！"
            f"{action}了 {exec_result.files_copied + exec_result.files_moved} 个文件"
            f"，耗时 {elapsed:.1f} 秒"
        )
    else:
        st.error(f"❌ 执行过程中出现 {len(exec_result.errors)} 个错误")
        for err in exec_result.errors[:10]:
            st.code(err)

    # 权限修复结果
    if perm_report:
        st.subheader("🔧 权限修复结果")
        for mode, result in perm_report.results.items():
            mode_names = {
                "perm": "🔐 授予完全控制权",
                "readonly": "📖 去除只读属性",
                "hidden": "👁️ 去除隐藏属性",
                "zone": "🌐 清除网络标识",
            }
            name = mode_names.get(mode, mode)

            if result.status == "success":
                detail = result.details[0] if result.details else f"{result.files_processed} 个文件"
                st.success(f"{name}: ✅ {detail}")
            elif result.status == "partial":
                st.warning(
                    f"{name}: ⚠️ {result.files_processed - result.errors} 成功, "
                    f"{result.errors} 失败"
                )
            elif result.status == "skipped":
                detail = result.details[0] if result.details else "跳过"
                st.info(f"{name}: ⏭️ {detail}")
            else:
                st.error(f"{name}: ❌ {result.error_messages[0] if result.error_messages else '失败'}")

    # 日志文件
    log_dir = os.path.join(target, ".nest_tidy_logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"run_{time.strftime('%Y%m%d_%H%M%S')}.jsonl")
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            log_entry = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "source": source,
                "target": target,
                "strategy": st.session_state.strategy,
                "files_copied": exec_result.files_copied,
                "files_moved": exec_result.files_moved,
                "files_skipped": exec_result.files_skipped,
                "errors": len(exec_result.errors),
                "elapsed_seconds": round(elapsed, 2),
            }
            if perm_report:
                log_entry["permission_fix"] = {
                    mode: {
                        "status": r.status,
                        "files_processed": r.files_processed,
                        "errors": r.errors,
                    }
                    for mode, r in perm_report.results.items()
                }
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        st.caption(f"📄 日志: {log_file}")
    except Exception as e:
        st.warning(f"日志写入失败: {e}")

    # 重新开始
    if st.button("🔄 重新开始", use_container_width=True):
        for k in _defaults:
            st.session_state[k] = _defaults[k]
        st.rerun()


# ── 路由 ─────────────────────────────────────────────────

step = st.session_state.step

if step == 1:
    step_config()
elif step == 2:
    step_scan()
elif step == 3:
    step_preview()
elif step == 4:
    step_execute()
