from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, replace
from pathlib import Path

APP_NAME = "理巢 NestTidy"
# 1.5.0：新增最小本地配置（仅上次的源目录与端口，见 load_local_config）。历次版本明细见 ROADMAP。
APP_VERSION = "1.5.0"
# v3：R1 不再是终态（穿透后必须继续 R0-R4）；R5 增加「前缀无法区分时的路径短码兜底」；
# 单文件名分量硬上限 180 落实到 R5 与 clean_name 两处。
RULE_VERSION = "P0-R0-R5-v3"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766

INCOMPLETE_SUFFIXES = {
    ".crdownload",
    ".part",
    ".!qb",
    ".aria2",
    ".tmp",
    ".downloading",
}

REPORT_DIR_NAME = "理巢报告"
OUTPUT_DIR_PREFIX = "理巢输出_"
# 历史隔离区前缀：v1.3.0 起重复副本改走 Windows 回收站，旧隔离目录仍参与扫描跳过，避免被当成资源包。
QUARANTINE_DIR_PREFIX = "理巢隔离_"

CONFIG_FILE_NAME = "理巢配置.json"
# 路线图 P1「最小化本地配置」：只放上次的源目录与端口。规则阈值、语义、排除编辑器一律不开放。
_CONFIG_LOCK = threading.Lock()


@dataclass
class LocalConfig:
    selected_root: str = ""
    port: int = DEFAULT_PORT


def config_path(base_dir: str | Path | None = None) -> Path:
    """与理巢报告、历史库同一口径：跟着启动目录走，不在被整理目录内另设位置。"""
    return (Path(base_dir).resolve() if base_dir else Path.cwd()) / CONFIG_FILE_NAME


def _clean_root(raw: str) -> str:
    return raw.strip().strip('"').strip("'").strip()


def load_local_config(base_dir: str | Path | None = None) -> LocalConfig:
    """读取本地配置；文件不存在即默认值。字段损坏时显式报错，绝不静默回落（口径同 v1.4.0 的空路径修复）。"""
    path = config_path(base_dir)
    if not path.exists():
        return LocalConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise ValueError(f"配置文件无法解析：{path}（{exc}）；删除该文件可恢复默认") from exc
    if not isinstance(data, dict):
        raise ValueError(f"配置文件必须是一个 JSON 对象：{path}")
    root = data.get("selected_root", "")
    port = data.get("port", DEFAULT_PORT)
    if not isinstance(root, str):
        raise ValueError(f"配置文件的 selected_root 必须是字符串：{path}")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError(f"配置文件的 port 必须是 1–65535 的整数：{path}")
    return LocalConfig(selected_root=_clean_root(root), port=port)


def save_local_config(config: LocalConfig, base_dir: str | Path | None = None) -> Path:
    """原子写回（临时文件 + replace），进程被杀也不会留下半个 JSON。"""
    path = config_path(base_dir)
    tmp = path.with_name(path.name + ".tmp")
    payload = {"selected_root": config.selected_root, "port": config.port}
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def remember_selected_root(root: str, base_dir: str | Path | None = None) -> Path:
    """试运行/诊断/修复成功后写回「上次的源目录」；端口保持文件里的值，不被启动参数覆盖。"""
    cleaned = _clean_root(root)
    if not cleaned:
        raise ValueError("不记录空源目录")
    with _CONFIG_LOCK:
        config = replace(load_local_config(base_dir), selected_root=cleaned)
        return save_local_config(config, base_dir)
