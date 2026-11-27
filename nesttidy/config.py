from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, replace
from pathlib import Path

APP_NAME = "理巢 NestTidy"
# 1.9.0：分发形态阶段起点——管理员裁决解除 EXE 暂缓、启动封装准备；文档一致性治理：
# ROADMAP 新增「当前态速查表」为唯一当前态权威，修正风险台账提权条目与 guard 表述。
# 规则语义、计划/报告格式与 RULE_VERSION 均未变（按 AGENTS.md 6.1 严格口径本更接近 patch，
# 管理员以 1.9.0 作为 EXE 阶段起点，裁决优先）。EXE 构建期工具仅用于打包，运行时零依赖不变。
# 1.8.21：修复主启动入口 start_nesttidy.bat 的编码缺陷——原文件以 LF 行尾 + UTF-8 中文保存，
# cmd.exe 下 %VAR% 被截断、中文提示被当命令执行，双击无法打开浏览器/服务（管理员实机用 CLI
# 直接跑故未暴露）。转 CRLF + GBK，实测双击 HTTP 200、Chrome 真实发起 /api/health 请求；
# 新增 test_web_plans.py:PortBindingTests 字节格式断言（CRLF + GBK 可解码），旧文件上先红。
# 规则语义与 RULE_VERSION 未变。
# 1.8.1：修复权限修复工具链噪音——run_attrib / run_icacls 的 subprocess.run(text=True) 补
# encoding="utf-8", errors="replace"。中文 Windows 上 attrib/icacls 输出 GBK，UTF-8 模式解码
# 抛 UnicodeDecodeError（读取线程，不传播主流程、产品行为不变，但 stderr 刷堆栈）。规则语义与
# RULE_VERSION 未变；新增 tests/test_repair_seams.py 3 例（接线 + 中文路径行为级 + 变异已验证）。
# 1.8.0：新增 R6「移动完成后变空的目录一并删除」（move 收尾清扫资源根以外的空目录，
# 逐条写 rmdir 事务行）；撤销侧 rmdir 行由「不重放」改为「重建目录」，删掉的空目录可原样还原。
# 1.7.1：审计修复 10 项——repair 的 icacls 传参 / 账户名取不到 / 不跟随链接，/api/repair 补齐两项转发，
# copy 边界复查前移到 mkdir 之前，Web 请求来源防线（Host + Origin + 只收 JSON），进度槽加锁与淘汰口径统一，
# 非 Windows 回收站逐行失败，复制方式未填输出目录前置提示，--host 非本机一行报错。规则语义与 RULE_VERSION 未变。
# 1.7.0：P0-2 scandir 元数据复用 / P0-3 单次规划内 resolve 记忆化（规则语义与计划输出不变）；
# P1-2 服务端 per-plan 执行锁 + progress_key 幂等（同 plan 并发执行 409）。历次版本明细见 ROADMAP。
# 1.8.2：A1 执行及撤销边界保护；Windows CI 243 例，规则语义不变。
# 1.8.5：A4 真实重复子组、deep 哈希复用及硬链接估算。
# 1.8.4：A3 事务意图持久化及撤销身份校验。
# 1.8.3：A2 防重放、运行计划保留及重叠目录写任务互斥。
# 1.8.6：C1 惰性分页索引，规则及计划/报告格式不变。
# 1.8.7：C2 进度发布节流，不改变规则或终态契约。
# 1.8.8：C3 计划缓存条目预算第一切片。
# 1.8.9：C3 紧凑记录与路径字符串复用，外部格式不变。
# 1.8.10：工作台视觉与可访问性改造，规则不变。
# 1.8.11：紧凑预览、提前选择执行方式、重复明细索引。
# 1.8.12：复用去重哈希线程池及有界提交。
# 1.8.13：无写入 keep 的同路径预检去重。
# 1.8.14：有界预览 DOM、轻量摘要和服务端完整筛选。
# 1.8.15：D1 普通 Windows 枚举条目元数据复用。
# 1.8.16：依据连续 20 万文件基准校准默认缓存预算。
# 1.8.17：D3 残留目录祖先闭包短路。
# 1.8.18：展示统计目录级缓存及减少临时列表。
# 1.8.19：重复文件明细有界分页与跨页勾选。
# 1.8.20：辅助结果表格统一有界分页。
APP_VERSION = "1.13.1"
# v4：新增 R6——执行收尾清扫「移动完成后变空」的目录（不含资源根）；
# undo 对 rmdir 行改为重建目录（可还原）。R0–R5 语义未变。
# v3：R1 不再是终态（穿透后必须继续 R0-R4）；R5 增加「前缀无法区分时的路径短码兜底」；
# 单文件名分量硬上限 180 落实到 R5 与 clean_name 两处。
RULE_VERSION = "P0-R0-R5-R6-v5"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766

INCOMPLETE_SUFFIXES = (
    ".crdownload",
    ".part",
    ".!qb",
    ".aria2",
    ".tmp",
    ".downloading",
    ".xltd",
    ".td",
    ".td.cfg",
    ".bc!",
    ".!ut",
)

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
