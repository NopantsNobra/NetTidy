"""日志系统：统一的结构化日志配置（提前定义，供其他模块导入）。"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

try:
    import colorama
    colorama.init(autoreset=True)
except ImportError:
    # colorama 是可选依赖，未安装时使用默认终端输出
    pass

_LOG_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
_DATE_FORMAT = "%H:%M:%S"


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[Path] = None,
) -> None:
    """配置全局日志。应在程序入口（cli.main）处调用一次。"""
    handlers: list = [logging.StreamHandler(sys.stderr)]
    
    if log_file:
        # 【增强建议】自动创建日志目录，防止单纯因为目录不存在导致程序无法启动
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # 如果连创建目录都失败（比如权限极差），才降级为报错
            print(f"[警告] 无法创建日志目录 {log_file.parent}，将仅输出到控制台: {exc}", file=sys.stderr)
        else:
            handlers.append(
                logging.FileHandler(log_file, encoding="utf-8")
            )
            
    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        datefmt=_DATE_FORMAT,
        handlers=handlers,
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    """获取命名日志记录器（各模块统一使用此入口）。"""
    return logging.getLogger(name)