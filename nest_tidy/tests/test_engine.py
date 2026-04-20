"""核心算法引擎测试 — 覆盖 TC-01 ~ TC-16"""
import pytest
from nest_tidy.models import TreeNode
from nest_tidy.engine import run_engine, format_tree


def _f(name: str, path: str = "") -> TreeNode:
    """快捷创建文件节点"""
    p = f"{path}/{name}" if path else name
    return TreeNode(name=name, path=p, is_dir=False)


def _d(name: str, children: list, path: str = "") -> TreeNode:
    """快捷创建目录节点，自动修正子节点路径"""
    p = f"{path}/{name}" if path else name
    for c in children:
        _fix_paths(c, p)
    return TreeNode(name=name, path=p, is_dir=True, children=children)


def _fix_paths(node: TreeNode, parent_path: str) -> None:
    """递归修正节点路径"""
    node.path = f"{parent_path}/{node.name}" if parent_path else node.name
    if node.is_dir:
        for child in node.children:
            _fix_paths(child, node.path)


def _tree_paths(node: TreeNode) -> list[str]:
    """提取树中所有文件的路径（排序后），用于结构比较"""
    paths = []
    if not node.is_dir:
        paths.append(node.path)
    for child in node.children:
        paths.extend(_tree_paths(child))
    return sorted(paths)


def _run(source: TreeNode) -> tuple[TreeNode, dict]:
    """运行引擎的快捷方式"""
    return run_engine(source, case_sensitive=False)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-01 直链穿透（2层链→文件）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tc01():
    """
    R/ A/ B/ f1.txt
    → R1：链 A→B（2层）。消除 A、B。f1.txt 到 R。
    → R/ f1.txt
    """
    src = _d("R", [_d("A", [_d("B", [_f("f1.txt")])])])
    result, mapping = _run(src)
    paths = _tree_paths(result)
    assert paths == ["R/f1.txt"], f"TC-01 失败: {paths}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-02 直链穿透（3层链→文件）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tc02():
    """
    R/ A/ B/ C/ f1.txt
    → R1：链 A→B→C（3层）。消除 A、B、C。f1.txt 到 R。
    → R/ f1.txt
    """
    src = _d("R", [_d("A", [_d("B", [_d("C", [_f("f1.txt")])])])])
    result, mapping = _run(src)
    paths = _tree_paths(result)
    assert paths == ["R/f1.txt"], f"TC-02 失败: {paths}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-03 直链穿透（链终止于分叉口，之后不再处理）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tc03():
    """
    R/ A/ B/ C/ D/ f1.mp4
                 E/ f2.jpg
    → R1：A(壳)→B(2个子目录，终止)。消除 A、B。B 的内容提到 R。结束。
    → R/ C/ D/ f1.mp4
         E/ f2.jpg
    """
    src = _d("R", [
        _d("A", [
            _d("B", [
                _d("C", [_d("D", [_f("f1.mp4")])]),
                _d("E", [_f("f2.jpg")]),
            ])
        ])
    ])
    result, mapping = _run(src)
    paths = _tree_paths(result)
    assert "R/C/D/f1.mp4" in paths, f"TC-03 失败: {paths}"
    assert "R/E/f2.jpg" in paths, f"TC-03 失败: {paths}"
    # C 和 E 应该在根下
    names = [c.name for c in result.children if c.is_dir]
    assert "C" in names and "E" in names, f"TC-03 目录名失败: {names}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-04 壳吸收 + 根强制全留
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tc04():
    """
    R/ A/ B/ f1.mp4
         C/ f2.jpg
    → R2：A 吸收 B → A/f1.mp4。R3：根全留 → 冻结。
    → R/ A/ f1.mp4
         C/ f2.jpg
    """
    src = _d("R", [
        _d("A", [_d("B", [_f("f1.mp4")])]),
        _d("C", [_f("f2.jpg")]),
    ])
    result, mapping = _run(src)
    paths = _tree_paths(result)
    assert "R/A/f1.mp4" in paths, f"TC-04 失败: {paths}"
    assert "R/C/f2.jpg" in paths, f"TC-04 失败: {paths}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-05 根有文件，对其中的子文件夹执行清理
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tc05():
    """
    R/ f1.txt
         A/ B/ f2.mp4
    → R0：根有文件 → R1 跳过，R4 根层级跳过。R2 正常执行。
    → R/ f1.txt
         A/ f2.mp4
    """
    src = _d("R", [
        _f("f1.txt"),
        _d("A", [_d("B", [_f("f2.mp4")])]),
    ])
    result, mapping = _run(src)
    paths = _tree_paths(result)
    assert "R/f1.txt" in paths, f"TC-05 失败: {paths}"
    assert "R/A/f2.mp4" in paths, f"TC-05 失败: {paths}"
    # A 不应被提级（根有文件 → R4 根层级跳过）
    a_children = [c for c in result.children if c.name == "A"]
    assert len(a_children) == 1 and a_children[0].is_dir, f"TC-05 A应为目录"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-06 链式壳吸收（根全留冻结）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tc06():
    """
    R/ A/ B/ C/ D/ f1.py
         E/ F/ G/ f2.md
    → R3：根全留 → 冻结 A、E。递归：A 逐层吸收 → A/f1.py。E 同理 → E/f2.md。
    → R/ A/ f1.py
         E/ f2.md
    """
    src = _d("R", [
        _d("A", [_d("B", [_d("C", [_d("D", [_f("f1.py")])])])]),
        _d("E", [_d("F", [_d("G", [_f("f2.md")])])]),
    ])
    result, mapping = _run(src)
    paths = _tree_paths(result)
    assert "R/A/f1.py" in paths, f"TC-06 失败: {paths}"
    assert "R/E/f2.md" in paths, f"TC-06 失败: {paths}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-07 全留冻结（混合结构，阻止单文件提级）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tc07():
    """
    R/ A/ B/ f1.txt
             f2.txt
         C/ D/ f3.txt
    → R2：C 吸收 D → C/f3.txt。
    → R3：根全留 → 冻结 A、C。递归 A：B 是单文件但被冻结 → 不提取。
    → R/ A/ B/ f1.txt
                  f2.txt
         C/ f3.txt
    """
    src = _d("R", [
        _d("A", [
            _d("B", [_f("f1.txt")]),
            _f("f2.txt"),
        ]),
        _d("C", [_d("D", [_f("f3.txt")])]),
    ])
    result, mapping = _run(src)
    paths = _tree_paths(result)
    assert "R/A/B/f1.txt" in paths, f"TC-07 失败: {paths}"
    assert "R/A/f2.txt" in paths, f"TC-07 失败: {paths}"
    assert "R/C/f3.txt" in paths, f"TC-07 失败: {paths}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-08 空目录清理 + 单文件提级
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tc08():
    """
    R/ A/ f1.txt
         B/ C/
              D/
    → R0：C、D 空 → 删。B 空 → 删。R4：A 单文件 → 提取。
    → R/ f1.txt
    """
    src = _d("R", [
        _d("A", [_f("f1.txt")]),
        _d("B", [_d("C", []), _d("D", [])]),
    ])
    result, mapping = _run(src)
    paths = _tree_paths(result)
    assert paths == ["R/f1.txt"], f"TC-08 失败: {paths}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-09 非根分叉口全提
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tc09():
    """
    R/ X/ A/ V/ f1.mp4
         B/ V/ f2.mp4
    → R3：根全留 → 冻结 X。内部：R2 壳吸收。R3：X 分叉口全单 → 全提。
    → R/ X/ f1.mp4
              f2.mp4
    """
    src = _d("R", [
        _d("X", [
            _d("A", [_d("V", [_f("f1.mp4")])]),
            _d("B", [_d("V", [_f("f2.mp4")])]),
        ])
    ])
    result, mapping = _run(src)
    paths = _tree_paths(result)
    assert "R/X/f1.mp4" in paths, f"TC-09 失败: {paths}"
    assert "R/X/f2.mp4" in paths, f"TC-09 失败: {paths}"
    # X 下不应再有 A、B 子目录
    x_node = [c for c in result.children if c.name == "X"][0]
    subdirs = [c.name for c in x_node.children if c.is_dir]
    assert subdirs == [], f"TC-09 X下不应有子目录: {subdirs}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-10 非根分叉口全留
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tc10():
    """
    R/ X/ A/ V/ f1.mp4
                   f2.mp4
         B/ V/ f3.mp4
         C/ V/ f4.mp4
    → R3：根全留 → 冻结 X。内部：R2 壳吸收。R3：A 有 2 文件 → 全留 → 冻结。
    → R/ X/ A/ f1.mp4
                  f2.mp4
              B/ f3.mp4
              C/ f4.mp4
    """
    src = _d("R", [
        _d("X", [
            _d("A", [_d("V", [_f("f1.mp4"), _f("f2.mp4")])]),
            _d("B", [_d("V", [_f("f3.mp4")])]),
            _d("C", [_d("V", [_f("f4.mp4")])]),
        ])
    ])
    result, mapping = _run(src)
    paths = _tree_paths(result)
    assert "R/X/A/f1.mp4" in paths, f"TC-10 失败: {paths}"
    assert "R/X/A/f2.mp4" in paths, f"TC-10 失败: {paths}"
    assert "R/X/B/f3.mp4" in paths, f"TC-10 失败: {paths}"
    assert "R/X/C/f4.mp4" in paths, f"TC-10 失败: {paths}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-11 深层壳吸收（保留外层名字）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tc11():
    """
    R/ X/ A/ B/ f1.mp4
                   f2.mp4
         C/ f3.jpg
              f4.jpg
    → R3：根全留 → 冻结 X。内部：R2：A 吸收 B → A 保留名字。R3：X 全留。
    → R/ X/ A/ f1.mp4
                  f2.mp4
              C/ f3.jpg
                  f4.jpg
    """
    src = _d("R", [
        _d("X", [
            _d("A", [_d("B", [_f("f1.mp4"), _f("f2.mp4")])]),
            _d("C", [_f("f3.jpg"), _f("f4.jpg")]),
        ])
    ])
    result, mapping = _run(src)
    paths = _tree_paths(result)
    assert "R/X/A/f1.mp4" in paths, f"TC-11 失败: {paths}"
    assert "R/X/A/f2.mp4" in paths, f"TC-11 失败: {paths}"
    assert "R/X/C/f3.jpg" in paths, f"TC-11 失败: {paths}"
    assert "R/X/C/f4.jpg" in paths, f"TC-11 失败: {paths}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-12 复杂嵌套
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tc12():
    """
    R/ X/ A/ B/ C/ f1.jpg
                        f2.jpg
                   D/ f3.mp4
         E/ F/ G/ f4.jpg
                   f5.jpg
              H/ f6.txt
    → 壳吸收后全留冻结。A 吸收 B → A(C/,D/)。E 吸收 F → E(G/,H/)。全留冻结。
    → R/ X/ A/ C/ f1.jpg
                      f2.jpg
                  D/ f3.mp4
              E/ G/ f4.jpg
                      f5.jpg
                  H/ f6.txt
    """
    src = _d("R", [
        _d("X", [
            _d("A", [
                _d("B", [
                    _d("C", [_f("f1.jpg"), _f("f2.jpg")]),
                    _d("D", [_f("f3.mp4")]),
                ])
            ]),
            _d("E", [
                _d("F", [
                    _d("G", [_f("f4.jpg"), _f("f5.jpg")]),
                    _d("H", [_f("f6.txt")]),
                ])
            ]),
        ])
    ])
    result, mapping = _run(src)
    paths = _tree_paths(result)
    assert "R/X/A/C/f1.jpg" in paths, f"TC-12 失败: {paths}"
    assert "R/X/A/C/f2.jpg" in paths, f"TC-12 失败: {paths}"
    assert "R/X/A/D/f3.mp4" in paths, f"TC-12 失败: {paths}"
    assert "R/X/E/G/f4.jpg" in paths, f"TC-12 失败: {paths}"
    assert "R/X/E/G/f5.jpg" in paths, f"TC-12 失败: {paths}"
    assert "R/X/E/H/f6.txt" in paths, f"TC-12 失败: {paths}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-13 两个独立分支壳吸收（同名文件不撞名）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tc13():
    """
    R/ A/ B/ C/ f1.mp4
         D/ E/ F/ f1.mp4
    → R2：A 吸收链 → A/f1.mp4。D 吸收链 → D/f1.mp4。不同父目录，不撞名。
    → R/ A/ f1.mp4
         D/ f1.mp4
    """
    src = _d("R", [
        _d("A", [_d("B", [_d("C", [_f("f1.mp4")])])]),
        _d("D", [_d("E", [_d("F", [_f("f1.mp4")])])]),
    ])
    result, mapping = _run(src)
    paths = _tree_paths(result)
    assert "R/A/f1.mp4" in paths, f"TC-13 失败: {paths}"
    assert "R/D/f1.mp4" in paths, f"TC-13 失败: {paths}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-14 全提导致撞名
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tc14():
    """
    R/ X/ A/ f1.txt
         B/ f1.txt
    → R3：根全留 → 冻结 X。内部：A(1文件)、B(1文件) → 全提。撞名 → R5 溯源。
    → R/ X/ A-f1.txt
              B-f1.txt
    """
    src = _d("R", [
        _d("X", [
            _d("A", [_f("f1.txt")]),
            _d("B", [_f("f1.txt")]),
        ])
    ])
    result, mapping = _run(src)
    x_node = [c for c in result.children if c.name == "X"][0]
    names = sorted([c.name for c in x_node.children])
    assert "A-f1.txt" in names, f"TC-14 失败: {names}"
    assert "B-f1.txt" in names, f"TC-14 失败: {names}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-15 壳吸收后全提导致撞名
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tc15():
    """
    R/ A/ B/ X/ f1.txt
              Y/ f1.txt
    → R3：根全留 → 冻结 A。内部：R2 吸收 B → A(X/,Y/)。R3 全提。撞名 → R5。
    → R/ A/ X-f1.txt
              Y-f1.txt
    """
    src = _d("R", [
        _d("A", [
            _d("B", [
                _d("X", [_f("f1.txt")]),
                _d("Y", [_f("f1.txt")]),
            ])
        ])
    ])
    result, mapping = _run(src)
    a_node = [c for c in result.children if c.name == "A"][0]
    names = sorted([c.name for c in a_node.children])
    assert "X-f1.txt" in names, f"TC-15 失败: {names}"
    assert "Y-f1.txt" in names, f"TC-15 失败: {names}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-16 R0 清理后触发 R1
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tc16():
    """
    R/ A/ B/ C/ f1.txt
              D/
    → R0：D 空 → 删。A 只剩 B → A 变成壳。R1：链 A→B→C（3层）。f1.txt 到 R。
    → R/ f1.txt
    """
    src = _d("R", [
        _d("A", [
            _d("B", [_d("C", [_f("f1.txt")])]),
            _d("D", []),
        ])
    ])
    result, mapping = _run(src)
    paths = _tree_paths(result)
    assert paths == ["R/f1.txt"], f"TC-16 失败: {paths}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 辅助测试
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_mapping_generated():
    """验证映射表是否正确生成"""
    src = _d("R", [_d("A", [_d("B", [_f("f1.txt")])])])
    result, mapping = _run(src)
    assert len(mapping) > 0, "映射表不应为空"


def test_format_tree():
    """验证树格式化输出不报错"""
    src = _d("R", [_d("A", [_f("f1.txt")])])
    result, _ = _run(src)
    text = format_tree(result)
    assert "f1.txt" in text


def test_empty_root():
    """空根目录不应报错"""
    src = _d("R", [])
    result, mapping = _run(src)
    assert result.is_empty


def test_root_with_files_only():
    """根目录只有文件，无子目录"""
    src = _d("R", [_f("a.txt"), _f("b.txt")])
    result, mapping = _run(src)
    paths = _tree_paths(result)
    assert sorted(paths) == ["R/a.txt", "R/b.txt"]
