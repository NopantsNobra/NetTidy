"""工具模块：提供通用工具函数。

模块：
  - hash: 文件哈希计算
  - path: 路径处理工具
"""
from nesttidy.utils.hash import file_md5, file_sha256
from nesttidy.utils.path import names_equal, is_same_volume, safe_relative

__all__ = [
    "file_md5",
    "file_sha256",
    "names_equal",
    "is_same_volume",
    "safe_relative",
]
