"""偏置摆法（部分环）与弧覆盖率判据的单元测试（unittest，环境无 pytest）。

合成图像 + 真实几何常量（dist = 1.5958 m、pixel = 200 µm，与
lmfp1_lab6 配置一致）验证：
  - 弧覆盖率几何（arc_coverage_fraction / peak_azimuth_coverage）；
  - 几何失效点（geometric_failure_point，覆盖率跌破自身峰值 50% 的
    相对判据，居中/半环/四分之一环三种摆法）；
  - 偏置摆法下自研积分的正确性（pyFAI 2026.x 在该条件下径向分箱
    错误，本组测试即该缺陷的回归护栏）；
  - χ 标注如实反映图像实际覆盖的方位角跨度。

运行：python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.services.integrator import (
    _covered_azimuth_labels, integrate_1d, integrate_sectors)
from xrd_toolkit.services.range_selector import (
    arc_coverage_fraction, geometric_failure_point, peak_azimuth_coverage)

SHAPE = (2048, 2048)
PIXEL_M = 200e-6
DIST_M = 1.5958


def _synthetic_rings(x0, y0, tths, shape=SHAPE, value=100.0,
                     pixel_m=PIXEL_M, dist_m=DIST_M):
    """画 1 px 宽的合成衍射环（无噪声、无背景，只在图像内画）。"""
    img = np.zeros(shape)
    rows, cols = np.mgrid[0:shape[0], 0:shape[1]]
    for tth in tths:
        r = dist_m * np.tan(np.radians(tth)) / pixel_m
        rr = np.hypot(cols - x0, rows - y0)
        img[np.abs(rr - r) < 0.5] = value
    return img


class TestArcCoverage(unittest.TestCase):
    """弧覆盖率几何：居中/边缘/角落/束心在图像外四种摆法。"""

    def test_centered_small_radius_full(self):
        cov = arc_coverage_fraction((1024, 1024), SHAPE, 100.0)
        self.assertAlmostEqual(cov, 1.0, places=2)

    def test_centered_large_radius_partial(self):
        # r=1200：环穿过四条边，约 30% 弧段留在图像内。
        # （r > 对角线一半 1448 px 后整环都在图像外，覆盖率恒为 0）
        cov = arc_coverage_fraction((1024, 1024), SHAPE, 1200.0)
        self.assertTrue(0.1 < cov < 0.5, f"cov = {cov}")
        cov_out = arc_coverage_fraction((1024, 1024), SHAPE, 1500.0)
        self.assertEqual(cov_out, 0.0)

    def test_corner_beam_quarter(self):
        cov = arc_coverage_fraction((0, 0), SHAPE, 100.0)
        self.assertAlmostEqual(cov, 0.25, delta=0.02)

    def test_edge_beam_half(self):
        cov = arc_coverage_fraction((0, 1024), SHAPE, 100.0)
        self.assertAlmostEqual(cov, 0.5, delta=0.02)

    def test_beam_outside_zero(self):
        cov = arc_coverage_fraction((-50, 1024), SHAPE, 40.0)
        self.assertEqual(cov, 0.0)

    def test_peak_coverage_by_geometry(self):
        self.assertAlmostEqual(
            peak_azimuth_coverage((1024, 1024), SHAPE), 1.0, places=2)
        self.assertAlmostEqual(
            peak_azimuth_coverage((0, 1024), SHAPE), 0.5, places=2)
        self.assertAlmostEqual(
            peak_azimuth_coverage((0, 0), SHAPE), 0.25, places=2)


class TestGeometricFailurePoint(unittest.TestCase):
    """几何失效点：覆盖率跌破自身峰值 50% 的位置（三种摆法）。"""

    def _hi(self, poni):
        return geometric_failure_point(poni, SHAPE, PIXEL_M, DIST_M)

    def test_centered(self):
        # 峰值覆盖 100% → 判据即覆盖率 < 50%，失效半径 ~1108 px ≈ 7.9°
        hi = self._hi((1024, 1024))
        self.assertTrue(7.3 < hi < 8.6, f"hi = {hi}")

    def test_corner(self):
        # 峰值覆盖 25% → 判据即覆盖率 < 12.5%；覆盖率在小半径处恒为
        # 25%（四分之一象限），至 r ≈ 2048 后下降，失效半径 ~2217 px
        # ≈ 15.5°（注意：不是最远角 2896 px ≈ 20°——那一点覆盖率趋近 0）
        hi = self._hi((0, 0))
        self.assertTrue(15.0 < hi < 16.0, f"hi = {hi}")

    def test_edge(self):
        # 峰值覆盖 50% → 判据即覆盖率 < 25%，失效半径 ~1448 px ≈ 10.3°
        hi = self._hi((0, 1024))
        self.assertTrue(9.5 < hi < 11.0, f"hi = {hi}")


class TestCoveredAzimuthLabels(unittest.TestCase):
    """χ 标注如实反映图像覆盖的方位角跨度（部分环摆法）。"""

    def test_beam_inside_full_circle(self):
        chi = _covered_azimuth_labels((1024, 1024), SHAPE, 36)
        self.assertAlmostEqual(chi[0], -175.0, places=1)
        self.assertAlmostEqual(chi[-1], 175.0, places=1)

    def test_beam_outside_left_span(self):
        # 束心在图像左侧外 50 px：可见方位角约 ±87.2°（atan2 约定，
        # 0° 沿 +x 向右），跨度 174.4° 而不是整圈 360°
        chi = _covered_azimuth_labels((-50, 1024), SHAPE, 36)
        self.assertAlmostEqual(chi[0], -87.2, delta=0.5)
        self.assertAlmostEqual(chi[-1], 87.2, delta=0.5)


class TestDiyIntegration(unittest.TestCase):
    """偏置摆法下自研积分的正确性（pyFAI 2026.x 缺陷的回归护栏）。"""

    def test_integrate_1d_off_center_peaks(self):
        # 束心 (1148, 1024)（沿 x 轴偏离 124 px > 阈值）：两个完整
        # 合成环。注意必须是"轴方向"偏离——实测 pyFAI 2026.x 的
        # 径向分箱缺陷只在轴方向衰减（124 px 时峰高 <1%），45° 对角
        # 方向即使偏离 500 px 也不衰减；本用例才真正踩中该缺陷。
        # 自研路径应峰位准确、峰高接近画入值。
        img = _synthetic_rings(1148.0, 1024.0, (3.0, 5.0))
        tth, curve = integrate_1d(img, PIXEL_M, 0.1223e-10, DIST_M,
                                  poni1_m=1148.0 * PIXEL_M,
                                  poni2_m=1024.0 * PIXEL_M,
                                  rot1_deg=0.0, rot2_deg=0.0, npt=3000)
        for t0 in (3.0, 5.0):
            win = (tth > t0 - 0.1) & (tth < t0 + 0.1)
            j = int(np.argmax(curve[win]))
            self.assertAlmostEqual(tth[win][j], t0, delta=0.05)
            self.assertGreater(curve[win][j], 50.0)   # pyFAI 缺陷下 < 5

    def test_integrate_sectors_off_center_dead_sectors(self):
        # 束心在图像左侧外 (-50, 1024)：可见方位角 ≈ ±87°（真实跨度由
        # _covered_azimuth_labels 提供，见 TestCoveredAzimuthLabels）。
        # 扇区分箱保持全局约定（sector k 恒为 [-180°+10k, -180°+10(k+1))，
        # 与居中数据一致），死区扇区整列 NaN：8° 环上下缘被截断，
        # 只覆盖中部扇区（chi ≈ ±66° 以内），两侧 8 个扇区死区；
        # 5° 环右半弧横贯全部可见方位角，所有扇区有数据。
        img = _synthetic_rings(-50.0, 1024.0, (5.0, 8.0))
        tth, I2d, chi = integrate_sectors(
            img, PIXEL_M, 0.1223e-10, DIST_M,
            poni1_m=-50.0 * PIXEL_M, poni2_m=1024.0 * PIXEL_M,
            rot1_deg=0.0, rot2_deg=0.0, n_sectors=36, npt=3000)
        self.assertAlmostEqual(chi[0], -175.0, places=1)   # 全局 χ 约定不变
        self.assertAlmostEqual(chi[-1], 175.0, places=1)
        # 5° 环（r ≈ 698 px）可见方位角 ≈ ±85.9°（x ≥ 0 约束）：
        # 与 [-90°, 90°) 相交的扇区 k=9..26 有数据，其余死区
        row5 = int(np.argmin(np.abs(tth - 5.0)))
        for k in range(9, 27):
            self.assertTrue(np.all(np.isfinite(I2d[row5, k])),
                            f"sector {k} should be alive at 5 deg")
        for k in (0, 1, 8, 27, 28, 35):
            self.assertTrue(np.all(~np.isfinite(I2d[row5, k])),
                            f"sector {k} should be dead at 5 deg")
        # 8° 环（r ≈ 1121 px）额外受上下边缘约束，可见方位角 ≈ ±66°：
        # 扇区 k=11..24 有数据，其余死区
        row8 = int(np.argmin(np.abs(tth - 8.0)))
        self.assertGreater(np.nanmax(I2d[row8, :]), 0.0)
        for k in range(11, 25):
            self.assertTrue(np.all(np.isfinite(I2d[row8, k])),
                            f"sector {k} should be alive at 8 deg")
        for k in (0, 5, 9, 10, 25, 26, 30, 35):
            self.assertTrue(np.all(~np.isfinite(I2d[row8, k])),
                            f"sector {k} should be dead at 8 deg")

    def test_integrate_sectors_centered_keeps_pyfai_labels(self):
        img = _synthetic_rings(1024.0, 1024.0, (5.0,))
        _, _, chi = integrate_sectors(
            img, PIXEL_M, 0.1223e-10, DIST_M,
            poni1_m=1024.0 * PIXEL_M, poni2_m=1024.0 * PIXEL_M,
            rot1_deg=0.0, rot2_deg=0.0, n_sectors=36, npt=3000)
        self.assertAlmostEqual(chi[0], -175.0, places=1)
        self.assertAlmostEqual(chi[-1], 175.0, places=1)


class TestIntegrate1DRange(unittest.TestCase):
    """integrate_1d 的 2θ 区间参数：npt 摊在区间内、区间外数据不进谱。

    GUI 积分设置（2θ 上下限）经 geom 传入这里——区间约束必须在
    两条路径（pyFAI / DIY）都生效，且不传 = 全范围（CLI 行为不变）。
    """

    def test_centered_pyfai_path_range(self):
        # 居中束心（pyFAI 路径）：环 (1.0, 5.0)°，只积 [2, 6]° →
        # 谱从 2° 起、峰只有 5° 一个；1° 环整段不在谱里
        img = _synthetic_rings(1024.0, 1024.0, (1.0, 5.0))
        tth, curve = integrate_1d(img, PIXEL_M, 0.1223e-10, DIST_M,
                                  poni1_m=1024.0 * PIXEL_M,
                                  poni2_m=1024.0 * PIXEL_M,
                                  rot1_deg=0.0, rot2_deg=0.0, npt=2000,
                                  tth_min_deg=2.0, tth_max_deg=6.0)
        self.assertEqual(len(tth), 2000)
        self.assertAlmostEqual(tth[0], 2.0, delta=0.05)
        self.assertAlmostEqual(tth[-1], 6.0, delta=0.05)
        j = int(np.argmax(curve))
        self.assertAlmostEqual(tth[j], 5.0, delta=0.1)
        self.assertGreater(curve[j], 20.0)

    def test_off_center_diy_path_range(self):
        # 轴方向偏置束心（DIY 路径，见 TestDiyIntegration）：环 (1.0, 5.0)°，
        # 只积 [2, 6]° → 5° 峰在、1° 环不进箱（区间外像素 idx = -1）
        img = _synthetic_rings(1148.0, 1024.0, (1.0, 5.0))
        tth, curve = integrate_1d(img, PIXEL_M, 0.1223e-10, DIST_M,
                                  poni1_m=1148.0 * PIXEL_M,
                                  poni2_m=1024.0 * PIXEL_M,
                                  rot1_deg=0.0, rot2_deg=0.0, npt=2000,
                                  tth_min_deg=2.0, tth_max_deg=6.0)
        self.assertAlmostEqual(tth[0], 2.0, delta=0.1)
        self.assertAlmostEqual(tth[-1], 6.0, delta=0.1)
        win = (tth > 4.9) & (tth < 5.1)
        self.assertGreater(np.nanmax(curve[win]), 20.0)

    def test_no_range_keeps_full_span(self):
        # 不传区间 = 全探测器范围（CLI 行为不变）：谱从近 0° 起
        img = _synthetic_rings(1024.0, 1024.0, (3.0,))
        tth, curve = integrate_1d(img, PIXEL_M, 0.1223e-10, DIST_M,
                                  poni1_m=1024.0 * PIXEL_M,
                                  poni2_m=1024.0 * PIXEL_M,
                                  rot1_deg=0.0, rot2_deg=0.0, npt=500)
        self.assertLess(tth[0], 0.5)

    def test_inverted_range_raises(self):
        img = _synthetic_rings(1024.0, 1024.0, (3.0,))
        with self.assertRaises(ValueError):
            integrate_1d(img, PIXEL_M, 0.1223e-10, DIST_M,
                         poni1_m=1024.0 * PIXEL_M,
                         poni2_m=1024.0 * PIXEL_M,
                         rot1_deg=0.0, rot2_deg=0.0,
                         tth_min_deg=8.0, tth_max_deg=1.0)


if __name__ == "__main__":
    unittest.main()
