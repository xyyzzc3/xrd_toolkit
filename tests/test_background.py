"""背景扣除纯函数（services/background.py）的单元测试（unittest，环境无 pytest）。

合成"背景 + 高峰"曲线验证算法的数学性质，不依赖真实数据：
  - SNIP：线性斜坡被精确保持、峰下能还原背景、基线恒不高于信号、
    窗口越大基线越低（min 迭代的单调性）、负值/短输入/非法参数；
  - 迭代包络：多项式型背景被还原、陡升段比 SNIP 更贴真值；
  - 手动锚点：折线过点、两端线性外推 + 夹到 ≥0、样条降级与平滑；
  - 网格重插（未覆盖区间返回 0）、减基线（负值保留 vs 截断）、
    分派器 compute_baseline 的各模式与异常。

运行：python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.services.background import (
    compute_baseline, estimate_baseline_sliding, estimate_baseline_snip,
    fit_anchor_baseline, interp_onto_grid, subtract_background)

TTH = np.linspace(0.0, 10.0, 2001)


def _gauss(x, mu, height, sigma):
    return height * np.exp(-0.5 * ((x - mu) / sigma) ** 2)


def _ramp_background():
    """缓升型真背景（低角高、随 2θ 平滑衰减），量级贴近真实 LMFP 数据。"""
    return 100.0 + 2000.0 * np.exp(-TTH / 2.0)


def _poly_background():
    """多项式型真背景——低阶多项式能精确表示，供包络法做紧容差断言。"""
    u = 10.0 - TTH
    return 50.0 + 5.0 * u ** 2 + 3.0 * u


def _with_peaks(bg, peaks=((3.0, 5000.0), (5.0, 2000.0), (7.0, 800.0)),
                sigma=0.06):
    y = bg.copy()
    for mu, h in peaks:
        y = y + _gauss(TTH, mu, h, sigma)
    return y


class TestSnipBaseline(unittest.TestCase):
    """SNIP 自动基线的数学性质。"""

    def test_constant_background_is_exact_fixed_point(self):
        """常数是 SNIP 的精确不动点（两端均值 = 中点，min 不改变它）。"""
        const = np.full_like(TTH, 750.0)
        base = estimate_baseline_snip(TTH, const, 2.0)
        np.testing.assert_allclose(base, const, rtol=1e-9, atol=1e-6)

    def test_linear_ramp_is_underestimated_documented_limitation(self):
        """标定测试（如实记录算法性质，不是"应该如此"）：log-log-sqrt
        压缩把 y 空间的线性斜坡变成 v 空间的凹陷 → 被明显削低。所以
        SNIP 的保真性取决于背景在压缩空间里凹不凹，并非"平滑就保得住"，
        这正是需要手动锚点模式兜底的原因。"""
        line = 200.0 + 300.0 * TTH
        base = estimate_baseline_snip(TTH, line, 2.0)
        self.assertTrue(np.all(base <= line + 1e-9))
        self.assertLess(base[1000], 0.6 * line[1000],
                        "线性斜坡中段应被明显削低（实测 1700 → 760）")

    def test_recovers_background_under_peaks(self):
        """峰中心处基线与真背景的差 << 峰高（峰被削掉、背景留下）。"""
        bg = _ramp_background()
        y = _with_peaks(bg)
        base = estimate_baseline_snip(TTH, y, 2.0)
        for mu, h in ((3.0, 5000.0), (5.0, 2000.0), (7.0, 800.0)):
            i = int(np.argmin(np.abs(TTH - mu)))
            self.assertLess(abs(base[i] - bg[i]), 0.05 * h,
                            f"2θ={mu}° 处基线应贴近真背景 {bg[i]:.1f}")

    def test_baseline_never_exceeds_signal(self):
        """基线处处 ≤ 曲线（min 迭代 + 单调逆变换的必然结果）。"""
        y = _with_peaks(_ramp_background())
        base = estimate_baseline_snip(TTH, y, 1.5)
        self.assertTrue(np.all(base <= y + 1e-9))

    def test_larger_window_gives_lower_baseline(self):
        """窗口越大削得越狠（迭代是 min 的累积，单调非增）。

        用**宽峰**（σ=0.3°）让窗口宽度真正起作用：窄峰（σ=0.06°）在
        0.5° 和 4.0° 两种半窗下都被完整削掉，峰中心结果一模一样，测不出
        差别（实测两者在 2θ=3° 处同为 546.81）。容差留 1e-6 是因为
        逆变换 exp(exp(v)-1) 在双指数上放大了 v 空间的浮点差。"""
        y = _with_peaks(_ramp_background(), sigma=0.3)
        small = estimate_baseline_snip(TTH, y, 0.5)
        big = estimate_baseline_snip(TTH, y, 4.0)
        self.assertTrue(np.all(big <= small + 1e-6))
        i = int(np.argmin(np.abs(TTH - 3.0)))
        self.assertLess(big[i], small[i] - 1.0, "宽峰应被大窗口削得更低")

    def test_negative_and_zero_input_stays_finite(self):
        """含负值的曲线（真实 tif 就有负值）不产生 NaN，基线非负。"""
        y = np.array([-50.0, 0.0, 10.0, 5.0, -3.0, 400.0, 0.0, 1.0])
        tth = np.linspace(0.0, 7.0, y.size)
        base = estimate_baseline_snip(tth, y, 1.0)
        self.assertTrue(np.all(np.isfinite(base)))
        self.assertTrue(np.all(base >= 0.0))

    def test_short_input_returns_zeros(self):
        base = estimate_baseline_snip(np.array([1.0, 2.0]),
                                      np.array([5.0, 6.0]), 1.0)
        np.testing.assert_array_equal(base, [0.0, 0.0])

    def test_invalid_window_raises(self):
        for bad in (0.0, -1.0, None):
            with self.assertRaises(ValueError):
                estimate_baseline_snip(TTH, TTH, bad)


def _shaped_background(t):
    """仿真实 LMFP 的背景形状：低角陡升 + 2.2° 宽鼓包 + 缓衰减。"""
    return (250.0 + 1100.0 * np.exp(-t / 3.5)
            + 600.0 * np.exp(-((t - 2.2) / 1.0) ** 2))


class TestSlidingBaseline(unittest.TestCase):
    """滑动窗自动基线（GUI 默认自动法）的性质。"""

    def test_flat_background_recovered_exactly(self):
        """平坦常数背景：局部下限就是它，迭代里 sigma=0 直接退出，无偏差。"""
        const = np.full_like(TTH, 500.0)
        base = estimate_baseline_sliding(TTH, const, 1.0)
        np.testing.assert_allclose(base, const, rtol=1e-9)

    def test_tracks_steep_ramp_and_broad_hump(self):
        """真实形状（陡升 + 宽鼓包 + 缓衰减）：基线应贴着真背景走，且
        宽鼓包**不得被当成峰削掉**（SNIP 正是在这里塌向 0）。"""
        bg = _shaped_background(TTH)
        base = estimate_baseline_sliding(TTH, _with_peaks(bg), 1.0)
        self.assertLess(float(np.mean(np.abs(base - bg) / bg)), 0.15,
                        "平均相对误差应 <15%")
        i = int(np.argmin(np.abs(TTH - 2.2)))
        self.assertGreater(float(base[i]), 0.5 * bg[i],
                           "宽鼓包属于背景，基线应跟着它走而不是削掉它")

    def test_baseline_stays_in_range(self):
        """基线的量级合理：不塌向 0，也不追上峰顶。

        注：SNIP 在**真实 LMFP** 上会塌向 0（2θ=1° 处给 26，真值约 1141），
        但那个塌陷依赖真实数据的峰密度，合成形状复现不出来，所以不在
        单测里断言——真实数据证据见 background.py 的 SNIP docstring。"""
        bg = _shaped_background(TTH)
        base = estimate_baseline_sliding(TTH, _with_peaks(bg), 1.0)
        self.assertTrue(np.all(base >= 0.0))
        self.assertTrue(np.all(np.isfinite(base)))
        self.assertTrue(np.all(base <= bg * 2.0), "基线不该失控地高")
        i = int(np.argmin(np.abs(TTH - 1.0)))
        self.assertGreater(float(base[i]), 0.7 * bg[i], "低角端不该塌陷")

    def test_window_must_be_a_few_times_the_peak_width(self):
        """窗口参数的语义：应取最宽峰宽的 3~10 倍（σ=0.06° → FWHM 0.14°）。

        实测标定（2θ=3° 处 σ=0.06° 的峰，真背景 546；阶段 2 于
        2026-09-26 改成"窗口内直线拟合"后重测）：
            窗口 0.1°(≈峰宽) → 4541（8.3 倍，基线骑在峰上）
            窗口 1.0°(7×峰宽) →  555（1.02 倍）
            窗口 3.0°(21×峰宽)→  589（1.08 倍，落在"扣不足"一侧）
        取小了峰被当背景留下；取大了跟不上背景自身的起伏——但改成局部
        线性以后"取大"的代价小得多（旧版 3° 窗口掉到 0.73 倍 = 扣过头，
        现在只偏 +8% = 安全的那一侧），可用窗口从"1° 附近"放宽到
        0.5~3°（全曲线平均相对偏差 ≤6%）。
        """
        bg = _ramp_background()
        y = _with_peaks(bg)                      # σ=0.06°
        i = int(np.argmin(np.abs(TTH - 3.0)))
        narrow = estimate_baseline_sliding(TTH, y, 0.1)
        sweet = estimate_baseline_sliding(TTH, y, 1.0)
        wide = estimate_baseline_sliding(TTH, y, 3.0)
        self.assertGreater(float(narrow[i]), 3.0 * bg[i], "小窗口骑在峰上")
        self.assertTrue(0.6 * bg[i] < float(sweet[i]) < 1.6 * bg[i],
                        "合适窗口应落在真背景附近")
        # 过大窗口只该"扣不足"（安全方向），不该掉到真值以下
        self.assertTrue(0.85 * bg[i] < float(wide[i]) < 1.5 * bg[i],
                        "过大窗口应扣不足，而不是过扣")
        self.assertLess(float(wide[i]), float(narrow[i]), "仍应比小窗口低")

    def test_auto_with_anchors_lands_on_them(self):
        """自动 + 锚点校正：基线在锚点处**等于**点到的值，形状仍是自动那份。

        用户 2026-09-27："背景扣除采取自动加手动矫正"。锚点少（2 个）也
        要稳：只有 1 个锚点退化成常数平移。
        """
        tth = np.linspace(1.0, 8.0, 800)
        bg = 300.0 + 900.0 * np.exp(-(tth - 1.0) / 1.5)
        y = bg + 700.0 * np.exp(-0.5 * ((tth - 3.0) / 0.08) ** 2)
        # 用户故意点得比真背景高一点（模拟"手给的尺度"）
        anchors = [(1.2, float(np.interp(1.2, tth, bg)) + 60.0),
                   (7.0, float(np.interp(7.0, tth, bg)) + 40.0)]
        base = compute_baseline(tth, y, {"mode": "auto", "window_deg": 0.3,
                                         "anchors": anchors})
        for x, v in anchors:
            self.assertAlmostEqual(float(np.interp(x, tth, base)), v,
                                   delta=1e-6, msg=f"锚点 {x}° 上该严格过点")
        plain = compute_baseline(tth, y, {"mode": "auto", "window_deg": 0.3})
        self.assertFalse(np.allclose(base, plain), "校正真的动了基线")
        # 一个锚点 = 常数平移
        one = compute_baseline(tth, y, {"mode": "auto", "window_deg": 0.3,
                                        "anchors": anchors[:1]})
        self.assertAlmostEqual(
            float(np.interp(1.2, tth, one)),
            anchors[0][1], delta=1e-6)

    def test_steep_decay_low_angle_is_not_over_subtracted(self):
        """陡降背景（低角空气散射）上，基线的低角端不再系统性偏低。

        阶段 2 的旧写法（掩峰后取均值）在陡降段会掩掉窗口高的一侧、
        均值落到真值以下 → 低角扣过头：合成真值（背景 1420→300、峰高
        最大 2600、噪声 σ=12）实测低角端偏 −139（1° 窗）/ −163（2°）/
        −533（3°）。改成窗口内直线拟合后回到 +3.7 / +1.0 / −9.3，
        平均绝对偏差也从 42/63/94 降到 35/46/67。
        """
        tth = np.linspace(1.0, 8.0, 3000)
        true_bg = 260.0 + 1160.0 * np.exp(-(tth - 1.0) / 1.35)

        def peak(c, h, s):
            return h * np.exp(-0.5 * ((tth - c) / s) ** 2)

        rng = np.random.default_rng(7)
        y = (true_bg + peak(1.85, 1500.0, 0.09) + peak(2.98, 2600.0, 0.117)
             + peak(4.20, 700.0, 0.108) + peak(5.55, 500.0, 0.099)
             + peak(3.75, 900.0, 0.09) + peak(6.60, 380.0, 0.126)
             + rng.normal(0.0, 12.0, tth.size))
        edge = slice(0, tth.size // 20)          # 最低角 5%
        for w in (1.0, 2.0, 3.0):
            base = estimate_baseline_sliding(tth, y, w)
            bias = float(np.mean((base - true_bg)[edge]))
            rel = abs(bias) / float(np.mean(true_bg[edge]))
            self.assertLess(rel, 0.05,
                            f"窗口 {w}°：低角端偏差 {bias:+.1f}（{rel:.1%}）"
                            f"——不该再有百分之几以上的系统性过扣")
            err = float(np.mean(np.abs(base - true_bg)))
            self.assertLess(err, 0.15 * float(np.mean(true_bg)),
                            f"窗口 {w}°：平均绝对偏差 {err:.0f}")

    def test_two_stage_beats_floor_only(self):
        """标定：阶段 1 的低分位（下限）本身偏低，阶段 2 的"窗口内直线拟合"
        才把基线抬到背景水平上——两阶段不是装饰。"""
        bg = _shaped_background(TTH)
        y = _with_peaks(bg)
        floor_only = estimate_baseline_sliding(TTH, y, 1.0, n_iter=0)
        two_stage = estimate_baseline_sliding(TTH, y, 1.0)
        err_floor = float(np.mean(np.abs(floor_only - bg) / bg))
        err_two = float(np.mean(np.abs(two_stage - bg) / bg))
        self.assertLess(err_two, err_floor)
        self.assertLess(float(np.mean(floor_only - bg)), 0.0,
                        "只用低分位应系统性偏低")

    def test_nan_gap_does_not_spread_into_the_baseline(self):
        """NaN（自研积分空箱、瀑布坏扇区都会产生）按有效点插值补齐，
        基线**处处有限**——不补的话一个 NaN 点会污染 ±半个窗口。"""
        bg = _ramp_background()
        y = _with_peaks(bg)
        y[700] = np.nan
        base = estimate_baseline_sliding(TTH, y, 1.0)
        self.assertTrue(np.all(np.isfinite(base)),
                        f"基线出现 NaN：{int((~np.isfinite(base)).sum())} 点")
        # 缺口附近仍贴着真背景
        self.assertLess(abs(float(base[700]) - float(bg[700])), 0.15 * bg[700])

    def test_all_nan_input_gives_zeros(self):
        """整条曲线都是 NaN（坏扇区）→ 全 0 基线（不扣），不抛异常。"""
        y = np.full_like(TTH, np.nan)
        np.testing.assert_array_equal(
            estimate_baseline_sliding(TTH, y, 1.0), np.zeros(TTH.size))
        np.testing.assert_array_equal(
            estimate_baseline_snip(TTH, y, 1.0), np.zeros(TTH.size))

    def test_snip_also_survives_nan(self):
        y = _with_peaks(_ramp_background())
        y[500] = np.nan
        base = estimate_baseline_snip(TTH, y, 1.0)
        self.assertTrue(np.all(np.isfinite(base)))

    def test_noisy_background_lands_on_the_level(self):
        """带泊松噪声时基线应落在背景**水平**上，而不是贴着噪声下沿
        （那是"连噪声一起扣掉"）。"""
        bg = _ramp_background()
        y = np.random.default_rng(5).poisson(bg).astype(float)
        base = estimate_baseline_sliding(TTH, y, 1.0)
        mid = slice(400, 1600)
        self.assertLess(abs(float(np.mean(base[mid] - bg[mid])) / bg.mean()),
                        0.05, "平均偏差应在真背景 5% 以内")

    def test_nonnegative_and_finite(self):
        y = _with_peaks(_ramp_background())
        base = estimate_baseline_sliding(TTH, y, 1.0)
        self.assertTrue(np.all(np.isfinite(base)))
        self.assertTrue(np.all(base >= 0.0))

    def test_short_input_returns_zeros(self):
        np.testing.assert_array_equal(
            estimate_baseline_sliding(np.array([1.0, 2.0]),
                                      np.array([5.0, 6.0]), 1.0),
            [0.0, 0.0])

    def test_invalid_window_raises(self):
        for bad in (0.0, -2.0, None):
            with self.assertRaises(ValueError):
                estimate_baseline_sliding(TTH, TTH, bad)


class TestAnchorBaseline(unittest.TestCase):
    """手动锚点基线：过点、外推、降级。"""

    def test_linear_passes_through_anchors(self):
        anchors = [(2.0, 400.0), (5.0, 200.0), (8.0, 120.0)]
        base = fit_anchor_baseline(TTH, anchors)
        for x, yv in anchors:
            self.assertAlmostEqual(float(np.interp(x, TTH, base)), yv, places=6)

    def test_linear_interpolates_between_anchors(self):
        """两个锚点之间是直线（折线语义）。"""
        base = fit_anchor_baseline(TTH, [(2.0, 1000.0), (6.0, 200.0)])
        mid = float(np.interp(4.0, TTH, base))
        self.assertAlmostEqual(mid, 600.0, places=6)

    def test_linear_extrapolates_with_anchor_slope_and_clamps_at_zero(self):
        """两端按首两/末两锚点的斜率线性外推，且夹到 ≥0。"""
        base = fit_anchor_baseline(TTH, [(4.0, 400.0), (6.0, 200.0)])
        # 斜率 -100/度：低角端外推得 400 + (-100)(1-4) = 700
        self.assertAlmostEqual(float(np.interp(1.0, TTH, base)), 700.0, places=6)
        # 高角端 200 + (-100)(9-6) = -100 → 夹到 0（不是平铺 200）
        self.assertAlmostEqual(float(np.interp(9.0, TTH, base)), 0.0, places=6)

    def test_unsorted_duplicate_free_anchors_are_sorted(self):
        base = fit_anchor_baseline(TTH, [(8.0, 120.0), (2.0, 400.0)])
        self.assertAlmostEqual(float(np.interp(2.0, TTH, base)), 400.0, places=6)
        self.assertAlmostEqual(float(np.interp(8.0, TTH, base)), 120.0, places=6)

    def test_single_anchor_gives_constant(self):
        base = fit_anchor_baseline(TTH, [(5.0, 321.0)])
        np.testing.assert_allclose(base, 321.0)

    def test_no_anchor_gives_zeros(self):
        np.testing.assert_array_equal(fit_anchor_baseline(TTH, []),
                                      np.zeros(TTH.size))

    def test_duplicate_anchor_x_keeps_the_last(self):
        """同一 2θ 上的重复锚点取后一个、合并成一点：样条不该抛
        "x must be strictly increasing"，折线也不该留垂直台阶。"""
        for method in ("linear", "spline"):
            base = fit_anchor_baseline(
                TTH, [(3.0, 100.0), (3.0, 900.0), (6.0, 300.0)],
                method=method)
            self.assertAlmostEqual(float(np.interp(3.0, TTH, base)), 900.0,
                                   places=6, msg=f"{method}：应取后一个")
            step = float(np.max(np.abs(np.diff(base))))
            self.assertLess(step, 50.0, f"{method}：不该出现垂直台阶")

    def test_spline_needs_three_anchors_else_falls_back_to_linear(self):
        two = [(2.0, 400.0), (8.0, 120.0)]
        np.testing.assert_allclose(
            fit_anchor_baseline(TTH, two, method="spline"),
            fit_anchor_baseline(TTH, two, method="linear"))

    def test_spline_passes_through_points_and_is_smoother(self):
        """样条过点，且中点不与折线重合（说明真的在弯）。"""
        anchors = [(1.0, 900.0), (4.0, 500.0), (7.0, 300.0), (9.0, 260.0)]
        spline = fit_anchor_baseline(TTH, anchors, method="spline")
        linear = fit_anchor_baseline(TTH, anchors, method="linear")
        for x, yv in anchors:
            self.assertAlmostEqual(float(np.interp(x, TTH, spline)), yv,
                                   places=6)
        i = int(np.argmin(np.abs(TTH - 2.5)))
        self.assertNotAlmostEqual(float(spline[i]), float(linear[i]),
                                  places=3)

    def test_all_negative_extrapolation_is_clamped(self):
        """整条外推都为负时不出现负基线。"""
        base = fit_anchor_baseline(TTH, [(1.0, 10.0), (2.0, 5.0)])
        self.assertTrue(np.all(base >= 0.0))

    def test_pchip_passes_through_anchors_and_never_overshoots(self):
        """保单调平滑（pchip，界面默认）：过点、光滑、**不**过冲。

        与自然样条的差别就在"不过冲"：样条在锚点之间会冲到锚点值域
        之外（背景上表现为压到真值以下 = 扣过头），pchip 不会——所以
        它落在锚点值的上下包络之内。合成真值上平均绝对偏差 6.1
        （折线 28.8、样条 16.4）。
        """
        anchors = [(1.0, 900.0), (4.0, 500.0), (7.0, 300.0), (9.0, 260.0)]
        pch = fit_anchor_baseline(TTH, anchors, method="pchip")
        for x, yv in anchors:
            self.assertAlmostEqual(float(np.interp(x, TTH, pch)), yv,
                                   places=6)
        lo, hi = min(a[1] for a in anchors), max(a[1] for a in anchors)
        inner = (TTH >= anchors[0][0]) & (TTH <= anchors[-1][0])
        self.assertTrue(np.all(pch[inner] >= lo - 1e-6))
        self.assertTrue(np.all(pch[inner] <= hi + 1e-6))
        # 只给两个锚点 → 与折线一致（降级路径）
        np.testing.assert_allclose(
            fit_anchor_baseline(TTH, anchors[:2], method="pchip"),
            fit_anchor_baseline(TTH, anchors[:2], method="linear"))


class TestInterpOntoGrid(unittest.TestCase):
    """空扫曲线重插到样品网格。"""

    def test_linear_interpolation_inside_range(self):
        src = np.array([0.0, 1.0, 2.0])
        val = interp_onto_grid(src, np.array([0.0, 10.0, 20.0]),
                               np.array([0.5, 1.5]))
        np.testing.assert_allclose(val, [5.0, 15.0])

    def test_outside_source_range_is_zero_not_edge_value(self):
        """未覆盖区间返回 0（不扣），不是把端点值平铺出去。"""
        val = interp_onto_grid(np.array([2.0, 4.0]), np.array([100.0, 300.0]),
                               np.array([0.0, 3.0, 6.0]))
        np.testing.assert_allclose(val, [0.0, 200.0, 0.0])


class TestSubtractBackground(unittest.TestCase):
    """减基线：默认保留负值，可显式截断。"""

    def test_negative_values_kept_by_default(self):
        out = subtract_background(np.array([10.0, 5.0]), np.array([4.0, 9.0]))
        np.testing.assert_allclose(out, [6.0, -4.0])

    def test_clip_negative_option(self):
        out = subtract_background(np.array([10.0, 5.0]), np.array([4.0, 9.0]),
                                  clip_negative=True)
        np.testing.assert_allclose(out, [6.0, 0.0])


class TestComputeBaselineDispatch(unittest.TestCase):
    """分派器：绘制层与导出层共用的唯一口径。"""

    def setUp(self):
        self.bg = _ramp_background()
        self.y = _with_peaks(self.bg)

    def test_off_returns_none(self):
        self.assertIsNone(compute_baseline(TTH, self.y, {"mode": "off"}))
        self.assertIsNone(compute_baseline(TTH, self.y, {}))

    def test_auto_and_snip_dispatch(self):
        auto = compute_baseline(TTH, self.y, {"mode": "auto",
                                              "window_deg": 1.0})
        snip = compute_baseline(TTH, self.y, {"mode": "snip",
                                              "window_deg": 2.0})
        np.testing.assert_allclose(
            auto, estimate_baseline_sliding(TTH, self.y, 1.0))
        np.testing.assert_allclose(
            snip, estimate_baseline_snip(TTH, self.y, 2.0))

    def test_anchor_dispatch(self):
        anchors = [(2.0, 300.0), (8.0, 150.0)]
        base = compute_baseline(TTH, self.y, {"mode": "anchor",
                                              "anchors": anchors})
        np.testing.assert_allclose(base, fit_anchor_baseline(TTH, anchors))

    def test_blank_without_curve_returns_none(self):
        self.assertIsNone(compute_baseline(TTH, self.y, {"mode": "blank"}))

    def test_blank_scale_and_grid_mismatch(self):
        """空扫网格不同 → 重插；归一化系数作为倍率乘上去。"""
        b_tth = np.linspace(1.0, 9.0, 401)
        b_i = 100.0 + 2000.0 * np.exp(-b_tth / 2.0)
        base = compute_baseline(TTH, self.y,
                                {"mode": "blank", "blank_scale": 2.0},
                                blank_curve=(b_tth, b_i))
        # 覆盖区间内 = 2× 真背景；覆盖区间外 = 0（不扣）
        mid = int(np.argmin(np.abs(TTH - 5.0)))
        self.assertAlmostEqual(base[mid], 2.0 * b_i[np.argmin(
            np.abs(b_tth - TTH[mid]))], places=6)
        self.assertEqual(base[0], 0.0)
        self.assertEqual(base[-1], 0.0)

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            compute_baseline(TTH, self.y, {"mode": "nope"})


if __name__ == "__main__":
    unittest.main()
