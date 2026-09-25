"""处理链（services/process.py）的单元测试——纯数组、零 Qt。

合成曲线（含已知峰 + 已知背景），断言的是**性质**而不是具体数字：
平滑必须守恒（峰面积不变、常数曲线不动）、窗口按度换算（换点数结果一致）、
裁剪只标空不改网格、链的顺序（锚点在裁剪前取样）真的成立。
"""
import unittest

import numpy as np

from xrd_toolkit.services import process


def _curve(n=300, lo=1.0, hi=8.0):
    """一条带高斯峰的合成曲线：常数背景 10 + 2.6°/5.0° 两个峰。"""
    tth = np.linspace(lo, hi, n)
    y = np.full_like(tth, 10.0)
    for center, height, width in ((2.6, 500.0, 0.06), (5.0, 120.0, 0.08)):
        y += height * np.exp(-0.5 * ((tth - center) / width) ** 2)
    return tth, y


class TestSmooth(unittest.TestCase):
    def test_constant_curve_is_unchanged(self):
        """常数曲线平滑后还是常数（边缘不补零 → 两端不会被压低）。"""
        tth = np.linspace(1.0, 8.0, 200)
        y = np.full_like(tth, 7.5)
        out = process.smooth_boxcar(tth, y, 0.5)
        self.assertTrue(np.allclose(out, 7.5), "两端出现了假的凹陷")

    def test_area_is_preserved_and_peak_flattens(self):
        """滑动平均守恒：总面积基本不变，峰高下降、峰变宽。"""
        tth, y = _curve()
        out = process.smooth_boxcar(tth, y, 0.3)
        self.assertAlmostEqual(float(out.sum()), float(y.sum()),
                               delta=float(y.sum()) * 0.01)
        i_peak = int(np.argmax(y))
        self.assertLess(out[i_peak], y[i_peak], "峰该被削矮")

    def test_window_is_given_in_degrees_not_points(self):
        """窗口按 2θ 给：同一个窗口在不同点数下削掉的高度应当接近。"""
        tth_a, y_a = _curve(n=300)
        tth_b, y_b = _curve(n=1200)
        a = process.smooth_boxcar(tth_a, y_a, 0.3)
        b = process.smooth_boxcar(tth_b, y_b, 0.3)
        drop_a = 1.0 - float(a.max() / y_a.max())
        drop_b = 1.0 - float(b.max() / y_b.max())
        self.assertAlmostEqual(drop_a, drop_b, delta=0.03,
                               msg=f"{drop_a:.3f} vs {drop_b:.3f}")

    def test_zero_window_returns_copy(self):
        tth, y = _curve()
        out = process.smooth_boxcar(tth, y, 0.0)
        self.assertTrue(np.array_equal(out, y))
        self.assertIsNot(out, y, "要返回新数组，别把入参交出去")

    def test_does_not_touch_input(self):
        tth, y = _curve()
        before = y.copy()
        process.smooth_boxcar(tth, y, 0.5)
        self.assertTrue(np.array_equal(y, before))


class TestSmoothMethods(unittest.TestCase):
    """两种平滑方法：滑动平均 vs Savitzky–Golay（SG 保峰）。"""

    def test_savgol_keeps_more_peak_than_boxcar(self):
        """同一个窗口，SG 削峰明显少于滑动平均（真数据上也是这个方向）。"""
        tth, y = _curve()
        box = process.smooth(tth, y, 0.3, "boxcar")
        sg = process.smooth(tth, y, 0.3, "savgol", 3)
        self.assertGreater(sg.max(), box.max(),
                           "SG 的峰该比滑动平均高（保峰）")

    def test_savgol_keeps_the_constant_background(self):
        """常数背景不该被 SG 弄出波纹（边缘用 interp 模式）。"""
        tth = np.linspace(1.0, 8.0, 300)
        y = np.full_like(tth, 5.0)
        out = process.smooth(tth, y, 0.3, "savgol", 3)
        self.assertTrue(np.allclose(out, 5.0, atol=1e-9))

    def test_dispatch_defaults_to_boxcar(self):
        tth, y = _curve()
        self.assertTrue(np.array_equal(
            process.smooth(tth, y, 0.3), process.smooth_boxcar(tth, y, 0.3)))
        self.assertTrue(np.array_equal(
            process.smooth(tth, y, 0.3, "savgol", 3),
            process.smooth_savgol(tth, y, 0.3, 3)))

    def test_extreme_order_does_not_crash(self):
        """阶数比窗口还大时不炸、也不乱抹：要么抬窗口，要么原样返回。"""
        tth, y = _curve(n=40)
        out = process.smooth(tth, y, 0.05, "savgol", 6)
        self.assertEqual(out.shape, y.shape)
        self.assertTrue(np.isfinite(out).all())

    def test_wrong_order_raises(self):
        """阶数为 0/负值是调用方的错——让它以清楚的方式失败，而不是静默不做事。"""
        tth, y = _curve(n=40)
        with self.assertRaises(Exception):
            process.smooth(tth, y, 0.2, "savgol", 0)


class TestCut(unittest.TestCase):
    def test_marks_only_the_window(self):
        tth, y = _curve()
        out = process.cut_ranges(tth, y, [(2.0, 3.0)])
        inside = (tth >= 2.0) & (tth <= 3.0)
        self.assertTrue(np.all(np.isnan(out[inside])), "区间内该是空值")
        self.assertTrue(np.all(np.isfinite(out[~inside])), "区间外不该动")
        self.assertEqual(out.shape, y.shape, "不删点：网格与长度都不变")

    def test_multiple_ranges_and_bad_input(self):
        tth, y = _curve()
        out = process.cut_ranges(tth, y, [(2.0, 3.0), (7.0, 8.0),
                                          (4.0, 4.0), (6.0, 5.0)])
        self.assertTrue(np.isnan(out[(tth >= 2) & (tth <= 3)]).all())
        self.assertTrue(np.isnan(out[(tth >= 7) & (tth <= 8)]).all())
        self.assertTrue(np.isfinite(out[(tth >= 4) & (tth <= 7)]).all(),
                        "起点 ≥ 终点的区间应当忽略")

    def test_empty_ranges_is_a_noop(self):
        tth, y = _curve()
        self.assertTrue(np.array_equal(process.cut_ranges(tth, y, []), y))
        self.assertTrue(np.array_equal(process.cut_ranges(tth, y, None), y))


class TestChain(unittest.TestCase):
    def _params(self, **over):
        base = {"mode": "off", "window_deg": 1.0, "blank_scale": 1.0,
                "anchors": [], "anchor_method": "linear", "clip": False,
                "smooth_deg": 0.0, "cut_ranges": []}
        base.update(over)
        return base

    def test_order_anchors_survive_the_cut(self):
        """链的顺序：锚点在裁剪前取样——锚点落进裁剪区也不会变成空值。"""
        tth, y = _curve()
        anchors = [[2.5, float(np.interp(2.5, tth, y))],      # 落在裁剪区里
                   [7.0, float(np.interp(7.0, tth, y))]]
        params = self._params(mode="anchor", anchors=anchors,
                              cut_ranges=[(2.0, 3.0)])
        out, base = process.apply_chain(tth, y, params)
        self.assertIsNotNone(base, "基线该算出来（锚点没被裁掉）")
        self.assertTrue(np.all(np.isfinite(base[tth < 2.0])))
        self.assertTrue(np.isnan(out[(tth >= 2.0) & (tth <= 3.0)]).all(),
                        "裁剪区在最后才被标空")

    def test_smooth_runs_before_the_cut(self):
        """平滑在裁剪前：切口两侧的点不掺入空值（切线处仍是有限值）。"""
        tth, y = _curve()
        params = self._params(smooth_deg=0.2, cut_ranges=[(2.0, 3.0)])
        out, _ = process.apply_chain(tth, y, params)
        keep = (tth < 2.0) | (tth > 3.0)
        self.assertTrue(np.all(np.isfinite(out[keep])))

    def test_chain_is_a_noop_without_options(self):
        tth, y = _curve()
        out, base = process.apply_chain(tth, y, self._params())
        self.assertIsNone(base)
        self.assertTrue(np.array_equal(out, y))

    def test_chain_parts_only_lists_active_ops(self):
        """没开的操作不进键：背景单独时 parts 为空（老产物键不变的根据）。"""
        self.assertEqual(process.chain_parts(self._params()), {})
        with_smooth = process.chain_parts(self._params(smooth_deg=0.15))
        self.assertEqual(with_smooth, {"smooth_deg": 0.15},
                         "默认方法（滑动平均）不写方法字段——老键逐位不变")
        with_sg = process.chain_parts(self._params(smooth_deg=0.15,
                                                  smooth_method="savgol",
                                                  smooth_order=3))
        self.assertEqual(with_sg, {"smooth_deg": 0.15,
                                   "smooth_method": "savgol",
                                   "smooth_order": 3})
        with_cut = process.chain_parts(self._params(cut_ranges=[(3.0, 2.0),
                                                               (7.0, 8.0)]))
        self.assertEqual(with_cut, {"cut_ranges": [[7.0, 8.0]]},
                         "起点 ≥ 终点的区间不进键，顺序无关")

    def test_chain_label_and_desc(self):
        settings = {"mode": "anchor", "window_deg": 2.0,
                    "anchors": [[1.0, 1.0]] * 5, "clip": True,
                    "smooth_deg": 0.15, "cut_ranges": [(2.0, 3.0)]}
        label = process.chain_label(settings)
        self.assertIn("锚点 5 个", label)
        self.assertIn("平滑 0.15°", label)
        self.assertIn("删 2–3°", label)
        desc = process.chain_desc(settings)
        self.assertIn("bg=anchor(n=5)/win=2/clip", desc)
        self.assertIn("smooth=boxcar/0.15°", desc)
        self.assertIn("cut=2–3°", desc)
        self.assertEqual(process.chain_desc(self._params()), "none")
        self.assertEqual(process.chain_label(self._params()), "未做处理")


if __name__ == "__main__":
    unittest.main()
