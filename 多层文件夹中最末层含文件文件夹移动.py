import os
import shutil
import hashlib
import time
import threading
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import sys

class Config:
    PENDING_DIR_NAME = "待定"
    LOG_FILE = "operation_log.json"
    HASH_ALGORITHM = hashlib.sha256()
    WORKER_THREADS = 4
    EXCLUDED_ITEMS = {'.DS_Store', 'desktop.ini', 'Thumbs.db'}
    SHOW_PROGRESS = True
    DEBUG_MODE = True

class DirectoryProcessor:
    def __init__(self, source_root):
        self.src_root = Path(source_root).resolve()
        self.dest_root = self.src_root / Config.PENDING_DIR_NAME
        self.log = []
        self.lock = threading.Lock()
        self.progress_bar = None
        self.valid_dirs = set()
        self.task_queue = []
        self.completed_tasks = 0

        if Config.DEBUG_MODE:
            print("[DEBUG] 源目录:", self.src_root)
            print("[DEBUG] 目标目录:", self.dest_root)

    def _validate_paths(self):
        if not self.src_root.exists():
            raise FileNotFoundError(f"源目录不存在: {self.src_root}")
        if self.dest_root == self.src_root:
            raise ValueError("目标目录不能与源目录相同")

    def _log_debug(self, message):
        if Config.DEBUG_MODE:
            with self.lock:
                print(f"[DEBUG] {time.strftime('%H:%M:%S')} {message}")

    def _is_excluded(self, path):
        try:
            return path.resolve().is_relative_to(self.dest_root)
        except ValueError:
            return False

    def _has_real_content(self, path):
        """优化的有效性检查（带短路返回）"""
        try:
            for entry in os.scandir(path):
                if entry.name in Config.EXCLUDED_ITEMS:
                    continue
                
                if entry.is_file():
                    self._log_debug(f"发现有效文件: {entry.path}")
                    return True
                
                if entry.is_dir() and self._has_real_content(entry.path):
                    return True
            return False
        except Exception as e:
            self._log_debug(f"扫描异常: {path} - {str(e)}")
            return False

    def _pre_scan(self):
        """精准预扫描机制"""
        self._log_debug("开始精准预扫描...")
        valid_dirs = set()

        # 自底向上遍历确保父目录继承有效性
        for root, dirs, files in os.walk(self.src_root, topdown=False):
            current_path = Path(root)
            if self._is_excluded(current_path):
                continue

            if self._has_real_content(current_path):
                # 添加当前目录及其所有父目录
                parent = current_path
                while parent != self.src_root.parent:
                    if parent.is_relative_to(self.src_root):
                        valid_dirs.add(parent)
                    parent = parent.parent

        self.valid_dirs = valid_dirs
        self._log_debug(f"发现有效目录 {len(valid_dirs)} 个")
        return len(valid_dirs)

    def _file_hash(self, file_path):
        """增强型哈希计算"""
        hasher = Config.HASH_ALGORITHM.copy()
        try:
            with open(file_path, 'rb') as f:
                while chunk := f.read(4096):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception as e:
            self._log_debug(f"哈希计算失败: {file_path} - {str(e)}")
            return "ERROR"

    def _safe_copy(self, src, dst):
        """原子化文件操作"""
        try:
            # 创建目标目录（如果不存在）
            dst.parent.mkdir(parents=True, exist_ok=True)
            
            # 处理已存在文件
            if dst.exists():
                # Windows权限处理
                if sys.platform == 'win32':
                    os.chmod(dst, 0o666)
                
                src_hash = self._file_hash(src)
                dst_hash = self._file_hash(dst)
                
                if src_hash == dst_hash:
                    return False, dst
                
                # 生成唯一冲突文件名
                timestamp = int(time.time() * 1000)
                dst = dst.with_name(f"{dst.stem}_CONFLICT_{timestamp}{dst.suffix}")

            # 执行原子操作
            shutil.copy2(src, dst)
            return True, dst
        except Exception as e:
            self._log_debug(f"复制失败: {str(e)}")
            return False, dst

    def _process_directory(self, current_path):
        """线程安全目录处理"""
        try:
            # 跳过已处理目录
            with self.lock:
                if current_path in self.task_queue:
                    return
                self.task_queue.append(current_path)

            self._log_debug(f"开始处理目录: {current_path}")
            relative_path = current_path.relative_to(self.src_root)
            dest_dir = self.dest_root / relative_path
            created_files = 0

            # 处理文件
            for entry in os.scandir(current_path):
                if entry.name in Config.EXCLUDED_ITEMS:
                    continue

                src = Path(entry.path)
                if entry.is_file():
                    dest = dest_dir / entry.name
                    copied, final_dest = self._safe_copy(src, dest)
                    
                    with self.lock:
                        self.log.append({
                            "time": time.time(),
                            "op": "COPY" if copied else "SKIP",
                            "src": str(src),
                            "dst": str(final_dest),
                            "hash": self._file_hash(src)
                        })
                    
                    if copied:
                        try:
                            src.unlink()
                            created_files += 1
                        except Exception as e:
                            self._log_debug(f"删除失败: {src} - {str(e)}")

            # 强制更新进度
            with self.lock:
                self.completed_tasks += 1
                if Config.SHOW_PROGRESS:
                    self.progress_bar.update(1)
                
                # 清理空目录
                try:
                    if dest_dir.exists() and not any(dest_dir.iterdir()):
                        dest_dir.rmdir()
                        self._log_debug(f"清理空目录: {dest_dir}")
                except OSError:
                    pass

        except Exception as e:
            self._log_debug(f"处理异常: {current_path} - {str(e)}")
            with self.lock:
                self.log.append({
                    "time": time.time(),
                    "error": str(e),
                    "path": str(current_path)
                })

    def execute(self):
        try:
            self._validate_paths()
            total = self._pre_scan()
            
            if Config.SHOW_PROGRESS:
                self.progress_bar = tqdm(total=total, desc="处理进度")

            with ThreadPoolExecutor(max_workers=Config.WORKER_THREADS) as executor:
                # 提交任务并记录future对象
                futures = []
                for path in self.valid_dirs:
                    if not self._is_excluded(path):
                        futures.append(executor.submit(self._process_directory, path))

                # 等待所有任务完成
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        print(f"任务异常: {str(e)}")

            # 最终清理空目录链
            self._log_debug("执行最终清理...")
            for root, dirs, files in os.walk(self.dest_root, topdown=False):
                current_dir = Path(root)
                try:
                    if not any(current_dir.iterdir()):
                        current_dir.rmdir()
                        self._log_debug(f"清理空目录: {current_dir}")
                except OSError:
                    pass

            # 保存日志
            with open(Config.LOG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.log, f, ensure_ascii=False, indent=2)

            print(f"\n操作完成！处理目录数: {self.completed_tasks}/{total}")
            print(f"日志文件: {Config.LOG_FILE}")

        except Exception as e:
            print(f"致命错误: {str(e)}")
            sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"使用方法: python {Path(__file__).name} <源目录>")
        print(r'示例: python script.py "E:\测试目录"')
        sys.exit(1)

    try:
        processor = DirectoryProcessor(sys.argv[1])
        processor.execute()
    except Exception as e:
        print(f"初始化错误: {str(e)}")
        sys.exit(1)
