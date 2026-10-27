from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

RESERVED_RE = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.I)
ILLEGAL_CHARS = '<>:"/\\|?*'
# 单个文件名分量的硬上限。Windows 是 255 字符，留余量给 R5 前缀与输出根拼接，
# 超过 180 的名字在 Windows 上叠加前缀后极易越过 255，直接导致执行期 ENAMETOOLONG。
MAX_NAME_LEN = 180
# 扩展名超过这一长度时不再当作扩展名保留，按普通字符一起截断。
MAX_SUFFIX_LEN = 24


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


def truncate_middle(text: str, limit: int) -> str:
    """保留首尾、折叠中段，保证长度不超过 limit。"""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    room = limit - 3
    left = room // 2
    right = room - left
    return f"{text[:left]}...{text[-right:]}"


def fit_component(name: str, limit: int = MAX_NAME_LEN) -> str:
    """把单个文件名分量压到 limit 以内，尽量保留扩展名。"""
    if len(name) <= limit:
        return name
    stem, suffix = os.path.splitext(name)
    if len(suffix) > MAX_SUFFIX_LEN:
        suffix = suffix[:MAX_SUFFIX_LEN]
    return truncate_middle(stem, max(1, limit - len(suffix))) + suffix


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
    if len(cleaned) > MAX_NAME_LEN:
        cleaned = fit_component(cleaned)
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
    # 基础名在改名前快照一次：前缀只由祖先链决定，绝不把上一轮前缀叠进下一轮。
    base_names = {id(entry): entry.name for entry in entries}
    rounds = 0
    while rounds < 64:
        buckets: dict[str, list[VFile | VDir]] = {}
        for entry in entries:
            buckets.setdefault(entry.name.casefold(), []).append(entry)
        conflicts = [group for group in buckets.values() if len(group) > 1]
        if not conflicts:
            return
        rounds += 1
        for group in conflicts:
            before = {id(entry): entry.name.casefold() for entry in group}
            for entry in group:
                entry.name = prefixed_name(entry.original_path, base_names[id(entry)], rounds)
            # 同组仍重名是中间轮次的正常现象（祖先链要往上走到分叉层才区分开）。
            # 只有「这一轮改名后名字与上一轮完全相同」才说明前缀已到顶、
            # 再循环也分不开——同目录截断撞名就是这种情形。
            if all(entry.name.casefold() == before[id(entry)] for entry in group):
                for entry in group:
                    entry.name = hashed_name(base_names[id(entry)], entry.original_path)
                _trace_conflict(group, "R5 撞名重命名（路径短码兜底）")
            else:
                _trace_conflict(group, "R5 撞名重命名")
    raise ValueError(f"无法解决命名冲突: {node.original_path}")


def _trace_conflict(group: list[VFile | VDir], label: str) -> None:
    for entry in group:
        if isinstance(entry, VFile):
            entry.conflict_trace.append(f"{label}: {entry.name}")
            entry.traces.append(label)
        else:
            entry.traces.append(f"{label}: {entry.name}")


def hashed_name(base_name: str, original_path: Path) -> str:
    """前缀无法区分时的确定性唯一化：在原路径哈希短码前保留基础名。"""
    digest = hashlib.sha1(str(original_path).encode("utf-8", "surrogatepass")).hexdigest()[:8]
    stem, suffix = os.path.splitext(base_name)
    if len(suffix) > MAX_SUFFIX_LEN:
        suffix = suffix[:MAX_SUFFIX_LEN]
    tag = f"-{digest}"
    keep = max(1, MAX_NAME_LEN - len(suffix) - len(tag))
    return truncate_middle(stem, keep) + tag + suffix


def prefixed_name(original_path: Path, base_name: str, depth: int) -> str:
    """取 original_path 由近及远 depth 个祖先，前缀化基础名。

    前缀是「祖先链」的完整表达，第 N 轮必须**替换**前 N-1 轮的前缀，而不是续接：
    cover.jpg → A-cover.jpg → Group1-A-cover.jpg（正确）
    cover.jpg → A-cover.jpg → Group1-A-A-cover.jpg（错误，A 的信息重复）
    """
    parents = [part for part in original_path.parent.parts if part]
    selected = list(reversed(parents))[:depth]
    prefix = "-".join(reversed(selected))
    name = f"{prefix}-{base_name}" if prefix else base_name
    return fit_component(name)


def final_files(node: VDir, base: Path) -> list[tuple[VFile, Path]]:
    rows: list[tuple[VFile, Path]] = []
    for file_item in sorted(node.files, key=lambda f: stable_key(f.original_path, "file")):
        rows.append((file_item, base / file_item.name))
    for child in sorted(node.dirs, key=lambda d: stable_key(d.original_path, "dir")):
        rows.extend(final_files(child, base / child.name))
    return rows

