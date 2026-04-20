"""权限修复引擎 — 四种操作模式"""
import os
import subprocess
import ctypes
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class FixResult:
    """单项修复结果"""
    mode: str
    status: str = "pending"  # pending / success / partial / failed / skipped
    files_processed: int = 0
    errors: int = 0
    skipped: int = 0
    details: list = field(default_factory=list)
    error_messages: list = field(default_factory=list)


@dataclass
class PermissionFixReport:
    """权限修复总报告"""
    results: dict = field(default_factory=dict)
    target_dir: str = ""

    @property
    def all_success(self) -> bool:
        return all(r.status in ("success", "skipped") for r in self.results.values())


def fix_permissions(
    target_dir: str,
    *,
    fix_perm: bool = False,
    fix_readonly: bool = False,
    fix_hidden: bool = False,
    fix_zone: bool = False,
) -> PermissionFixReport:
    """
    对目标目录执行权限修复。

    Parameters
    ----------
    target_dir : str
        目标目录绝对路径
    fix_perm : bool
        授予完全控制权（icacls）
    fix_readonly : bool
        去除只读属性（attrib -R）
    fix_hidden : bool
        去除隐藏属性（attrib -H）
    fix_zone : bool
        清除 Zone.Identifier ADS

    Returns
    -------
    PermissionFixReport
    """
    report = PermissionFixReport(target_dir=target_dir)

    if fix_perm:
        report.results["perm"] = _fix_perm(target_dir)
    if fix_readonly:
        report.results["readonly"] = _fix_readonly(target_dir)
    if fix_hidden:
        report.results["hidden"] = _fix_hidden(target_dir)
    if fix_zone:
        report.results["zone"] = _fix_zone(target_dir)

    return report


def _fix_perm(target_dir: str) -> FixResult:
    """icacls 递归授予 Users 组完全控制权限"""
    result = FixResult(mode="perm")
    try:
        proc = subprocess.run(
            ["icacls", target_dir, "/grant", "Users:F", "/T", "/C"],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode == 0:
            result.status = "success"
        else:
            result.status = "partial"
            result.error_messages.append(proc.stderr.strip())
        # 统计处理的文件数
        result.files_processed = _count_files(target_dir)
    except FileNotFoundError:
        result.status = "skipped"
        result.details.append("icacls 命令不可用（非 Windows 系统）")
    except subprocess.TimeoutExpired:
        result.status = "failed"
        result.error_messages.append("icacls 执行超时（300s）")
    except Exception as e:
        result.status = "failed"
        result.error_messages.append(str(e))
    return result


def _fix_readonly(target_dir: str) -> FixResult:
    """attrib -R 递归去除只读属性"""
    result = FixResult(mode="readonly")
    try:
        proc = subprocess.run(
            ["attrib", "-R", os.path.join(target_dir, "*"), "/S", "/D"],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode == 0:
            result.status = "success"
        else:
            result.status = "partial"
            result.error_messages.append(proc.stderr.strip())
        result.files_processed = _count_files(target_dir)
    except FileNotFoundError:
        result.status = "skipped"
        result.details.append("attrib 命令不可用（非 Windows 系统）")
    except subprocess.TimeoutExpired:
        result.status = "failed"
        result.error_messages.append("attrib 执行超时（300s）")
    except Exception as e:
        result.status = "failed"
        result.error_messages.append(str(e))
    return result


def _fix_hidden(target_dir: str) -> FixResult:
    """attrib -H 递归去除隐藏属性"""
    result = FixResult(mode="hidden")
    try:
        proc = subprocess.run(
            ["attrib", "-H", os.path.join(target_dir, "*"), "/S", "/D"],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode == 0:
            result.status = "success"
        else:
            result.status = "partial"
            result.error_messages.append(proc.stderr.strip())
        result.files_processed = _count_files(target_dir)
    except FileNotFoundError:
        result.status = "skipped"
        result.details.append("attrib 命令不可用（非 Windows 系统）")
    except subprocess.TimeoutExpired:
        result.status = "failed"
        result.error_messages.append("attrib 执行超时（300s）")
    except Exception as e:
        result.status = "failed"
        result.error_messages.append(str(e))
    return result


def _fix_zone(target_dir: str) -> FixResult:
    """清除 Zone.Identifier ADS（NTFS 特性）"""
    result = FixResult(mode="zone")

    # 检查文件系统是否支持 ADS
    try:
        import ctypes
        # GetVolumeInformationW 检查文件系统类型
        kernel32 = ctypes.windll.kernel32
        volume = os.path.splitdrive(os.path.abspath(target_dir))[0] + "\\"
        fs_type = ctypes.create_unicode_buffer(256)
        kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(volume), None, 0, None, None, None,
            fs_type, ctypes.sizeof(fs_type),
        )
        if fs_type.value.upper() not in ("NTFS", ""):
            result.status = "skipped"
            result.details.append(f"当前文件系统 {fs_type.value} 不支持 ADS")
            return result
    except (AttributeError, OSError):
        # 非 Windows 系统或无法检测，尝试执行
        pass

    streams_removed = 0
    errors = 0

    for root, dirs, files in os.walk(target_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            zone_path = fpath + ":Zone.Identifier"
            try:
                if os.path.exists(zone_path):
                    os.remove(zone_path)
                    streams_removed += 1
            except OSError as e:
                errors += 1
                result.error_messages.append(f"{fpath}: {str(e)}")

    result.files_processed = streams_removed + errors
    if errors == 0:
        result.status = "success"
        result.details.append(f"移除了 {streams_removed} 个 Zone.Identifier")
    else:
        result.status = "partial"

    return result


def _count_files(directory: str) -> int:
    """统计目录下文件总数"""
    count = 0
    for _, _, files in os.walk(directory):
        count += len(files)
    return count
