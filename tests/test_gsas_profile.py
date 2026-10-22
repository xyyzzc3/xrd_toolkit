"""GSAS-II 同口径峰形/峰宽测试（2026-10-22）。

数字来自真 GSAS-II 的 `getFCJVoigt3` / `GetSampleSigGam` 实测
（conda env `gsas2`，见 /tmp 探针与 scripts/gsas2_crosscheck.py）：

    (sig, gam) centideg²/centideg → FWHM(°)
    (18.033, 0)   → 0.10000
    (18.033, 1)   → 0.10535
    (18.033, 10)  → 0.16346
    (0, 10)       → 0.10000
    (1, 5)        → 0.05976

两条硬指标：**面积不变**（峰高 ∝ 1/FWHM）、**峰宽一致**（同参数同宽）。
"""
import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.simulation import core  # noqa: E402
from xrd_toolkit.simulation import gsas_profile as gp  # noqa: E402

STRUCT_DIR = Path(__file__).resolve().parents[1] / "data" / "structures"
CU_KA = 1.5406


def _fwhm(x, y, center, span):
    m = (x > center - span) & (x < center + span)
    xs, ys = x[m], y[m]
    i = int(np.argmax(ys))
    half = ys[i] / 2.0
    left = float(np.interp(half, ys[:i + 1], xs[:i + 1]))
    right = float(np.interp(half, ys[i:][::-1], xs[i:][::-1]))
    return right - left


class TestTchMatchesGsas(unittest.TestCase):
    """TCH 合成后的总 FWHM 与 GSAS-II 的 FCJ 峰形逐点同值。"""

    def test_probe_values(self):
        cases = {
            (18.033, 0.0): 0.10000,
            (18.033, 1.0): 0.10535,
            (18.033, 10.0): 0.16346,
            (0.0, 10.0): 0.10000,
            (1.0, 5.0): 0.05976,
        }
        for (sig, gam), expected in cases.items():
            f, _eta = gp.sig_gam_to_fwhm_eta(sig, gam)
            self.assertAlmostEqual(float(f), expected, delta=2e-5)

    def test_pure_gaussian_and_lorentzian_limits(self):
        f, eta = gp.sig_gam_to_fwhm_eta(18.033, 0.0)
        self.assertAlmostEqual(float(f), 0.1, delta=2e-4)
        self.assertLess(float(eta), 1e-3)
        f, eta = gp.sig_gam_to_fwhm_eta(0.0, 10.0)
        self.assertAlmostEqual(float(f), 0.1, delta=2e-4)
        self.assertGreater(float(eta), 0.999)


class TestGsasWidthModel(unittest.TestCase):
    """Size/Mustrain/仪器项的单位与系数，与 GSAS-II 同式。"""

    def test_size_is_lorentzian_with_k_one(self):
        # Sgam = 1.8λ/(π·Size·cosθ)，Size 用 µm
        sig, gam = gp.sample_sig_gam(30.38, CU_KA, size_um=0.05)
        theta = math.radians(30.38 / 2.0)
        expected = 1.8 * CU_KA / (math.pi * 0.05 * math.cos(theta))
        self.assertAlmostEqual(float(gam), expected, places=10)
        self.assertAlmostEqual(float(sig), 0.0, places=12)
        # 与 Scherrer K=1 等价：Γ(rad) = λ/(D_Å cosθ)
        fwhm, _ = gp.sig_gam_to_fwhm_eta(sig, gam)
        self.assertAlmostEqual(float(fwhm), math.degrees(
            CU_KA / (500.0 * math.cos(theta))), delta=2e-4)

    def test_mustrain_coefficient(self):
        # Mgam = 0.018·M·tanθ/π；M=4000 ↔ ε=0.1%（4ε·10⁶）
        theta = math.radians(30.38 / 2.0)
        sig, gam = gp.sample_sig_gam(30.38, CU_KA, mustrain=4000.0)
        expected = 0.018 * 4000.0 * math.tan(theta) / math.pi
        self.assertAlmostEqual(float(gam), expected, places=10)
        self.assertAlmostEqual(float(sig), 0.0, places=12)
        physical = math.degrees(4.0 * 0.001 * math.tan(theta))
        self.assertAlmostEqual(float(gam) / 100.0, physical, delta=2e-4)

    def test_mixing_zero_moves_to_gaussian(self):
        sig, gam = gp.sample_sig_gam(30.38, CU_KA, size_um=0.05, size_mix=0.0)
        self.assertAlmostEqual(float(gam), 0.0, places=12)
        # 全 Gaussian 时 FWHM = Sgam（GSAS 的 ateln2=8ln2 正好消掉）
        fwhm, _ = gp.sig_gam_to_fwhm_eta(sig, gam)
        expected = 1.8 * CU_KA / (math.pi * 0.05 * math.cos(
            math.radians(30.38 / 2.0))) / 100.0
        self.assertAlmostEqual(float(fwhm), expected, places=8)

    def test_instrument_terms(self):
        sig, gam = gp.instrument_sig_gam(90.0, u=0.3, v=0.2, w=0.5,
                                         x=1.0, y=0.5, z=0.1)
        self.assertAlmostEqual(float(sig), 1.0, places=12)      # tan45=1
        # gam = X/cosθ + Y·tanθ + Z（不是 (X+Y)/cosθ）
        self.assertAlmostEqual(float(gam), 1.0 / math.cos(math.pi / 4) + 0.5 + 0.1,
                               places=12)
        # GSAS 的 0.001 下限是在仪器 + 样品求和之后
        sig, gam = gp.reflection_sig_gam(30.0)
        self.assertAlmostEqual(float(sig), 0.001, places=12)
        self.assertAlmostEqual(float(gam), 0.001, places=12)


class TestAreaNormalization(unittest.TestCase):
    """面积守恒：伪 Voigt 积分 = 1；整谱峰高 ∝ 1/FWHM。"""

    def test_pv_integrates_to_one(self):
        for eta in (0.0, 0.5, 1.0):
            x = np.arange(-10.0, 10.0, 0.0005)
            y = gp.pv_area_normalized(x, 0.0, 0.1, eta)
            self.assertAlmostEqual(float(np.trapezoid(y, x)), 1.0, delta=0.01)

    def test_peak_height_scales_inversely_with_width(self):
        si = core.load_structure(STRUCT_DIR / "Si_9008565.cif")
        kw = dict(wavelength=CU_KA, two_theta_range=(20.0, 40.0),
                  step_deg=0.002, size_um=1e5, mustrain=0.0,
                  normalize=False)
        _, y1, _, _ = gp.simulate_pattern_gsas(si, w=18.033, **kw)
        _, y2, _, _ = gp.simulate_pattern_gsas(si, w=72.132, **kw)
        # FWHM 0.1° → 0.2°，面积不变 ⇒ 峰高减半
        self.assertAlmostEqual(float(y1.max() / y2.max()), 2.0, delta=0.02)


class TestFullPatternWidths(unittest.TestCase):
    """整谱峰宽与公式同值（GSAS-II 实测 0.1003/0.1851/0.0157）。"""

    @classmethod
    def setUpClass(cls):
        cls.lab6 = core.load_structure(STRUCT_DIR / "LaB6_1000055.cif")

    def _sim(self, **kw):
        kw.setdefault("w", 0.0)   # 单项测试只开一个展宽通道
        kw.setdefault("size_um", 1e5)
        kw.setdefault("mustrain", 0.0)
        return gp.simulate_pattern_gsas(
            self.lab6, two_theta_range=(29.0, 32.0), step_deg=0.0002, **kw)

    def test_w_18_033(self):
        tth, y, _, _ = self._sim(w=18.033)
        self.assertAlmostEqual(_fwhm(tth, y, 30.38, 0.5), 0.1, delta=0.002)

    def test_x_one_centideg(self):
        tth, y, _, _ = self._sim(x=1.0)
        self.assertAlmostEqual(_fwhm(tth, y, 30.38, 0.2), 0.0105, delta=0.001)

    def test_size_50_nm(self):
        tth, y, _, _ = self._sim(size_um=0.05)
        self.assertAlmostEqual(_fwhm(tth, y, 30.38, 0.5), 0.183, delta=0.003)

    def test_mustrain_1000(self):
        tth, y, _, _ = self._sim(mustrain=1000.0)
        self.assertAlmostEqual(_fwhm(tth, y, 30.38, 0.2), 0.0156, delta=0.001)


class TestPolarizationAndConversions(unittest.TestCase):
    def test_lp_matches_legacy_at_unpolarized(self):
        theta = math.radians(30.38 / 2.0)
        # core.lp_factor 保留了旧口径的常数 4（峰表归一化会吸收），
        # gsas_profile.lp_gsas 是 GSAS 原式；差常数 4，θ 依赖相同。
        self.assertAlmostEqual(4.0 * float(gp.lp_gsas(30.38, 0.5)),
                               core.lp_factor(theta), places=12)

    def test_polarization_changes_theta_dependence(self):
        # P=0.95 时高角相对低角更强（同步辐射水平偏振）
        low, high = 10.0, 80.0
        ratio_05 = float(gp.lp_gsas(high, 0.5) / gp.lp_gsas(low, 0.5))
        ratio_95 = float(gp.lp_gsas(high, 0.95) / gp.lp_gsas(low, 0.95))
        self.assertGreater(ratio_95, ratio_05)

    def test_legacy_conversion_constants(self):
        kw = gp.legacy_to_gsas(u=0.08, w=0.01, x=1.0, size_nm=50.0,
                               strain=0.002)
        self.assertAlmostEqual(kw["w"], 18.0337, delta=1e-3)
        self.assertAlmostEqual(kw["u"], 0.08 * 1803.3688, delta=1e-2)
        self.assertAlmostEqual(kw["x"], 100.0)
        self.assertAlmostEqual(kw["size_um"], 0.05)
        self.assertAlmostEqual(kw["mustrain"], 8000.0)


if __name__ == "__main__":
    unittest.main()
