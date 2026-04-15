# 理巢 (NestTidy) v1.2.0 使用手册

**版本**: v1.2.0  
**平台**: Windows 10/11 / Linux / macOS  
**语言**: Python 3.9+

---

## 目录

1. [安装指南](#一安装指南)
2. [快速开始](#二快速开始)
3. [命令行使用](#三命令行使用)
4. [Python API 使用](#四python-api-使用)
5. [核心算法说明](#五核心算法说明)
6. [配置文件](#六配置文件)
7. [输出文件说明](#七输出文件说明)
8. [常见问题](#八常见问题)

---

## 一、安装指南

### 1.1 系统要求

- Python 3.9 或更高版本
- 支持的操作系统：Windows 10/11、Linux、macOS

### 1.2 依赖项

```
tqdm>=4.65.0      # 进度条显示
colorama>=0.4.6   # 终端颜色输出
jinja2>=3.1.0     # HTML 报告生成
```

### 1.3 安装方式

**方式一：从源码安装**

```bash
# 解压代码包
unzip nesttidy_v1.2.0_audited.zip

# 进入目录
cd nesttidy

# 安装
pip install -e .
```

**方式二：直接使用（无需安装）**

```bash
# 解压后直接运行
cd nesttidy
python -m nesttidy --help
```

### 1.4 验证安装

```bash
nesttidy --version
# 输出: nesttidy 1.2.0

nesttidy --help
# 显示帮助信息
```

---

## 二、快速开始

### 2.1 基本使用流程

```bash
# 1. 演练模式（预览变更，不实际执行）
nesttidy ./messy_folder ./clean_folder --dry-run

# 2. 查看预览结果后，执行实际操作
nesttidy ./messy_folder ./clean_folder --mode copy
```

### 2.2 典型使用场景

**场景一：整理网盘下载的文件夹**

```bash
# 网盘下载的文件夹往往有多层嵌套
nesttidy ~/Downloads/百度网盘/电影合集 ./整理后的电影 --dry-run
```

**场景二：整理解压后的文件**

```bash
# 解压后的文件结构混乱
nesttidy ./解压目录 ./整理后目录 --mode copy
```

**场景三：快速移动整理（同卷操作）**

```bash
# 同一磁盘内快速移动
nesttidy ./messy ./clean --mode move
```

---

## 三、命令行使用

### 3.1 命令格式

```bash
nesttidy [选项] <源目录> <目标目录>
```

### 3.2 必需参数

| 参数 | 说明 |
|------|------|
| `source` | 源目录路径（待重构的目录） |
| `target` | 目标目录路径（重构结果输出目录） |

### 3.3 可选参数

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `--mode` | `-m` | copy | 执行模式：copy/move/auto |
| `--dry-run` | `-n` | False | 演练模式，只预览不执行 |
| `--jobs` | `-j` | 4 | 并发线程数 |
| `--no-dup-detect` | | False | 禁用重复文件检测 |
| `--force-cross-volume` | | False | 强制允许跨卷移动（危险） |
| `--output-dir` | `-o` | 目标目录 | 报告输出目录 |
| `--log-file` | `-l` | 无 | 日志文件路径 |
| `--log-level` | | INFO | 日志级别：DEBUG/INFO/WARNING/ERROR |
| `--config` | `-c` | 无 | 配置文件路径 |
| `--no-progress` | | False | 禁用进度条 |
| `--version` | `-v` | | 显示版本号 |
| `--help` | `-h` | | 显示帮助信息 |

### 3.4 执行模式说明

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| `copy` | 始终复制文件，保留源文件 | 需要保留原始文件的安全场景 |
| `move` | 同卷用移动，跨卷用跨卷移动 | 需要快速整理且不需要保留源文件 |
| `auto` | 同卷用移动，跨卷用复制 | 自动选择最优策略 |

### 3.5 命令示例

**示例 1：演练模式预览**

```bash
nesttidy ./downloads ./organized --dry-run
```

输出示例：
```
============================================================
任务执行摘要
============================================================

✓ 任务执行成功

  源目录: /home/user/downloads
  目标目录: /home/user/organized
  执行模式: copy

  文件统计:
    总文件数: 15
    总大小: 1.2 GB

  执行耗时:
    扫描: 0.05s
    分析: 0.02s
    总耗时: 0.08s
```

**示例 2：复制模式执行**

```bash
nesttidy ./downloads ./organized --mode copy --jobs 8
```

**示例 3：移动模式执行**

```bash
nesttidy ./downloads ./organized --mode move
```

**示例 4：使用配置文件**

```bash
nesttidy ./downloads ./organized --config nesttidy.toml
```

**示例 5：详细日志输出**

```bash
nesttidy ./downloads ./organized --log-level DEBUG --log-file nesttidy.log
```

**示例 6：指定报告输出目录**

```bash
nesttidy ./downloads ./organized --output-dir ./reports
```

---

## 四、Python API 使用

### 4.1 基本使用

```python
from pathlib import Path
from nesttidy.core.engine import RestructureEngine

# 创建引擎实例
engine = RestructureEngine(
    source=Path("./messy_folder"),
    target=Path("./clean_folder"),
    mode="copy"
)

# 执行三阶段流水线（链式调用）
engine.scan().analyze().build_map()

# 获取映射表
entries = engine.map

# 打印结果
for entry in entries:
    print(f"{entry.source_path} -> {entry.target_path}")
```

### 4.2 分步执行

```python
from pathlib import Path
from nesttidy.core.engine import RestructureEngine
from nesttidy.core.scanner import Scanner
from nesttidy.core.penetrator import penetrator

# 方式一：使用引擎的链式调用
engine = RestructureEngine(
    source=Path("./source"),
    target=Path("./target"),
    mode="copy"
)

# 阶段一：扫描
engine.scan()
print(f"扫描完成，根节点: {engine.root.name}")

# 阶段二：分析
engine.analyze()
print("穿透判定完成")

# 阶段三：生成映射表
engine.build_map()
print(f"生成映射表: {len(engine.map)} 条")

# 方式二：使用独立组件
scanner = Scanner(Path("./source"))
root = scanner.scan()
print(f"节点总数: {scanner.node_count}")

# 对根节点执行穿透判定
penetrator.penetrate(root)
```

### 4.3 执行文件操作

```python
from pathlib import Path
from nesttidy.core.engine import RestructureEngine
from nesttidy.safety.file_executor import FileExecutor

# 生成映射表
engine = RestructureEngine(
    source=Path("./source"),
    target=Path("./target"),
    mode="copy"
)
engine.scan().analyze().build_map()
entries = engine.map

# 执行文件操作
executor = FileExecutor(
    mode="copy",
    max_workers=4,
    progress_callback=lambda current, total, result: 
        print(f"进度: {current}/{total}")
)

# 批量执行
summary = executor.execute_batch(entries, Path("./source"), Path("./target"))

print(f"成功: {summary.success_count}")
print(f"失败: {summary.failure_count}")
print(f"吞吐量: {summary.throughput_mbps:.2f} MB/s")
```

### 4.4 预检查

```python
from pathlib import Path
from nesttidy.safety.pre_checker import PreChecker

checker = PreChecker()
result = checker.check(
    source=Path("./source"),
    target=Path("./target"),
    mode="copy"
)

if result.passed:
    print("预检通过")
else:
    print("预检失败:")
    for error in result.errors:
        print(f"  - {error}")
```

### 4.5 文件名清洗

```python
from nesttidy.safety.name_sanitizer import NameSanitizer, sanitize_filename

# 使用便捷函数
safe_name = sanitize_filename("file<invalid>.txt")
print(safe_name)  # 输出: file_invalid_.txt

# 使用完整类
sanitizer = NameSanitizer(
    replacement="_",
    max_name_length=200
)

# 清洗各种文件名
names = [
    "normal.txt",
    "file<with>invalid:chars?.txt",
    "CON",  # Windows 保留名
    "  spaced  .txt  ",
]

for name in names:
    safe = sanitizer.sanitize(name)
    print(f"{name!r} -> {safe!r}")
```

### 4.6 重复文件检测

```python
from pathlib import Path
from nesttidy.core.scanner import Scanner
from nesttidy.enhance.dup_detector import DupDetector

# 扫描目录
scanner = Scanner(Path("./source"))
root = scanner.scan()

# 检测重复文件
detector = DupDetector()
dup_groups = detector.detect(root)

print(f"发现 {len(dup_groups)} 组重复文件")

for md5_hash, nodes in dup_groups.items():
    print(f"\nMD5: {md5_hash[:16]}...")
    for node in nodes:
        print(f"  - {node.path}")
        if node.is_duplicate:
            print(f"    (重复于: {node.duplicate_of})")
```

### 4.7 空壳目录清理

```python
from pathlib import Path
from nesttidy.safety.shell_cleaner import ShellCleaner

# 演练模式（不实际删除）
cleaner = ShellCleaner(dry_run=True)
deleted_count = cleaner.clean(Path("./source"))
print(f"将删除 {deleted_count} 个空目录")

# 实际清理
cleaner = ShellCleaner(dry_run=False)
deleted_count = cleaner.clean(Path("./source"))
print(f"已删除 {deleted_count} 个空目录")

# 获取清理报告
report = cleaner.get_report()
print(f"删除: {report['deleted_count']}")
print(f"跳过: {report['skipped_count']}")
```

### 4.8 使用调度器（完整流程）

```python
from pathlib import Path
from nesttidy.runtime.scheduler import Scheduler

# 创建调度器
scheduler = Scheduler(
    source=Path("./source"),
    target=Path("./target"),
    mode="copy",
    dry_run=False,
    detect_duplicates=True,
    max_workers=4,
)

# 运行完整流程
result = scheduler.run()

# 检查结果
if result.success:
    print("任务成功")
    print(f"文件数: {result.file_count}")
    print(f"总大小: {result.total_bytes} bytes")
    print(f"耗时: {result.total_time_ms/1000:.2f}s")
else:
    print("任务失败")
    for error in result.errors:
        print(f"  - {error}")
```

---

## 五、核心算法说明

### 5.1 穿透规则（剥洋葱）

当一个文件夹里面**没有任何文件**、只有**唯一一个**子文件夹时，这个外层文件夹被视为空壳，直接消失，继续向内穿透。

**穿透停止条件：**

1. **只包着一个单文件** → 提取文件，壳子消失
2. **包着多个文件** → 保留这个文件夹
3. **出现分叉**（≥2个子文件夹）→ 保留分叉点
4. **既有文件又有子文件夹** → 保留

### 5.2 同生共死规则

因"出现分叉"停下来后，检查分叉出的子文件夹：

- **全部**子文件夹里都各自只有1个文件 → 全部提取到分叉点下，子文件夹消失
- 只要**有一个**子文件夹含≥2个文件 → 全部保留，不提取

### 5.3 冲突重命名规则

出现重名时，使用溯源前缀法：

1. 向上追溯各自的穿透链，找到第一个**名字不同**的祖先
2. 用该祖先名加前缀：`祖先名-原名`
3. 若祖先名含非法字符，先清洗再加前缀

### 5.4 算法示例

**输入结构：**
```
source/
├── project/
│   ├── docs/
│   │   └── manuals/
│   │       ├── v1/
│   │       │   └── guide.txt
│   │       └── v2/
│   │           └── guide.txt
│   └── src/
│       └── components/
│           ├── ui/
│           │   └── Button.tsx
│           └── layout/
│               └── Header.tsx
└── deep/
    └── a/
        └── b/
            └── c/
                └── file.txt
```

**输出结构：**
```
target/
├── project/
│   ├── manuals/
│   │   ├── v1/
│   │   │   └── guide.txt
│   │   └── v2/
│   │       └── guide.txt
│   └── components/
│       ├── ui/
│       │   └── Button.tsx
│       └── layout/
│           └── Header.tsx
└── file.txt  # 穿透提升
```

**变更说明：**
- `deep/a/b/c/file.txt` → `file.txt`（穿透提升）
- `project/docs/manuals/` → `project/manuals/`（空壳穿透）
- `project/src/components/` → `project/components/`（空壳穿透）

---

## 六、配置文件

### 6.1 TOML 格式配置文件

创建 `nesttidy.toml`：

```toml
[nesttidy]
# 执行模式
mode = "copy"

# 并发设置
max_workers = 4

# 功能开关
detect_duplicates = true
dry_run = false
force_cross_volume = false

# 输出设置
output_dir = "./reports"
log_file = "./nesttidy.log"
log_level = "INFO"

# 过滤设置
exclude_patterns = ["*.tmp", "*.bak"]
include_patterns = ["*"]

# 安全设置
max_nodes = 5000000
verify_integrity = true
```

### 6.2 YAML 格式配置文件

创建 `nesttidy.yaml`：

```yaml
nesttidy:
  # 执行模式
  mode: copy
  
  # 并发设置
  max_workers: 4
  
  # 功能开关
  detect_duplicates: true
  dry_run: false
  force_cross_volume: false
  
  # 输出设置
  output_dir: ./reports
  log_file: ./nesttidy.log
  log_level: INFO
  
  # 过滤设置
  exclude_patterns:
    - "*.tmp"
    - "*.bak"
  include_patterns:
    - "*"
  
  # 安全设置
  max_nodes: 5000000
  verify_integrity: true
```

### 6.3 使用配置文件

```bash
nesttidy ./source ./target --config nesttidy.toml
```

### 6.4 配置优先级

1. 命令行参数（最高优先级）
2. 配置文件
3. 默认值（最低优先级）

---

## 七、输出文件说明

### 7.1 输出文件列表

执行完成后，在目标目录或指定的输出目录生成以下文件：

| 文件 | 说明 |
|------|------|
| `map.json` | 映射表 JSON 文件 |
| `changes.txt` | 变更摘要文本文件 |
| `report.html` | HTML 可视化报告 |

### 7.2 map.json 格式

```json
{
  "schema_version": "1.1",
  "total_files": 8,
  "total_bytes": 1024,
  "entries": [
    {
      "source": "/path/to/source/file1.txt",
      "target": "/path/to/target/file1.txt",
      "action": "copy",
      "action_reason": "直接复制",
      "rename_reason": null,
      "file_size": 100
    },
    {
      "source": "/path/to/source/deep/a/b/file2.txt",
      "target": "/path/to/target/file2.txt",
      "action": "copy",
      "action_reason": "穿透深度 sw=3 提升",
      "rename_reason": null,
      "file_size": 50
    }
  ]
}
```

### 7.3 changes.txt 格式

```
============================================================
理巢 — 目录重构变更摘要
============================================================

生成时间: 2026-04-17 10:30:00

源目录: /path/to/source
目标目录: /path/to/target

统计摘要:
  总文件数: 8
  总大小: 1.0 KB
  复制操作: 8
  移动操作: 0
  重命名文件: 0

------------------------------------------------------------
变更详情
------------------------------------------------------------

0001. [复制] file1.txt
      原因: 直接复制

0002. [复制] deep/a/b/file2.txt
      → file2.txt
      原因: 穿透深度 sw=3 提升
```

### 7.4 report.html

生成一个可视化的 HTML 报告，包含：
- 操作统计
- 变更列表表格
- 源路径和目标路径对比

---

## 八、常见问题

### 8.1 如何预览变更而不实际执行？

使用 `--dry-run` 参数：

```bash
nesttidy ./source ./target --dry-run
```

### 8.2 如何保留原始文件？

使用 `copy` 模式（默认）：

```bash
nesttidy ./source ./target --mode copy
```

### 8.3 如何快速移动文件？

使用 `move` 模式（同卷操作更快）：

```bash
nesttidy ./source ./target --mode move
```

### 8.4 如何处理跨卷操作？

- `copy` 模式：自动复制
- `move` 模式：默认阻止跨卷移动，回退到复制
- `auto` 模式：同卷移动，跨卷复制

强制允许跨卷移动（危险）：

```bash
nesttidy ./source ./target --mode move --force-cross-volume
```

### 8.5 如何提高执行速度？

增加并发线程数：

```bash
nesttidy ./source ./target --jobs 8
```

### 8.6 如何查看详细日志？

```bash
nesttidy ./source ./target --log-level DEBUG --log-file debug.log
```

### 8.7 系统文件会被处理吗？

以下系统文件会被自动跳过：
- `.DS_Store`（macOS）
- `desktop.ini`（Windows）
- `Thumbs.db`（Windows）

### 8.8 如何处理文件名冲突？

工具会自动使用溯源前缀法重命名冲突文件：

```
原始: docs/v1/guide.txt, docs/v2/guide.txt
结果: v1-guide.txt, v2-guide.txt
```

### 8.9 支持的最大文件数量？

默认最大支持 500 万节点，超过会触发熔断保护。

### 8.10 如何在代码中获取执行进度？

使用进度回调：

```python
def on_progress(current, total, result):
    percent = current / total * 100
    print(f"进度: {percent:.1f}% ({current}/{total})")

executor = FileExecutor(
    mode="copy",
    progress_callback=on_progress
)
```

---

## 附录：完整示例脚本

```python
#!/usr/bin/env python3
"""
理巢完整使用示例
"""
from pathlib import Path
from nesttidy.core.engine import RestructureEngine
from nesttidy.safety.file_executor import FileExecutor
from nesttidy.safety.pre_checker import PreChecker

def main():
    source = Path("./messy_folder")
    target = Path("./clean_folder")
    
    # 1. 预检查
    print("=== 预检查 ===")
    checker = PreChecker()
    pre_result = checker.check(source, target, mode="copy")
    
    if not pre_result.passed:
        print("预检失败:")
        for error in pre_result.errors:
            print(f"  - {error}")
        return 1
    
    print("预检通过")
    
    # 2. 分析目录结构
    print("\n=== 分析目录结构 ===")
    engine = RestructureEngine(source=source, target=target, mode="copy")
    engine.scan().analyze().build_map()
    
    print(f"扫描完成，生成 {len(engine.map)} 个映射条目")
    
    # 3. 显示部分映射
    print("\n=== 映射预览 ===")
    for entry in engine.map[:5]:
        src_rel = entry.source_path.relative_to(source)
        tgt_rel = entry.target_path.relative_to(target)
        print(f"  {src_rel} -> {tgt_rel}")
    
    if len(engine.map) > 5:
        print(f"  ... 还有 {len(engine.map) - 5} 个")
    
    # 4. 执行文件操作
    print("\n=== 执行文件操作 ===")
    executor = FileExecutor(mode="copy", max_workers=4)
    summary = executor.execute_batch(engine.map, source, target)
    
    print(f"成功: {summary.success_count}")
    print(f"失败: {summary.failure_count}")
    print(f"耗时: {summary.duration_ms/1000:.2f}s")
    
    # 5. 写入映射表
    print("\n=== 生成报告 ===")
    executor.write_map_json(engine.map, target / "map.json")
    print(f"映射表已写入: {target / 'map.json'}")
    
    return 0

if __name__ == "__main__":
    exit(main())
```

---

**文档版本**: v1.2.0  
**最后更新**: 2026-04-17
