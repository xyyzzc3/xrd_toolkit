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
from xrd_toolkit.simulation.fit import (  # noqa: E402
    FitResult,
    _piecewise_linear_basis,
    fit_pattern,
    rwp,
)
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


def _synth_two_phase(main, extra, *, factor=0.7, extra_scale=100.0,
                     w=18.0, step=0.01, two_theta_range=(10.0, 80.0),
                     peak_deg=44.72):
    """主相 + 第二相；第二相在 peak_deg 的反射强度乘 factor。"""
    tth, y_main, _, _ = simulate_pattern_gsas(
        main, wavelength=CU_KA, two_theta_range=two_theta_range,
        step_deg=step, scale=1000.0, size_um=1e5, mustrain=0.0,
        zero_deg=0.0, u=0.5, v=-0.1, w=w, x=1.0, y=0.3)
    sticks, inten, _, _ = core.pattern_from_structure(
        extra, wavelength=CU_KA, two_theta_range=two_theta_range,
        min_intensity=0.0)
    sig, gam = reflection_sig_gam(
        sticks, CU_KA, u=0.5, v=-0.1, w=w, x=1.0, y=0.3,
        size_um=1e5, mustrain=0.0)
    fwhm, eta = sig_gam_to_fwhm_eta(sig, gam)
    factors = np.ones(len(sticks))
    i_pert = int(np.argmin(np.abs(sticks - peak_deg)))
    factors[i_pert] = factor
    y_extra = np.zeros_like(tth)
    for c, ii, fw, et, fac in zip(sticks, inten, fwhm, eta, factors):
        y_extra += (extra_scale * float(fac) * ii
                    * pv_area_normalized(tth, c, fw, et))
    true_bg = 50.0 + 0.5 * (tth - two_theta_range[0])
    return tth, y_main + y_extra + true_bg, true_bg, sticks, i_pert


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

    def test_cubic_cell_refinement_recovers_scaled_a(self):
        from pymatgen.core import Lattice, Structure
        base = core.load_structure(STRUCT_DIR / "LaB6_1000055.cif")
        scaled = Structure(
            Lattice.cubic(base.lattice.a * 1.005),
            [site.species for site in base],
            [site.frac_coords for site in base])
        tth, y, _, _ = simulate_pattern_gsas(
            scaled, wavelength=CU_KA, two_theta_range=(10.0, 80.0),
            step_deg=0.005, scale=1000.0, w=18.0, size_um=1e5,
            mustrain=0.0)
        res = fit_pattern(
            base, tth, y, background=None, weights="uniform",
            free=("zero", "w", "a"),
            initial={"w": 18.0, "a": base.lattice.a})
        self.assertLess(res.rwp, 1.0)
        self.assertAlmostEqual(res.params["a"], 1.005 * base.lattice.a,
                               delta=0.002)
        with self.assertRaises(ValueError):
            fit_pattern(base, tth, y, background=None, weights="uniform",
                        free=("a", "b"))

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
        # 峰位（含 zero）与第二相峰位：布拉格刻度要用，结构模式也要给
        self.assertIsNotNone(res.peak_tth)
        self.assertGreater(len(res.peak_tth), 5)
        self.assertIsNotNone(res.extra_peak_tth)
        self.assertIn("Al", res.extra_peak_tth)
        al_tt = res.extra_peak_tth["Al"]
        # 铝 (111) 在 Cu Kα 下约 38.5°
        self.assertTrue(np.any((al_tt > 38.0) & (al_tt < 39.0)))

    def test_x_y_default_bounds_are_nonnegative(self):
        from xrd_toolkit.simulation.fit import DEFAULT_BOUNDS
        self.assertEqual(DEFAULT_BOUNDS["x"][0], 0.0)
        self.assertEqual(DEFAULT_BOUNDS["y"][0], 0.0)


class TestDynamicRefinement(unittest.TestCase):
    """分元素 B 与可精修织构（md_r）：合成谱能精确反解。"""

    @classmethod
    def setUpClass(cls):
        cls.st = core.load_structure(STRUCT_DIR / "LaB6_1000055.cif")
        cls.kw = dict(wavelength=CU_KA, two_theta_range=(10.0, 80.0),
                      step_deg=0.005, scale=1000.0, size_um=1e5,
                      mustrain=0.0)
        cls.true = dict(zero_deg=0.01, u=0.5, v=-0.1, w=18.0, x=0.0, y=0.0)
        cls.init = dict(zero=0.01, u=0.5, v=-0.1, w=18.0, x=0.0, y=0.0,
                        size_um=1e5, mustrain=0.0, b_iso=0.0, polariz=0.5)

    def test_per_element_b_recovered(self):
        tth, y, _, _ = simulate_pattern_gsas(
            self.st, b_iso={"La": 0.5, "B": 2.0}, **self.kw, **self.true)
        res = fit_pattern(
            self.st, tth, y, background=None, weights="uniform",
            free=("b_La", "b_B"),
            initial={**self.init, "b_La": 1.0, "b_B": 1.0})
        self.assertLess(res.rwp, 0.05)
        self.assertAlmostEqual(res.params["b_La"], 0.5, delta=0.02)
        self.assertAlmostEqual(res.params["b_B"], 2.0, delta=0.02)

    def test_march_dollase_r_recovered(self):
        tth, y, _, _ = simulate_pattern_gsas(
            self.st, md=((1, 1, 1), 0.7), **self.kw, **self.true)
        res = fit_pattern(
            self.st, tth, y, md=((1, 1, 1), 1.0), background=None,
            weights="uniform", free=("md_r",),
            initial={**self.init, "md_r": 1.0})
        self.assertLess(res.rwp, 0.05)
        self.assertAlmostEqual(res.params["md_r"], 0.7, delta=0.01)

    def test_global_b_iso_is_not_parsed_as_an_element(self):
        # 回归：b_iso 不能按 b_<元素> 解析成元素 "iso"
        tth, y = _synth(self.st, x=0.0, y=0.0)
        res = fit_pattern(
            self.st, tth, y, background=2, weights="uniform",
            free=("zero", "w", "b_iso"), initial={"b_iso": 0.5})
        self.assertIn("b_iso", res.params)

    def test_rejects_global_and_element_b_together(self):
        tth, y = _synth(self.st)
        with self.assertRaises(ValueError):
            fit_pattern(self.st, tth, y, background=2, weights="uniform",
                        free=("b_iso", "b_La"))


class TestPlotFitLayout(unittest.TestCase):
    """论文式拟合图：默认自动加布拉格刻度，旧的两栏布局仍可用。"""

    def _result(self):
        tth = np.linspace(10.0, 20.0, 200)
        peak = np.exp(-((tth - 15.0) / 0.1) ** 2)

        class Res:
            pass

        r = Res()
        r.tth = tth
        r.y_obs = 100.0 + peak
        r.y_calc = 100.0 + 0.98 * peak
        r.residual = r.y_obs - r.y_calc
        r.background = np.full_like(tth, 100.0)
        r.rwp = 1.0
        r.rp = 1.0
        r.peak_tth = np.array([15.0])
        r.extra_peak_tth = {"Al": np.array([16.0])}
        r.phase_label = "LaB6"
        return r

    def test_auto_bragg_and_legacy_layout(self):
        import tempfile
        from xrd_toolkit.simulation.plots import plot_fit
        root = Path(tempfile.mkdtemp(prefix="xrd_plot_"))
        out = root / "auto.png"
        plot_fit(self._result(), "test", str(out))
        self.assertTrue(out.exists() and out.stat().st_size > 0)
        out2 = root / "legacy.png"
        plot_fit(self._result(), "test", str(out2), bragg=None)
        self.assertTrue(out2.exists() and out2.stat().st_size > 0)


class TestFitReport(unittest.TestCase):
    """报告要能独立当结果看：参数 + 逐峰强度 + 峰位（2026-10-26）。"""

    def _result(self, **overrides):
        base = dict(
            params={"a": 4.1568}, stderr={"a": 1e-4}, rwp=1.5, rp=1.0,
            chi2=2.0, reduced_chi2=1.1, n_obs=100, n_params=1,
            success=True, message="ok",
            tth=np.array([1.0, 2.0]), y_obs=np.array([2.0, 3.0]),
            y_calc=np.array([2.1, 2.9]), residual=np.array([-0.1, 0.1]),
            background=np.zeros(2), scale=1.0, initial={"a": 4.0},
            peak_tth=np.array([1.5, 2.5]),
            peak_intensities=np.array([10.0, 0.0]),
            peak_table_intensities=np.array([5.0, 0.0]),
            extra_peak_tth={"Al": np.array([3.0])})
        base.update(overrides)
        return FitResult(**base)

    def test_report_contains_per_peak_table_and_extra_positions(self):
        text = self._result().report()
        self.assertIn("I_fit", text)
        self.assertIn(" 1.5000", text)
        self.assertIn("extra phase Al Bragg 2theta: 3.0000", text)
        self.assertIn("fit/table ratio", text)

    def test_report_marks_zero_table_intensity_as_nan(self):
        self.assertIn("nan", self._result().report())

    def test_report_lists_bragg_positions_without_le_bail(self):
        text = self._result(peak_intensities=None,
                            peak_table_intensities=None).report()
        self.assertIn("Bragg 2theta: 1.5000 2.5000", text)

    def test_report_lists_extra_phase_per_reflection(self):
        text = self._result(extra_lebail={"Al": {
            "tth": np.array([3.0, 4.0]),
            "i_fit": np.array([5.0, 0.0]),
            "i_table": np.array([10.0, 1e-31]),
        }}).report()
        self.assertIn("extra phase Al (Le Bail)", text)
        self.assertIn("0.500", text)
        self.assertNotIn("1e-31", text)   # 数值噪声反射不打印


class TestExtraPhaseLebailAndBackgroundSmooth(unittest.TestCase):
    """2026-10-27：Al 第二相逐反射强度 + 背景曲率罚项。

    LMFP 的 Al (331) 被单轴 MD 高估 ~1.7×，旧代码让背景在 7.5° 塌
    47 counts 去补偿。这两个机制一个去掉根因，一个防止背景再被拉走。
    """

    @classmethod
    def setUpClass(cls):
        cls.st = core.load_structure(STRUCT_DIR / "LaB6_1000055.cif")
        cls.al = core.load_structure(STRUCT_DIR / "Al_metal.cif")
        cls.al_a = float(cls.al.lattice.abc[0])

    def _fit(self, tth, y, true_bg, *, lebail, smooth=0.0):
        return fit_pattern(
            self.st, tth, y, wavelength=CU_KA, background=true_bg,
            background_knots=0.25, background_smooth=smooth,
            weights="uniform",
            extra_phases=[{
                "structure": self.al, "label": "Al",
                "a": self.al_a, "b": self.al_a, "c": self.al_a,
                "size_um": 1e5, "mustrain": 0.0,
                "refine_intensities": lebail,
            }],
            free=("zero", "u", "v", "w"),
            initial={"zero": 0.0, "u": 0.5, "v": -0.1, "w": 18.0},
            stages=[("zero", "u", "v", "w")])

    def test_al_per_reflection_recovers_perturbed_intensity(self):
        tth, y, true_bg, _sticks, _i = _synth_two_phase(
            self.st, self.al, factor=0.7)
        res = self._fit(tth, y, true_bg, lebail=True)
        data = res.extra_lebail["Al"]
        i_tab = np.asarray(data["i_table"], dtype=float)
        i_fit = np.asarray(data["i_fit"], dtype=float)
        self.assertTrue(np.all(i_tab > 1e-6))   # 1e-31 噪声反射被剔除
        amp = i_fit / i_tab

        def amp_at(deg):
            i = int(np.argmin(np.abs(np.asarray(data["tth"]) - deg)))
            return float(amp[i])

        # (200) 被乘 0.7，(220) 保持 1.0 → 振幅比 = 0.7
        self.assertAlmostEqual(amp_at(44.72) / amp_at(65.11), 0.7,
                               delta=0.05)
        # 代表 scale 取最强反射，不能因为弱峰为 0 而被中位数拉成 0
        self.assertGreater(res.params["Al:scale"], 1.0)

    def test_al_per_reflection_keeps_background_from_compensating(self):
        tth, y, true_bg, _sticks, _i = _synth_two_phase(
            self.st, self.al, factor=0.7)
        raw = self._fit(tth, y, true_bg, lebail=False)
        leb = self._fit(tth, y, true_bg, lebail=True)
        basis, _knots = _piecewise_linear_basis(tth, 0.25)
        corr_raw = basis @ raw.background_coeffs
        corr_leb = basis @ leb.background_coeffs
        m = (tth >= 44.3) & (tth <= 45.1)
        self.assertLess(float(np.max(np.abs(corr_leb[m]))),
                        0.3 * float(np.max(np.abs(corr_raw[m]))))
        self.assertLess(leb.rwp, raw.rwp)

    def test_background_smoothness_penalty(self):
        sticks, inten, _, _ = core.pattern_from_structure(
            self.st, wavelength=CU_KA, two_theta_range=(10.0, 80.0),
            min_intensity=0.0)
        factors = np.ones(len(sticks))
        factors[int(np.argmax(inten))] = 1.5
        tth, y, _ = _synth_modified(self.st, factors=factors, step=0.01)
        true_bg = 50.0 + 0.5 * (tth - 10.0)
        y = y + true_bg

        def fit(smooth):
            return fit_pattern(
                self.st, tth, y, background=true_bg,
                background_knots=0.25, background_smooth=smooth,
                weights="uniform", free=("zero", "u", "v", "w"),
                initial={"zero": 0.012, "u": 0.5, "v": -0.1, "w": 18.0},
                stages=[("zero", "u", "v", "w")])

        raw = fit(0.0)
        smooth = fit(1.0)
        basis, _knots = _piecewise_linear_basis(tth, 0.25)
        corr_raw = basis @ raw.background_coeffs
        corr_smooth = basis @ smooth.background_coeffs
        self.assertLess(
            np.linalg.norm(np.diff(smooth.background_coeffs, 2)),
            0.7 * np.linalg.norm(np.diff(raw.background_coeffs, 2)))
        self.assertLess(np.max(np.abs(corr_smooth)),
                        np.max(np.abs(corr_raw)))
        self.assertLess(smooth.rwp, raw.rwp + 0.6)


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
