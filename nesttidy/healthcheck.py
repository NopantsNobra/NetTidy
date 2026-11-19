from __future__ import annotations

import os
import socket
import sqlite3
import sys
import tempfile
from dataclasses import dataclass


@dataclass
class HealthItem:
    name: str
    ok: bool
    detail: str


def run_healthcheck(host: str = "127.0.0.1") -> list[HealthItem]:
    items: list[HealthItem] = []
    items.append(HealthItem("Python 版本", sys.version_info >= (3, 11), sys.version.split()[0]))
    try:
        sqlite3.connect(":memory:").close()
        items.append(HealthItem("SQLite", True, sqlite3.sqlite_version))
    except Exception as exc:  # pragma: no cover
        items.append(HealthItem("SQLite", False, str(exc)))
    try:
        with tempfile.TemporaryDirectory(prefix="nesttidy_") as tmp:
            probe = os.path.join(tmp, "probe.txt")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
        items.append(HealthItem("本地数据目录", True, "可创建临时文件"))
    except Exception as exc:
        items.append(HealthItem("本地数据目录", False, str(exc)))
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((host, 0))
        sock.close()
        items.append(HealthItem("本地 Web 绑定", True, host))
    except Exception as exc:
        items.append(HealthItem("本地 Web 绑定", False, str(exc)))
    if os.name == "nt":
        items.append(HealthItem("长路径支持", True, "使用 pathlib/os 接口；系统策略仍可能限制"))
    else:
        items.append(HealthItem("长路径支持", True, "非 Windows 环境"))
    return items

