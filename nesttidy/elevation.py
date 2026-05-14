from __future__ import annotations

import ctypes
import os
import subprocess
import sys


def is_admin() -> bool:
    if os.name != "nt":
        return os.geteuid() == 0  # type: ignore[attr-defined]
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(argv: list[str] | None = None) -> bool:
    if os.name != "nt":
        return False
    args = argv if argv is not None else sys.argv
    params = subprocess.list2cmdline(args[1:])
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    return result > 32

