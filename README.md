# 理巢 NestTidy

理巢是一个离线本地目录整理工具，用确定性结构规则清理资源目录内部多余嵌套壳目录。它默认只生成试运行计划，不直接修改磁盘。

## 双击运行

双击 `start_nesttidy.bat`，软件会在当前文件夹启动，并打开 `http://127.0.0.1:8765`。

## 命令行运行

```powershell
python -m nesttidy.app --host 127.0.0.1 --port 8765
```

浏览器打开 `http://127.0.0.1:8765`。

## 命令行生成计划

```powershell
python -m nesttidy.app plan "D:\下载\资源包" --mode batch --report-dir ".\理巢报告"
python -m nesttidy.app plan "D:\下载\资源包\电影A" --mode single --report-dir ".\理巢报告"
```

## 测试

```powershell
python -m unittest discover -s tests
```

## MVP4 功能范围

本交付实现 MVP1-MVP4 的本地可用能力：

- 批量模式 / 单资源模式。
- P0 边界保护。
- R0-R5 计划生成。
- Windows 文件名清洗计划。
- 本地 Web UI，默认绑定 `127.0.0.1`。
- copy 执行，源目录不变。
- move 执行，要求显式确认，写入事务日志。
- 报告导出：`summary.md`、`summary.json`、`mappings.csv`、`issues.csv`。
- 主动权限修复：清除只读、普通隐藏、Zone.Identifier，可通过 CLI 扩展到 icacls。
- 重复文件诊断：快速、标准、深度三档，只报告不自动删除。
- SQLite 操作历史：`理巢报告\history.sqlite`。
- Web 预览限制前 500 条，完整数据写入报告，避免大目录刷屏。
- PDF 操作手册：`理巢_NestTidy_简要操作手册.pdf`。

## 诊断命令

```powershell
python -m nesttidy.app duplicates "D:\下载\资源包" --level standard
python -m nesttidy.app repair "D:\下载\资源包" --clear-readonly --clear-zone-identifier
python -m nesttidy.app history
python -m nesttidy.app manual
```
