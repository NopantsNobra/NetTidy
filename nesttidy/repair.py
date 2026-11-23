from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .planner import should_skip_path

# 属性与 ACL 一律不跟随链接：chmod / attrib / icacls 作用在链接目标上，
# 目标可能落在选定目录之外（页面承诺「符号链接，不跟随不处理」）。
_NO_FOLLOW_PREFIXES = ("SKIPPED_SYSTEM", "SKIPPED_SYMLINK", "SKIPPED_REPARSE")


@dataclass
class RepairResult:
    path: str
    action: str
    status: str
    message: str


def repair_permissions(
    root: str,
    clear_readonly: bool = False,
    clear_hidden: bool = False,
    clear_zone_identifier: bool = False,
    grant_current_user: bool = False,
    fix_inheritance: bool = False,
    progress=None,
) -> list[RepairResult]:
    base = Path(root).resolve()
    rows: list[RepairResult] = []
    # icacls 授权只要一次账户名；os.getlogin() 在非交互会话会抛 OSError，
    # 取不到就逐行记失败，不让整轮修复半路崩掉。
    account = current_account() if grant_current_user and os.name == "nt" else ""
    paths = sorted(iter_paths(base, progress), key=lambda p: (str(p).casefold(), str(p)))
    for index, path in enumerate(paths, 1):
        if progress:
            progress(index, len(paths), path.name, "修复")
        skip = should_skip_path(path)
        if skip and skip.startswith(_NO_FOLLOW_PREFIXES):
            rows.append(RepairResult(str(path), "skip", "skipped", skip))
            continue
        if clear_readonly:
            rows.append(clear_readonly_attr(path))
        if clear_hidden and os.name == "nt":
            rows.append(run_attrib(path, "-H", "clear_hidden"))
        if clear_zone_identifier and os.name == "nt" and path.is_file():
            rows.append(remove_ads(path, "Zone.Identifier"))
        if grant_current_user and os.name == "nt":
            if account:
                # action 必须走关键字：写成位置参数会被 *args 吞掉，run_icacls 缺 action 直接 TypeError。
                rows.append(run_icacls(path, "/grant:r", f"{account}:F", action="grant_current_user"))
            else:
                rows.append(RepairResult(str(path), "grant_current_user", "failed", "无法确定当前登录用户名，未执行"))
        if fix_inheritance and os.name == "nt":
            rows.append(run_icacls(path, "/inheritance:e", action="fix_inheritance"))
    return rows


def iter_paths(base: Path, progress=None):
    seen = 0

    def report(path: Path) -> Path:
        nonlocal seen
        seen += 1
        if progress:
            progress(seen, 0, path.name, "扫描")
        return path

    yield report(base)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort(key=str.casefold)
        filenames.sort(key=str.casefold)
        for name in dirnames:
            yield report(Path(dirpath) / name)
        for name in filenames:
            yield report(Path(dirpath) / name)


def current_account() -> str:
    """当前登录账户名；取不到返回空串，由调用方按失败行记录。"""
    try:
        return os.getlogin()
    except OSError:
        return ""


def clear_readonly_attr(path: Path) -> RepairResult:
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IWRITE | stat.S_IREAD)
        return RepairResult(str(path), "clear_readonly", "ok", "已确保当前用户可写位")
    except Exception as exc:
        return RepairResult(str(path), "clear_readonly", "failed", str(exc))


def run_attrib(path: Path, flag: str, action: str) -> RepairResult:
    try:
        subprocess.run(["attrib", flag, str(path)], check=True, capture_output=True, text=True)
        return RepairResult(str(path), action, "ok", "attrib 执行成功")
    except Exception as exc:
        return RepairResult(str(path), action, "failed", str(exc))


def remove_ads(path: Path, stream_name: str) -> RepairResult:
    ads = Path(f"{path}:{stream_name}")
    try:
        if ads.exists():
            ads.unlink()
            return RepairResult(str(path), "clear_zone_identifier", "ok", "已删除 Zone.Identifier")
        return RepairResult(str(path), "clear_zone_identifier", "skipped", "未发现 Zone.Identifier")
    except Exception as exc:
        return RepairResult(str(path), "clear_zone_identifier", "failed", str(exc))


def run_icacls(path: Path, *args: str, action: str) -> RepairResult:
    try:
        subprocess.run(["icacls", str(path), *args], check=True, capture_output=True, text=True)
        return RepairResult(str(path), action, "ok", "icacls 执行成功")
    except Exception as exc:
        return RepairResult(str(path), action, "failed", str(exc))


def rows_as_dicts(rows: list[RepairResult]) -> list[dict]:
    return [asdict(row) for row in rows]

