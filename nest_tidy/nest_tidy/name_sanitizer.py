"""文件名清洗模块 — 确保文件名符合 Windows 规范"""
import re
import os

# Windows 保留文件名
_RESERVED_NAMES = {
    'con', 'prn', 'aux', 'nul',
    'com1', 'com2', 'com3', 'com4', 'com5', 'com6', 'com7', 'com8', 'com9',
    'lpt1', 'lpt2', 'lpt3', 'lpt4', 'lpt5', 'lpt6', 'lpt7', 'lpt8', 'lpt9',
}

# Windows 非法字符
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*]')

# 路径最大长度（Windows 默认）
_MAX_PATH_LEN = 259


def sanitize_name(name: str) -> str:
    """
    清洗单个文件/目录名，确保符合 Windows 规范。

    规则：
    1. 非法字符替换为 _
    2. 尾部的 . 和空格清除
    3. 保留名称加 _ 后缀
    """
    # 1. 非法字符替换
    cleaned = _ILLEGAL_CHARS.sub('_', name)

    # 2. 尾部分隔符清除
    cleaned = cleaned.rstrip('. ')

    # 3. 空名保护
    if not cleaned:
        cleaned = '_'

    # 4. 保留名称规避（不区分大小写）
    stem = cleaned.split('.')[0].lower()
    if stem in _RESERVED_NAMES:
        cleaned = cleaned + '_'

    return cleaned


def sanitize_path(path: str) -> str:
    """
    清洗完整路径，确保符合 Windows 规范。
    处理每个路径段的文件名，并检查总长度。
    """
    parts = path.replace('\\', '/').split('/')
    cleaned_parts = [sanitize_name(p) for p in parts]
    result = '/'.join(cleaned_parts)

    # 路径长度截断
    if len(result) > _MAX_PATH_LEN:
        # 保留根目录，截断最后的文件名
        if len(cleaned_parts) > 1:
            head = '/'.join(cleaned_parts[:-1])
            remaining = _MAX_PATH_LEN - len(head) - 1
            if remaining > 0:
                tail = cleaned_parts[-1]
                stem, ext = os.path.splitext(tail)
                result = f"{head}/{stem[:remaining - len(ext)]}{ext}"
            else:
                result = head[:_MAX_PATH_LEN]
        else:
            result = result[:_MAX_PATH_LEN]

    return result
