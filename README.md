# 理巢 · NestTidy

简体中文 | [English](README.en.md)

**面向下载资源和本地媒体库的 Windows 离线目录整理工具。先看清文件会去哪里，再决定是否执行。**

![Version](https://img.shields.io/badge/version-1.13.1-166451)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB)
[![Windows regression](https://github.com/NopantsNobra/NetTidy/actions/workflows/windows-regression.yml/badge.svg?branch=main)](https://github.com/NopantsNobra/NetTidy/actions/workflows/windows-regression.yml)
[![Browser smoke](https://github.com/NopantsNobra/NetTidy/actions/workflows/browser-smoke.yml/badge.svg?branch=main)](https://github.com/NopantsNobra/NetTidy/actions/workflows/browser-smoke.yml)

理巢针对 BT/PT、网盘下载、批量解压后的多层嵌套目录。它按固定结构规则消除冗余层级、保留必要的分支、处理同名冲突，并提供可解释的文件路径对照、两阶段重复文件诊断和文件属性修复。不依赖 AI 分类，不上传文件，不需要第三方 Python 运行时依赖。

## 整理效果

```text
整理前                         整理后
电影A/                         电影A/
  下载/                          01.mp4
    解压后/
      正文/
        01.mp4

电影B/                         电影B/
  花絮/cover.jpg                 花絮-cover.jpg
  预告/cover.jpg                 预告-cover.jpg
```

这不是无条件将全部文件拍平。复杂分支受到规则保护，文件不会被提升到所属资源包之外。同层撞名按来源目录链处理，必要时用原路径短码兜底，而不是覆盖或使用数字序号后缀。规则以 [规则对照表](docs/规则对照表.md) 为准。

## 主要功能

| 功能 | 说明 |
| --- | --- |
| 整理预览 | 只读扫描源目录，冻结计划；摘要、整理前后路径、动作及原因；支持按资源包查看和全量服务端筛选 |
| 复制整理 | 将整理后的副本写入独立输出目录，源目录不写入、不清理；适合初次使用和保留做种目录 |
| 原地整理 | 在资源包内移动、改名及清理空目录；执行前确认，事务日志支持条件满足时撤销 |
| 重复文件 | 初筛 + 全文签名复核；按组保留一份，跨页勾选副本，默认移入 Windows 回收站 |
| 权限修复 | 按需清除只读、普通隐藏、Zone.Identifier；CLI 另提供授权当前用户、修复继承选项 |
| 诊断与报告 | 规则命中、边界检查、异常项、历史、CSV/JSON/Markdown 报告及 PDF 操作手册 |

四个 Web 页签相互独立，共用工作目录。主要列表固定 80 行分页，翻页替换旧 DOM；整理预览按资源包和筛选条件从服务端请求明细。进度条、风险确认、全局错误反馈、键盘页签和窄屏布局均已实现。

[实际 Chromium 界面截图及验证记录](https://github.com/NopantsNobra/NetTidy/actions/runs/45388702957)（在 Actions 的 Artifacts 中下载截图；产物可能按 GitHub 保留策略过期）。

## 快速开始

推荐 Windows 10/11、Python 3.11 或更高版本，使用现代浏览器。主要验证环境为 Windows；其他系统不承诺完整功能，特别是回收站与权限修复。当前推荐源码运行；已有 EXE 试制产物，但尚未通过发布验收。

```powershell
git clone https://github.com/NopantsNobra/NetTidy.git
cd NetTidy
python -m nesttidy.app
```

打开 [http://127.0.0.1:8766](http://127.0.0.1:8766)（默认端口）。也可双击 `start_nesttidy.bat`；该脚本固定使用 8766，服务成功绑定后才打开浏览器；端口占用时不会弹出页面。无需 pip install。

手动指定端口：`python -m nesttidy.app --host 127.0.0.1 --port 8767`。启动目录中的 `理巢配置.json` 记录上次成功使用的源目录及端口；手动启动端口优先级为命令行、配置文件、默认 8766。仅支持本机监听。

### 整理工作流

1. 粘贴或拖入源目录。批量模式把直接子目录分别视作资源包，运行根下散落文件不动；单个模式把选中目录整体视作一个资源包。
2. 在试运行前选择原地整理或复制；复制需填写不与源目录互相包含的输出目录。
3. 点击「试运行」，查看摘要及文件路径对照。选择资源包、筛选全部/会变/改名/原地不动，按页浏览或跳转。
4. 确认后执行。首次使用建议先复制到独立目录，验证结果，再考虑原地整理。
5. 按需导出完整计划报告；原地整理结束后可根据事务日志尝试撤销；关闭网页或重启后，到「诊断信息 → 任务与报告历史」恢复最近 50 条中的可撤销任务，旧日志或更早任务可手工粘贴日志路径。更改执行方式、试运行失败、计划被淘汰或已执行时，应重新规划。

复制不自动删除源文件，会额外占用磁盘空间。中断后可对同一输出目录重新试运行并执行；仅在大小、修改时间及全文签名一致且不是链接/同一物理文件时跳过已有副本，不一致仍报冲突。新文件先写同目录私有临时文件，再以不覆盖方式发布；进程被强制终止可能留下 `.nesttidy-copy-*.tmp`，确认无任务运行后可手工清理。原地整理的 R6 收尾清扫可能删除执行期间变空的目录（含空资源包），选中的运行根保留；撤销按日志重建能够安全恢复的目录。撤销不是备份，也无法恢复随后被其他程序改写或永久删除的文件。

### 重复文件

| 初筛级别 | 比较方式 |
| --- | --- |
| fast | 大小 + 文件名；可能漏掉异名同内容文件 |
| standard（默认） | 大小 + 首尾各 1 MB |
| deep | 全文签名 |

候选自动按全文 SHA-256 内部签名复核，每个真实重复子组独立输出；deep 复用已计算的签名。内部签名包含十进制大小与文件内容，不是裸文件 SHA-256。

Web 端可按需读取完整重复诊断结果，每组固定 80 行、资源组分页并可跳转；「全选」只选本页可删副本，跨页已选保持。诊断缓存最多 4 份、合计 20 万行（单份超预算可独占）；淘汰或完成删除后需重新诊断。CLI 可导出 CSV。删除前重新检查诊断归属、文件、保留项、边界与签名；硬链接别名不会被当成可释放空间的普通副本。默认进入回收站，彻底删除需额外确认且不可撤销。回收站还原请使用 Windows 回收站，不使用整理撤销按钮。

## 安全设计与边界

- 结构规则与执行分离，Web 执行使用冻结计划 ID；计划状态及目录写操作互斥防止重放和重叠任务写入。
- 写入前重新校验源状态、目标冲突、资源根及链接边界；不静默覆盖。未完成下载（包括 `.xltd`、`.td`、`.td.cfg`、`.bc!`、`.!ut`）、符号链接、junction、系统文件及理巢专用输出目录按排除规则处理。
- 移动与目录清理的事务意图先记录再执行；撤销复核文件身份和状态，无法确认的恢复保留日志。不是 ACID 事务，无法消除所有检查与使用之间的竞态。
- 只绑定 127.0.0.1，校验 Host、Origin 与 JSON 请求类型；不提供云服务、遥测、多人远程访问或自动后台删除。
- 不做内容语义分类、通用文件管理器、云同步或自定义规则编辑器。重要数据请先备份，下载或做种期间优先使用复制。

## 大规模性能

维护者已在 20 万级实际文件目录中使用；以下可重复的数字来自隔离的 Windows 云端合成夹具，不是物理 SSD/HDD 性能承诺。

| 20 万文件 / 100 个资源包 | 旧基准 | v1.8.18 复测 |
| --- | ---: | ---: |
| 整体规划中位耗时 | 62.02 s | 22.07 s |
| 首屏服务端派生 | 4.85 s | 1.15 s |
| 首屏响应体积 | 538.7 KB | 43.8 KB |
| 正常轮进程工作集峰值 | 486 MB | 438 MB |
| 连续试运行驻留大型计划 | 3 | 1 |

同形态、不同云端 runner、200,000 个全 keep 小文件、三轮中位数；不能作严格单因素归因。新版 80 行分页首建索引约 0.48 s，热页约 0.60 ms（含 JSON 序列化，不含网络和渲染）。默认计划缓存预算 60 万条目（非字节限额），不淘汰执行中的计划。

Chromium 压力测试还验证了模拟 20 万文件/1,000 个资源包的整理预览，以及模拟 20 万条辅助结果的固定 80 行 DOM。辅助结果仍可能一次接收完整数组；辅助结果完整服务端分页、物理盘基准和 50 万/100 万规模验收尚未完成。

证据：[旧基准](https://github.com/NopantsNobra/NetTidy/actions/runs/45377368779) · [综合复测](https://github.com/NopantsNobra/NetTidy/actions/runs/45384922866) · [预览压力测试](https://github.com/NopantsNobra/NetTidy/actions/runs/45381771395) · [结果表格压力测试](https://github.com/NopantsNobra/NetTidy/actions/runs/45388412202)。

## 命令行

所有子命令均以 `python -m nesttidy.app` 开头；加 `--help` 查看对应参数。

| 子命令 | 用法（示例） |
| --- | --- |
| plan | `plan "D:/Downloads" --mode batch` |
| copy | `copy "D:/Downloads" "D:/Organized" --mode batch` |
| move | `move "D:/Downloads" --confirm` |
| undo | `undo "path/to/transaction_log.jsonl"` |
| duplicates | `duplicates "D:/Downloads" --level standard --workers 4` |
| dedupe | `dedupe "D:/Downloads" --level standard --groups dup-1 --mode recycle --confirm` |
| repair | `repair "D:/Downloads" --clear-readonly` |
| health / history | `health` 或 `history` |
| manual | `manual --output "NestTidy.pdf"` |

plan/copy/move/duplicates/dedupe 支持 `--report-dir`；plan 支持 `--output-root`；move 支持 `--no-elevate`。CLI 的 move 会重新生成计划，执行前请先用 plan 核对；dedupe 会重新诊断，组编号可能变化，不指定 --groups 则处理全部可删组，务必确认范围。CLI 删除模式是 recycle 或 purge，不是旧版 quarantine 参数。

计划报告包含 `summary.md`、`summary.json`、`mappings.csv`、`issues.csv`；重复诊断 CLI 导出 `duplicate_report.csv`。CLI 和 Web 默认在启动目录的「理巢报告」独立任务子目录写报告/事务日志，以实际返回的日志路径为准；旧版本日志仍可手工选择。Web 在执行前登记任务，执行后更新历史，不依赖导出报告；新记录仅存轻量索引，完整计划仍在导出的报告。启动目录应独立于源目录和输出目录。

## 开发与验证

Python 标准库服务 + 原生 HTML/CSS/JavaScript；无前端构建和第三方运行时依赖。浏览器回归是独立开发测试，需要 Node.js 22 和 Chromium。

```powershell
python -m unittest discover -s tests -v
python -m benchmarks.plan_bench --fixture-files 20000 --shape multi --repeat 3
python -m benchmarks.plan_bench --fixture-files 200000 --shape multi --repeat 3 --pages 20 --profile --memory
node benchmarks/ui_smoke.mjs ui-screenshots
```

基准默认使用隔离临时文件；也可用 --root 指定真实目录进行只读规划测量。重型测试不属于日常单测。

本次 v1.13.0 Windows CI 3.11/3.13 通过；Chromium 使用 408 个真实隔离文件验证第 400 行之后的分页、跨页选择、实际删除及保留文件。历史撤销回归仍通过。见 [Windows 回归](https://github.com/NopantsNobra/NetTidy/actions/runs/45931215118) 与 [浏览器回归](https://github.com/NopantsNobra/NetTidy/actions/runs/45931215141)。

## 项目资料

- [详细介绍](docs/理巢_详细介绍.md)：设计、架构与取舍
- [产品手册](docs/理巢_产品开发手册_v1.0.md)：产品范围与历史设计依据
- [规则对照表](docs/规则对照表.md)：唯一权威规则定义，当前 `P0-R0-R5-R6-v5`
- [路线图](ROADMAP.md)：验收状态、性能实验、未完成工作及裁决
- [完整旧版版本记录](https://github.com/NopantsNobra/NetTidy/blob/b9e55c7da395e1fea14a7e58372d3a876007c805/README.md#十版本记录) · [提交历史](https://github.com/NopantsNobra/NetTidy/commits/main)

本项目以 [MIT License](LICENSE) 授权（Copyright © 2025-2026 服雲）：可自由使用、修改、分发、商用，包括闭源衍生作品；唯一要求是保留版权与许可声明，作者不承担任何担保责任。许可文本见仓库根目录 `LICENSE`。作者主页：[github.com/NopantsNobra](https://github.com/NopantsNobra)；仓库主页：[github.com/NopantsNobra/NetTidy](https://github.com/NopantsNobra/NetTidy)。
