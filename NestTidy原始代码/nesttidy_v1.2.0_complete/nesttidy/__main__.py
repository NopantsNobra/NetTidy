"""允许以 python -m nesttidy 方式调用。"""
import sys
from nesttidy.cli import main

if __name__ == "__main__":
    # ══════════════════════════════════════════════════
    # 【QC修复】必须将 main() 的返回值(退出码)回传给操作系统
    # 否则 python -m 调用时，脚本失败也会返回 0
    # ══════════════════════════════════════════════════
    sys.exit(main())
