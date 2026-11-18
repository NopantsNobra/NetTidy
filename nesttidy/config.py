from __future__ import annotations

APP_NAME = "理巢 NestTidy"
# 1.3.0：去重改走 Windows 回收站；诊断与删除带进度条；资源管理器定位。
# 1.0.0：首个可用版本。0.5.x 的剥壳闭环 + 1.0.0 的可读计划视图、两阶段去重、点选式隔离删除合流。
APP_VERSION = "1.3.0"
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
