"""Scherrer / Williamson-Hall 测试（2026-10-23）。"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.simulation import microstructure as ms  # noqa: E402


class TestScherrer(unittest.TestCase):
    def test_round_trip(self):
        d_nm, tt, lam, K = 50.0, 30.0, 1.5406, 0.9
        theta = np.radians(tt / 2.0)
        beta_deg = np.degrees(K * lam / (d_nm * 10.0 * np.cos(theta)))
        self.assertAlmostEqual(
            float(ms.scherrer_size_nm(beta_deg, tt, wavelength=lam, K=K)),
            d_nm, delta=1e-6)


class TestWilliamsonHall(unittest.TestCase):
    def setUp(self):
        self.centers = np.array([20.0, 28.0, 36.0, 44.0, 52.0, 60.0, 68.0])
        self.d_nm, self.strain = 50.0, 0.002
        self.lam, self.K = 1.5406, 0.9
        theta = np.radians(self.centers / 2.0)
        size_rad = self.K * self.lam / (self.d_nm * 10.0 * np.cos(theta))
        strain_rad = 4.0 * self.strain * np.tan(theta)
        self.sample_deg = np.degrees(
            np.sqrt(size_rad ** 2 + strain_rad ** 2))
        self.inst_deg = 0.018 + 0.004 * np.tan(theta) ** 2

    def test_recovers_size_and_strain_with_instrument_correction(self):
        obs = np.degrees(np.sqrt(
            np.radians(self.sample_deg) ** 2
            + np.radians(self.inst_deg) ** 2))
        res = ms.williamson_hall(self.centers, obs,
                                 wavelength=self.lam, K=self.K,
                                 instrument_fwhm_deg=self.inst_deg)
        self.assertAlmostEqual(res.size_nm, self.d_nm, delta=1.0)
        self.assertAlmostEqual(res.strain, self.strain, delta=0.0001)
        self.assertEqual(res.warning, "")

    def test_without_instrument_correction_size_is_biased(self):
        # 仪器展宽比样品还宽时，不扣仪器 → 尺寸明显偏小
        inst = 0.09 + 0.01 * np.tan(np.radians(self.centers / 2.0)) ** 2
        obs = np.degrees(np.sqrt(
            np.radians(self.sample_deg) ** 2
            + np.radians(inst) ** 2))
        res = ms.williamson_hall(self.centers, obs,
                                 wavelength=self.lam, K=self.K)
        self.assertLess(res.size_nm, self.d_nm * 0.9)

    def test_linear_subtraction(self):
        obs = np.degrees(np.radians(self.sample_deg)
                         + np.radians(self.inst_deg))
        res = ms.williamson_hall(self.centers, obs, wavelength=self.lam,
                                 K=self.K, instrument_fwhm_deg=self.inst_deg,
                                 combine="linear")
        self.assertAlmostEqual(res.size_nm, self.d_nm, delta=2.0)
        self.assertAlmostEqual(res.strain, self.strain, delta=0.0002)


class TestPlot(unittest.TestCase):
    def test_williamson_hall_plot_smoke(self):
        import tempfile
        from xrd_toolkit.simulation import plot_williamson_hall
        centers = np.array([20.0, 30.0, 40.0, 50.0, 60.0])
        inst = np.full(5, 0.02)
        res = ms.williamson_hall(
            centers, inst + np.array([0.01, 0.012, 0.014, 0.016, 0.018]),
            instrument_fwhm_deg=inst)
        out = Path(tempfile.mkdtemp()) / "wh.png"
        plot_williamson_hall(res, str(out))
        self.assertTrue(out.exists() and out.stat().st_size > 0)


if __name__ == "__main__":
    unittest.main()
