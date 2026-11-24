# NestTidy (理巢)

[简体中文](README.md) | English

**A local, offline directory organizer for downloaded resources and media libraries on Windows. Preview exactly where files will go before deciding to execute.**

![Version](https://img.shields.io/badge/version-1.8.20-166451)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB)
[![Windows regression](https://github.com/NopantsNobra/NetTidy/actions/workflows/windows-regression.yml/badge.svg?branch=main)](https://github.com/NopantsNobra/NetTidy/actions/workflows/windows-regression.yml)
[![Browser smoke](https://github.com/NopantsNobra/NetTidy/actions/workflows/browser-smoke.yml/badge.svg?branch=main)](https://github.com/NopantsNobra/NetTidy/actions/workflows/browser-smoke.yml)

NestTidy addresses deeply nested folders created by BitTorrent/private trackers, cloud-drive downloads and repeated extraction. It removes redundant nesting according to deterministic structural rules, preserves meaningful branches, resolves name collisions, and provides explainable path previews, two-stage duplicate detection and file attribute repair. No AI classification, file uploads or third-party Python runtime dependencies.

## Before and after

```text
Before                         After
Movie A/                       Movie A/
  Download/                      01.mp4
    Extracted/
      Content/
        01.mp4

Movie B/                       Movie B/
  Extras/cover.jpg               Extras-cover.jpg
  Trailers/cover.jpg             Trailers-cover.jpg
```

NestTidy is not an unconditional folder flattener. It preserves protected branches and never promotes a file beyond its own resource package. Name collisions use prefixes derived from the original directory chain, with a short path-derived hash as a fallback, rather than overwriting or appending numeric counters. The authoritative definitions are in the [rule reference](docs/规则对照表.md) (Chinese).

## Features

| Feature | What it does |
| --- | --- |
| Dry-run preview | Read-only source scan and frozen plan; summary, before/after paths, actions and reasons; package navigation and server-side filtering |
| Copy organization | Writes an organized copy to a separate output directory without writing to or cleaning the source; useful for first runs and preserving seeding folders |
| In-place organization | Moves and renames files within packages and removes empty directories; explicit confirmation and transaction-log-based conditional undo |
| Duplicate detection | Initial grouping followed by full-content signature verification; keeps a copy per group, preserves selections across pages, defaults to the Windows Recycle Bin |
| Attribute repair | Clears read-only, ordinary hidden and Zone.Identifier flags on request; the CLI also exposes current-user access and inheritance repair |
| Diagnostics and reports | Rule counts, boundary checks, issues, history, CSV/JSON/Markdown reports and a PDF user guide |

Four independent web tabs share one source directory. Major lists render at most 80 rows per page, replacing rather than accumulating DOM nodes. Organization preview pages and filters are served on demand. Progress reporting, risk confirmations, global error feedback, keyboard tab navigation and narrow-screen layouts are implemented.

[Actual Chromium screenshots and verification](https://github.com/NopantsNobra/NetTidy/actions/runs/45388702957) (download the screenshots from Actions artifacts; subject to GitHub artifact retention).

## Quick start

Windows 10/11, Python 3.11 or later and a modern browser are recommended. Windows is the primary tested platform; full functionality on other systems, especially the Recycle Bin and Windows attribute repair, is not promised. Run from source; there is no official EXE installer yet.

```powershell
git clone https://github.com/NopantsNobra/NetTidy.git
cd NetTidy
python -m nesttidy.app
```

Open [http://127.0.0.1:8766](http://127.0.0.1:8766) (default port). Alternatively, double-click `start_nesttidy.bat`; it uses port 8766 explicitly and opens the browser before starting the server, so a refresh may be necessary. No pip install is required.

To choose another port: `python -m nesttidy.app --host 127.0.0.1 --port 8767`. Local configuration is stored as `理巢配置.json` in the working directory and remembers the last successful source directory and port. For manual startup, the CLI option takes precedence over the config file, then the default. Loopback access only.

The application UI, CLI messages and generated documentation are currently primarily in Chinese. This English README is documentation, not an English-language UI release.

### Organization workflow

1. Paste or drag in a source directory. Batch mode treats each immediate subdirectory as a separate resource package and leaves loose files in the selected parent untouched. Single mode treats the selected directory as one package.
2. Choose copy or in-place organization before the dry run. Copy requires a separate output directory that does not overlap the source.
3. Run a dry run. Inspect the summary and before/after file paths. Select a package, filter all/changed/renamed/unchanged files, and page or jump through results.
4. Confirm execution. For the first run, copy to a separate directory and inspect the result before considering in-place organization.
5. Export the full plan report if needed. After an in-place run, use the transaction log to attempt undo. Replan after a mode change, a failed dry run, eviction or execution of a plan.

Copy never deletes the source automatically and requires additional storage. In-place R6 cleanup can remove directories that become empty during execution, including empty package roots; the selected run root is retained. Undo recreates directories when the recorded state can be safely restored. It is not a backup and cannot restore arbitrary later modifications or permanently deleted files.

### Duplicate files

| Initial grouping | Comparison |
| --- | --- |
| fast | Size + filename; may miss identical content with different names |
| standard (default) | Size + first and last 1 MB |
| deep | Full-content signature |

Candidates are automatically verified using a full-content SHA-256-based internal signature. Each genuine duplicate subgroup is retained independently; deep mode reuses its signature. The internal signature includes decimal file size and file bytes, so it is not the plain file SHA-256 digest.

The web UI currently loads at most 400 diagnostic detail rows, paginated at 80 rows per group. Select all means all eligible, already-loaded copies only; unloaded files are never silently selected. Use the CLI for complete diagnostic CSV export. Before deletion, the application rechecks the candidate, retained copy, boundaries and signatures. Hard-link aliases are not treated as ordinary reclaimable copies. Recycle Bin is the default; permanent deletion requires additional confirmation and cannot be undone. Restore recycled files from Windows Recycle Bin, not the organization undo button.

## Safety and scope

- Structural planning and disk execution are separate. Web execution uses frozen plan IDs, lifecycle checks and write-scope exclusion to prevent replay and overlapping write tasks.
- Source state, destination conflicts, package identity and link boundaries are checked before writing; no silent overwrite. Incomplete downloads, symbolic links, junctions, system files and designated NestTidy output directories are handled by exclusion rules.
- Transaction intent is recorded before moves and directory removal. Recovery checks file identity and state, retaining the log when recovery cannot be confirmed. This is not an ACID transaction and cannot eliminate every time-of-check/time-of-use race.
- Binds to 127.0.0.1, validates Host, Origin and JSON request types; no cloud service, telemetry, multi-user remote access or unattended background deletion.
- Not a semantic classifier, general file manager, cloud sync client or custom rule editor. Back up important data; prefer copy while downloads or seeding are active.

## Large-directory performance

The maintainer has used the tool on real directories at the 200,000-file scale. The reproducible numbers below come from isolated synthetic fixtures on GitHub-hosted Windows, not guaranteed performance on a physical SSD or HDD.

| 200,000 files / 100 packages | Earlier baseline | v1.8.18 retest |
| --- | ---: | ---: |
| Median plan generation | 62.02 s | 22.07 s |
| Server-side initial display derivation | 4.85 s | 1.15 s |
| Initial response size | 538.7 KB | 43.8 KB |
| Normal-run peak process working set | 486 MB | 438 MB |
| Large plans retained after repeated runs | 3 | 1 |

Same fixture shape, different hosted runners, 200,000 small keep-only files, median of three runs; not a controlled single-factor attribution. The new 80-row endpoint takes about 0.48 s for the first index build and 0.60 ms for warm pages, including JSON serialization but excluding network and browser rendering. The default cache budget is 600,000 entries (not bytes); running plans are protected from eviction.

Chromium stress tests also validate bounded 80-row rendering for simulated 200,000-file/1,000-package previews and 200,000 auxiliary result rows. Auxiliary results can still arrive as complete arrays. Full server-side result pagination, physical-drive benchmarking and acceptance at 500,000/1,000,000 files remain unfinished.

Evidence: [earlier baseline](https://github.com/NopantsNobra/NetTidy/actions/runs/45377368779) · [comprehensive retest](https://github.com/NopantsNobra/NetTidy/actions/runs/45384922866) · [preview stress test](https://github.com/NopantsNobra/NetTidy/actions/runs/45381771395) · [result-table stress test](https://github.com/NopantsNobra/NetTidy/actions/runs/45388412202).

## Command line

Prefix each subcommand with `python -m nesttidy.app`. Add `--help` for its options.

| Subcommand | Example |
| --- | --- |
| plan | `plan "D:/Downloads" --mode batch` |
| copy | `copy "D:/Downloads" "D:/Organized" --mode batch` |
| move | `move "D:/Downloads" --confirm` |
| undo | `undo "path/to/transaction_log.jsonl"` |
| duplicates | `duplicates "D:/Downloads" --level standard --workers 4` |
| dedupe | `dedupe "D:/Downloads" --level standard --groups dup-1 --mode recycle --confirm` |
| repair | `repair "D:/Downloads" --clear-readonly` |
| health / history | `health` or `history` |
| manual | `manual --output "NestTidy.pdf"` |

plan/copy/move/duplicates/dedupe accept `--report-dir`; plan accepts `--output-root`; move accepts `--no-elevate`. CLI move creates a new plan, so inspect with plan first. CLI dedupe rescans, and group IDs may change; omitting --groups selects all eligible groups. Confirm the scope before proceeding. Current CLI deletion modes are recycle and purge, not the former quarantine option.

Plan reports include `summary.md`, `summary.json`, `mappings.csv` and `issues.csv`; the CLI duplicate scan exports `duplicate_report.csv`. CLI reports default to task subdirectories under `理巢报告` in the working directory. Web in-place transaction logs are under `理巢报告` beside the source directory; use the actual returned log path.

## Development and validation

Python standard-library HTTP server and plain HTML/CSS/JavaScript; no frontend build or third-party runtime dependencies. Browser regression tests are developer-only and use Node.js 22 and Chromium.

```powershell
python -m unittest discover -s tests -v
python -m benchmarks.plan_bench --fixture-files 20000 --shape multi --repeat 3
python -m benchmarks.plan_bench --fixture-files 200000 --shape multi --repeat 3 --pages 20 --profile --memory
node benchmarks/ui_smoke.mjs ui-screenshots
```

Benchmarks use isolated temporary fixtures by default. Use --root for read-only planning on a real directory. Heavy benchmarks are opt-in, not part of the routine unit suite.

As of v1.8.20, Windows Python 3.11 and 3.13 each ran 301 unittest cases (one skipped per version), and the Chromium smoke test passed. See [Windows regression](https://github.com/NopantsNobra/NetTidy/actions/runs/45388703069) and [browser regression](https://github.com/NopantsNobra/NetTidy/actions/runs/45388702957).

## Project documentation

- [Detailed introduction](docs/理巢_详细介绍.md) — design and architecture (Chinese)
- [Product manual](docs/理巢_产品开发手册_v1.0.md) — product scope and design history (Chinese)
- [Rule reference](docs/规则对照表.md) — sole authoritative rule specification, currently `P0-R0-R5-R6-v4` (Chinese)
- [Roadmap](ROADMAP.md) — acceptance status, performance experiments and outstanding work (Chinese)
- [Previous full version history](https://github.com/NopantsNobra/NetTidy/blob/b9e55c7da395e1fea14a7e58372d3a876007c805/README.md#十版本记录) · [Commit history](https://github.com/NopantsNobra/NetTidy/commits/main)

The source repository is publicly visible, but a formal LICENSE has not been selected. Public visibility does not itself grant an open-source license.
