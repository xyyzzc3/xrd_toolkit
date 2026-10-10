"""XRD 模拟模块的单元测试（RY04 第六周并入）。

全部数字来自第六周的模拟结果，逐条可在 CLI 输出中复核：
  - Si (111) 在 Cu Kα 下位于 28.444°（±0.01°），10°–80° 内恰好 5 条峰；
  - Mo Kα（0.7093 Å）下同一峰移到 12.989°（波长效应）；
  - 金刚石结构消光：(200)、(222) 的 |F|² 为 0，(111)、(220) 显著非零；
  - 散射因子自检 f(s=0) = 原子序数；
  - 多重性：Si (111) 轨道 8 个、LaB₆ (100) 轨道 6 个；
  - 手写版与 pymatgen 版三个材料逐峰一致（峰数相等、2θ 偏差 < 1e-3°）。

运行：python -m unittest tests.test_simulation -v（或 scripts/run_tests.py）。
"""
import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.simulation import core

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


if __name__ == "__main__":
    unittest.main()
