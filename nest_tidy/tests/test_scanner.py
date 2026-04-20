"""扫描模块测试"""
import os
import tempfile
import pytest
from nest_tidy.scanner import scan_directory, tree_to_dict


@pytest.fixture
def sample_dir():
    """创建临时测试目录结构"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # R/ A/ B/ f1.txt
        #      C/ f2.jpg
        os.makedirs(os.path.join(tmpdir, "A", "B"))
        os.makedirs(os.path.join(tmpdir, "C"))
        with open(os.path.join(tmpdir, "A", "B", "f1.txt"), "w") as f:
            f.write("hello")
        with open(os.path.join(tmpdir, "C", "f2.jpg"), "w") as f:
            f.write("world")
        yield tmpdir


def test_scan_basic(sample_dir):
    """基本扫描功能"""
    tree = scan_directory(sample_dir)
    assert tree.is_dir
    assert tree.total_items == 2
    assert tree.total_dirs == 3  # A, B, C


def test_scan_empty_dir():
    """空目录扫描"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tree = scan_directory(tmpdir)
        assert tree.is_dir
        assert tree.total_items == 0
        assert tree.is_empty


def test_scan_nested(sample_dir):
    """嵌套结构扫描"""
    tree = scan_directory(sample_dir)
    names = [c.name for c in tree.children]
    assert "A" in names
    assert "C" in names


def test_scan_system_file_filter():
    """系统文件过滤"""
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "real.txt"), "w") as f:
            f.write("data")
        with open(os.path.join(tmpdir, "desktop.ini"), "w") as f:
            f.write("ini")
        with open(os.path.join(tmpdir, "Thumbs.db"), "w") as f:
            f.write("thumb")
        tree = scan_directory(tmpdir)
        file_names = [c.name for c in tree.children if not c.is_dir]
        assert file_names == ["real.txt"]


def test_scan_max_depth():
    """深度限制"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "A", "B", "C"))
        with open(os.path.join(tmpdir, "A", "B", "C", "f.txt"), "w") as f:
            f.write("deep")
        # depth=1: 只扫描到 A 层
        tree = scan_directory(tmpdir, max_depth=1)
        a_node = [c for c in tree.children if c.name == "A"][0]
        # A 在深度限制下应该作为叶节点（无子节点展开）
        assert a_node.total_items == 0  # 未展开


def test_tree_to_dict(sample_dir):
    """格式化输出"""
    tree = scan_directory(sample_dir)
    text = tree_to_dict(tree)
    assert "f1.txt" in text
    assert "f2.jpg" in text
