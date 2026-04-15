"""安全层：提供文件操作的安全保障。

模块：
  - strategies: 执行策略（复制、移动、跨卷移动）
  - file_executor: 策略调度器
  - shell_cleaner: 空壳目录清理器
  - pre_checker: 执行前预检器
  - name_sanitizer: 文件名清洗器
"""
from nesttidy.safety.file_executor import FileExecutor, ExecutionSummary
from nesttidy.safety.shell_cleaner import ShellCleaner, clean_empty_dirs
from nesttidy.safety.pre_checker import PreChecker, PreCheckResult, pre_check
from nesttidy.safety.name_sanitizer import NameSanitizer, sanitize_filename

__all__ = [
    "FileExecutor",
    "ExecutionSummary",
    "ShellCleaner",
    "clean_empty_dirs",
    "PreChecker",
    "PreCheckResult",
    "pre_check",
    "NameSanitizer",
    "sanitize_filename",
]
