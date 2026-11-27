"""UI contracts: keep behavior IDs, distinguish risk and preserve keyboard access."""
import unittest

from nesttidy.ui import INDEX_HTML


class WorkspaceVisualContractTests(unittest.TestCase):
    def test_semantic_regions_and_input_labels(self):
        for marker in ('aria-labelledby="workspaceTitle"', 'for="root"', 'for="mode"',
                       'for="output"', 'aria-live="polite"', 'aria-controls="panel-preview"',
                       'role="tabpanel"', 'aria-label="整理前后对照"'):
            self.assertIn(marker, INDEX_HTML)

    def test_keyboard_and_risk_feedback(self):
        for marker in ('button:focus-visible', 'prefers-reduced-motion:reduce',
                       'aria-pressed', 't.tabIndex = on ? 0 : -1',
                       'if(opts.danger) noBtn.focus()', "if(e.key==='Tab')",
                       "btn.classList.toggle('danger', !isCopy && !planExecuted)",
                       "confirmText:'确认原地整理', danger:true"):
            self.assertIn(marker, INDEX_HTML)

    def test_large_preview_keeps_bounded_scroll_and_pagination(self):
        for marker in ('max-height:60vh', 'overflow:auto', '.scrollbox th{position:sticky',
                       'min-width:800px;table-layout:fixed', '/api/plan/rows', 'PREVIEW_PAGE = 80',
                       'limit:PREVIEW_PAGE'):
            self.assertIn(marker, INDEX_HTML)

    def test_execution_choice_precedes_first_plan_and_idle_progress_is_compact(self):
        self.assertLess(INDEX_HTML.index('id="execMode"'), INDEX_HTML.index('onclick="makePlan()"'))
        self.assertEqual(INDEX_HTML.count('id="execMode"'), 1)
        self.assertIn('.task-progress.idle{display:none}', INDEX_HTML)
        self.assertIn("fill.closest('.task-progress')", INDEX_HTML)

    def test_duplicate_render_uses_single_group_index(self):
        self.assertIn('const rowsByGroup = new Map()', INDEX_HTML)
        self.assertIn('const deletableSet = new Set(g.deletable_paths || [])', INDEX_HTML)
        self.assertNotIn('data.rows.filter(r=>r.duplicate_group_id===g.duplicate_group_id)', INDEX_HTML)

    def test_preview_has_bounded_pages_and_stale_request_guard(self):
        for marker in ('GROUP_OPTIONS = 100', "view:'summary'", 'filter:kind',
                       'request!==previewRequest', 'id="pageNext"', 'id="groupSelect"'):
            self.assertIn(marker, INDEX_HTML)
        self.assertNotIn('g.rows = g.rows.concat', INDEX_HTML)

    def test_duplicate_page_bounds_and_selection_independence(self):
        for marker in ('const DUP_PAGE=80', 'rows.slice(dupOffset,dupOffset+DUP_PAGE)',
                       'dupState.selected', 'view:\'bounded\'', '源目录已改变，请重新诊断重复文件',
                       '删除本组已选副本', '全选已加载可删副本'):
            self.assertIn(marker, INDEX_HTML)
        self.assertNotIn("scope.querySelectorAll('.delbox:checked')", INDEX_HTML)

    def test_readonly_result_tables_are_bounded(self):
        for marker in ('const RESULT_PAGE=80', 'rows.slice(offset,offset+RESULT_PAGE)',
                       "mountPagedRows('folders'", "mountPagedRows('issues'",
                       "mountPagedRows('dupResult'", "mountPagedTable('repairResult'",
                       "mountPagedTable('execResult'", "mountPagedRows(fromHistory?'historyUndoResult':'execResult'",
                       "mountPagedRows('history'", '仅加载 '):
            self.assertIn(marker, INDEX_HTML)
        self.assertNotIn('v.folders.map(', INDEX_HTML)
        self.assertNotIn('plan.issues.map(', INDEX_HTML)
