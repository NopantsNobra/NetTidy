"""0.6.0 新能力的三条接缝：可读视图、两阶段复核、重复副本删除。

三条都是「页面点了按钮就该发生」的行为，所以断言写在行为层：
视图必须给出相对路径与中文原因；复核必须把首尾相同中段不同的文件剔出去；
删除必须只删已复核副本，并留下可撤销的隔离副本与事务日志。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from nesttidy.duplicate import CHUNK, PHASE1_METHOD, detect_duplicates, full_sha256, group_rows, summarize
from nesttidy.executor import DeleteEntry, delete_duplicates
from nesttidy.planner import build_plan
from nesttidy.recovery import undo_transaction
from nesttidy.reports import plan_display_stats, readable_plan_view
from nesttidy.web import Handler


def touch(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ReadableViewTests(unittest.TestCase):
    def test_shell_chain_becomes_relative_before_after(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "电影A" / "百度网盘" / "解压后" / "正文" / "01.mp4", "a")

            view = plan_display_stats(build_plan(str(root), "batch"))["readable"]

            self.assertIn("换位置", view["headline"])
            self.assertEqual(view["groups"][0]["root_name"], "电影A")
            row = view["groups"][0]["rows"][0]
            self.assertEqual(row["before"], str(Path("百度网盘") / "解压后" / "正文" / "01.mp4"))
            self.assertEqual(row["after"], "01.mp4")
            self.assertGreaterEqual(row["depth"], 3)
            self.assertEqual(row["action_code"], "move")
            self.assertTrue(row["changed"])
            self.assertIn("资源根内直链穿透", row["rules"])
            self.assertIn("壳", row["why"])
            self.assertTrue(view["folders"], "提走后变空的壳目录必须单独列出")
            self.assertEqual(view["counts"]["moved"], 1)
            self.assertTrue(any(card["label"] == "会换位置的文件" for card in view["cards"]))

    def test_readable_view_marks_renamed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "电影A" / "百度网盘" / "解压后" / "正文" / "01.mp4", "a")
            touch(root / "电影A" / "百度网盘" / "解压后" / "花絮" / "cover.jpg", "b")
            touch(root / "电影A" / "百度网盘" / "解压后" / "预告" / "cover.jpg", "c")

            view = readable_plan_view(build_plan(str(root), "batch"))

            renamed = [row for group in view["groups"] for row in group["rows"] if row["renamed"]]
            self.assertEqual(len(renamed), 2, "撞名改名的文件必须被逐个标出")
            self.assertEqual({row["after"] for row in renamed}, {"花絮-cover.jpg", "预告-cover.jpg"})
            for row in renamed:
                self.assertIn("前缀", row["why"])
                self.assertIn("撞名重命名", row["rules"])
                self.assertTrue(row["changed"])
            self.assertEqual(view["counts"]["renamed"], 2)

    def test_view_is_pure_read_of_frozen_plan(self) -> None:
        """视图不得改动计划字段，否则冻结计划的报告会与执行行为脱钩。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "电影A" / "百度网盘" / "解压后" / "01.mp4", "a")
            plan = build_plan(str(root), "batch")
            before = json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True)

            readable_plan_view(plan)

            self.assertEqual(before, json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True))


class TwoPhaseDuplicateTests(unittest.TestCase):
    @staticmethod
    def _pair(base: Path, middle_a: bytes, middle_b: bytes) -> tuple[Path, Path]:
        head, tail = b"A" * CHUNK, b"C" * CHUNK
        first, second = base / "A" / "same.bin", base / "B" / "same.bin"
        first.parent.mkdir(parents=True, exist_ok=True)
        second.parent.mkdir(parents=True, exist_ok=True)
        first.write_bytes(head + middle_a + tail)
        second.write_bytes(head + middle_b + tail)
        return first, second

    def test_same_head_and_tail_but_different_middle_is_rejected(self) -> None:
        """标准初筛只看首尾 1MB；中段不同必须靠第二遍全量哈希剔除，否则会误删。"""
        with tempfile.TemporaryDirectory() as tmp:
            self._pair(Path(tmp), b"\x00" * CHUNK, b"\x01" * CHUNK)

            rows = detect_duplicates(tmp, "standard", workers=2)
            stats = summarize(rows)

            self.assertEqual(stats["confirmed_groups"], 0)
            self.assertEqual(stats["deletable_files"], 0, "复核不通过的文件绝不能进入可删集合")
            self.assertEqual(stats["recheck_rejected_groups"], 1)
            self.assertTrue(all(not row.verified for row in rows))
            self.assertTrue(all(row.duplicate_group_id.endswith("-recheck") for row in rows))
            self.assertTrue(all("复核剔除" in row.conflict_note for row in rows))

    def test_verified_twins_are_deletable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first, second = self._pair(Path(tmp), b"\x00" * CHUNK, b"\x00" * CHUNK)
            rows = detect_duplicates(tmp, "standard", workers=2)
            groups = group_rows(rows)

            self.assertEqual(len(groups), 1)
            self.assertTrue(groups[0].verified)
            self.assertEqual(len(groups[0].deletable_paths), 1)
            self.assertEqual(groups[0].verified_hash, full_sha256(first))
            self.assertEqual(Path(groups[0].keep_path).resolve(), first.resolve(), "保留项取层级最浅者")
            self.assertEqual(summarize(rows)["reclaimable_bytes"], first.stat().st_size)

    def test_without_second_phase_rows_are_flagged_unverified(self) -> None:
        """--no-verify 是显式跳过复核：可删集合保留，但每行必须标明未复核。"""
        with tempfile.TemporaryDirectory() as tmp:
            self._pair(Path(tmp), b"\x00" * CHUNK, b"\x00" * CHUNK)

            rows = detect_duplicates(tmp, "standard", workers=2, verify=False)
            groups = group_rows(rows)

            self.assertTrue(all(not row.verified for row in rows))
            self.assertTrue(all("未做第二遍复核" in row.reason for row in rows))
            self.assertTrue(all(row.verify_method == "未启用" for row in rows))
            self.assertEqual(len(groups[0].deletable_paths), 1)
            self.assertIn("删除前会校验全文哈希", groups[0].note)

    def test_fast_phase_one_false_positive_is_rescued_by_phase_two(self) -> None:
        """fast 只比「大小 + 文件名」，同名同大小不同内容必被误判——第二遍要把它救回来。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            touch(base / "A" / "cover.jpg", "aaaa")
            touch(base / "B" / "cover.jpg", "bbbb")

            self.assertEqual(len(detect_duplicates(tmp, "fast", workers=2, verify=False)), 2)
            stats = summarize(detect_duplicates(tmp, "fast", workers=2, verify=True))

            self.assertEqual(stats["deletable_files"], 0)
            self.assertEqual(stats["recheck_rejected_groups"], 1)


class DeleteDuplicateTests(unittest.TestCase):
    def _twins(self, base: Path) -> list[DeleteEntry]:
        root = base / "资源"
        touch(root / "A" / "same.txt", "same-content")
        touch(root / "B" / "same.txt", "same-content")
        group = group_rows(detect_duplicates(str(root), "deep"))[0]
        return [DeleteEntry(path=path, keep_path=group.keep_path, expected_sha256=group.verified_hash) for path in group.deletable_paths]

    def test_recycle_bin_keeps_one_copy_and_is_not_auto_undoable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            entries = self._twins(base)
            victim, keep = Path(entries[0].path), Path(entries[0].keep_path)
            log = base / "delete_log.jsonl"

            result = delete_duplicates(str(base / "资源"), entries, mode="recycle", transaction_log=str(log))

            self.assertEqual((result.recycled, result.failed, result.freed_bytes), (1, 0, 0))
            self.assertFalse(victim.exists(), "副本必须已经从原位置消失")
            self.assertTrue(keep.exists(), "每组必须保留一份")

            undo = undo_transaction(str(log))

            self.assertEqual([row.status for row in undo], ["skipped"])
            self.assertIn("回收站", undo[0].message)
            self.assertFalse(victim.exists(), "回收站还原由资源管理器完成，程序不代劳")

    def test_purge_requires_confirm_and_frees_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            entries = self._twins(base)
            with self.assertRaises(ValueError):
                delete_duplicates(str(base / "资源"), entries, mode="purge")
            result = delete_duplicates(str(base / "资源"), entries, mode="purge", confirm=True, transaction_log=str(base / "log.jsonl"))

            self.assertEqual(result.deleted, 1)
            self.assertGreater(result.freed_bytes, 0)
            self.assertFalse(Path(entries[0].path).exists())

    def test_refuses_keep_as_victim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            entries = self._twins(base)
            bad = [DeleteEntry(path=entries[0].keep_path, keep_path=entries[0].keep_path)]
            with self.assertRaises(ValueError):
                delete_duplicates(str(base / "资源"), bad)

    def test_refuses_out_of_root_and_changed_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            entries = self._twins(base)
            root = base / "资源"
            # 越界：把根收窄到 A，那么 A 之外的副本不许删
            other = DeleteEntry(path=str(root / "B" / "same.txt"), keep_path=str(root / "A" / "same.txt"))
            outside = delete_duplicates(str(root / "A"), [other])
            self.assertEqual(outside.skipped, 1)
            self.assertEqual(outside.issues[0].code, "OUT_OF_ROOT")

            victim = Path(entries[0].path)
            victim.write_text("changed-content", encoding="utf-8")
            changed = delete_duplicates(str(root), entries, transaction_log=str(base / "log.jsonl"))
            self.assertEqual(changed.skipped, 1)
            self.assertEqual(changed.issues[0].code, "NOT_DUPLICATE")
            self.assertTrue(victim.exists(), "内容变了就不许删")

    def test_missing_keep_blocks_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            entries = self._twins(base)
            Path(entries[0].keep_path).unlink()

            result = delete_duplicates(str(base / "资源"), entries)

            self.assertEqual(result.issues[0].code, "KEEP_MISSING")
            self.assertTrue(Path(entries[0].path).exists())


class RecycleFallbackTests(unittest.TestCase):
    """非 Windows 没有回收站：必须按行失败说清原因，不许半路炸掉整批，也不许悄悄改成删除。"""

    @unittest.skipUnless(os.name != "nt", "此用例只覆盖非 Windows 的降级路径；Windows 走真实回收站")
    def test_recycle_on_non_windows_fails_per_row_without_aborting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "资源"
            touch(root / "A" / "same.txt", "same-content")
            touch(root / "B" / "same.txt", "same-content")
            group = group_rows(detect_duplicates(str(root), "deep"))[0]
            entries = [
                DeleteEntry(path=path, keep_path=group.keep_path, expected_sha256=group.verified_hash)
                for path in group.deletable_paths
            ]
            victim = Path(entries[0].path)

            result = delete_duplicates(str(root), entries, mode="recycle", transaction_log=str(base / "log.jsonl"))

            self.assertEqual(result.failed, 1, "回收站不可用必须记为失败，而不是抛异常中断整批")
            self.assertEqual(result.rows[0]["code"], "EXECUTION_FAILED")
            self.assertIn("回收站", result.rows[0]["message"])
            self.assertTrue(victim.exists(), "回收站不可用时不得悄悄降级为直接删除")


class DuplicatePayloadTests(unittest.TestCase):
    def test_payload_carries_groups_for_delete_buttons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            touch(Path(tmp) / "A" / "same.txt", "same")
            touch(Path(tmp) / "B" / "same.txt", "same")
            # 直接调用真实处理器：诊断成功会记住「上次的源目录」，配置必须落在临时目录而非进程 CWD。
            previous_cwd = Path.cwd()
            os.chdir(tmp)
            try:
                payload = Handler.detect_duplicates_payload(None, {"selected_root": tmp, "level": "standard", "verify": True})
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(payload["phase1_label"], PHASE1_METHOD["standard"])
            self.assertTrue(payload["verify"])
            self.assertEqual(payload["stats"]["deletable_files"], 1)
            self.assertEqual(len(payload["groups"][0]["deletable_paths"]), 1)
            self.assertTrue(payload["groups"][0]["verified_hash"])
            self.assertEqual(payload["selected_root"], str(Path(tmp).resolve()))
            self.assertTrue(payload["rows"])


class DuplicateTruncationDisclosureTests(unittest.TestCase):
    """v1.5.1 截断披露缺口：可删路径与 rows 都截到 400，页面必须说清「只显示前 X 条，共 Y 条」。"""

    def setUp(self) -> None:
        # 诊断成功会写「上次的源目录」到 cwd；测试必须与真实启动一样落在临时目录，不污染仓库根。
        self._old_cwd = Path.cwd()
        self._tmp_cwd = tempfile.TemporaryDirectory()
        os.chdir(self._tmp_cwd.name)
        self.addCleanup(self._restore_cwd)

    def _restore_cwd(self) -> None:
        os.chdir(self._old_cwd)
        self._tmp_cwd.cleanup()

    def _payload(self, copies: int) -> dict:
        """构造 1 个保留项 + copies 个全同副本：可删项与 rows 都超过 DUPLICATE_ROW_LIMIT。"""
        root = Path(self._tmp_cwd.name) / "资源"
        touch(root / "保留" / "same.bin", "dup-content")
        for index in range(copies):
            touch(root / f"副本{index:04d}" / "same.bin", "dup-content")
        return Handler.detect_duplicates_payload(None, {"selected_root": str(root), "level": "deep", "verify": True})

    def test_truncated_payload_carries_true_totals(self) -> None:
        payload = self._payload(405)
        group = payload["groups"][0]

        self.assertIn("deletable_total", group)
        self.assertIn("row_total", payload)
        self.assertEqual(len(group["deletable_paths"]), 400)
        self.assertEqual(group["deletable_total"], 405)
        self.assertEqual(len(payload["rows"]), 400)
        self.assertEqual(payload["row_total"], 406)

    def test_untruncated_payload_has_no_gap_to_disclose(self) -> None:
        payload = self._payload(2)
        group = payload["groups"][0]

        self.assertEqual(group["deletable_total"], len(group["deletable_paths"]))
        self.assertEqual(payload["row_total"], len(payload["rows"]))

    def test_page_discloses_both_truncated_totals(self) -> None:
        from nesttidy.ui import INDEX_HTML

        # 披露函数：只在 total>shown 时输出，未截断返回空串，不给误导性提示。
        self.assertIn("function dupTruncNote(", INDEX_HTML)
        self.assertIn("total>shown", INDEX_HTML)
        self.assertIn("这里只显示前 ", INDEX_HTML)
        self.assertIn("，共 ", INDEX_HTML)
        # 两处调用点分别接在可删组与 rows 的真实字段上，X/Y 才是下发数与总数。
        # 必须匹配真实执行语句而非任意子串：注释掉调用后残留的 `// dupTruncNote(...)` 不能算通过。
        self.assertRegex(INDEX_HTML, r"(?m)^\s*html \+= dupTruncNote\(g\.deletable_paths\.length, g\.deletable_total,")
        self.assertRegex(INDEX_HTML, r"(?m)^\s*html \+= dupTruncNote\(data\.rows\.length, data\.row_total,")


class BlockFilterTests(unittest.TestCase):
    """1.0.0：只显示会动的资源包——筛选必须作用在包这一级，不能只藏行。"""

    def _plan(self, tmp: str) -> dict:
        root = Path(tmp) / "素材"
        touch(root / "包A" / "外壳" / "正文" / "a.mp4", "aaa")
        touch(root / "包B" / "b1.mp4", "bbb")
        touch(root / "包B" / "b2.mp4", "ccc")
        return plan_display_stats(build_plan(str(root), mode="batch"))["readable"]

    def test_each_group_carries_its_own_change_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            view = self._plan(tmp)
            by_name = {group["root_name"]: group for group in view["groups"]}

            self.assertEqual(by_name["包A"]["moved"], 1)
            self.assertEqual(by_name["包B"]["moved"], 0)
            self.assertEqual(sum(1 for row in by_name["包B"]["rows"] if row["changed"]), 0)

    def test_page_marks_group_blocks_so_filter_can_hide_whole_packages(self) -> None:
        from nesttidy.ui import INDEX_HTML

        self.assertIn('class="gblock"', INDEX_HTML)
        self.assertIn('data-changed="', INDEX_HTML)
        self.assertIn('data-renamed="', INDEX_HTML)
        self.assertIn("block.dataset[filterMode]", INDEX_HTML)
        self.assertIn("previewEmpty", INDEX_HTML)


class ScriptHygieneTests(unittest.TestCase):
    """页面脚本一旦有语法错误，整页按钮全失效——这两条守住这类回归。"""

    def test_template_has_no_single_backslash_escapes(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "nesttidy" / "ui.py").read_text(encoding="utf-8")
        stray = re.findall(r"(?<!\\)\\([ntr0-7uxabfv'\"])", source)

        self.assertEqual(stray, [], f"ui.py 模板里出现单反斜杠转义：{stray}（在 Python 字符串里会被吃掉，JS 侧就断行了）")

    def test_no_raw_newlines_inside_page_script_strings(self) -> None:
        from nesttidy.ui import INDEX_HTML

        script = INDEX_HTML[INDEX_HTML.index("<script>") + 8 : INDEX_HTML.index("</script>")]

        # v1.4.0：原生 confirm 换成页内模态后，确认文案用 <br> 分行，
        # 脚本字符串里不允许出现裸换行（含 "\n\n"）这一约束不变。
        self.assertNotIn("\n\n会换位置", script)
        self.assertNotIn("\\n\\n", script)
        self.assertIn("<br>会清掉的空文件夹", script)
        self.assertIn("showModal(", script)
        self.assertNotIn("undoRun(''", script)
        self.assertIn("button[data-tx]", script)


class ProgressAndRepairTests(unittest.TestCase):
    """1.2.0：执行与权限修复都要有进度反馈；权限修复只列异常项。"""

    def setUp(self) -> None:
        # 修复成功会把「上次的源目录」写进启动目录的 理巢配置.json，测试必须与真实启动一样落在临时目录里。
        self._old_cwd = Path.cwd()
        self._tmp_cwd = tempfile.TemporaryDirectory()
        os.chdir(self._tmp_cwd.name)
        self.addCleanup(self._restore_cwd)

    def _restore_cwd(self) -> None:
        os.chdir(self._old_cwd)
        self._tmp_cwd.cleanup()

    def _serve(self):
        from http.server import ThreadingHTTPServer
        import threading

        from nesttidy import web

        server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return port

    @staticmethod
    def _post(port: int, path: str, body: dict) -> dict:
        import urllib.request

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _get(port: int, path: str) -> dict:
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_executor_calls_progress_for_every_operation(self) -> None:
        from nesttidy.executor import execute_plan

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "素材"
            touch(root / "包A" / "壳" / "a.mp4", "aaa")
            touch(root / "包A" / "壳" / "b.mp4", "bbb")
            plan = build_plan(str(root), mode="batch")
            seen: list[tuple[int, int]] = []

            execute_plan(plan, "move", confirm_move=True, transaction_log=str(Path(tmp) / "tx.jsonl"),
                         progress=lambda done, total, label="": seen.append((done, total)))

            self.assertTrue(seen)
            self.assertEqual(seen[-1][0], seen[-1][1])
            self.assertEqual([done for done, _ in seen], sorted(done for done, _ in seen))

    def test_progress_placeholder_is_returned_before_any_work(self) -> None:
        port = self._serve()

        slot = self._get(port, "/api/progress?key=never-started")

        self.assertEqual(slot, {"phase": "", "status": "running", "done": 0, "total": 0, "label": "", "note": "", "updated_at": ""})

    def test_repair_lists_only_anomalies_and_reports_counts(self) -> None:
        from unittest import mock

        from nesttidy.repair import RepairResult

        port = self._serve()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "素材"
            touch(root / "a.txt", "a")
            touch(root / "b.txt", "b")
            bad = str((root / "b.txt").resolve())

            def fake(path):
                if str(Path(path).resolve()) == bad:
                    return RepairResult(str(path), "clear_readonly", "failed", "模拟失败")
                return RepairResult(str(path), "clear_readonly", "ok", "已确保当前用户可写位")

            with mock.patch("nesttidy.repair.clear_readonly_attr", side_effect=fake):
                data = self._post(port, "/api/repair", {"selected_root": str(root), "clear_readonly": True, "progress_key": "p-repair"})

            self.assertEqual(data["checked"], 3)  # 素材 + a.txt + b.txt
            self.assertEqual(data["passed"], 2)
            self.assertEqual([row["status"] for row in data["rows"]], ["failed"])
            self.assertEqual(data["rows"][0]["path"], bad)

    def test_page_has_progress_bars_for_execute_and_repair(self) -> None:
        from nesttidy.ui import INDEX_HTML

        for marker in ('id="execFill"', 'id="repairFill"', "trackProgress(", "/api/progress?key=", "正常项略"):
            self.assertIn(marker, INDEX_HTML)

    def test_duplicate_diagnosis_and_delete_report_progress(self) -> None:
        from nesttidy.duplicate import detect_duplicates

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "素材"
            touch(root / "A" / "same.txt", "dup-content")
            touch(root / "B" / "same.txt", "dup-content")
            touch(root / "C" / "other.txt", "other")
            seen: list[tuple[int, int]] = []

            rows = detect_duplicates(str(root), "standard", progress=lambda done, total, label="", phase=None: seen.append((done, total)))
            self.assertTrue(seen, "诊断过程必须上报进度")
            self.assertEqual(seen[-1][0], seen[-1][1])
            group = group_rows(rows)[0]

            deleted: list[tuple[int, int]] = []
            entries = [DeleteEntry(path=path, keep_path=group.keep_path, expected_sha256=group.verified_hash) for path in group.deletable_paths]
            delete_duplicates(str(root), entries, mode="recycle", transaction_log=str(Path(tmp) / "log.jsonl"),
                              progress=lambda done, total, label="": deleted.append((done, total)))
            self.assertEqual(deleted, [(1, 1)])

    def test_reveal_endpoint_rejects_missing_path(self) -> None:
        from urllib.error import HTTPError

        port = self._serve()
        with self.assertRaises(HTTPError) as ctx:
            self._post(port, "/api/reveal", {"path": str(Path(tempfile.gettempdir()) / "理巢不存在_xyz"), "select": True})
        self.assertEqual(ctx.exception.code, 400)

    def test_page_offers_explorer_jumps_for_duplicates_and_folders(self) -> None:
        from nesttidy.ui import INDEX_HTML

        self.assertIn('id="dupFill"', INDEX_HTML)
        self.assertIn('id="dupProg"', INDEX_HTML)
        self.assertIn("data-pick=", INDEX_HTML)
        self.assertIn("data-open=", INDEX_HTML)
        self.assertIn("'/api/reveal'", INDEX_HTML)
        self.assertIn("addEventListener('contextmenu'", INDEX_HTML)
        self.assertIn("addEventListener('dblclick'", INDEX_HTML)
        self.assertIn("移入回收站", INDEX_HTML)
        self.assertNotIn('value="quarantine"', INDEX_HTML)

    def test_each_block_puts_its_controls_above_its_results(self) -> None:
        from nesttidy.ui import INDEX_HTML

        pairs = [
            ('id="filters"', 'id="preview"'),
            ('id="execMode"', 'id="preview"'),
            ('id="execBtn"', 'id="preview"'),
            ('id="dupLevel"', 'id="dupResult"'),
            ('id="dupTools"', 'id="dupResult"'),
            ('id="fixReadonly"', 'id="repairResult"'),
        ]
        for control, result in pairs:
            self.assertLess(INDEX_HTML.index(control), INDEX_HTML.index(result), f"{control} 必须在 {result} 之前")

    def test_toolbars_are_sticky(self) -> None:
        from nesttidy.ui import INDEX_HTML

        self.assertIn(".bar{position:sticky", INDEX_HTML)
        self.assertEqual(INDEX_HTML.count('class="bar"'), 5)  # 预览工具 / 筛选+执行 / 重复诊断 / 权限修复 / 诊断信息
        self.assertNotIn('id="execSection"', INDEX_HTML)  # 执行按钮已并入对照表顶部工具栏

    def test_four_blocks_are_tabs_and_long_tables_scroll_in_place(self) -> None:
        from nesttidy.ui import INDEX_HTML

        self.assertIn('<div class="tabs" id="tabs">', INDEX_HTML)
        for tab in ("preview", "dup", "repair", "diag"):
            self.assertIn(f'data-tab="{tab}"', INDEX_HTML)
            self.assertIn(f'id="panel-{tab}"', INDEX_HTML)
        self.assertIn('<div class="panel" id="panel-preview">', INDEX_HTML)  # 预览默认可见
        self.assertEqual(INDEX_HTML.count('class="panel hidden"'), 3)  # 另外三块默认收起
        # 长表格必须关在自身滚动容器里，否则会把后面的板块推到几屏之外
        for box in ('id="preview" class="scrollbox"', 'id="dupResult" class="scrollbox"', 'id="issues" class="scrollbox"'):
            self.assertIn(box, INDEX_HTML)
        self.assertIn(".scrollbox{max-height:58vh", INDEX_HTML)
        self.assertIn(".scrollbox th{position:sticky", INDEX_HTML)
        self.assertIn("function setTab(", INDEX_HTML)
        self.assertIn("setTab('preview')", INDEX_HTML)
        # 页面高度不得随表格行数线性增长：三张长表都不在 <main> 直接子级铺开
        self.assertLess(INDEX_HTML.index('id="tabs"'), INDEX_HTML.index('id="panel-preview"'))
        # 页签在最上面（header 之后、目录条之前）
        self.assertLess(INDEX_HTML.index('id="tabs"'), INDEX_HTML.index('id="root"'))

    def test_each_tab_carries_its_own_actions(self) -> None:
        from nesttidy.ui import INDEX_HTML

        def panel(name: str) -> str:
            start = INDEX_HTML.index(f'id="panel-{name}"')
            later = [INDEX_HTML.index(f'id="panel-{other}"') for other in ("preview", "dup", "repair", "diag")
                     if INDEX_HTML.index(f'id="panel-{other}"') > start]
            return INDEX_HTML[start:min(later) if later else len(INDEX_HTML)]

        need = {
            "preview": ("makePlan(", "确认执行", "executePlan("),
            "dup": ("runDuplicate(", "诊断重复文件", "dupTools"),
            "repair": ("runRepair(", "执行所选权限修复"),
            "diag": ("loadHistory(", "makePlan(true)", "刷新历史"),
        }
        for name, markers in need.items():
            body = panel(name)
            for marker in markers:
                self.assertIn(marker, body, f"{name} 页签缺少自己的动作入口 {marker}")


if __name__ == "__main__":
    unittest.main()

class LargePlanPagingTests(unittest.TestCase):
    """28 万个文件把浏览器卡死之后（v1.5.1）：一次只给一页，余下按需取。"""

    def setUp(self) -> None:
        self._old_cwd = Path.cwd()
        self._tmp_cwd = tempfile.TemporaryDirectory()
        os.chdir(self._tmp_cwd.name)
        self.addCleanup(self._restore_cwd)

    def _restore_cwd(self) -> None:
        os.chdir(self._old_cwd)
        self._tmp_cwd.cleanup()

    def _serve(self) -> int:
        from http.server import ThreadingHTTPServer
        import threading

        from nesttidy import web

        server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return port

    @staticmethod
    def _post(port: int, path: str, body: dict) -> dict:
        import urllib.request

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _big_plan(self, count: int = 300) -> tuple[str, dict]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "素材"
            for index in range(count):
                touch(root / "包A" / "壳" / f"{index:04d}.mp4", "x")
            port = self._serve()
            return str(root), self._post(port, "/api/plan", {"selected_root": str(root), "mode": "batch"})

    def test_plan_payload_never_ships_every_mapping(self) -> None:
        _, payload = self._big_plan(300)
        group = payload["display"]["readable"]["groups"][0]
        body = json.dumps(payload, ensure_ascii=False)

        self.assertNotIn("mappings", payload)
        self.assertEqual(payload["mapping_count"], 300)
        self.assertEqual(group["row_total"], 300)
        self.assertEqual(len(group["rows"]), 80, "一次最多下发 ROW_LIMIT 行")
        self.assertLess(len(body), 300_000, "28 万个文件时下发体必须被截断，否则浏览器直接卡死")

    def test_rows_endpoint_pages_the_rest(self) -> None:
        _, payload = self._big_plan(300)
        port = self._serve()
        plan_id = payload["plan_id"]
        root = payload["display"]["readable"]["groups"][0]["root_path"]

        page = self._post(port, "/api/plan/rows", {"plan_id": plan_id, "root": root, "offset": 80, "limit": 200})
        tail = self._post(port, "/api/plan/rows", {"plan_id": plan_id, "root": root, "offset": 280, "limit": 200})

        self.assertEqual(page["total"], 300)
        self.assertEqual(len(page["rows"]), 200)
        self.assertEqual(len(tail["rows"]), 20, "最后一页只给剩下的行，不越界")
        self.assertEqual(page["rows"][0]["before"], str(Path("壳") / "0080.mp4"))

    def test_page_limit_is_clamped(self) -> None:
        _, payload = self._big_plan(1200)
        port = self._serve()
        root = payload["display"]["readable"]["groups"][0]["root_path"]

        page = self._post(port, "/api/plan/rows", {"plan_id": payload["plan_id"], "root": root, "offset": 0, "limit": 9999})

        self.assertEqual(len(page["rows"]), 1000, "单页上限 1000 行，别让一次请求又把浏览器拖死")

    def test_unknown_plan_is_rejected(self) -> None:
        import urllib.error

        port = self._serve()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._post(port, "/api/plan/rows", {"plan_id": "nope", "root": "", "offset": 0, "limit": 10})

        self.assertEqual(caught.exception.code, 400)
        self.assertIn("计划不存在", caught.exception.read().decode("utf-8"))

    def test_page_template_renders_groups_lazily(self) -> None:
        from nesttidy.ui import INDEX_HTML

        self.assertIn("function loadMore(", INDEX_HTML)
        self.assertIn("data-more=", INDEX_HTML)
        self.assertIn("rowMoreHint(", INDEX_HTML)
        self.assertIn("id=\"more-", INDEX_HTML.replace("'", '"'))
        self.assertIn("trackProgress('planFill'", INDEX_HTML.replace('"', "'"))
