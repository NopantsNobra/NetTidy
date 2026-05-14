# 理巢 NestTidy

理巢是一个离线本地目录整理工具，用确定性结构规则清理资源目录内部多余嵌套壳目录。它默认只生成试运行计划，不直接修改磁盘。

## 运行

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

## 当前版本范围

本交付实现 MVP 1 和 MVP 2 的核心能力，并为 MVP 3 提供受控 move、事务日志和预检查框架：

- 批量模式 / 单资源模式。
- P0 边界保护。
- R0-R5 计划生成。
- Windows 文件名清洗计划。
- 本地 Web UI，默认绑定 `127.0.0.1`。
- copy 执行，源目录不变。
- move 执行，要求显式确认，写入事务日志。
- 报告导出：`summary.md`、`summary.json`、`mappings.csv`、`issues.csv`。

增强诊断、权限批量修复、重复文件深度检测和百万级 SQLite 分页可在后续版本继续扩展。
