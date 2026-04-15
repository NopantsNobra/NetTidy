"""文件名清洗器：确保输出的文件名完全符合 Windows 文件系统规范。
《开发手册 v1.0》第四章第 4 节规则严格实现：
1. 非法字符替换
2. Windows 保留名称规避
3. 尾部分隔符清除
4. 智能长度截断（保留扩展名）
5. 空名称兜底
"""
from __future__ import annotations
import os
import re
from typing import Optional

# Windows 文件名中禁止的字符
INVALID_CHARS_PATTERN = re.compile(r'[<>:"/\\|?*]')

# Windows 保留设备名称（不区分大小写）
# 包括 COM1-COM9, LPT1-LPT9
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM0", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT0", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

def sanitize_filename(name: str, max_length: Optional[int] = None) -> str:
    """清洗单个文件/文件夹名称，使其符合 Windows 规范。
    
    注意：此函数仅处理“名称”本身，不处理路径分隔符。
    调用方（如 renamer 或后续的 copier）需负责处理完整路径长度的计算。
    
    Args:
        name: 原始文件名（可能包含扩展名，如 "fi<le.txt"）
        max_length: 允许的最大字符长度。如果指定，会在保留扩展名的前提下智能截断。
                    如果不指定，则不进行截断。
                    
    Returns:
        清洗后的安全文件名。
    """
    if not name or not name.strip():
        return "_unnamed"

    # ══════════════════════════════════════════════════
    # Step 1: 替换非法字符
    # ══════════════════════════════════════════════════
    safe_name = INVALID_CHARS_PATTERN.sub('_', name)

    # ══════════════════════════════════════════════════
    # Step 2: 规避 Windows 保留名称
    # 必须在去掉扩展名后判断基础名
    # ══════════════════════════════════════════════════
    base, ext = os.path.splitext(safe_name)
    
    # 去除基础名首尾的空格和点后再判断（防止 "CON " 绕过）
    clean_base = base.strip('. ')
    
    if clean_base.upper() in RESERVED_NAMES:
        # 在基础名后追加下划线，如 "CON" -> "CON_"
        safe_name = f"{base}_"

    # ══════════════════════════════════════════════════
    # Step 3: 移除尾部的空格和点
    # Windows 文件资源管理器不允许以这些字符结尾
    # ══════════════════════════════════════════════════
    safe_name = safe_name.rstrip('. ')

    # ══════════════════════════════════════════════════
    # Step 4: 智能长度截断
    # 必须保证扩展名（如 .txt）不被截断
    # ══════════════════════════════════════════════════
    if max_length is not None and len(safe_name) > max_length:
        base, ext = os.path.splitext(safe_name)
        # 重新计算允许给基础名的最大长度
        allowed_base_len = max(1, max_length - len(ext))
        safe_name = base[:allowed_base_len] + ext
        # 截断后可能尾部又出现了非法字符（虽然概率极低），再清一次尾部
        safe_name = safe_name.rstrip('. ')

    # ══════════════════════════════════════════════════
    # Step 5: 最终兜底
    # 经过上述所有步骤（例如原始名是 "..." 或全是非法字符 "????"）
    # ══════════════════════════════════════════════════
    if not safe_name or safe_name in ('.', '..'):
        if max_length and max_length < len("_unnamed"):
            return "_" * max_length # 极端截断下的终极兜底
        return "_unnamed"

    return safe_name
