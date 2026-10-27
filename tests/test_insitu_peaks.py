"""模型无关 (200) 双峰质心测试（2026-10-28）。

合成 rich/poor 双峰 + 线性背景，确认 `_doublet_stats` 的质心落在
两个峰心之间，并随哪个峰更强而移动。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import check_insitu_peaks as cip  # noqa: E402


def _doublet(a_rich, a_poor):
    t = np.linspace(1.15, 1.58, 800)
    y = 20.0 + 3.0 * (t - 1.15)
    for amp, center in ((a_rich, 1.355), (a_poor, 1.430)):
        y += amp * np.exp(-0.5 * ((t - center) / 0.014) ** 2)
    return t, y


class TestDoubletStats(unittest.TestCase):
    def test_centroid_moves_with_phase(self):
        _t, y_rich = _doublet(200.0, 0.0)
        _t2, y_poor = _doublet(0.0, 200.0)
        _t3, y_both = _doublet(100.0, 100.0)
        t = _doublet(1, 1)[0]
        _a1, c_rich = cip._doublet_stats(t, y_rich)
        _a2, c_poor = cip._doublet_stats(t, y_poor)
        _a3, c_both = cip._doublet_stats(t, y_both)
        self.assertAlmostEqual(c_rich, 1.355, delta=0.006)
        self.assertAlmostEqual(c_poor, 1.430, delta=0.006)
        self.assertGreater(c_both, c_rich)
        self.assertLess(c_both, c_poor)

    def test_area_positive_and_finite(self):
        t, y = _doublet(150.0, 80.0)
        area, cen = cip._doublet_stats(t, y)
        self.assertGreater(area, 0.0)
        self.assertTrue(np.isfinite(cen))


if __name__ == "__main__":
    unittest.main()
