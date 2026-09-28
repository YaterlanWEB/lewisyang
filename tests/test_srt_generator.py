from __future__ import annotations

import unittest

from srt_generator import (
    TimedChar,
    _allocate_by_character_alignment,
    _fill_missing_starts,
    _norm_text,
)


class SrtGeneratorTests(unittest.TestCase):
    def test_windows_traditional_chinese_normalization(self) -> None:
        self.assertEqual(_norm_text("海絲緣起（和聲）在泉州"), "海丝缘起在泉州")

    def test_character_alignment_survives_wrong_and_extra_words(self) -> None:
        lyrics = ["海丝缘起在泉州", "衣冠南渡遇刺桐", "五洲相逢"]
        heard_text = "前奏海是缘起在泉舟衣冠难度遇刺桐五洲相逢尾声"
        heard = [TimedChar(char, index * 0.25, (index + 1) * 0.25) for index, char in enumerate(heard_text)]

        lines = _allocate_by_character_alignment(lyrics, heard, len(heard) * 0.25, None)

        self.assertIsNotNone(lines)
        assert lines is not None
        self.assertEqual([line.text for line in lines], lyrics)
        self.assertTrue(all(left.start < right.start for left, right in zip(lines, lines[1:])))
        self.assertAlmostEqual(lines[0].start, 0.5, delta=0.6)

    def test_missing_line_starts_after_previous_sung_line(self) -> None:
        lyrics = ["第一句歌词", "识别漏掉的第二句", "第三句歌词"]
        starts = [10.0, None, 18.0]
        raw_ends = [13.5, None, 20.0]

        filled = _fill_missing_starts(lyrics, starts, 24.0, raw_ends)

        self.assertGreaterEqual(filled[1], 13.5)
        self.assertLess(filled[1], 18.0)

if __name__ == "__main__":
    unittest.main()
