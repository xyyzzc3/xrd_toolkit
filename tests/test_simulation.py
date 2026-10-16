"""XRD 模拟模块的单元测试（RY04 第六周并入）。

全部数字来自第六周的模拟结果，逐条可在 CLI 输出中复核：
  - Si (111) 在 Cu Kα 下位于 28.444°（±0.01°），10°–80° 内恰好 5 条峰；
  - Mo Kα（0.7093 Å）下同一峰移到 12.989°（波长效应）；
  - 金刚石结构消光：(200)、(222) 的 |F|² 为 0，(111)、(220) 显著非零；
  - 散射因子自检 f(s=0) = 原子序数；
  - 多重性：Si (111) 轨道 8 个、LaB₆ (100) 轨道 6 个；
  - 指数代表与排版：刚玉取 h≥k≥0 规范写法（110 / 113 / 116 / 214 / 300）；
    六方标签默认三位卡片式（2026-10-17 起），四位 (h k i l) 为显式选项；
  - 手写版与 pymatgen 版三个材料逐峰一致（峰数相等、2θ 偏差 < 1e-3°）。

运行：python -m unittest tests.test_simulation -v（或 scripts/run_tests.py）。
"""
import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.simulation import core, profile

STRUCT_DIR = Path(__file__).resolve().parents[1] / "data" / "structures"
CU_KA = 1.5406
MO_KA = 0.7093


class TestSiliconPattern(unittest.TestCase):
    """Si：峰数、(111) 峰位与波长效应。"""

    @classmethod
    def setUpClass(cls):
        cls.si = core.load_structure(STRUCT_DIR / "Si_9008565.cif")
        cls.tth, cls.inten, cls.peaks, cls.is_hex = core.pattern_from_structure(
            cls.si, CU_KA, (10.0, 80.0))

    def test_five_peaks(self):
        self.assertEqual(len(self.tth), 5)

    def test_111_position(self):
        self.assertAlmostEqual(float(self.tth[0]), 28.444, delta=0.01)

    def test_wavelength_mo_ka_shifts_peak(self):
        tth, *_ = core.pattern_from_structure(self.si, MO_KA, (10.0, 80.0))
        self.assertAlmostEqual(float(tth[0]), 12.989, delta=0.01)


class TestScatteringAndExtinction(unittest.TestCase):
    """散射因子自检 + 金刚石消光（(200)/(222) 的 |F|² 数值为 0）。"""

    @classmethod
    def setUpClass(cls):
        cls.si = core.load_structure(STRUCT_DIR / "Si_9008565.cif")

    def _f2(self, hkl):
        d = core.d_spacing(hkl, self.si.lattice.matrix)
        tt = core.two_theta_from_d(d, CU_KA)
        theta = math.radians(tt / 2.0)
        s = math.sin(theta) / CU_KA
        return abs(core.structure_factor(hkl, self.si, s)) ** 2

    def test_f_at_zero_is_z(self):
        self.assertAlmostEqual(core.scattering_factor("Si", 0.0), 14.0, delta=1e-6)

    def test_extinctions_are_zero(self):
        for hkl in [(2, 0, 0), (2, 2, 2)]:
            self.assertLess(self._f2(hkl), 1e-3)   # 数值 ~1e-30，留足裕量

    def test_allowed_reflections_are_strong(self):
        for hkl in [(1, 1, 1), (2, 2, 0)]:
            self.assertGreater(self._f2(hkl), 100.0)


class TestMultiplicity(unittest.TestCase):
    """晶面家族的多重性 = 对称操作轨道的大小。"""

    def test_si_111_orbit(self):
        rots = core.distinct_rotations("Fd-3m")
        self.assertEqual(len(core.multiplicity((1, 1, 1), rots)), 8)

    def test_lab6_100_orbit(self):
        rots = core.distinct_rotations("Pm-3m")
        self.assertEqual(len(core.multiplicity((1, 0, 0), rots)), 6)


class TestManualMatchesPymatgen(unittest.TestCase):
    """手写版与 pymatgen 参考版逐峰一致（并入时的验收条件）。"""

    def _check(self, name):
        path = STRUCT_DIR / name
        t1, i1, _, _ = core.pattern_from_cif(path, CU_KA, (10.0, 80.0))
        t2, i2, _ = core.pattern_from_cif_pymatgen(path, CU_KA, (10.0, 80.0))
        self.assertEqual(len(t1), len(t2))
        for tt, ii in zip(t1, i1):
            j = int(np.argmin(np.abs(t2 - tt)))
            self.assertLess(abs(float(t2[j]) - float(tt)), 1e-3)
            self.assertLess(abs(float(i2[j]) - float(ii)), 0.5)

    def test_si(self):
        self._check("Si_9008565.cif")

    def test_lab6(self):
        self._check("LaB6_1000055.cif")

    def test_al2o3(self):
        self._check("Al2O3_1000032.cif")


class TestHexRepresentatives(unittest.TestCase):
    """刚玉的显示代表面取规范正指数写法（与 PDF 卡片常见行号一致）。"""

    @classmethod
    def setUpClass(cls):
        _, _, cls.peaks, _ = core.pattern_from_cif(
            STRUCT_DIR / "Al2O3_1000032.cif", CU_KA, (10.0, 80.0))

    def _rep_at(self, two_theta):
        peak = min(self.peaks, key=lambda p: abs(p[0] - two_theta))
        return peak[2][0][0]

    def test_conventional_representatives(self):
        expect = {35.140: (1, 0, 4), 37.764: (1, 1, 0), 43.340: (1, 1, 3),
                  57.481: (1, 1, 6), 66.495: (2, 1, 4), 68.185: (3, 0, 0)}
        for tt, hkl in expect.items():
            self.assertEqual(self._rep_at(tt), hkl)


class TestHklFormatting(unittest.TestCase):
    """峰位标签排版：**默认三位卡片式**（用户 2026-10-17："hkl 就显示三位，
    图上不要写四位"）；四位 (h k i l) 是显式选项；立方不受影响。"""

    def test_hex_three_index_is_the_default(self):
        self.assertEqual(core.format_hkls([((1, 1, 6), 12)], True), "(116) x12")

    def test_hex_four_index_opt_in(self):
        self.assertEqual(core.format_hkls([((1, 1, 6), 12)], True, four_index=True),
                         "(1 1 -2 6) x12")

    def test_plot_defaults_stay_card_style(self):
        """画图入口的默认也必须是三位（图上的口径由这里钉住）。"""
        import inspect
        from xrd_toolkit.simulation import plots
        for fn in (plots.plot_annotated, plots.plot_profile):
            self.assertIs(inspect.signature(fn).parameters["four_index"].default,
                          False, f"{fn.__name__} 的默认应是三位卡片式")

    def test_three_index_multi_digit_keeps_spaces(self):
        self.assertEqual(core.format_hkls([((1, 0, 10), 12)], True, four_index=False),
                         "(1 0 10) x12")

    def test_cubic_three_index_unchanged(self):
        self.assertEqual(core.format_hkls([((1, 1, 1), 8)], False, four_index=False),
                         "(111) x8")


class TestCifThermalFactors(unittest.TestCase):
    """CIF 温度因子读取（b_iso_from_cif）：Uiso → B = 8π²·Uiso。"""

    def test_lab6_uiso_becomes_b(self):
        b = core.b_iso_from_cif(str(STRUCT_DIR / "LaB6_1000055.cif"))
        self.assertAlmostEqual(b["La"], 8 * math.pi ** 2 * 0.00858, places=9)
        self.assertAlmostEqual(b["B"], 8 * math.pi ** 2 * 0.0090, places=9)
        self.assertEqual(set(b), {"La", "B"})

    def test_cif_without_uiso_returns_empty(self):
        # Si_9008565 没写位移参数 → 当理想刚性晶格（空字典）
        self.assertEqual(core.b_iso_from_cif(str(STRUCT_DIR / "Si_9008565.cif")),
                         {})

    def test_anisotropic_only_returns_empty(self):
        # Al₂O₃ 只有各向异性 U11/U22/…（没有 Uiso 这个记号）→ 不硬折算
        self.assertEqual(
            core.b_iso_from_cif(str(STRUCT_DIR / "Al2O3_1000032.cif")), {})


class TestInstrumentProfile(unittest.TestCase):
    """峰形公式层（第七周）：Caglioti / Lorentzian / 伪 Voigt / TCH 合成。"""

    def test_caglioti_reduces_to_w_at_zero(self):
        # 2θ=0 处 tanθ=0 → Γ_G 就是常数项 W 的平方根
        tt = np.array([0.0])
        np.testing.assert_allclose(profile.caglioti_fwhm_deg(tt, u=1.0, v=2.0, w=0.01),
                                   [0.1])

    def test_caglioti_at_90_deg(self):
        # 2θ=90° → tanθ=1 → Γ_G² = U+V+W
        f = profile.caglioti_fwhm_deg(np.array([90.0]), u=0.3, v=0.2, w=0.5)
        self.assertAlmostEqual(float(f[0]), math.sqrt(1.0), places=12)

    def test_lorentz_matches_gsas_form(self):
        # (X + Y·tanθ)/cosθ：θ=45°（2θ=90°）时 tan=1、cos=√2/2
        f = profile.lorentz_fwhm_deg(np.array([90.0]), x=1.0, y=0.5)
        self.assertAlmostEqual(float(f[0]), 1.5 / math.cos(math.pi / 4), places=12)

    def test_pseudo_voigt_is_half_at_half_fwhm(self):
        # 峰顶 1；z=±0.5（即方均半宽处）时 Gauss 与 Lorentz **恰好都等于 0.5**
        # （G = exp(−ln2)，L = 1/(1+1)）→ 任意 η 下伪 Voigt 都是 0.5
        for eta in (0.0, 0.5, 1.0):
            self.assertAlmostEqual(
                float(profile.pseudo_voigt(np.array([101.0, 99.0]), 100.0, 2.0, eta)[0]),
                0.5, places=12)
        self.assertAlmostEqual(
            float(profile.pseudo_voigt(np.array([100.0]), 100.0, 2.0, 0.3)[0]),
            1.0, places=12)

    def test_tch_limits(self):
        g, e = profile.tch_fwhm_eta(np.array([0.2]), np.array([0.0]))
        self.assertAlmostEqual(float(g[0]), 0.2, places=12)   # 纯 Gauss：Γ=Γ_G、η=0
        self.assertAlmostEqual(float(e[0]), 0.0, places=12)
        g, e = profile.tch_fwhm_eta(np.array([0.0]), np.array([0.3]))
        self.assertAlmostEqual(float(g[0]), 0.3, places=12)   # 纯 Lorentz：Γ=Γ_L、η=1
        self.assertAlmostEqual(float(e[0]), 1.0, places=6)

    def test_scherrer_formula_value(self):
        # Si (111) 2θ=28.444°、50 nm：Γ = 0.9·1.5406/(500·cos(14.22°)) rad → 度
        f = profile.scherrer_fwhm_deg(np.array([28.444]), 50.0, 1.5406)
        expected = math.degrees(0.9 * 1.5406 / (500.0 * math.cos(math.radians(14.222))))
        self.assertAlmostEqual(float(f[0]), expected, places=10)

    def test_strain_scales_with_tan_theta(self):
        two_theta = np.array([30.0, 120.0])
        f = profile.strain_fwhm_deg(two_theta, 0.001)
        ratio_expected = math.tan(math.radians(60.0)) / math.tan(math.radians(15.0))
        self.assertAlmostEqual(float(f[1] / f[0]), ratio_expected, places=10)


class TestSimulatePattern(unittest.TestCase):
    """可调用函数 simulate_pattern（第七周交付）：参数 → 连续谱。"""

    SILICON = STRUCT_DIR / "Si_9008565.cif"

    @classmethod
    def setUpClass(cls):
        cls.si = core.load_structure(cls.SILICON)

    def _peak_center(self, tth, curve, around):
        """找 around(°) 附近 ±0.6° 内的局部最大位置（用质心提高亚步长精度）。"""
        m = (tth > around - 0.6) & (tth < around + 0.6)
        w = curve[m]
        return float(np.sum(tth[m] * w) / np.sum(w))

    def _measure_fwhm(self, tth, curve, around):
        """半高宽：找 around 附近的最大值，再线性插值出两侧半高点的位置。"""
        m = (tth > around - 0.8) & (tth < around + 0.8)
        xs, ys = tth[m], curve[m]
        i0 = int(np.argmax(ys))
        half = ys[i0] / 2.0
        left = np.interp(half, ys[:i0 + 1], xs[:i0 + 1])
        right = np.interp(half, ys[i0:][::-1], xs[i0:][::-1])
        return float(right - left)

    def test_ideal_default_hits_si_111_and_scales_to_100(self):
        tth, curve, peaks, is_hex = profile.simulate_pattern(
            self.si, two_theta_range=(20.0, 80.0), step_deg=0.01)
        self.assertAlmostEqual(curve.max(), 100.0, places=6)
        self.assertAlmostEqual(self._peak_center(tth, curve, 28.444), 28.444,
                               places=2)
        self.assertFalse(is_hex)

    def test_zero_offset_shifts_every_peak(self):
        kw = dict(two_theta_range=(20.0, 80.0), step_deg=0.005)
        t0, c0, _, _ = profile.simulate_pattern(self.si, zero_deg=0.0, **kw)
        t1, c1, _, _ = profile.simulate_pattern(self.si, zero_deg=0.05, **kw)
        for around in (28.444, 47.302, 56.121):
            d = (self._peak_center(t1, c1, around + 0.05)
                 - self._peak_center(t0, c0, around))
            self.assertAlmostEqual(d, 0.05, places=2)

    def test_caglioti_broadens_high_angle_most(self):
        """U·tan²θ 项：低角窄、高角宽——实测 FWHM 与公式同值、比值 ≈3。"""
        tth, curve, _, _ = profile.simulate_pattern(
            self.si, two_theta_range=(20.0, 80.0), step_deg=0.002,
            u=0.02, w=0.0)
        f_low = self._measure_fwhm(tth, curve, 28.444)
        f_high = self._measure_fwhm(tth, curve, 76.376)
        tt = np.array([28.444, 76.376])
        f_formula = profile.caglioti_fwhm_deg(tt, u=0.02)
        self.assertAlmostEqual(f_low, float(f_formula[0]), delta=0.01)
        self.assertAlmostEqual(f_high, float(f_formula[1]), delta=0.02)
        self.assertGreater(f_high / f_low, 2.5)   # √(tan²(38.19°)/tan²(14.22°)) ≈ 3.1

    def test_williamson_hall_recovers_size_and_strain(self):
        """W-H 往返：合成一条"尺寸+应变"的谱 → 从 FWHM 反解出两个参数。

        (FWHM·cosθ)² = (Kλ/D)² + (4ε·sinθ)² —— 对 sin²θ 做直线：截距开方
        给尺寸、斜率开方给应变。这正是思考题 2 的答案本体：尺寸按 1/cosθ、
        应变按 tanθ，θ 依赖不同，所以一条直线能把两者分开。

        用**平方形式**而不是教材常见的线性形式（FWHM·cosθ = Kλ/D + 4ε·sinθ）：
        线性形式隐含"两种展宽直接相加"，而本模拟器里两类展宽按方差合成
        （β² = β_size² + β_strain²，即 Gaussian 世界的正确加法）——平方形式
        在这套口径下才是严格的，线性形式会系统高估尺寸（实测约 +50%）。
        这条差别本身写进周报，是个好素材。
        """
        size_nm, strain = 50.0, 0.002
        tth, curve, _, _ = profile.simulate_pattern(
            self.si, two_theta_range=(20.0, 80.0), step_deg=0.002,
            w=0.0, size_nm=size_nm, strain=strain)
        centers = [28.444, 47.302, 56.121, 69.132, 76.376]   # Si 五个分离峰
        xs, ys = [], []
        for c in centers:
            fwhm = self._measure_fwhm(tth, curve, c)
            theta = math.radians(c / 2.0)
            xs.append(math.sin(theta) ** 2)
            ys.append((math.radians(fwhm) * math.cos(theta)) ** 2)
        slope, intercept = np.polyfit(xs, ys, 1)
        d_nm = 0.9 * 1.5406 / (math.sqrt(intercept) * 10.0)
        eps = math.sqrt(slope) / 4.0
        self.assertAlmostEqual(d_nm, size_nm, delta=size_nm * 0.05)
        self.assertAlmostEqual(eps, strain, delta=strain * 0.05)


class TestRealEffectsIntensity(unittest.TestCase):
    """温度因子与择优取向（强度侧，走 core 的峰表）。"""

    SILICON = STRUCT_DIR / "Si_9008565.cif"

    @classmethod
    def setUpClass(cls):
        cls.si = core.load_structure(cls.SILICON)

    def test_debye_waller_damps_high_angle_exactly(self):
        """单元素晶体：逐峰强度比 = exp(−2B·(s²−s²max))——精确可比。

        注意峰表是**归一化到最强峰=100%** 的：比值里最强峰那份基准会一起
        变，所以对照式要减去最强峰的 s²（Si 的最强峰恰是 (111)）。
        """
        b = 0.8
        t0, i0, _, _ = core.pattern_from_structure(
            self.si, two_theta_range=(20.0, 80.0))
        t1, i1, _, _ = core.pattern_from_structure(
            self.si, two_theta_range=(20.0, 80.0), b_iso=b)
        np.testing.assert_allclose(t0, t1)       # 峰位不动

        def s2_of(tt):
            return (math.sin(math.radians(tt / 2.0)) / 1.5406) ** 2

        s2_max = s2_of(float(t0[int(np.argmax(i0))]))
        for tt, a, c in zip(t0, i0, i1):
            self.assertAlmostEqual(c / a, math.exp(-2 * b * (s2_of(tt) - s2_max)),
                                   places=9)

    def test_debye_waller_b_drops_high_angle_relative(self):
        _, i0, _, _ = core.pattern_from_structure(
            self.si, two_theta_range=(20.0, 80.0))
        _, i1, _, _ = core.pattern_from_structure(
            self.si, two_theta_range=(20.0, 80.0), b_iso=1.0)
        # 最末峰的相对强度（对最强峰归一）必须比理想晶体低
        self.assertLess(i1[-1] / i1.max(), i0[-1] / i0.max())

    def test_march_dollase_unity_is_identity(self):
        t0, i0, _, _ = core.pattern_from_structure(
            self.si, two_theta_range=(20.0, 80.0))
        t1, i1, _, _ = core.pattern_from_structure(
            self.si, two_theta_range=(20.0, 80.0), md=((1, 1, 1), 1.0))
        np.testing.assert_allclose(t0, t1)
        np.testing.assert_allclose(i0, i1, rtol=1e-12)

    def test_march_dollase_axis_peak_scales_as_r_cubed(self):
        r = 0.6
        t, i0, _, _ = core.pattern_from_structure(
            self.si, two_theta_range=(20.0, 80.0))
        _, i1, _, _ = core.pattern_from_structure(
            self.si, two_theta_range=(20.0, 80.0), md=((1, 1, 1), r))
        i111 = int(np.argmin(np.abs(t - 28.444)))
        i220 = int(np.argmin(np.abs(t - 47.302)))
        lat = self.si.lattice.matrix
        p111 = core.march_dollase((1, 1, 1), (1, 1, 1), r, lat)
        p220 = core.march_dollase((2, 2, 0), (1, 1, 1), r, lat)
        self.assertAlmostEqual(p111, r ** -3.0, places=12)
        # 峰表归一化到最强峰：(111) 恰是 Si 的最强峰 → 它的比值恒为 1；
        # (220) 的相对变化 = P220 / P111（两份归一化基准一起除掉了）
        self.assertAlmostEqual(i1[i111] / i0[i111], 1.0, places=12)
        self.assertAlmostEqual(i1[i220] / i0[i220], p220 / p111, places=6)


if __name__ == "__main__":
    unittest.main()
