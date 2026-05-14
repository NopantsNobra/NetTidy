from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

RESERVED_RE = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.I)
ILLEGAL_CHARS = '<>:"/\\|?*'


@dataclass
class VFile:
    name: str
    original_path: Path
    size: int
    mtime_ns: int
    traces: list[str] = field(default_factory=list)
    name_cleaning_trace: list[str] = field(default_factory=list)
    conflict_trace: list[str] = field(default_factory=list)


@dataclass
class VDir:
    name: str
    original_path: Path
    files: list[VFile] = field(default_factory=list)
    dirs: list["VDir"] = field(default_factory=list)
    traces: list[str] = field(default_factory=list)

    def sort(self) -> None:
        self.files.sort(key=lambda f: stable_key(f.original_path, "file"))
        self.dirs.sort(key=lambda d: stable_key(d.original_path, "dir"))
        for child in self.dirs:
            child.sort()


def stable_key(path: Path, entry_type: str) -> tuple[str, str, int]:
    order = 0 if entry_type == "dir" else 1
    text = str(path)
    return (os.path.normcase(text).casefold(), text, order)


def clean_name(name: str) -> tuple[str, list[str]]:
    traces: list[str] = []
    cleaned_chars = []
    for ch in name:
        if ch in ILLEGAL_CHARS or ord(ch) < 32:
            cleaned_chars.append("_")
        else:
            cleaned_chars.append(ch)
    cleaned = "".join(cleaned_chars)
    if cleaned != name:
        traces.append("非法字符替换为下划线")
    trimmed = cleaned.rstrip(" .")
    if trimmed != cleaned:
        traces.append("去除尾随空格或点号")
        cleaned = trimmed
    if not cleaned:
        cleaned = "_"
        traces.append("空文件名替换为下划线")
    if RESERVED_RE.match(cleaned):
        cleaned = f"_{cleaned}"
        traces.append("Windows 保留名添加前缀")
    if len(cleaned) > 180:
        stem, suffix = os.path.splitext(cleaned)
        keep = max(16, 180 - len(suffix) - 3)
        left = keep // 2
        right = keep - left
        cleaned = f"{stem[:left]}...{stem[-right:]}{suffix}"
        traces.append("过长文件名保留扩展名截断")
    return cleaned, traces


def build_tree(root: Path, should_skip) -> tuple[VDir, list[dict]]:
    issues: list[dict] = []

    def walk(path: Path) -> VDir:
        node = VDir(name=path.name, original_path=path)
        try:
            children = sorted(path.iterdir(), key=lambda p: stable_key(p, "dir" if p.is_dir() else "file"))
        except OSError as exc:
            issues.append({"path": str(path), "code": "PERMISSION_DENIED", "message": str(exc)})
            return node
        for child in children:
            skip = should_skip(child)
            if skip:
                issues.append({"path": str(child), "code": skip, "message": "已按扫描排除规则跳过"})
                continue
            try:
                if child.is_dir():
                    node.dirs.append(walk(child))
                elif child.is_file():
                    stat = child.stat()
                    cleaned, clean_trace = clean_name(child.name)
                    node.files.append(
                        VFile(
                            name=cleaned,
                            original_path=child,
                            size=stat.st_size,
                            mtime_ns=stat.st_mtime_ns,
                            name_cleaning_trace=clean_trace,
                        )
                    )
                else:
                    issues.append({"path": str(child), "code": "SKIPPED_SPECIAL", "message": "特殊文件系统条目"})
            except OSError as exc:
                issues.append({"path": str(child), "code": "PERMISSION_DENIED", "message": str(exc)})
        return node

    tree = walk(root)
    tree.sort()
    return tree, issues


def prune_empty_dirs(node: VDir, removed: list[Path]) -> None:
    kept: list[VDir] = []
    for child in node.dirs:
        prune_empty_dirs(child, removed)
        if child.files or child.dirs:
            kept.append(child)
        else:
            child.traces.append("R0 空目录清理")
            removed.append(child.original_path)
    node.dirs = kept


def apply_root_chain_penetration(root: VDir) -> bool:
    if root.files or len(root.dirs) != 1:
        return False
    chain = [root.dirs[0]]
    current = root.dirs[0]
    while not current.files and len(current.dirs) == 1:
        current = current.dirs[0]
        chain.append(current)
    if not current.files and not current.dirs:
        return False
    root.files = current.files
    root.dirs = current.dirs
    for item in root.files:
        item.traces.append("R1 资源根内直链穿透")
    for child in root.dirs:
        child.traces.append("R1 资源根内直链穿透")
    root.traces.append("R1 资源根内直链穿透: " + " -> ".join(d.name for d in chain))
    return True


def absorb_shell_children_until_stable(node: VDir) -> None:
    changed = True
    while changed:
        changed = False
        for child in list(node.dirs):
            while not child.files and len(child.dirs) == 1:
                inner = child.dirs[0]
                child.files = inner.files
                child.dirs = inner.dirs
                child.traces.append(f"R2 壳吸收: {child.name} 吸收 {inner.name}")
                for item in child.files:
                    item.traces.append("R2 壳吸收")
                changed = True


def is_branch(node: VDir) -> bool:
    return not node.files and len(node.dirs) >= 2


def is_single_file_dir(node: VDir) -> bool:
    return len(node.files) == 1 and not node.dirs


def process_node(node: VDir, removed: list[Path]) -> None:
    prune_empty_dirs(node, removed)
    absorb_shell_children_until_stable(node)
    if is_branch(node):
        if all(is_single_file_dir(child) for child in node.dirs):
            for child in list(node.dirs):
                file_item = child.files[0]
                file_item.traces.append("R3 分叉口全提")
                node.files.append(file_item)
                removed.append(child.original_path)
            node.dirs.clear()
            resolve_names(node)
            return
        for child in node.dirs:
            child.traces.append("R3 父级保护")
            process_node(child, removed)
        resolve_names(node)
        return
    for child in node.dirs:
        process_node(child, removed)
    kept_dirs: list[VDir] = []
    for child in node.dirs:
        if "R3 父级保护" not in child.traces and is_single_file_dir(child):
            file_item = child.files[0]
            file_item.traces.append("R4 单文件提级")
            node.files.append(file_item)
            removed.append(child.original_path)
        else:
            kept_dirs.append(child)
    node.dirs = kept_dirs
    resolve_names(node)


def resolve_names(node: VDir) -> None:
    entries: list[VFile | VDir] = [*node.dirs, *node.files]
    if not entries:
        return
    rounds = 0
    while rounds < 64:
        buckets: dict[str, list[VFile | VDir]] = {}
        for entry in entries:
            buckets.setdefault(entry.name.casefold(), []).append(entry)
        conflicts = [group for group in buckets.values() if len(group) > 1]
        if not conflicts:
            return
        for group in conflicts:
            for entry in group:
                entry.name = prefixed_name(entry.original_path, entry.name, rounds + 1)
                if isinstance(entry, VFile):
                    entry.conflict_trace.append(f"R5 撞名重命名: {entry.name}")
                    entry.traces.append("R5 撞名重命名")
                else:
                    entry.traces.append(f"R5 撞名重命名: {entry.name}")
        rounds += 1
    raise ValueError(f"无法解决命名冲突: {node.original_path}")


def prefixed_name(original_path: Path, current_name: str, depth: int) -> str:
    parents = [part for part in original_path.parent.parts if part]
    selected = list(reversed(parents))[:depth]
    prefix = "-".join(reversed(selected))
    return f"{prefix}-{current_name}" if prefix else current_name


def final_files(node: VDir, base: Path) -> list[tuple[VFile, Path]]:
    rows: list[tuple[VFile, Path]] = []
    for file_item in sorted(node.files, key=lambda f: stable_key(f.original_path, "file")):
        rows.append((file_item, base / file_item.name))
    for child in sorted(node.dirs, key=lambda d: stable_key(d.original_path, "dir")):
        rows.extend(final_files(child, base / child.name))
    return rows

