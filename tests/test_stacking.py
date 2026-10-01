"""错开叠放的行距（services/stacking）：瀑布图 / 对比堆叠 / CLI 共用的一处口径。

用户 2026-10-02："瀑布图……还是不行，改成行间距小一点，让峰明显一点"——
规则从"全场最大 × 0.7"换成"**第二高的那一行** × 0.7"。这里钉的是规则本身
（界面那侧由 TestNewViews 的瀑布用例与 TestCompareStackAndHeatLink 的堆叠
用例盯着）。
"""
import unittest

import numpy as np

from xrd_toolkit.services.stacking import (ROW_STEP_FACTOR, row_step)


class TestRowStep(unittest.TestCase):

    def test_uniform_rows_match_the_old_rule(self):
        """均匀样品（各行差不多强）：第二高 ≈ 最大 → 与老口径逐位相同。

        这条是"改成新规则不会反过来糊成一团"的护栏：没有离群值时，
        新老口径给的行距必须一样（老口径 = 全场最大 × 0.7）。
        """
        peaks = [30.0, 30.0, 30.0, 30.0]
        self.assertAlmostEqual(row_step(peaks), 30.0 * ROW_STEP_FACTOR)
        # 差一点点也算"均匀"：取第二高，行距只跟着第二高走
        peaks = [100.0, 99.0, 98.0]
        self.assertAlmostEqual(row_step(peaks), 99.0 * ROW_STEP_FACTOR)

    def test_one_outlier_does_not_set_the_row_height(self):
        """一个特别强的行（实测那批 LMFP：χ=165° 的主峰是第二名的 3.3 倍）
        不该把其余行压成平线——这正是换规则的原因。"""
        peaks = [48230.0, 14753.0, 2586.0, 1839.0]
        step = row_step(peaks)
        self.assertAlmostEqual(step, 14753.0 * ROW_STEP_FACTOR)
        self.assertLess(step, 48230.0 * ROW_STEP_FACTOR * 0.5,
                        "比按全场最大的老口径小一大截")
        # 中位那一行的峰该占到行高的两位数百分比（旧口径下只有 8%）
        self.assertGreater(2586.0 / step, 0.2)

    def test_two_rows_ignore_the_stronger_one(self):
        """只有两行时同样成立（对比图只勾两个文件）：不该让强的那一个
        决定弱的那一个看不见。"""
        self.assertAlmostEqual(row_step([3.0, 30.0]), 3.0 * ROW_STEP_FACTOR)

    def test_single_row_uses_itself(self):
        self.assertAlmostEqual(row_step([12.0]), 12.0 * ROW_STEP_FACTOR)

    def test_bad_values_are_dropped(self):
        """坏扇区（NaN / inf / ≤0）先丢掉，再取第二高。"""
        peaks = [np.nan, 20.0, np.inf, -5.0, 0.0, 10.0]
        self.assertAlmostEqual(row_step(peaks), 10.0 * ROW_STEP_FACTOR)

    def test_empty_returns_placeholder(self):
        """一条有效曲线都没有（空图）：返回 1.0 的占位行距，不炸也不返回 0。"""
        for peaks in ([], [np.nan], [0.0], [-1.0]):
            self.assertEqual(row_step(peaks), 1.0)


if __name__ == "__main__":
    unittest.main()
