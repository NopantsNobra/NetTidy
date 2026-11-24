from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import REPORT_DIR_NAME


@dataclass
class UndoResult:
    action: str
    source: str
    target: str
    status: str
    message: str


def _undo_path(raw: object, scope: object = None, allow_root: bool = False) -> Path:
    """撤销不得通过链接或重解析点写入；旧日志仍接受绝对路径。"""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("日志路径为空或无效")
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts or path == path.parent:
        raise ValueError(f"日志路径不是安全的绝对路径: {path}")
    if scope is not None:
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError("日志资源根无效")
        root = Path(scope)
        if not root.is_absolute() or ".." in root.parts or not path.is_relative_to(root):
            raise ValueError(f"撤销路径越过记录的资源根: {path}")
        if path == root and not allow_root:
            raise ValueError(f"拒绝对资源根本身执行此撤销动作: {path}")
    # 从祖先到叶子复核，避免穿过重解析点后才发现路径被替换。
    for component in reversed((path, *path.parents)):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"拒绝符号链接或重解析点: {component}")
    return path


def _directory_identity(folder: Path) -> list[int]:
    info = folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"不是普通目录: {folder}")
    if not info.st_ino:
        raise ValueError(f"目录身份不可验证: {folder}")
    return [info.st_dev, info.st_ino]


def _identity_pair(value: object) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2 or not all(isinstance(part, int) for part in value) or not value[1]:
        raise ValueError("无效目录身份")
    return value[0], value[1]


def _verify_undo_scope(item: dict, recreated: dict, allow_missing_root: bool = False) -> None:
    if "resource_identity" not in item:
        return  # 兼容旧日志：仍由 _undo_path 保护边界及链接。
    root = Path(item["resource_root"])
    selected = Path(item["selected_root"])
    if not selected.is_absolute() or ".." in selected.parts or not root.is_relative_to(selected):
        raise ValueError("日志选中根与资源根关系无效")
    if _directory_identity(selected) != list(_identity_pair(item.get("selected_identity"))):
        raise ValueError(f"选中根身份已变化: {selected}")
    original = _identity_pair(item.get("resource_identity"))
    expected = recreated.get((root, original), original)
    try:
        current = _directory_identity(root)
    except FileNotFoundError:
        if allow_missing_root and root != selected:
            return
        raise
    if current != list(expected):
        raise ValueError(f"资源根身份已变化: {root}")


def _record_recreated_root(log: Path, item: dict, folder: Path, recreated: dict) -> None:
    if "resource_identity" not in item or folder != Path(item["resource_root"]):
        return
    original = _identity_pair(item["resource_identity"])
    actual = _directory_identity(folder)
    row = {
        "action": "undo_root_recreated", "status": "done", "resource_root": str(folder),
        "original_identity": list(original), "restored_identity": actual,
    }
    # 若后续撤销失败保留日志，下次仍能识别本次明确重建的目录。
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    recreated[(folder, original)] = tuple(actual)


def undo_transaction(transaction_log: str, progress=None) -> list[UndoResult]:
    path = Path(transaction_log).resolve()
    if not path.exists():
        raise FileNotFoundError(f"事务日志不存在: {path}")
    # started 行必须参与撤销：move 已完成但 done 日志写失败（磁盘满、进程被杀）时，
    # 文件已经在新位置，只认 done 会把这条操作从可恢复集合里彻底丢掉。
    collected: dict[tuple, dict] = {}
    order: list[tuple] = []
    recreated: dict[tuple[Path, tuple[int, int]], tuple[int, int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            # 末行截断（写一半崩溃）恰是 undo 最需要的场景：跳过该行，其余完整行照常回收；只捕获解析错误，不吞其他异常。
            continue
        if item.get("action") == "undo_root_recreated":
            try:
                root = Path(item["resource_root"])
                recreated[(root, _identity_pair(item["original_identity"]))] = _identity_pair(item["restored_identity"])
            except (KeyError, TypeError, ValueError):
                continue  # 无效标记不提供身份豁免
            continue
        if item.get("status") not in {"started", "done"}:
            continue
        key = (item.get("action"), item.get("source"), item.get("target"))
        if key not in collected:
            collected[key] = item
            order.append(key)
        elif item.get("status") == "done":
            collected[key] = item
    results: list[UndoResult] = []
    for index, key in enumerate(reversed(order), 1):
        if progress:
            progress(index, len(order), str(key[1] or ""), "撤销")
        item = collected[key]
        action = item.get("action", "")
        scope = item.get("resource_root")
        if action in {"mkdir", "rmdir", "move", "quarantine"}:
            try:
                source_path = _undo_path(item.get("source"), scope, allow_root=action == "rmdir")
                if action in {"move", "quarantine"}:
                    target_path = _undo_path(item.get("target"), scope if action == "move" else None)
                restoring_root = action == "rmdir" and scope is not None and source_path == Path(scope)
                _verify_undo_scope(item, recreated, allow_missing_root=restoring_root)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                results.append(UndoResult(action, str(item.get("source", "")), str(item.get("target", "")), "failed", f"拒绝不安全的撤销路径: {exc}"))
                continue
        if action == "recycle":
            results.append(UndoResult(action, str(item.get("source", "")), "", "skipped", "已移入回收站：在回收站里右键「还原」即可放回原位置"))
            continue
        if action == "mkdir":
            # 整理时新建的空文件夹：文件都已搬回原处后再收掉，非空则一律不动（里面还有别人的东西）。
            folder = source_path
            try:
                if not folder.exists():
                    results.append(UndoResult(action, str(folder), "", "skipped", "目录已不存在"))
                elif any(folder.iterdir()):
                    results.append(UndoResult(action, str(folder), "", "skipped", "目录里还有别的内容，未删除"))
                else:
                    _undo_path(str(folder), scope)
                    _verify_undo_scope(item, recreated)
                    folder.rmdir()
                    results.append(UndoResult(action, str(folder), "", "ok", "已删除整理时新建的空文件夹"))
            except (OSError, ValueError, RuntimeError) as exc:
                results.append(UndoResult(action, str(folder), "", "failed", f"撤销目录失败: {exc}"))
            continue
        if action == "rmdir":
            # v1.8.0（R6）起空目录删除改为可还原：重建目录本身，文件早已各自搬回原路径。
            # 重建出来同样是空目录——这正是整理前的样子（当初它就是个空壳或只含被搬走的文件）。
            raw = str(item.get("source", ""))
            if not raw:
                results.append(UndoResult(action, "", "", "skipped", "日志缺少目录路径，未重建"))
                continue
            folder = source_path
            try:
                _undo_path(raw, scope, allow_root=True)
                _verify_undo_scope(item, recreated, allow_missing_root=restoring_root)
                if folder.is_dir():
                    results.append(UndoResult(action, raw, "", "ok", "目录本来就在，无需重建"))
                else:
                    folder.mkdir(parents=True)
                    _record_recreated_root(path, item, folder, recreated)
                    results.append(UndoResult(action, raw, "", "ok", "已重建被删除的空目录"))
            except (OSError, ValueError, RuntimeError) as exc:
                results.append(UndoResult(action, raw, "", "failed", f"重建目录失败：{exc}"))
            continue
        if action not in {"move", "quarantine"}:
            # delete（彻底删除）没有现场可回收，必须显式说清，不能让它静默混在 skipped 里。
            note = "彻底删除无法撤销（未进入隔离区）" if action == "delete" else "该操作不支持自动撤销"
            results.append(UndoResult(action, item.get("source", ""), item.get("target") or "", "skipped", note))
            continue
        source = source_path
        target = target_path
        source_exists = source.exists()
        target_exists = target.exists()
        if source_exists and target_exists:
            results.append(UndoResult(action, str(source), str(target), "failed", "原路径与隔离副本同时存在，需人工确认"))
            continue
        if not target_exists:
            results.append(UndoResult(action, str(source), str(target), "skipped", "现场未见隔离副本，跳过"))
            continue
        try:
            _undo_path(str(source), scope)
            _undo_path(str(target), scope if action == "move" else None)
            _verify_undo_scope(item, recreated)
            source.parent.mkdir(parents=True, exist_ok=True)
            _undo_path(str(source), scope)
            _undo_path(str(target), scope if action == "move" else None)
            _verify_undo_scope(item, recreated)
            if source.exists() or source.is_symlink():
                raise FileExistsError(f"原路径已存在: {source}")
            if action == "move" and os.name == "nt":
                os.rename(target, source)
            else:
                shutil.move(str(target), str(source))
        except (OSError, ValueError, RuntimeError) as exc:
            results.append(UndoResult(action, str(source), str(target), "failed", f"撤销移动失败: {exc}"))
            continue
        if action == "quarantine":
            message = "已从隔离区放回原路径" if item.get("status") == "done" else "日志缺 done，按现场状态放回"
        else:
            message = "已移动回原路径" if item.get("status") == "done" else "日志缺 done，按现场状态回收"
        results.append(UndoResult(action, str(source), str(target), "ok", message))
    _retire_log(path, results)
    return results


def _retire_log(path: Path, results: list[UndoResult]) -> None:
    """撤销全部到位后收掉这次执行留下的记录物，别把「整理产生的文件夹」留在用户目录里。

    只在没有任何失败行时动手（失败行还要靠日志再撤一次）；只删本次日志，并且仅当
    `理巢报告` 目录已经空了才连带删掉它——里面还有别的日志就原样保留。
    """
    if any(row.status == "failed" for row in results):
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return
    parent = path.parent
    if parent.name.casefold() != REPORT_DIR_NAME.casefold():
        return
    try:
        if not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        return


def rows_as_dicts(rows: list[UndoResult]) -> list[dict]:
    return [asdict(row) for row in rows]

