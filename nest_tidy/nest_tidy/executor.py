"""执行策略模块 — copy/move/auto 文件操作"""
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class ExecutionResult:
    """单次执行结果"""
    success: bool
    files_copied: int = 0
    files_moved: int = 0
    files_skipped: int = 0
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def detect_same_volume(source: str, target: str) -> bool:
    """检测源与目标是否在同一卷（Windows 盘符比较）"""
    src_drive = os.path.splitdrive(os.path.abspath(source))[0].upper()
    tgt_drive = os.path.splitdrive(os.path.abspath(target))[0].upper()
    return src_drive == tgt_drive


def execute_mapping(
    mapping: dict[str, str],
    source_dir: str,
    target_dir: str,
    strategy: str = "auto",
    *,
    dry_run: bool = False,
) -> ExecutionResult:
    """
    按照映射表执行文件复制/移动。

    Parameters
    ----------
    mapping : dict
        源相对路径 → 目标相对路径
    source_dir : str
        源目录绝对路径
    target_dir : str
        目标目录绝对路径
    strategy : str
        copy / move / auto
    dry_run : bool
        仅模拟，不实际操作

    Returns
    -------
    ExecutionResult
    """
    result = ExecutionResult(success=True)

    # 确定实际策略
    if strategy == "auto":
        actual_strategy = "move" if detect_same_volume(source_dir, target_dir) else "copy"
    else:
        actual_strategy = strategy

    for src_rel, tgt_rel in mapping.items():
        src_abs = os.path.join(source_dir, src_rel)
        tgt_abs = os.path.join(target_dir, tgt_rel)

        if not os.path.exists(src_abs):
            result.warnings.append(f"源文件不存在，跳过: {src_rel}")
            result.files_skipped += 1
            continue

        # 确保目标目录存在
        tgt_parent = os.path.dirname(tgt_abs)
        if not dry_run:
            os.makedirs(tgt_parent, exist_ok=True)

        try:
            if dry_run:
                # 模拟模式只计数
                if actual_strategy == "move":
                    result.files_moved += 1
                else:
                    result.files_copied += 1
                continue

            if actual_strategy == "move":
                # 同卷移动：优先 os.rename（原子操作，极快）
                try:
                    os.rename(src_abs, tgt_abs)
                except OSError:
                    # 跨卷或权限问题，fallback 到 shutil.move
                    shutil.move(src_abs, tgt_abs)
                result.files_moved += 1
            else:
                # 复制
                shutil.copy2(src_abs, tgt_abs)
                result.files_copied += 1

        except Exception as e:
            result.errors.append(f"{src_rel}: {str(e)}")
            result.success = False

    return result


def clean_empty_source_dirs(source_dir: str, mapping: dict[str, str]) -> list[str]:
    """
    移动策略完成后，清理源目录中变空的目录。
    返回被删除的目录列表。
    """
    # 找出所有涉及的源目录
    src_dirs = set()
    for src_rel in mapping:
        parts = os.path.dirname(src_rel).split(os.sep)
        for i in range(len(parts)):
            src_dirs.add(os.path.join(source_dir, *parts[:i+1]))

    # 自底向上删除空目录
    deleted = []
    for d in sorted(src_dirs, key=lambda x: -x.count(os.sep)):
        try:
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
                deleted.append(d)
        except OSError:
            pass

    return deleted
