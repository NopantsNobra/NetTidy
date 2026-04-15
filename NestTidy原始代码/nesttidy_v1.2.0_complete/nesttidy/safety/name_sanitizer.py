"""文件名清洗器：处理非法字符、超长路径等问题。 """
from __future__ import annotations
import re
import uuid
from pathlib import Path
from typing import Optional

from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)

_WINDOWS_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
})
_WINDOWS_MAX_PATH = 260


class NameSanitizer:
    """文件名清洗器。"""

    def __init__(
        self,
        replacement: str = "_",
        max_name_length: int = 200,
        max_path_length: int = _WINDOWS_MAX_PATH,
    ):
        self.replacement = replacement
        self.max_name_length = max_name_length
        self.max_path_length = max_path_length

    def sanitize(self, name: str) -> str:
        """清洗文件名。"""
        if not name:
            return "unnamed"

        # 1. 替换非法字符
        safe_name = _WINDOWS_INVALID_CHARS.sub(self.replacement, name)

        # 2. 处理 Windows 保留名称
        base_name = safe_name.upper().split('.')[0] if '.' in safe_name else safe_name.upper()
        if base_name in _WINDOWS_RESERVED_NAMES:
            safe_name = f"_{safe_name}"

        # 3. 移除首尾空格和点
        safe_name = safe_name.strip(' .')

        # 4. 截断超长文件名
        if len(safe_name) > self.max_name_length:
            safe_name = self._truncate_name(safe_name)

        # 5. 确保不为空
        if not safe_name:
            safe_name = "unnamed"

        return safe_name

    def _truncate_name(self, name: str) -> str:
        """智能截断超长文件名。"""
        if '.' in name:
            parts = name.rsplit('.', 1)
            base = parts[0]
            ext = '.' + parts[1]
        else:
            base = name
            ext = ''

        available = self.max_name_length - len(ext)
        
        # ══════════════════════════════════════════════════
        # 【QC修复2】修复负数切片陷阱：如果空间不足以容纳 "..."，直接暴力截断
        # ══════════════════════════════════════════════════
        if available <= 3:
            return name[:self.max_name_length]
            
        truncated_base = base[:available - 3] + '...' if len(base) > available else base
        return truncated_base + ext

    def sanitize_path(self, path: Path, base: Optional[Path] = None) -> Path:
        """清洗路径。"""
        parts = list(path.parts)
        safe_parts = []
        
        # ══════════════════════════════════════════════════
        # 【QC修复1】绝对禁区：跳过盘符（如 'C:\'）和根目录（如 '/' 或 '\\'）
        # ══════════════════════════════════════════════════
        # path.is_absolute() 为 True 时，parts[0] 通常是盘符或根
        is_abs = path.is_absolute()
        
        for i, part in enumerate(parts):
            if is_abs and i == 0:
                safe_parts.append(part)  # 原样保留根/盘符
            elif part in ('/', '\\'):
                safe_parts.append(part) # 兜底保留分隔符
            else:
                safe_parts.append(self.sanitize(part))

        # 处理超长路径
        if base and len(str(Path(*safe_parts))) > self.max_path_length:
            safe_parts = self._shorten_path(safe_parts, base)
            
        return Path(*safe_parts)

    def _shorten_path(self, parts: list[str], base: Path) -> list[str]:
        """缩短超长路径。"""
        result = parts.copy()
        
        # ══════════════════════════════════════════════════
        # 【QC修复4】引入 while 循环，确保极深层级也能被压缩到限制内
        # ══════════════════════════════════════════════════
        start = 1  # 保留根目录
        end = len(result) - 1  # 保留文件名
        
        while len(str(Path(*result))) > self.max_path_length:
            shortened_any = False
            # 从倒数第二层开始往前缩短（保留深层结构，缩短上层目录）
            for i in range(end - 1, start - 1, -1):
                part = result[i]
                # 如果还能继续缩短
                if len(part) > 4 and not part.endswith("..."):
                    result[i] = part[:4] + "..."
                    shortened_any = True
                    if len(str(Path(*result))) <= self.max_path_length:
                        break
            if not shortened_any:
                break  # 所有目录都已经缩短到极限，退出避免死循环
                
        return result

    def is_valid(self, name: str) -> bool:
        """检查文件名是否有效。"""
        if not name:
            return False
        if _WINDOWS_INVALID_CHARS.search(name):
            return False
        base_name = name.upper().split('.')[0] if '.' in name else name.upper()
        if base_name in _WINDOWS_RESERVED_NAMES:
            return False
        if name != name.strip(' .'):
            return False
        if len(name) > self.max_name_length:
            return False
        return True

    def get_safe_name(
        self,
        name: str,
        existing_names: Optional[set[str]] = None,
    ) -> str:
        """获取一个安全的、不冲突的文件名。"""
        safe_name = self.sanitize(name)
        if existing_names is None:
            return safe_name

        existing_lower = {n.lower() for n in existing_names}
        
        if safe_name.lower() not in existing_lower:
            return safe_name

        base, ext = self._split_extension(safe_name)
        counter = 1
        
        while counter <= 1000:
            new_name = f"{base}_{counter}{ext}"
            if new_name.lower() not in existing_lower:
                return new_name
            counter += 1

        # ══════════════════════════════════════════════════
        # 【QC修复3】使用 UUID 兜底，并强制加入 existing_lower 确保绝对唯一
        # ══════════════════════════════════════════════════
        unique_id = uuid.uuid4().hex[:8]
        final_name = f"{base}_{unique_id}{ext}"
        # 即使是 UUID，理论上也不应该冲突，但安全层不赌概率
        existing_lower.add(final_name.lower()) 
        return final_name

    def _split_extension(self, name: str) -> tuple[str, str]:
        """分离文件名和扩展名。"""
        if '.' in name:
            parts = name.rsplit('.', 1)
            return parts[0], '.' + parts[1]
        return name, ''


# 模块级单例
name_sanitizer = NameSanitizer()

def sanitize_filename(name: str) -> str:
    """清洗文件名的便捷函数。"""
    return name_sanitizer.sanitize(name)
