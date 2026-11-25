"""PyInstaller 入口：显式导入 nesttidy.app.main 并调用。

不用 runpy.run_module——冻结环境下动态模块名不被 PyInstaller 静态分析收集，
会导致 ModuleNotFoundError: No module named 'nesttidy'。显式 import 让
Analysis 能看到整个 nesttidy 包，同时 app.py 内部的相对导入照常工作。
等价于 `python -m nesttidy.app`。
"""
from __future__ import annotations

from nesttidy.app import main

main()
