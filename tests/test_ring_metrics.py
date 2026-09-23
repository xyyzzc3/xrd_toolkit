"""引擎指标（services/ring_metrics）的实测标定：真几何 + 合成图。

为什么用合成图标定：合成时"环画在哪"是我们自己定的（已知真值），所以
指标报出的偏差是**指标自己的**误差，而不是数据与几何的混合物。这里用
真内置几何（config.py lmfp1_lab6）+ 真 theoretical_ring_paths（不是桩），
合成点按亚像素落在路径上，噪声底因此降到 ~0.06 px——远小于要测的信号。

两组：
  * TestRingMetricsSynthetic：无误差基线，以及两类误差的**响应方式**
    （统一几何误差 → dev_px；单条环误差 → a 离散度）；
  * TestRingMetricsEdgeCases：纯噪声 / 弱环 / 半环 / 峰出窗 / 几何离谱 /
    坏输入——钉住"失败长什么样"，防静默误判（v1 的两个 bug 都属于这类：
  a 少乘 1e10 报 0.00000、阈值取比例导致纯噪声 43% 假检出）。
"""
import unittest

import numpy as np

from xrd_toolkit.services.integrator import theoretical_ring_paths
from xrd_toolkit.services.ring_metrics import ring_metrics

# 真内置几何（与 config.py 的 lmfp1_lab6 一致；束心几乎在图像对角线上，
# 倾斜角不为零 → 理论环是椭圆，正是生产路径）
PIX, LAM, D = 200e-6, 0.1223e-10, 1.59580
PONI1, PONI2, ROT1, ROT2 = 1045.2, 1022.0, -0.005, -0.163
N = 2048
GEO = dict(pixel_size_m=PIX, wavelength_m=LAM, dist_m=D,
           poni1_px=PONI1, poni2_px=PONI2, rot1_deg=ROT1, rot2_deg=ROT2)
BG, NSIG = 100.0, 5.0       # 平坦底、高斯噪声 σ
AMP = 60.0                  # 环斑峰值（≈12σ，稳稳检出）
TRUE_A = 4.1568             # LaB6 晶格常数（Å，NIST SRM 660）


def _synth(dist_m=D, n_azim=180, amp=AMP, noise=NSIG,
           half=False, shift_ring=None, shift_px=0.0, seed=0):
    """合成一张"环正好落在理论路径上"的图。

    返回 (image, n_full)：n_full = 完全落在图像内的环数——只有这些环
    才可能被报成"完整环"，是完整度断言的期望值。

    half=True 只画前一半方位角（半环）；shift_ring 指定的那条环沿径向
    额外挪 shift_px 像素（模拟单环被别的东西顶偏）。
    """
    paths = theoretical_ring_paths(image_shape=(N, N), n_rings=16,
                                   n_azim=n_azim, **{**GEO, "dist_m": dist_m})
    rng = np.random.default_rng(seed)
    img = rng.normal(BG, noise, (N, N))
    b_row, b_col = paths["beam_center_rc"]
    o = np.arange(-3, 4)                       # 7×7 窗口
    n_full = 0
    for k, pts in paths["rings"]:
        col, row = pts[:, 0].copy(), pts[:, 1].copy()
        if half:
            col, row = col[:n_azim // 2], row[:n_azim // 2]
        if k == shift_ring:
            dr = np.hypot(col - b_col, row - b_row)
            col += shift_px * (col - b_col) / dr
            row += shift_px * (row - b_row) / dr
        inside = (np.isfinite(col) & np.isfinite(row)
                  & (col > 3) & (col < N - 4) & (row > 3) & (row < N - 4))
        n_full += int(inside.all())
        for c, r in zip(col[inside], row[inside]):
            ci, ri = int(np.floor(c)), int(np.floor(r))
            # 高斯斑**亚像素**落点：只取整数像素会引入 ±0.5 px 量化误差
            # （≈几万 ppm 的假 a 离散度），把要测的信号淹掉
            gx = np.exp(-0.5 * ((o - (c - ci)) / 1.2) ** 2)
            gy = np.exp(-0.5 * ((o - (r - ri)) / 1.2) ** 2)
            img[ri - 3:ri + 4, ci - 3:ci + 4] += amp * np.outer(gy, gx)
    return img, n_full


def _metric(image, dist_m=D, **kw):
    return ring_metrics(image, **{**GEO, "dist_m": dist_m}, **kw)


_base = None


def _base_image():
    """基准合成图（无误差）——两个测试类共用，整个测试模块只建一次。"""
    global _base
    if _base is None:
        _base = _synth()
    return _base


class TestRingMetricsSynthetic(unittest.TestCase):
    """无误差基线与两类误差的响应方式（合成图只建一次，测试间复用）。"""

    @classmethod
    def setUpClass(cls):
        cls.img, cls.n_full = _base_image()
        cls.m = _metric(cls.img)

    # ── 基线：没有误差时指标该报多少 ────────────────────────────
    def test_no_error_detects_nothing(self):
        """无误差 → 偏差只剩噪声底（0.5 px 径向采样 + 1.2 px 峰宽）。"""
        self.assertLess(self.m["dev_px"], 0.1)
        self.assertLess(self.m["dev_rms_px"], 0.2)
        self.assertLess(abs(self.m["dev_signed_px"]), 0.05)

    def test_every_visible_ring_complete(self):
        """合成环都是真实的完整环 → 完整环数 = 完全落在图像内的环数。"""
        self.assertEqual(self.n_full, 16)      # 前提本身要成立
        self.assertEqual(self.m["n_complete"], self.n_full)
        self.assertEqual(self.m["n_rings_used"], 16)
        for row in self.m["rings"]:
            self.assertGreater(row["coverage"], 0.95, row)
            # 合成环整圈均匀 → 谱相干覆盖与取点覆盖几乎相等（真数据上
            # FFT 值明显更低是常态，见模块 docstring 第 2 条的实测）
            self.assertLess(row["coverage"] - row["coverage_fft"], 0.15, row)

    def test_lattice_constant_is_angstrom(self):
        """a 反推回 4.1568 Å（v1 在这里少乘 1e10，报过 0.00000）。"""
        self.assertAlmostEqual(self.m["a"]["mean_angstrom"], TRUE_A,
                               delta=0.005)
        self.assertLess(self.m["a"]["spread_ppm"], 200.0)

    def test_peak_well_inside_window(self):
        """正确几何下没有峰被搜索窗截断（clip_frac 是"几何离谱"的证据）。"""
        self.assertLess(self.m["clip_frac"], 0.05)

    def test_fft_coverage_is_conservative_bound(self):
        """不变量：coverage_fft 只用检出样本，恒 ≤ coverage。"""
        for row in self.m["rings"]:
            self.assertLessEqual(row["coverage_fft"], row["coverage"] + 1e-9,
                                 row)

    # ── 统一几何误差 → 环位偏差 ────────────────────────────────
    def test_distance_error_moves_dev_not_spread(self):
        """距离错 1%：dev 强响应；a 离散度只有二阶残余。

        距离与波长在标样上一阶简并（所有环的 a 被整体缩放），所以均值
        动、离散度几乎不动——**别用离散度抓距离误差**。
        """
        m_in = _metric(self.img, dist_m=D * 0.99)
        m_out = _metric(self.img, dist_m=D * 1.01)
        self.assertGreater(abs(m_in["dev_signed_px"]), 2.0)
        self.assertGreater(abs(m_out["dev_signed_px"]), 2.0)
        # 距离偏大 → 理论环画得比真环大 → 实测比预测靠内（负偏差）
        self.assertLess(m_in["dev_signed_px"] * m_out["dev_signed_px"], 0)
        # 二阶残余：远弱于 1e4 ppm 的距离误差本身
        self.assertLess(max(m_in["a"]["spread_ppm"], m_out["a"]["spread_ppm"]),
                        0.2 * 1e4)
        # 均值随距离整体缩放（0.99 / 1.01 倍）
        self.assertAlmostEqual(m_in["a"]["mean_angstrom"], TRUE_A * 0.99,
                               delta=0.02)
        self.assertAlmostEqual(m_out["a"]["mean_angstrom"], TRUE_A * 1.01,
                               delta=0.02)

    def test_single_ring_shift_moves_spread_not_dev(self):
        """只把第 8 条环外推 2 px：a 离散度暴涨、全局偏差中位数不动。

        这是"单一几何解释不了这些环"的信号（环号认错、畸变、某峰被顶
        偏）——与上面的统一误差正好互补。
        """
        img, _ = _synth(shift_ring=8, shift_px=2.0)
        m = _metric(img)
        r8 = next(r for r in m["rings"] if r["ring"] == 8)
        self.assertAlmostEqual(abs(r8["dev_signed_px"]), 2.0, delta=0.4)
        self.assertGreater(m["a"]["spread_ppm"],
                           5 * max(self.m["a"]["spread_ppm"], 1.0))
        # 中位数抗单环：全局偏差几乎不动（所以 dev 单独看会漏掉这种错）
        self.assertLess(abs(m["dev_signed_px"] - self.m["dev_signed_px"]), 0.3)


class TestRingMetricsEdgeCases(unittest.TestCase):
    """失败长什么样（v1 的两个 bug 都是这一类的静默误判）。"""

    @classmethod
    def setUpClass(cls):
        cls.img, _ = _base_image()      # 与基线类共用同一张合成图

    def test_pure_noise_no_false_detection(self):
        """纯噪声图上不许报出环：覆盖率接近 0、偏差 NaN。

        v1 的阈值取"信噪比"（σ 来自平滑残差，而 prom 有 +0.84σ 偏置），
        在纯噪声上报出 43% 方位角假检出 + dev=11.4 px 的假结果。
        """
        rng = np.random.default_rng(1)
        m = _metric(rng.normal(BG, NSIG, (N, N)))
        self.assertFalse(np.isfinite(m["dev_px"]))
        self.assertEqual(m["n_complete"], 0)
        self.assertEqual(m["n_rings_used"], 0)
        for row in m["rings"]:
            self.assertLess(row["coverage"], 0.05, row)

    def test_detection_threshold_scales_with_ring_height(self):
        """检出下限 ≈4σ 环高：1σ 环测不到、4σ 环完整测到。

        （3σ 环覆盖率实测 0.911——接近但不达"完整"，故用两端点断言。）
        """
        weak, _ = _synth(amp=NSIG)          # 1σ
        strong, _ = _synth(amp=4 * NSIG)    # 4σ
        self.assertLess(_metric(weak)["rings"][0]["coverage"], 0.2)
        self.assertGreater(_metric(strong)["rings"][0]["coverage"], 0.9)

    def test_half_ring_coverage_and_fft_agree(self):
        """半环：取点法与谱相干法都报 ~0.5，且 fft ≤ coverage。"""
        img, _ = _synth(half=True)
        m = _metric(img)
        rows = [r for r in m["rings"] if r["fov_frac"] > 0.9]
        self.assertTrue(rows)
        for row in rows:
            self.assertAlmostEqual(row["coverage"], 0.5, delta=0.06, msg=row)
            self.assertAlmostEqual(row["coverage_fft"], 0.5, delta=0.06,
                                   msg=row)
            self.assertFalse(row["complete"], row)

    def test_geometry_far_off_gives_nan_not_exception(self):
        """几何离谱：不抛异常，如实给 NaN / 0 条可用环。

        两种离谱方式都试：半径整体偏 20%（看着还算合理的错几何，窗口
        里只剩背景与邻环）与环全被推出图像外（像素尺寸填错那种）。
        提示用户是调用方的事；引擎的职责是"不静默编造数字"。
        """
        for dist_m in (D * 1.20, D * 10.0):
            m = _metric(self.img, dist_m=dist_m)
            self.assertFalse(np.isfinite(m["dev_px"]), dist_m)
            self.assertEqual(m["n_complete"], 0, dist_m)
            self.assertEqual(m["n_rings_used"], 0, dist_m)

    def test_clip_frac_flags_peaks_leaving_the_window(self):
        """真峰顶到窗边 → 该环 clip_frac→1、coverage→0，是几何差的硬证据。

        实测（距离错 1%）：最外圈先出窗（close=1.00、coverage=0），内圈
        仍在窗内（clip=0）——所以要看**逐环**的 clip_frac；汇总值被内圈
        平均掉，只有 0.13。
        """
        m = _metric(self.img, dist_m=D * 1.01)
        self.assertGreater(m["rings"][-1]["clip_frac"], 0.9)
        self.assertEqual(m["rings"][-1]["coverage"], 0.0)
        self.assertLess(m["rings"][0]["clip_frac"], 0.05)   # 内圈正常
        self.assertGreater(m["clip_frac"], 0.0)             # 汇总也有痕迹
        self.assertLess(m["n_rings_used"], 16)              # 已有环掉出汇总

    def test_rejects_non_2d_input(self):
        with self.assertRaises(ValueError):
            _metric(np.zeros(16))


if __name__ == "__main__":
    unittest.main()
