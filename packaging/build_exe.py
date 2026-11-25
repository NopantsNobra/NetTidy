"""构建并校验理巢 NestTidy 的单文件 EXE。

用途：构建期工具链（PyInstaller）的封装，运行时零第三方依赖政策不变。
依据：ROADMAP E3 / 竞品分析 9.2 节 POC（可行性放行、五项前置未完成）。

用法（在隔离 venv 中安装 PyInstaller>=6 后）：
    python packaging/build_exe.py

本脚本只做本机可完成的四项：
  1. 前置检查：PyInstaller 可用、源码树干净、版本与 spec 一致
  2. 调用 PyInstaller 按 packaging/nesttidy.spec 构建
  3. 产物校验：文件存在、体积、SHA256、manifest longPathAware 片段实际存在
  4. 启动冒烟：拉起 EXE --host 127.0.0.1 --port <临时端口>，验证 /api/health 200

**不覆盖**（需外部条件，未验证）：无系统 Python 的干净机、DNS/系统级出站追踪、
杀软误报、冷温缓存启动阈值。这些在输出中明确列为「未验证」，不得声称通过。
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "nesttidy.spec"
DIST = ROOT / "dist"
EXE = DIST / "nesttidy.exe"

LONG_PATH_MARKER = b"longPathAware"


def check_prerequisites() -> str:
    """返回 PyInstaller 版本字符串；不满足条件时 SystemExit。"""
    try:
        import PyInstaller

        version = PyInstaller.__version__
    except ImportError:
        raise SystemExit(
            "PyInstaller 未安装。构建期请在隔离 venv 中执行：\n"
            "  python -m venv .venv-build && .venv-build/Scripts/python -m pip install 'PyInstaller>=6'"
        )
    major = int(version.split(".", 1)[0])
    if major < 6:
        raise SystemExit(f"PyInstaller 6+  required（当前 {version}）；版本过低不支持 Python 3.13")
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    # .workbuddy/ 与 _review/ 是约定不入库目录，忽略
    unexpected = [
        line for line in status.splitlines()
        if not line.startswith("?? .workbuddy/") and not line.startswith("?? _review/")
    ]
    if unexpected:
        raise SystemExit("构建前工作树有未提交改动，拒绝构建：\n" + "\n".join(unexpected[:10]))
    return version


def build(version: str) -> None:
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--distpath", str(DIST), "--workpath", str(ROOT / "build"),
           "--specpath", str(ROOT / "packaging"), str(SPEC)]
    print("[build] " + " ".join(cmd), flush=True)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"[build] PyInstaller 失败，退出码 {result.returncode}")


def verify_artifact() -> dict:
    if not EXE.exists():
        raise SystemExit(f"[verify] 产物不存在：{EXE}")
    data = EXE.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    has_manifest = LONG_PATH_MARKER in data
    info = {
        "path": str(EXE.relative_to(ROOT)),
        "size_bytes": len(data),
        "size_mib": round(len(data) / 1024 / 1024, 3),
        "sha256": digest,
        "longPathAware_marker_present": has_manifest,
    }
    print("[verify] " + json.dumps(info, ensure_ascii=False, indent=2), flush=True)
    if not has_manifest:
        raise SystemExit("[verify] EXE 中未找到 longPathAware 片段，长路径支持未内嵌，拒绝交付")
    return info


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def smoke_test() -> dict:
    port = free_port()
    proc = subprocess.Popen(
        [str(EXE), "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/api/health"
        for _ in range(60):
            try:
                with urllib.request.urlopen(url, timeout=1) as resp:
                    if resp.status == 200:
                        payload = resp.read().decode("utf-8", "replace")[:200]
                        return {"health_http": 200, "body_excerpt": payload, "port": port}
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                pass
            if proc.poll() is not None:
                raise SystemExit(f"[smoke] EXE 提前退出，退出码 {proc.returncode}")
            time.sleep(0.5)
        raise SystemExit("[smoke] 30 秒内 /api/health 未返回 200")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> None:
    if os.name != "nt":
        raise SystemExit("EXE 构建仅支持 Windows 本机")
    version = check_prerequisites()
    print(f"[precheck] PyInstaller {version}, Python {sys.version.split()[0]}", flush=True)
    build(version)
    info = verify_artifact()
    smoke = smoke_test()
    print("[smoke] " + json.dumps(smoke, ensure_ascii=False), flush=True)
    print(
        "\n=== 未验证项（本脚本不覆盖，不得声称通过）===\n"
        "- 无系统 Python 的干净机复测\n"
        "- DNS / 系统级出站追踪与更长采样窗口\n"
        "- 杀软误报\n"
        "- 冷温缓存启动耗时阈值\n"
        "- CLI 全部子命令在 --noconsole 下的可见性\n",
        flush=True,
    )
    out = DIST / "build_report.json"
    out.write_text(
        json.dumps({"artifact": info, "smoke": smoke, "pyinstaller": version},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[done] 报告：{out.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
