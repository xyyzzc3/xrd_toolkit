"""全谱最小二乘拟合测试（2026-10-22）。

用 GSAS 口径引擎合成"已知参数"的谱，再让拟合器从这些谱反解参数：
  * 从真值出发（单阶段）必须回到真值附近，Rwp ≈ 0；
  * 自动初值（不喂真值）也要收敛到 Rwp < 1%（无噪声）；
  * 固定背景 / 多项式背景都要能正确扣掉；
  * Rwp 的计算与独立实现一致。
"""
import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.simulation import core  # noqa: E402
from xrd_toolkit.simulation.fit import FitResult, fit_pattern, rwp  # noqa: E402
from xrd_toolkit.simulation.gsas_profile import (  # noqa: E402
    pv_area_normalized,
    reflection_sig_gam,
    sig_gam_to_fwhm_eta,
    simulate_pattern_gsas,
)

STRUCT_DIR = Path(__file__).resolve().parents[1] / "data" / "structures"
CU_KA = 1.5406


def _synth(structure, *, zero_deg=0.012, u=0.5, v=-0.1, w=18.0,
           x=1.0, y=0.3, scale=1000.0, step=0.005):
    tth, y, _, _ = simulate_pattern_gsas(
        structure, wavelength=CU_KA, two_theta_range=(10.0, 80.0),
        step_deg=step, scale=scale, size_um=1e5, mustrain=0.0,
        zero_deg=zero_deg, u=u, v=v, w=w, x=x, y=y)
    return tth, y


def _synth_modified(structure, *, factors=None, w=18.0, step=0.01,
                    two_theta_range=(10.0, 80.0), background=0.0):
    """合成一条"每峰强度被 factors 改过"的谱（Le Bail / 强度测试用）。"""
    sticks, inten, _, _ = core.pattern_from_structure(
        structure, wavelength=CU_KA, two_theta_range=two_theta_range,
        min_intensity=0.0)
    if factors is None:
        factors = 1.0 + 0.15 * np.cos(np.arange(len(sticks)) * 0.7)
    sig, gam = reflection_sig_gam(sticks, CU_KA, w=w)
    fwhm, eta = sig_gam_to_fwhm_eta(sig, gam)
    tth = np.arange(two_theta_range[0],
                    two_theta_range[1] + step / 2.0, step)
    y = np.zeros_like(tth)
    for c, ii, fw, et, fac in zip(sticks, inten, fwhm, eta, factors):
        y += float(fac) * ii * pv_area_normalized(tth, c, fw, et)
    return tth, y + background, factors


class TestFitRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.st = core.load_structure(STRUCT_DIR / "LaB6_1000055.cif")

    def test_from_truth_single_stage(self):
        true = dict(zero=0.012, u=20.0, v=-5.0, w=18.0, x=5.0, y=1.0)
        tth, y = _synth(self.st, **{f"{k}_deg" if k == "zero" else k: val
                                    for k, val in true.items()})
        y = y + 20.0 + 0.3 * (tth - 10.0)
        res = fit_pattern(
            self.st, tth, y, background=2, weights="uniform",
            free=("zero", "u", "v", "w", "x", "y"),
            initial=true, stages=[("zero", "u", "v", "w", "x", "y")])
        self.assertIsInstance(res, FitResult)
        self.assertLess(res.rwp, 0.05)
        for name, expected in true.items():
            self.assertAlmostEqual(res.params[name], expected,
                                   delta=max(0.02 * abs(expected), 0.01))
        self.assertTrue(np.isfinite(res.stderr["w"]))

    def test_auto_initial_gaussian_profile(self):
        """纯 Gaussian 合成谱：不喂真值也应收敛到 Rwp < 1%。"""
        tth, y = _synth(self.st, zero_deg=0.01, u=0.5, v=-0.1, w=18.0,
                        x=0.0, y=0.0)
        y = y + 20.0 + 0.3 * (tth - 10.0)
        res = fit_pattern(
            self.st, tth, y, background=2, weights="uniform",
            free=("zero", "u", "v", "w", "x", "y"))
        self.assertLess(res.rwp, 1.0)
        self.assertAlmostEqual(res.params["zero"], 0.01, delta=0.005)
        self.assertAlmostEqual(res.params["w"], 18.0, delta=2.0)

    def test_lorentzian_profile_found_from_default_staging(self):
        """默认分阶段、不喂初值：X=5/Y=1 的 Lorentzian 峰形也能找回。

        2026-10-23 的坑：X/Y 从 0 同时出发时，拟合会停在"几乎没有尾巴"
        的局部极小；默认小非零初值 + X/Y 阶段多起点 + 末尾联合抛光
        必须保住这个解。
        """
        true = dict(zero=0.012, u=20.0, v=-5.0, w=18.0, x=5.0, y=1.0)
        tth, y = _synth(self.st, **{f"{k}_deg" if k == "zero" else k: val
                                    for k, val in true.items()})
        y = y + 20.0 + 0.3 * (tth - 10.0)
        res = fit_pattern(
            self.st, tth, y, background=2, weights="uniform",
            free=("zero", "u", "v", "w", "x", "y"))
        self.assertLess(res.rwp, 0.2)
        for name, expected in true.items():
            self.assertAlmostEqual(res.params[name], expected,
                                   delta=max(0.05 * abs(expected), 0.05))

    def test_fixed_background_array(self):
        tth, y = _synth(self.st, u=0.0, v=0.0, x=0.0, y=0.0)
        bg = 30.0 + 0.5 * (tth - 10.0)
        y = y + bg
        res = fit_pattern(
            self.st, tth, y, background=bg, weights="uniform",
            free=("zero", "w"), initial={"w": 18.0},
            stages=[("zero", "w")])
        self.assertLess(res.rwp, 0.05)
        np.testing.assert_allclose(res.background, bg)

    def test_no_free_parameters(self):
        tth, y = _synth(self.st)
        res = fit_pattern(self.st, tth, y, background=2,
                          weights="uniform", free=(),
                          initial={"w": 18.0})
        self.assertTrue(res.success)
        self.assertGreaterEqual(res.params["scale"], 0.0)


class TestFitModes(unittest.TestCase):
    """Le Bail 逐峰强度、分段线性背景修正、非负 X/Y 边界。"""

    @classmethod
    def setUpClass(cls):
        cls.st = core.load_structure(STRUCT_DIR / "LaB6_1000055.cif")

    def test_lebail_recovers_modified_intensities(self):
        # 每峰乘一个已知因子；Le Bail 应逐峰反解出同样的因子
        tth, y, factors = _synth_modified(self.st)
        res = fit_pattern(
            self.st, tth, y, background=None, weights="uniform",
            refine_intensities=True, free=("zero", "u", "v", "w"),
            initial={"w": 18.0},
            stages=[("zero", "w"), ("u", "v", "w")])
        self.assertLess(res.rwp, 0.05)
        good = res.peak_table_intensities > 0.1
        ratio = (res.peak_intensities[good]
                 / res.peak_table_intensities[good])
        np.testing.assert_allclose(ratio, factors[good], rtol=0.01, atol=0.01)

    def test_lebail_rejects_b_iso(self):
        tth, y = _synth(self.st, x=0.0, y=0.0)
        with self.assertRaises(ValueError):
            fit_pattern(self.st, tth, y, refine_intensities=True,
                        free=("b_iso",))

    def test_smooth_intensity_poly_recovers_factor(self):
        sticks, _, _, _ = core.pattern_from_structure(
            self.st, wavelength=CU_KA, two_theta_range=(10.0, 80.0),
            min_intensity=0.0)
        z = (sticks - sticks.mean()) / (0.5 * (sticks.max() - sticks.min()))
        factors = 1.0 + 0.10 * z
        tth, y, _ = _synth_modified(self.st, factors=factors)
        res = fit_pattern(
            self.st, tth, y, background=None, weights="uniform",
            intensity_poly=1, free=("zero", "u", "v", "w"),
            initial={"w": 18.0},
            stages=[("zero", "w"), ("u", "v", "w")])
        self.assertLess(res.rwp, 0.1)
        self.assertAlmostEqual(res.params["intensity_a1"], 0.10, delta=0.02)

    def test_intensity_poly_rejected_with_lebail(self):
        tth, y = _synth(self.st, x=0.0, y=0.0)
        with self.assertRaises(ValueError):
            fit_pattern(self.st, tth, y, refine_intensities=True,
                        intensity_poly=1, free=("zero", "w"))

    def test_background_knots_correct_a_biased_baseline(self):
        tth, y = _synth(self.st, x=0.0, y=0.0)
        true_bg = 80.0 + 40.0 * np.exp(-(tth - 10.0) / 4.0)
        wrong_bg = true_bg + 12.0 + 0.15 * (tth - 10.0)
        res = fit_pattern(
            self.st, tth, y + true_bg, background=wrong_bg,
            background_knots=0.5, weights="uniform",
            free=("zero", "u", "v", "w"), initial={"w": 18.0},
            stages=[("zero", "w"), ("u", "v", "w")])
        self.assertLess(res.rwp, 0.2)
        # 修正后的背景应比错误基线更接近真背景
        self.assertLess(float(np.mean(np.abs(res.background - true_bg))),
                        float(np.mean(np.abs(wrong_bg - true_bg))))

    def test_cell_refinement_recovers_scaled_lattice(self):
        from pymatgen.core import Lattice, Structure
        base = core.load_structure(STRUCT_DIR / "LiFePO4_olivine.cif")
        lat = Lattice.from_parameters(
            base.lattice.a * 1.01, base.lattice.b * 1.01,
            base.lattice.c * 1.01, *base.lattice.angles)
        scaled = Structure(
            lat, [site.species for site in base],
            [site.frac_coords for site in base])
        tth, y, _, _ = simulate_pattern_gsas(
            scaled, wavelength=CU_KA, two_theta_range=(10.0, 80.0),
            step_deg=0.01, scale=1000.0, w=18.033, size_um=1e5,
            mustrain=0.0)
        res = fit_pattern(
            base, tth, y, background=None, weights="uniform",
            free=("a", "b", "c"),
            initial={"zero": 0.0, "u": 0.0, "v": 0.0, "w": 18.033})
        # 基础强度表按初始晶胞算的 B/LP 不再重算，允许 ~1% 的强度差
        self.assertLess(res.rwp, 2.0)
        self.assertAlmostEqual(res.params["a"], base.lattice.a * 1.01,
                               delta=0.01)
        self.assertAlmostEqual(res.params["b"], base.lattice.b * 1.01,
                               delta=0.01)
        self.assertAlmostEqual(res.params["c"], base.lattice.c * 1.01,
                               delta=0.01)

    def test_extra_phase_scale_recovered(self):
        main = core.load_structure(STRUCT_DIR / "LiFePO4_olivine.cif")
        al = core.load_structure(STRUCT_DIR / "Al_metal.cif")
        kw = dict(wavelength=CU_KA, two_theta_range=(10.0, 80.0),
                  step_deg=0.01, w=18.033, size_um=1e5, mustrain=0.0)
        tth, y1, _, _ = simulate_pattern_gsas(main, scale=1000.0, **kw)
        _, y2, _, _ = simulate_pattern_gsas(al, scale=300.0, **kw)
        res = fit_pattern(
            main, tth, y1 + y2, background=None, weights="uniform",
            free=("zero", "w"),
            initial={"zero": 0.0, "u": 0.0, "v": 0.0, "w": 18.033},
            extra_phases=[{
                "structure": al, "label": "Al",
                "a": 4.049, "b": 4.049, "c": 4.049,
                "size_um": 1e4, "mustrain": 0.0, "b_iso": None}])
        self.assertLess(res.rwp, 0.2)
        self.assertGreater(res.params["Al:scale"], 0.0)
        self.assertGreater(res.params["scale"], 0.0)

    def test_x_y_default_bounds_are_nonnegative(self):
        from xrd_toolkit.simulation.fit import DEFAULT_BOUNDS
        self.assertEqual(DEFAULT_BOUNDS["x"][0], 0.0)
        self.assertEqual(DEFAULT_BOUNDS["y"][0], 0.0)


class TestFitHelpers(unittest.TestCase):
    def test_rwp_matches_manual(self):
        obs = np.array([10.0, 20.0, 30.0, 40.0])
        calc = np.array([11.0, 19.0, 31.0, 38.0])
        w = 1.0 / np.maximum(obs, 1.0)
        expected = 100.0 * math.sqrt(np.sum(w * (obs - calc) ** 2)
                                     / np.sum(w * obs ** 2))
        self.assertAlmostEqual(rwp(obs, calc, "poisson"), expected, places=12)
        expected_u = 100.0 * math.sqrt(np.sum((obs - calc) ** 2)
                                       / np.sum(obs ** 2))
        self.assertAlmostEqual(rwp(obs, calc, "uniform"), expected_u,
                               places=12)


if __name__ == "__main__":
    unittest.main()
