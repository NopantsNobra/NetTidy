# PyInstaller spec — 理巢 NestTidy 单文件 EXE
# 构建期工具配置；运行时仍是纯标准库，零第三方依赖不变。
# 依据：deliverables/software-company/nesttidy-competitive-analysis-2026-11-16.md 9.2 节 POC。
# 注意：PyInstaller >=4.2 的默认 manifest 已含 longPathAware（官方 changelog 4.2），
# 6.x 无需再传 --manifest；构建后由 build_exe.py 从 EXE 字节中实际校验该片段。

import os

# 项目根目录（spec 位于 packaging/），保证Analysis能从源码树导入 nesttidy 包
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC_PATH)))

a = Analysis(
    [os.path.join(ROOT, "nesttidy", "app.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[],  # app.py 全部静态相对导入，ui.py 内联页面，无资源文件
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="nesttidy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,   # 保持系统临时目录；POC 已登记强杀后 _MEI* 残留风险
    console=False,         # --noconsole；与 POC 一致，双击无控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
