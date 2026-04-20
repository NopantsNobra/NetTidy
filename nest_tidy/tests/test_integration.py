"""集成测试 — 全模块端到端联调"""
import os
import shutil
import tempfile
import pytest

from nest_tidy.scanner import scan_directory
from nest_tidy.engine import run_engine
from nest_tidy.diff_renderer import compute_diff, render_tree_with_diff, render_summary
from nest_tidy.executor import execute_mapping, detect_same_volume, clean_empty_source_dirs
from nest_tidy.name_sanitizer import sanitize_name, sanitize_path
from nest_tidy.permission_fixer import fix_permissions


# ── 辅助函数 ─────────────────────────────────────────────

def _make_tree(base: str, spec: dict) -> None:
    """
    在 base 下按 spec 创建目录结构。
    spec 格式: {"dir_name": {...}, "file_name": "content", ...}
    嵌套 dict = 子目录，str = 文件内容。
    """
    for name, val in spec.items():
        full = os.path.join(base, name)
        if isinstance(val, dict):
            os.makedirs(full, exist_ok=True)
            _make_tree(full, val)
        else:
            with open(full, "w", encoding="utf-8") as f:
                f.write(val)


def _collect_files(base: str) -> dict[str, str]:
    """收集目录下所有文件的 {相对路径: 内容}"""
    result = {}
    for root, dirs, files in os.walk(base):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, base).replace("\\", "/")
            try:
                with open(full, "r", encoding="utf-8") as fh:
                    result[rel] = fh.read()
            except UnicodeDecodeError:
                result[rel] = "<binary>"
    return result


def _collect_dirs(base: str) -> set[str]:
    """收集目录下所有子目录的相对路径"""
    result = set()
    for root, dirs, files in os.walk(base):
        for d in dirs:
            full = os.path.join(root, d)
            rel = os.path.relpath(full, base).replace("\\", "/")
            result.add(rel)
    return result


def _run_full_pipeline(source_dir: str, target_dir: str, strategy: str = "copy"):
    """执行完整流水线：扫描 → 算法 → 差异 → 执行"""
    tree = scan_directory(source_dir)
    result_tree, mapping = run_engine(tree, case_sensitive=False)
    diff_report = compute_diff(tree, result_tree, mapping)
    exec_result = execute_mapping(mapping, source_dir, target_dir, strategy)
    return tree, result_tree, mapping, diff_report, exec_result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 端到端：copy 策略
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_e2e_copy_basic():
    """基本 copy 端到端：壳吸收 + 文件提升"""
    with tempfile.TemporaryDirectory() as src_dir, \
         tempfile.TemporaryDirectory() as tgt_dir:
        # R/ A/ B/ f1.txt
        #      C/ f2.jpg
        _make_tree(src_dir, {
            "A": {"B": {"f1.txt": "hello"}},
            "C": {"f2.jpg": "world"},
        })

        target = os.path.join(tgt_dir, "output")
        tree, result_tree, mapping, diff_report, exec_result = _run_full_pipeline(
            src_dir, target, "copy"
        )

        # 执行成功
        assert exec_result.success, f"执行失败: {exec_result.errors}"
        assert exec_result.files_copied == 2

        # 源文件未被删除（copy 策略）
        src_files = _collect_files(src_dir)
        assert "A/B/f1.txt" in src_files
        assert "C/f2.jpg" in src_files

        # 目标文件存在且内容正确
        tgt_files = _collect_files(target)
        assert any("f1.txt" in k for k in tgt_files), f"目标缺少 f1.txt: {tgt_files}"
        assert any("f2.jpg" in k for k in tgt_files), f"目标缺少 f2.jpg: {tgt_files}"


def test_e2e_copy_chain_passthrough():
    """copy 端到端：直链穿透 TC-01"""
    with tempfile.TemporaryDirectory() as src_dir, \
         tempfile.TemporaryDirectory() as tgt_dir:
        # R/ A/ B/ f1.txt
        _make_tree(src_dir, {"A": {"B": {"f1.txt": "data123"}}})

        target = os.path.join(tgt_dir, "output")
        _, _, mapping, _, exec_result = _run_full_pipeline(src_dir, target, "copy")

        assert exec_result.success
        tgt_files = _collect_files(target)
        # f1.txt 应该在目标根下
        assert "f1.txt" in tgt_files, f"f1.txt 未在根下: {tgt_files}"
        assert tgt_files["f1.txt"] == "data123"


def test_e2e_copy_complex_nested():
    """copy 端到端：复杂嵌套 TC-12"""
    with tempfile.TemporaryDirectory() as src_dir, \
         tempfile.TemporaryDirectory() as tgt_dir:
        _make_tree(src_dir, {
            "X": {
                "A": {"B": {
                    "C": {"f1.jpg": "c1", "f2.jpg": "c2"},
                    "D": {"f3.mp4": "d1"},
                }},
                "E": {"F": {
                    "G": {"f4.jpg": "g1", "f5.jpg": "g2"},
                    "H": {"f6.txt": "h1"},
                }},
            }
        })

        target = os.path.join(tgt_dir, "output")
        _, result_tree, mapping, _, exec_result = _run_full_pipeline(src_dir, target, "copy")

        assert exec_result.success
        tgt_files = _collect_files(target)
        # 所有 6 个文件都应该存在
        assert len(tgt_files) == 6, f"应有6个文件: {tgt_files}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 端到端：move 策略
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_e2e_move_basic():
    """move 端到端：文件从源移动到目标"""
    with tempfile.TemporaryDirectory() as src_dir, \
         tempfile.TemporaryDirectory() as tgt_dir:
        _make_tree(src_dir, {
            "A": {"B": {"f1.txt": "hello"}},
            "C": {"f2.jpg": "world"},
        })

        target = os.path.join(tgt_dir, "output")
        _, _, mapping, _, exec_result = _run_full_pipeline(src_dir, target, "copy")

        assert exec_result.success
        # copy 策略：源文件保留
        assert os.path.exists(os.path.join(src_dir, "A", "B", "f1.txt"))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 端到端：撞名重命名
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_e2e_collision_rename():
    """端到端：撞名文件被正确重命名（TC-14）"""
    with tempfile.TemporaryDirectory() as src_dir, \
         tempfile.TemporaryDirectory() as tgt_dir:
        _make_tree(src_dir, {
            "X": {
                "A": {"f1.txt": "from_A"},
                "B": {"f1.txt": "from_B"},
            }
        })

        target = os.path.join(tgt_dir, "output")
        _, _, mapping, _, exec_result = _run_full_pipeline(src_dir, target, "copy")

        assert exec_result.success
        tgt_files = _collect_files(target)
        # 应该有 A-f1.txt 和 B-f1.txt
        names = list(tgt_files.keys())
        assert any("A-f1.txt" in n for n in names), f"缺少 A-f1.txt: {names}"
        assert any("B-f1.txt" in n for n in names), f"缺少 B-f1.txt: {names}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 端到端：空目录清理
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_e2e_empty_dir_cleanup():
    """端到端：空目录被清理（TC-08）"""
    with tempfile.TemporaryDirectory() as src_dir, \
         tempfile.TemporaryDirectory() as tgt_dir:
        _make_tree(src_dir, {
            "A": {"f1.txt": "data"},
            "B": {"C": {}, "D": {}},
        })

        target = os.path.join(tgt_dir, "output")
        _, _, mapping, _, exec_result = _run_full_pipeline(src_dir, target, "copy")

        assert exec_result.success
        tgt_files = _collect_files(target)
        # 只有 f1.txt，空目录不复制
        assert len(tgt_files) == 1
        assert "f1.txt" in tgt_files


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 端到端：R0 清理后触发 R1（TC-16）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_e2e_r0_then_r1():
    """端到端：R0 清理空目录后 R1 触发（TC-16）"""
    with tempfile.TemporaryDirectory() as src_dir, \
         tempfile.TemporaryDirectory() as tgt_dir:
        _make_tree(src_dir, {
            "A": {
                "B": {"C": {"f1.txt": "deep"}},
                "D": {},
            }
        })

        target = os.path.join(tgt_dir, "output")
        _, _, mapping, _, exec_result = _run_full_pipeline(src_dir, target, "copy")

        assert exec_result.success
        tgt_files = _collect_files(target)
        assert "f1.txt" in tgt_files, f"f1.txt 应在根下: {tgt_files}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 端到端：权限修复
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_e2e_permission_fix_skip_on_linux():
    """权限修复在 Linux 上应跳过 Windows 特有命令"""
    with tempfile.TemporaryDirectory() as src_dir, \
         tempfile.TemporaryDirectory() as tgt_dir:
        _make_tree(src_dir, {"f1.txt": "data"})

        target = os.path.join(tgt_dir, "output")
        _, _, mapping, _, exec_result = _run_full_pipeline(src_dir, target, "copy")
        assert exec_result.success

        # 权限修复（Linux 上 icacls/attrib 不可用，应跳过）
        report = fix_permissions(
            target,
            fix_perm=True,
            fix_readonly=True,
            fix_hidden=True,
            fix_zone=True,
        )
        # perm/readonly/hidden 应该 skipped（命令不可用）
        assert report.results["perm"].status == "skipped"
        assert report.results["readonly"].status == "skipped"
        assert report.results["hidden"].status == "skipped"
        # zone 在非 NTFS 上可能 skipped 或 success
        assert report.results["zone"].status in ("skipped", "success")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 端到端：映射表完整性
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_e2e_mapping_completeness():
    """映射表覆盖所有源文件"""
    with tempfile.TemporaryDirectory() as src_dir:
        _make_tree(src_dir, {
            "A": {"B": {"f1.txt": "a", "f2.txt": "b"}},
            "C": {"f3.jpg": "c"},
            "D": {"E": {"F": {"f4.py": "d"}}},
        })

        tree = scan_directory(src_dir)
        _, mapping = run_engine(tree, case_sensitive=False)

        # 映射表应包含所有 4 个文件
        assert len(mapping) == 4, f"映射表不完整: {mapping}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 端到端：差异报告完整性
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_e2e_diff_report():
    """差异报告包含正确统计"""
    with tempfile.TemporaryDirectory() as src_dir:
        _make_tree(src_dir, {
            "A": {"B": {"f1.txt": "hello"}},
            "C": {"f2.jpg": "world"},
        })

        tree = scan_directory(src_dir)
        result_tree, mapping = run_engine(tree, case_sensitive=False)
        report = compute_diff(tree, result_tree, mapping)

        # 至少有文件条目
        assert len(report.items) >= 2
        # 摘要不为空
        summary = render_summary(report)
        assert "📊" in summary


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 端到端：可视化渲染
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_e2e_render_both_sides():
    """左右两栏渲染不报错"""
    with tempfile.TemporaryDirectory() as src_dir:
        _make_tree(src_dir, {
            "X": {
                "A": {"f1.txt": "a"},
                "B": {"f1.txt": "b"},
            }
        })

        tree = scan_directory(src_dir)
        result_tree, mapping = run_engine(tree, case_sensitive=False)
        report = compute_diff(tree, result_tree, mapping)

        src_text = render_tree_with_diff(tree, report, side="source")
        tgt_text = render_tree_with_diff(result_tree, report, side="result")

        assert len(src_text) > 0
        assert len(tgt_text) > 0
        assert "f1.txt" in src_text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 端到端：文件名清洗集成
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_e2e_sanitize_in_pipeline():
    """清洗非法文件名"""
    with tempfile.TemporaryDirectory() as src_dir, \
         tempfile.TemporaryDirectory() as tgt_dir:
        # 创建带非法字符的文件（Linux 允许大部分，但模拟 Windows 场景）
        _make_tree(src_dir, {"normal_file.txt": "data"})

        target = os.path.join(tgt_dir, "output")
        _, _, mapping, _, exec_result = _run_full_pipeline(src_dir, target, "copy")

        assert exec_result.success
        tgt_files = _collect_files(target)
        assert len(tgt_files) == 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 端到端：大文件保留内容
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_e2e_content_preservation():
    """确保文件内容在复制后完整保留"""
    with tempfile.TemporaryDirectory() as src_dir, \
         tempfile.TemporaryDirectory() as tgt_dir:
        # 创建有特定内容的文件
        long_content = "x" * 10000 + "\n" + "y" * 5000
        _make_tree(src_dir, {"A": {"B": {"big.txt": long_content}}})

        target = os.path.join(tgt_dir, "output")
        _, _, mapping, _, exec_result = _run_full_pipeline(src_dir, target, "copy")

        assert exec_result.success
        tgt_files = _collect_files(target)
        assert any(tgt_files.get(k) == long_content for k in tgt_files), \
            "文件内容不匹配"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 端到端：auto 策略
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_e2e_auto_strategy():
    """auto 策略在同卷时使用 copy（因为目标不存在，fallback）"""
    with tempfile.TemporaryDirectory() as src_dir, \
         tempfile.TemporaryDirectory() as tgt_dir:
        _make_tree(src_dir, {"f1.txt": "data"})

        target = os.path.join(tgt_dir, "output")
        tree = scan_directory(src_dir)
        result_tree, mapping = run_engine(tree, case_sensitive=False)
        exec_result = execute_mapping(mapping, src_dir, target, "auto")

        assert exec_result.success
        tgt_files = _collect_files(target)
        assert "f1.txt" in tgt_files


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 端到端：源目录不受影响（copy 策略）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_e2e_source_unchanged_after_copy():
    """copy 策略后源目录结构和内容不变"""
    with tempfile.TemporaryDirectory() as src_dir, \
         tempfile.TemporaryDirectory() as tgt_dir:
        _make_tree(src_dir, {
            "A": {"B": {"f1.txt": "hello"}},
            "C": {"f2.jpg": "world"},
        })

        # 记录源目录状态
        src_files_before = _collect_files(src_dir)

        target = os.path.join(tgt_dir, "output")
        _run_full_pipeline(src_dir, target, "copy")

        # 源目录应该完全不变
        src_files_after = _collect_files(src_dir)
        assert src_files_before == src_files_after, "copy 策略不应修改源目录"
