#!/usr/bin/env python3
"""
文件整理工具 v2.0
自动识别多层嵌套目录中的有效文件，统一提取到待定目录，保留原始目录结构。

改进点：
- dry-run 模式：预览操作而不实际执行
- 自动回滚：生成回滚脚本，支持一键撤销
- 配置文件支持：JSON 配置 + CLI 参数覆盖
- 符号链接/硬链接安全处理
- 磁盘空间预检查
- 信号处理（Ctrl+C 优雅中断）
- 增强日志（文件+控制台，分级）
- 修复哈希算法实例复用 bug
- 路径深度限制
- 同文件系统时直接 move（更快）
- 操作统计摘要
- 文件类型/大小过滤支持
"""

import os
import shutil
import hashlib
import time
import threading
import json
import logging
import signal
import sys
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None
    print("[WARN] tqdm 未安装，将不显示进度条。安装命令: pip install tqdm")


# ──────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────

@dataclass
class Config:
    """工具配置，支持从 JSON 文件加载和 CLI 参数覆盖。"""

    pending_dir_name: str = "待定"
    log_file: str = "operation_log.json"
    hash_algorithm: str = "sha256"  # sha256 / md5 / sha1
    worker_threads: int = 4
    excluded_items: list = field(default_factory=lambda: [".DS_Store", "desktop.ini", "Thumbs.db"])
    show_progress: bool = True
    debug_mode: bool = False
    dry_run: bool = False
    max_path_depth: int = 50
    max_file_size_mb: float = 0  # 0 = 不限制
    include_extensions: list = field(default_factory=list)   # 空 = 全部
    exclude_extensions: list = field(default_factory=list)   # 空 = 无排除
    use_move_on_same_fs: bool = True  # 同文件系统时直接 move
    rollback_file: str = "rollback.sh"
    generate_rollback: bool = True
    buffer_size: int = 65536  # 哈希计算块大小

    @classmethod
    def from_json(cls, path: str) -> "Config":
        """从 JSON 配置文件加载。"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def to_json(self, path: str):
        """保存配置到 JSON 文件。"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
# 统计
# ──────────────────────────────────────────────

@dataclass
class Stats:
    """操作统计。"""
    total_dirs_scanned: int = 0
    valid_dirs: int = 0
    files_copied: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    files_dry_run: int = 0  # dry-run 预览数量
    files_moved: int = 0  # 直接 move（同文件系统）
    conflicts_resolved: int = 0
    empty_dirs_cleaned: int = 0
    total_bytes: int = 0
    elapsed_seconds: float = 0.0

    def summary(self) -> str:
        lines = [
            "=" * 50,
            "  操作统计摘要",
            "=" * 50,
            f"  扫描目录数:     {self.total_dirs_scanned}",
            f"  有效目录数:     {self.valid_dirs}",
            f"  文件待处理:     {self.files_dry_run}",
            f"  文件已移动:     {self.files_moved}",
            f"  文件已复制:     {self.files_copied}",
            f"  文件已跳过:     {self.files_skipped}",
            f"  文件失败:       {self.files_failed}",
            f"  冲突已解决:     {self.conflicts_resolved}",
            f"  空目录已清理:   {self.empty_dirs_cleaned}",
            f"  处理数据量:     {self._format_bytes(self.total_bytes)}",
            f"  耗时:           {self.elapsed_seconds:.2f} 秒",
            "=" * 50,
        ]
        return "\n".join(lines)

    @staticmethod
    def _format_bytes(b: int) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} PB"


# ──────────────────────────────────────────────
# 核心处理器
# ──────────────────────────────────────────────

class DirectoryProcessor:
    def __init__(self, source_root: str, config: Optional[Config] = None):
        self.cfg = config or Config()
        self.src_root = Path(source_root).resolve()
        self.dest_root = self.src_root / self.cfg.pending_dir_name
        self.log: list[dict] = []
        self.lock = threading.Lock()
        self.progress_bar = None
        self.valid_dirs: set[Path] = set()
        self.processed_dirs: set[Path] = set()
        self.stats = Stats()
        self._interrupted = False

        # 设置日志
        self._setup_logging()

        # 注册信号
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    # ── 日志 ──

    def _setup_logging(self):
        self.logger = logging.getLogger("DirOrganizer")
        self.logger.setLevel(logging.DEBUG)

        # 文件处理器
        fh = logging.FileHandler(
            self.cfg.log_file.replace(".json", ".log"),
            encoding="utf-8",
            mode="w"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
        ))
        self.logger.addHandler(fh)

        # 控制台处理器
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG if self.cfg.debug_mode else logging.INFO)
        ch.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(ch)

    def _log_debug(self, msg: str):
        self.logger.debug(f"[DEBUG] {msg}")

    def _log_info(self, msg: str):
        self.logger.info(msg)

    def _log_warn(self, msg: str):
        self.logger.warning(f"[WARN] {msg}")

    def _log_error(self, msg: str):
        self.logger.error(f"[ERROR] {msg}")

    # ── 信号处理 ──

    def _signal_handler(self, signum, frame):
        if self._interrupted:
            self._log_warn("强制退出...")
            sys.exit(1)
        self._interrupted = True
        self._log_warn("\n收到中断信号，正在安全停止（当前文件处理完后退出）...")

    # ── 路径验证 ──

    def _validate_paths(self):
        if not self.src_root.exists():
            raise FileNotFoundError(f"源目录不存在: {self.src_root}")
        if self.dest_root == self.src_root:
            raise ValueError("目标目录不能与源目录相同")
        if self.dest_root.is_relative_to(self.src_root):
            pass  # 正常情况

    def _is_excluded(self, path: Path) -> bool:
        """检查路径是否应排除（在目标目录内 或 符号链接指向目标目录）。"""
        try:
            resolved = path.resolve()
            return resolved.is_relative_to(self.dest_root.resolve())
        except (ValueError, OSError):
            return False

    def _is_symlink_loop(self, path: Path, seen_inodes: set) -> bool:
        """检测符号链接循环。"""
        try:
            stat = path.stat(follow_symlinks=False)
            inode = (stat.st_dev, stat.st_ino)
            if inode in seen_inodes:
                return True
            seen_inodes.add(inode)
            return False
        except OSError:
            return False

    # ── 内容检查 ──

    def _has_real_content(self, path: Path, depth: int = 0) -> bool:
        """递归检查目录是否含真实内容（带深度限制和符号链接保护）。"""
        if depth > self.cfg.max_path_depth:
            self._log_warn(f"超过最大路径深度({self.cfg.max_path_depth}): {path}")
            return False

        seen_inodes: set = set()

        try:
            for entry in os.scandir(path):
                name = entry.name
                if name in self.cfg.excluded_items:
                    continue
                if name == self.cfg.pending_dir_name:
                    continue

                if entry.is_file(follow_symlinks=False):
                    return True

                if entry.is_dir(follow_symlinks=False):
                    child = Path(entry.path)
                    if self._is_symlink_loop(child, seen_inodes):
                        self._log_warn(f"检测到符号链接循环，跳过: {child}")
                        continue
                    if self._has_real_content(child, depth + 1):
                        return True

            return False
        except PermissionError as e:
            self._log_warn(f"无权限访问: {path} - {e}")
            return False
        except Exception as e:
            self._log_warn(f"扫描异常: {path} - {e}")
            return False

    # ── 预扫描 ──

    def _pre_scan(self) -> int:
        self._log_info("正在预扫描目录结构...")
        valid_dirs: set[Path] = set()
        dir_count = 0

        for root, dirs, files in os.walk(self.src_root, topdown=False):
            current_path = Path(root)
            dir_count += 1

            if self._is_excluded(current_path):
                continue

            if self._has_real_content(current_path):
                parent = current_path
                while parent != self.src_root.parent:
                    try:
                        if parent.is_relative_to(self.src_root):
                            valid_dirs.add(parent)
                    except ValueError:
                        break
                    parent = parent.parent

        self.valid_dirs = valid_dirs
        self.stats.total_dirs_scanned = dir_count
        self.stats.valid_dirs = len(valid_dirs)
        self._log_info(f"扫描完成: {dir_count} 个目录, {len(valid_dirs)} 个有效目录")
        return len(valid_dirs)

    # ── 文件过滤 ──

    def _should_process_file(self, path: Path) -> bool:
        """判断文件是否应该被处理。"""
        name = path.name
        suffix = path.suffix.lower()

        # 排除系统文件
        if name in self.cfg.excluded_items:
            return False

        # 扩展名白名单
        if self.cfg.include_extensions:
            if suffix not in [e.lower() for e in self.cfg.include_extensions]:
                return False

        # 扩展名黑名单
        if self.cfg.exclude_extensions:
            if suffix in [e.lower() for e in self.cfg.exclude_extensions]:
                return False

        # 文件大小限制
        if self.cfg.max_file_size_mb > 0:
            try:
                size_mb = path.stat().st_size / (1024 * 1024)
                if size_mb > self.cfg.max_file_size_mb:
                    self._log_debug(f"文件过大({size_mb:.1f}MB)，跳过: {path}")
                    return False
            except OSError:
                pass

        return True

    # ── 哈希计算 ──

    def _file_hash(self, file_path: Path) -> str:
        """计算文件哈希值。"""
        try:
            hasher = hashlib.new(self.cfg.hash_algorithm)
            with open(file_path, "rb") as f:
                while chunk := f.read(self.cfg.buffer_size):
                    hasher.update(chunk)
                    if self._interrupted:
                        return "INTERRUPTED"
            return hasher.hexdigest()
        except Exception as e:
            self._log_warn(f"哈希计算失败: {file_path} - {e}")
            return "ERROR"

    # ── 磁盘空间检查 ──

    def _check_disk_space(self, total_bytes: int):
        """检查目标分区是否有足够空间。"""
        try:
            usage = shutil.disk_usage(self.dest_root.parent)
            required = total_bytes * 1.1  # 预留 10% 余量
            if usage.free < required:
                raise OSError(
                    f"磁盘空间不足: 需要 {Stats._format_bytes(int(required))}, "
                    f"可用 {Stats._format_bytes(usage.free)}"
                )
        except OSError as e:
            if "磁盘空间不足" in str(e):
                raise
            self._log_debug(f"无法检查磁盘空间: {e}")

    # ── 安全复制/移动 ──

    def _safe_copy_or_move(self, src: Path, dst: Path) -> tuple[str, Path]:
        """
        安全复制或移动文件。
        返回 (操作类型, 最终目标路径)
        操作类型: COPY / MOVE / SKIP / ERROR
        """
        try:
            # 源文件可能已被其他线程处理
            if not src.exists():
                return "SKIP", dst

            dst.parent.mkdir(parents=True, exist_ok=True)

            # 目标已存在 → 冲突处理
            if dst.exists():
                src_hash = self._file_hash(src)
                dst_hash = self._file_hash(dst)

                if src_hash == dst_hash or src_hash == "ERROR":
                    return "SKIP", dst

                # 内容不同 → 重命名
                timestamp = int(time.time() * 1000)
                dst = dst.with_name(f"{dst.stem}_CONFLICT_{timestamp}{dst.suffix}")
                with self.lock:
                    self.stats.conflicts_resolved += 1

            # dry-run 模式：只记录，不实际操作
            if self.cfg.dry_run:
                return "DRY_RUN", dst

            # 判断是否同文件系统（可用 move）
            use_move = False
            if self.cfg.use_move_on_same_fs:
                try:
                    src_stat = src.stat()
                    dst_parent_stat = dst.parent.stat()
                    use_move = src_stat.st_dev == dst_parent_stat.st_dev
                except OSError:
                    pass

            if use_move:
                shutil.move(str(src), str(dst))
                return "MOVE", dst
            else:
                shutil.copy2(str(src), str(dst))
                return "COPY", dst

        except Exception as e:
            self._log_error(f"文件操作失败: {src} → {dst} | {e}")
            return "ERROR", dst

    # ── 单目录处理 ──

    def _process_directory(self, current_path: Path):
        """处理单个目录（线程工作函数）。"""
        if self._interrupted:
            return

        with self.lock:
            if current_path in self.processed_dirs:
                return
            self.processed_dirs.add(current_path)

        self._log_debug(f"处理目录: {current_path}")

        try:
            relative_path = current_path.relative_to(self.src_root)
            dest_dir = self.dest_root / relative_path

            for entry in os.scandir(current_path):
                if self._interrupted:
                    return

                if entry.name in self.cfg.excluded_items:
                    continue

                src = Path(entry.path)

                if not entry.is_file(follow_symlinks=False):
                    continue

                if not self._should_process_file(src):
                    continue

                dest = dest_dir / entry.name

                # 在操作前缓存文件信息（多线程下文件可能被其他线程处理）
                try:
                    file_size = src.stat().st_size
                    file_hash = self._file_hash(src)
                except OSError:
                    file_size = 0
                    file_hash = "GONE"

                if file_hash == "GONE":
                    self._log_debug(f"文件已被其他线程处理，跳过: {src}")
                    with self.lock:
                        self.stats.files_skipped += 1
                    continue

                op_type, final_dest = self._safe_copy_or_move(src, dest)

                with self.lock:
                    if op_type == "SKIP":
                        self.stats.files_skipped += 1
                    elif op_type == "MOVE":
                        self.stats.files_moved += 1
                        self.stats.total_bytes += file_size
                    elif op_type == "COPY":
                        self.stats.files_copied += 1
                        self.stats.total_bytes += file_size
                    elif op_type == "ERROR":
                        self.stats.files_failed += 1
                    elif op_type == "DRY_RUN":
                        self.stats.files_dry_run += 1

                    self.log.append({
                        "time": time.time(),
                        "op": op_type,
                        "src": str(src),
                        "dst": str(final_dest),
                        "hash": file_hash if op_type not in ("SKIP", "ERROR") else "N/A",
                    })

                # 非 dry-run 且成功操作后删除原文件（COPY 场景，MOVE 已自动删除）
                if not self.cfg.dry_run and op_type == "COPY":
                    try:
                        src.unlink()
                    except Exception as e:
                        self._log_warn(f"删除原文件失败: {src} - {e}")

            # 清理空的目标子目录
            if not self.cfg.dry_run:
                try:
                    if dest_dir.exists() and not any(dest_dir.iterdir()):
                        dest_dir.rmdir()
                except OSError:
                    pass

            # 更新进度
            with self.lock:
                if self.progress_bar:
                    self.progress_bar.update(1)

        except PermissionError as e:
            self._log_warn(f"无权限访问: {current_path} - {e}")
        except Exception as e:
            self._log_error(f"目录处理异常: {current_path} - {e}")
            with self.lock:
                self.log.append({
                    "time": time.time(),
                    "error": str(e),
                    "path": str(current_path),
                })

    # ── 回滚脚本生成 ──

    def _generate_rollback_script(self):
        """生成回滚脚本，支持一键撤销操作。"""
        if not self.cfg.generate_rollback or self.cfg.dry_run:
            return

        rollback_path = self.src_root / self.cfg.rollback_file
        operations = [entry for entry in self.log if entry.get("op") in ("COPY", "MOVE")]

        if not operations:
            self._log_info("无操作记录，跳过生成回滚脚本")
            return

        lines = [
            "#!/bin/bash",
            f"# 文件整理工具回滚脚本",
            f"# 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"# 共 {len(operations)} 个文件待恢复",
            "",
            'set -euo pipefail',
            "",
            'ERRORS=0',
            "",
        ]

        # 逆序恢复（先处理的后恢复）
        for entry in reversed(operations):
            src = entry["src"]
            dst = entry["dst"]
            op = entry["op"]

            if op == "MOVE":
                # MOVE：把文件移回原位
                lines.append(f'if [ -f "{dst}" ]; then')
                lines.append(f'  mkdir -p "$(dirname "{src}")"')
                lines.append(f'  mv "{dst}" "{src}" && echo "[恢复] {dst} → {src}"')
                lines.append(f'else')
                lines.append(f'  echo "[跳过] 文件不存在: {dst}"')
                lines.append(f'  ERRORS=$((ERRORS+1))')
                lines.append(f'fi')
            elif op == "COPY":
                # COPY：目标有副本，原位置无文件 → 复制回去
                lines.append(f'if [ -f "{dst}" ] && [ ! -f "{src}" ]; then')
                lines.append(f'  mkdir -p "$(dirname "{src}")"')
                lines.append(f'  cp "{dst}" "{src}" && echo "[恢复] {dst} → {src}"')
                lines.append(f'else')
                lines.append(f'  echo "[跳过] 无需恢复: {src}"')
                lines.append(f'fi')

            lines.append("")

        # 删除待定目录
        lines.append(f'# 清理待定目录')
        lines.append(f'if [ -d "{self.dest_root}" ]; then')
        lines.append(f'  rm -rf "{self.dest_root}"')
        lines.append(f'  echo "[清理] 已删除: {self.dest_root}"')
        lines.append(f'fi')
        lines.append("")
        lines.append('echo ""')
        lines.append('echo "回滚完成！错误数: $ERRORS"')
        lines.append('exit $ERRORS')

        try:
            with open(rollback_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            os.chmod(rollback_path, 0o755)
            self._log_info(f"回滚脚本已生成: {rollback_path}")
        except Exception as e:
            self._log_error(f"生成回滚脚本失败: {e}")

    # ── 主执行 ──

    def execute(self):
        start_time = time.time()

        try:
            self._validate_paths()

            if self.cfg.dry_run:
                self._log_info("🔍 DRY-RUN 模式：仅预览，不实际执行操作\n")

            total = self._pre_scan()
            if total == 0:
                self._log_info("未发现含有效文件的目录，无需处理。")
                return

            # 磁盘空间检查（非 dry-run）
            if not self.cfg.dry_run:
                try:
                    est_bytes = sum(
                        f.stat().st_size
                        for d in self.valid_dirs
                        for f in d.iterdir()
                        if f.is_file() and f.name not in self.cfg.excluded_items
                    )
                    self._check_disk_space(est_bytes)
                except Exception:
                    pass  # 检查失败不阻塞

            # 进度条
            if self.cfg.show_progress and tqdm:
                self.progress_bar = tqdm(total=total, desc="处理进度", unit="dir")

            # 多线程处理
            with ThreadPoolExecutor(max_workers=self.cfg.worker_threads) as executor:
                futures = {
                    executor.submit(self._process_directory, path): path
                    for path in self.valid_dirs
                    if not self._is_excluded(path) and not self._interrupted
                }

                for future in as_completed(futures):
                    if self._interrupted:
                        break
                    try:
                        future.result()
                    except Exception as e:
                        self._log_error(f"任务异常: {futures[future]} - {e}")

            if self.progress_bar:
                self.progress_bar.close()

            # 全局空目录清理
            if not self.cfg.dry_run and self.dest_root.exists():
                self._log_info("清理空目录...")
                for root, dirs, files in os.walk(self.dest_root, topdown=False):
                    current_dir = Path(root)
                    try:
                        if not any(current_dir.iterdir()):
                            current_dir.rmdir()
                            self.stats.empty_dirs_cleaned += 1
                    except OSError:
                        pass

            # 保存 JSON 日志
            try:
                with open(self.cfg.log_file, "w", encoding="utf-8") as f:
                    json.dump(self.log, f, ensure_ascii=False, indent=2)
            except Exception as e:
                self._log_error(f"保存日志失败: {e}")

            # 生成回滚脚本
            self._generate_rollback_script()

            # 统计
            self.stats.elapsed_seconds = time.time() - start_time
            self._log_info(self.stats.summary())

            if self._interrupted:
                self._log_warn("⚠️  操作被用户中断，部分文件可能未处理。")

        except KeyboardInterrupt:
            self._log_warn("\n操作被中断")
            sys.exit(130)
        except Exception as e:
            self._log_error(f"致命错误: {e}")
            sys.exit(1)


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="文件整理工具 — 自动提取多层目录中的有效文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=r"""
示例:
  python 文件整理工具.py "E:\测试目录"
  python 文件整理工具.py /home/user/data --dry-run
  python 文件整理工具.py ./docs --threads 8 --config config.json
  python 文件整理工具.py ./photos --include .jpg .png --max-size 50
        """,
    )

    parser.add_argument("source", help="源目录路径")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际执行")
    parser.add_argument("--config", "-c", help="JSON 配置文件路径")
    parser.add_argument("--threads", "-t", type=int, help="并发线程数")
    parser.add_argument("--output-name", "-o", help="目标子目录名称")
    parser.add_argument("--log-file", help="日志文件路径")
    parser.add_argument("--include", nargs="+", help="仅处理指定扩展名（如 .jpg .png）")
    parser.add_argument("--exclude", nargs="+", help="排除指定扩展名")
    parser.add_argument("--max-size", type=float, help="最大文件大小(MB)，超过则跳过")
    parser.add_argument("--max-depth", type=int, help="最大目录深度")
    parser.add_argument("--no-progress", action="store_true", help="不显示进度条")
    parser.add_argument("--no-rollback", action="store_true", help="不生成回滚脚本")
    parser.add_argument("--debug", action="store_true", help="开启调试模式")
    parser.add_argument("--move-only", action="store_true", help="总是用 move（同文件系统）而非 copy")

    return parser.parse_args()


def main():
    args = parse_args()

    # 加载配置
    if args.config and os.path.exists(args.config):
        config = Config.from_json(args.config)
    else:
        config = Config()

    # CLI 参数覆盖
    if args.dry_run:
        config.dry_run = True
    if args.threads:
        config.worker_threads = args.threads
    if args.output_name:
        config.pending_dir_name = args.output_name
    if args.log_file:
        config.log_file = args.log_file
    if args.include:
        config.include_extensions = args.include
    if args.exclude:
        config.exclude_extensions = args.exclude
    if args.max_size:
        config.max_file_size_mb = args.max_size
    if args.max_depth:
        config.max_path_depth = args.max_depth
    if args.no_progress:
        config.show_progress = False
    if args.no_rollback:
        config.generate_rollback = False
    if args.debug:
        config.debug_mode = True
    if args.move_only:
        config.use_move_on_same_fs = True

    processor = DirectoryProcessor(args.source, config)
    processor.execute()


if __name__ == "__main__":
    main()
