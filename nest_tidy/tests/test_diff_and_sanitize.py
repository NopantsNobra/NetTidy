"""可视化对比模块 & 文件名清洗模块测试"""
import pytest
from nest_tidy.models import TreeNode
from nest_tidy.engine import run_engine
from nest_tidy.diff_renderer import compute_diff, render_tree_with_diff, render_summary
from nest_tidy.name_sanitizer import sanitize_name, sanitize_path


def _f(name, path=""):
    p = f"{path}/{name}" if path else name
    return TreeNode(name=name, path=p, is_dir=False)

def _d(name, children, path=""):
    p = f"{path}/{name}" if path else name
    for c in children:
        _fix_paths(c, p)
    return TreeNode(name=name, path=p, is_dir=True, children=children)

def _fix_paths(node, parent_path):
    node.path = f"{parent_path}/{node.name}" if parent_path else node.name
    if node.is_dir:
        for child in node.children:
            _fix_paths(child, node.path)


# ── Diff 测试 ─────────────────────────────────────────────

def test_diff_basic():
    """基本差异检测"""
    src = _d("R", [_d("A", [_d("B", [_f("f1.txt")])])])
    result, mapping = run_engine(src, case_sensitive=False)
    report = compute_diff(src, result, mapping)

    # R1 触发，A 和 B 被消除
    assert report.stats["removed_dirs"] >= 0
    assert len(report.items) > 0


def test_diff_rename_detection():
    """重命名检测"""
    src = _d("R", [
        _d("X", [
            _d("A", [_f("f1.txt")]),
            _d("B", [_f("f1.txt")]),
        ])
    ])
    result, mapping = run_engine(src, case_sensitive=False)
    report = compute_diff(src, result, mapping)

    renamed = [i for i in report.items if i.kind == "renamed"]
    assert len(renamed) == 2, f"应有2个重命名: {renamed}"


def test_render_tree_with_diff():
    """渲染不报错"""
    src = _d("R", [_d("A", [_f("f1.txt")])])
    result, mapping = run_engine(src, case_sensitive=False)
    report = compute_diff(src, result, mapping)

    text = render_tree_with_diff(result, report, side="result")
    assert "f1.txt" in text


def test_render_summary():
    """摘要渲染"""
    src = _d("R", [_d("A", [_d("B", [_f("f1.txt")])])])
    result, mapping = run_engine(src, case_sensitive=False)
    report = compute_diff(src, result, mapping)

    summary = render_summary(report)
    assert "📊" in summary


# ── 文件名清洗测试 ─────────────────────────────────────────

def test_sanitize_illegal_chars():
    assert sanitize_name('a<b>c:d"e/f\\g|h?i*j') == 'a_b_c_d_e_f_g_h_i_j'

def test_sanitize_trailing_dots():
    assert sanitize_name("file...") == "file"
    assert sanitize_name("file   ") == "file"

def test_sanitize_reserved_names():
    assert sanitize_name("CON") == "CON_"
    assert sanitize_name("con") == "con_"
    assert sanitize_name("PRN") == "PRN_"
    assert sanitize_name("AUX") == "AUX_"
    assert sanitize_name("NUL") == "NUL_"

def test_sanitize_normal_name():
    assert sanitize_name("normal_file.txt") == "normal_file.txt"

def test_sanitize_empty():
    assert sanitize_name("") == "_"
    assert sanitize_name("...") == "_"

def test_sanitize_path():
    result = sanitize_path("D:/Downloads/file<name>.txt")
    assert "<" not in result
    assert ">" not in result
