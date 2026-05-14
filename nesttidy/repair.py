from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .planner import should_skip_path


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
) -> list[RepairResult]:
    base = Path(root).resolve()
    rows: list[RepairResult] = []
    for path in sorted(iter_paths(base), key=lambda p: (str(p).casefold(), str(p))):
        skip = should_skip_path(path)
        if skip and skip.startswith("SKIPPED_SYSTEM"):
            rows.append(RepairResult(str(path), "skip", "skipped", skip))
            continue
        if clear_readonly:
            rows.append(clear_readonly_attr(path))
        if clear_hidden and os.name == "nt":
            rows.append(run_attrib(path, "-H", "clear_hidden"))
        if clear_zone_identifier and os.name == "nt" and path.is_file():
            rows.append(remove_ads(path, "Zone.Identifier"))
        if grant_current_user and os.name == "nt":
            rows.append(run_icacls(path, "/grant:r", f"{os.getlogin()}:F", "grant_current_user"))
        if fix_inheritance and os.name == "nt":
            rows.append(run_icacls(path, "/inheritance:e", action="fix_inheritance"))
    return rows


def iter_paths(base: Path):
    yield base
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort(key=str.casefold)
        filenames.sort(key=str.casefold)
        for name in dirnames:
            yield Path(dirpath) / name
        for name in filenames:
            yield Path(dirpath) / name


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

