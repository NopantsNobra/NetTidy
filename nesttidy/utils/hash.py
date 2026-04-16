"""文件哈希计算工具，用于重复检测与跨卷复制完整性校验。"""
from __future__ import annotations
import hashlib
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════
# 【QC修复2】将块大小提升至 1MB，匹配现代 NVMe SSD 吞吐量
# ══════════════════════════════════════════════════
CHUNK_SIZE = 1_048_576  # 1 MB


def _calc_hash(path: Path, algorithm: str) -> Optional[str]:
    """底层哈希计算核心（内部使用）。
    
    Returns:
        哈希字符串，如果文件无法读取（如被占用、权限不足）则返回 None。
    """
    try:
        h = hashlib.new(algorithm)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
                h.update(chunk)
        return h.hexdigest()
    except (PermissionError, OSError) as exc:
        # ══════════════════════════════════════════════════
        # 【QC修复1】捕获 IO 异常，防止单个文件被占用导致全局崩溃
        # ══════════════════════════════════════════════════
        # 注意：这里只使用 path.name，防止日志泄露绝对路径
        logger.warning(f"无法读取文件以计算哈希，已跳过: {path.name} ({exc})")
        return None


def file_md5(path: Path) -> Optional[str]:
    """计算文件 MD5 哈希（用于重复检测，速度优先）。"""
    return _calc_hash(path, "md5")


def file_sha256(path: Path) -> Optional[str]:
    """计算文件 SHA-256 哈希（用于跨卷复制后的完整性校验，安全性优先）。"""
    return _calc_hash(path, "sha256")
