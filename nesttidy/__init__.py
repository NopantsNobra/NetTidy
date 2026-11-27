# 版本唯一来源是 config.APP_VERSION；planner 会把它写进每份计划的 app_version，两处必须同源。
from .config import APP_VERSION as __version__

__all__ = ["__version__"]
