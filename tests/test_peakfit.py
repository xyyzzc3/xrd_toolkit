"""单峰峰形拟合测试（2026-10-23）。"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.simulation import peakfit  # noqa: E402


class TestLineShapes(unittest.TestCase):
    def test_pearson7_limits(self):
        x = np.arange(-0.1, 0.1, 0.0005)
        lo = peakfit.lorentzian(x, 0.0, 0.02)
        g = peakfit.gaussian(x, 0.0, 0.02)
        np.testing.assert_allclose(peakfit.pearson7(x, 0.0, 0.02, 1.0),
                                   lo, rtol=1e-6, atol=1e-8)
        # m 很大 → 接近 Gaussian（中心附近；远处尾部 Pearson 仍略重）
        near = np.abs(x) < 0.04
        np.testing.assert_allclose(peakfit.pearson7(x[near], 0.0, 0.02, 80.0),
                                   g[near], atol=5e-3)

    def test_fwhm_definition(self):
        for m in (1.0, 2.0, 5.0):
            half = peakfit.pearson7(np.array([0.01]), 0.0, 0.02, m)[0]
            self.assertAlmostEqual(float(half), 0.5, places=10)


class TestSinglePeakFit(unittest.TestCase):
    def _synth(self, shape, **kw):
        x = np.arange(1.0, 2.0, 0.001)
        bg = 100.0 + 30.0 * (x - 1.5)
        if shape == "split_pearson7":
            peak = peakfit.split_pearson7(x, 1.5, kw["fwl"], kw["fwr"],
                                          kw["m"])
        else:
            peak = peakfit._shape(x, shape, 1.5, 0.02, 0.02, 0.02,
                                  kw.get("m", 2.0), kw.get("eta", 0.5))
        return x, bg + 500.0 * peak

    def test_gaussian(self):
        x, y = self._synth("gaussian")
        fit = peakfit.fit_single_peak(x, y, 1.5, shape="gaussian")
        self.assertAlmostEqual(fit.center, 1.5, delta=1e-4)
        self.assertAlmostEqual(fit.fwhm, 0.02, delta=1e-4)

    def test_lorentzian(self):
        x, y = self._synth("lorentzian")
        fit = peakfit.fit_single_peak(x, y, 1.5, shape="lorentzian")
        self.assertAlmostEqual(fit.fwhm, 0.02, delta=1e-4)

    def test_pseudovoigt(self):
        x, y = self._synth("pseudovoigt", eta=0.4)
        fit = peakfit.fit_single_peak(x, y, 1.5, shape="pseudovoigt")
        self.assertAlmostEqual(fit.fwhm, 0.02, delta=1e-4)
        self.assertAlmostEqual(fit.eta, 0.4, delta=0.02)

    def test_split_pearson7(self):
        x, y = self._synth("split_pearson7", fwl=0.018, fwr=0.024, m=2.0)
        fit = peakfit.fit_single_peak(x, y, 1.5,
                                      shape="split_pearson7")
        self.assertAlmostEqual(fit.fwhm_left, 0.018, delta=2e-4)
        self.assertAlmostEqual(fit.fwhm_right, 0.024, delta=2e-4)
        self.assertAlmostEqual(fit.m, 2.0, delta=0.05)

    def test_gaussian_area(self):
        # Gaussian 面积 = 峰高 × FWHM × √(π/(4ln2))
        x, y = self._synth("gaussian")
        fit = peakfit.fit_single_peak(x, y, 1.5, shape="gaussian")
        expected = fit.height * fit.fwhm * np.sqrt(np.pi / (4 * np.log(2)))
        self.assertAlmostEqual(fit.area, expected, delta=0.01 * expected)


if __name__ == "__main__":
    unittest.main()
