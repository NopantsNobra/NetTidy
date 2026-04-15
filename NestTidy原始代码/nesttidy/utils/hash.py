"""文件哈希计算工具，用于重复检测与跨卷复制完整性校验。"""
import hashlib
from pathlib import Path

CHUNK_SIZE = 65_536  # 64 KB，兼顾内存与 IO 效率


def file_md5(path: Path) -> str:
    """计算文件 MD5 哈希（用于重复检测，速度优先）。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def file_sha256(path: Path) -> str:
    """计算文件 SHA-256 哈希（用于跨卷复制后的完整性校验，安全性优先）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()