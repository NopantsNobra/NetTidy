from __future__ import annotations

APP_NAME = "理巢 NestTidy"
APP_VERSION = "0.5.1"
# v3：R1 不再是终态（穿透后必须继续 R0-R4）；R5 增加「前缀无法区分时的路径短码兜底」；
# 单文件名分量硬上限 180 落实到 R5 与 clean_name 两处。
RULE_VERSION = "P0-R0-R5-v3"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

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
