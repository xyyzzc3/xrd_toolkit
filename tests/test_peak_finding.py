"""1D 寻峰测试（2026-10-23）。"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.services.peak_finding import find_peaks_1d  # noqa: E402


def _gauss(x, center, fwhm, height):
    return height * np.exp(-4.0 * np.log(2.0) * ((x - center) / fwhm) ** 2)


def _gauss_area(height, fwhm):
    return height * fwhm * np.sqrt(np.pi / (4.0 * np.log(2.0)))


class TestFindPeaks(unittest.TestCase):
    def test_recovers_positions_height_fwhm_area(self):
        x = np.arange(1.0, 3.0, 0.0005)
        bg = 100.0 + 20.0 * (x - 2.0)
        y = bg + _gauss(x, 1.5, 0.02, 400.0) \
            + _gauss(x, 2.2, 0.03, 200.0)
        rng = np.random.default_rng(0)
        y = y + rng.normal(0.0, 1.0, y.size)
        peaks = find_peaks_1d(x, y, baseline=bg)
        self.assertEqual(len(peaks), 2)
        first, second = peaks
        self.assertAlmostEqual(first.center, 1.5, delta=2e-3)
        self.assertAlmostEqual(second.center, 2.2, delta=2e-3)
        self.assertAlmostEqual(first.height, 400.0, delta=0.03 * 400.0)
        self.assertAlmostEqual(second.height, 200.0, delta=0.03 * 200.0)
        self.assertAlmostEqual(first.fwhm, 0.02, delta=0.05 * 0.02)
        self.assertAlmostEqual(second.fwhm, 0.03, delta=0.05 * 0.03)
        self.assertAlmostEqual(first.area, _gauss_area(400.0, 0.02),
                               delta=0.05 * _gauss_area(400.0, 0.02))
        self.assertAlmostEqual(second.area, _gauss_area(200.0, 0.03),
                               delta=0.05 * _gauss_area(200.0, 0.03))
        self.assertLess(first.left, first.center)
        self.assertLess(first.center, first.right)

    def test_noise_only_can_be_tightened(self):
        # 纯噪声里 5σ 判据偶尔会放过几根噪声尖（1000 点约 4 根）；
        # 调高显著度门槛后应当一根不剩。
        x = np.arange(1.0, 2.0, 0.001)
        rng = np.random.default_rng(1)
        y = 100.0 + rng.normal(0.0, 1.0, x.size)
        self.assertEqual(find_peaks_1d(x, y, min_prominence=8.0), [])

    def test_flat_returns_empty(self):
        x = np.arange(1.0, 2.0, 0.01)
        self.assertEqual(find_peaks_1d(x, np.full(x.size, 50.0)), [])

    def test_min_distance_merges_close_peaks(self):
        x = np.arange(1.0, 3.0, 0.0005)
        y = _gauss(x, 1.50, 0.01, 100.0) + _gauss(x, 1.52, 0.01, 80.0)
        close = find_peaks_1d(x, y, min_distance_deg=0.005)
        merged = find_peaks_1d(x, y, min_distance_deg=0.05)
        self.assertEqual(len(close), 2)
        self.assertEqual(len(merged), 1)
        # 合并时保留较高的那根
        self.assertAlmostEqual(merged[0].center, 1.50, delta=2e-3)

    def test_fwhm_filter(self):
        x = np.arange(1.0, 3.0, 0.0005)
        y = _gauss(x, 1.5, 0.01, 100.0) + _gauss(x, 2.0, 0.05, 100.0)
        broad = find_peaks_1d(x, y, min_fwhm_deg=0.03)
        narrow = find_peaks_1d(x, y, max_fwhm_deg=0.03)
        self.assertEqual([round(p.center, 2) for p in broad], [2.0])
        self.assertEqual([round(p.center, 2) for p in narrow], [1.5])

    def test_max_peaks_keeps_strongest(self):
        x = np.arange(1.0, 3.0, 0.0005)
        y = (_gauss(x, 1.5, 0.02, 300.0) + _gauss(x, 2.0, 0.02, 100.0)
             + _gauss(x, 2.5, 0.02, 200.0))
        peaks = find_peaks_1d(x, y, max_peaks=2)
        self.assertEqual([round(p.center, 2) for p in peaks], [1.5, 2.5])

    def test_height_without_baseline_is_local(self):
        x = np.arange(1.0, 2.0, 0.001)
        y = 1000.0 + _gauss(x, 1.5, 0.02, 300.0)
        peak = find_peaks_1d(x, y)[0]
        # 不给基线时峰高是局部显著度，不是 1300 counts 的绝对高度
        self.assertAlmostEqual(peak.height, 300.0, delta=0.03 * 300.0)
        self.assertAlmostEqual(peak.prominence, peak.height,
                               delta=0.03 * 300.0)

    def test_validation(self):
        x = np.arange(1.0, 2.0, 0.01)
        y = np.ones(x.size)
        with self.assertRaises(ValueError):
            find_peaks_1d(x[::-1], y)
        with self.assertRaises(ValueError):
            find_peaks_1d(x, y[:-1])
        with self.assertRaises(ValueError):
            find_peaks_1d(x[:2], y[:2])
        with self.assertRaises(ValueError):
            find_peaks_1d(x, np.where(x > 1.5, np.nan, y))
        with self.assertRaises(ValueError):
            find_peaks_1d(x, y, baseline=np.ones(x.size - 1))
        with self.assertRaises(ValueError):
            find_peaks_1d(x, y, min_distance_deg=-0.01)
        with self.assertRaises(ValueError):
            find_peaks_1d(x, y, min_fwhm_deg=0.05, max_fwhm_deg=0.01)
        with self.assertRaises(ValueError):
            find_peaks_1d(x, y, max_peaks=0)
        with self.assertRaises(ValueError):
            find_peaks_1d(x, y, min_prominence=-1.0)
        with self.assertRaises(ValueError):
            find_peaks_1d(np.ones((5, 5)), np.ones((5, 5)))


if __name__ == "__main__":
    unittest.main()
