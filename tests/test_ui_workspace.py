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
                       'min-width:800px;table-layout:fixed', '/api/plan/rows', 'data-more=',
                       'limit:200'):
            self.assertIn(marker, INDEX_HTML)
