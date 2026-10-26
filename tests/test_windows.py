from __future__ import annotations

import unittest

from nesttidy.rules import clean_name


class WindowsNameCleaningTests(unittest.TestCase):
    """Windows 专用文件名清洗回归。

    这些用例只测纯函数，不落盘——保留名与非法字符在 Windows 上根本创建不出来，
    任何落盘夹具都会静默失效（历史上 test_name_cleaning_then_conflict 就是这么废掉的）。
    """

    def test_reserved_device_names_get_prefix(self) -> None:
        for raw in ["CON", "con.txt", "PRN", "aux.log", "NUL", "COM1", "LPT9", "nul.tar.gz"]:
            with self.subTest(raw=raw):
                cleaned, traces = clean_name(raw)
                self.assertTrue(cleaned.startswith("_"), f"{raw} 未被清洗为 {cleaned}")
                self.assertIn("Windows 保留名添加前缀", traces)

    def test_non_reserved_similar_names_untouched(self) -> None:
        for raw in ["CONSOLE.txt", "console", "COMPUTER.docx", "LPT99.txt", "AUXILIARY.md"]:
            with self.subTest(raw=raw):
                cleaned, traces = clean_name(raw)
                self.assertEqual(cleaned, raw)
                self.assertEqual(traces, [])

    def test_illegal_characters_replaced(self) -> None:
        cleaned, traces = clean_name('a<b>c:d"e|f?g*h.txt')
        self.assertEqual(cleaned, "a_b_c_d_e_f_g_h.txt")
        self.assertIn("非法字符替换为下划线", traces)

    def test_control_characters_replaced(self) -> None:
        cleaned, traces = clean_name("bad\x01\x1fname.txt")
        self.assertEqual(cleaned, "bad__name.txt")
        self.assertIn("非法字符替换为下划线", traces)

    def test_trailing_dots_and_spaces_trimmed(self) -> None:
        cleaned, traces = clean_name("报告 . . ")
        self.assertEqual(cleaned, "报告")
        self.assertIn("去除尾随空格或点号", traces)

    def test_empty_after_trim_becomes_underscore(self) -> None:
        cleaned, traces = clean_name(" . ")
        self.assertEqual(cleaned, "_")
        self.assertIn("空文件名替换为下划线", traces)

    def test_long_chinese_name_truncated_keeps_extension(self) -> None:
        raw = "中" * 200 + ".mp4"
        cleaned, traces = clean_name(raw)
        self.assertEqual(len(cleaned), 180)
        self.assertTrue(cleaned.endswith(".mp4"))
        self.assertIn("...", cleaned)
        self.assertIn("过长文件名保留扩展名截断", traces)

    def test_name_at_limit_not_truncated(self) -> None:
        raw = "中" * 176 + ".mp4"
        cleaned, traces = clean_name(raw)
        self.assertEqual(len(raw), 180)
        self.assertEqual(cleaned, raw)
        self.assertEqual(traces, [])

    def test_cleaning_is_idempotent(self) -> None:
        for raw in ["CON.txt", 'a<b>.txt', "报告 . ", "中" * 200 + ".mp4"]:
            with self.subTest(raw=raw[:20]):
                once, _ = clean_name(raw)
                twice, traces = clean_name(once)
                self.assertEqual(once, twice, "清洗结果不稳定，二次清洗会再变")
                self.assertEqual(traces, [], "清洗结果仍可被再次清洗")


if __name__ == "__main__":
    unittest.main()
