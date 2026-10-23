"""原子坐标精修测试（2026-10-23）。

只验证"按空间群约束的坐标参数化 + 合成谱反解"这条链路：
  * LMFP（Pnma）分成 6 条轨道，Li 4a 没有自由方向；
  * 移动 Fe5 的代表位点，4 个等效位点一起动；
  * 合成谱（已知位移）→ fit_pattern(free=("coord_Fe5",)) 反解回原值。
"""
import sys
import unittest
from pathlib import Path

import numpy as np
from pymatgen.core import Structure

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.simulation import core  # noqa: E402
from xrd_toolkit.simulation.fit import fit_pattern  # noqa: E402
from xrd_toolkit.simulation.gsas_profile import (  # noqa: E402
    simulate_pattern_gsas,
)
from xrd_toolkit.simulation.site_model import SiteModel  # noqa: E402

STRUCT_DIR = Path(__file__).resolve().parents[1] / "data" / "structures"
CU_KA = 1.5406


class TestSiteModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.st = core.load_structure(
            STRUCT_DIR / "LiMn0.5Fe0.5PO4_olivine.cif")
        cls.model = SiteModel(cls.st)

    def test_orbits_and_free_dimensions(self):
        dims = {o.label: o.n_free for o in self.model.orbits.values()}
        self.assertEqual(dims["Li0"], 0)          # 4a 特殊位置
        self.assertEqual(dims["Fe5"], 2)          # 4c：x、z
        self.assertEqual(dims["O24"], 3)          # 8d：x、y、z
        self.assertEqual(sum(dims.values()), 11)
        self.assertEqual(self.model.orbit("Fe5").params, ("x_Fe5", "z_Fe5"))

    def test_zero_displacement_keeps_structure(self):
        coords = self.model.positions({})
        base = np.array([s.frac_coords for s in self.st])
        d = (coords - base + 0.5) % 1.0 - 0.5
        self.assertLess(float(np.max(np.abs(d))), 1e-10)

    def test_equivalent_sites_move_together(self):
        orbit = self.model.orbit("Fe5")
        values = {orbit.params[0]: 0.01, orbit.params[1]: -0.005}
        coords = self.model.positions(values)
        rep_new = orbit.base + orbit.displacement(values)
        for j, op in zip(orbit.indices, orbit.transforms):
            expect = np.asarray(op.operate(rep_new)) % 1.0
            d = (coords[j] - expect + 0.5) % 1.0 - 0.5
            self.assertLess(float(np.max(np.abs(d))), 1e-10)


class TestCoordinateRefinement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.st = core.load_structure(
            STRUCT_DIR / "LiMn0.5Fe0.5PO4_olivine.cif")
        cls.model = SiteModel(cls.st)
        cls.init = dict(zero=0.0, u=0.5, v=-0.1, w=18.0, x=0.0, y=0.0,
                        size_um=1e5, mustrain=0.0, b_iso=0.0, polariz=0.5)

    def _synthetic(self, values):
        coords = self.model.positions(values)
        moved = Structure(self.st.lattice,
                          [s.species for s in self.st], coords)
        return simulate_pattern_gsas(
            moved, wavelength=CU_KA, two_theta_range=(10.0, 45.0),
            step_deg=0.01, zero_deg=0.0, u=0.5, v=-0.1, w=18.0,
            x=0.0, y=0.0, size_um=1e5, mustrain=0.0, scale=1000.0,
            normalize=False)

    def test_fe5_coordinate_recovered(self):
        orbit = self.model.orbit("Fe5")
        true = {orbit.params[0]: 0.010, orbit.params[1]: -0.005}
        tth, y, _, _ = self._synthetic(true)
        res = fit_pattern(self.st, tth, y, background=None,
                          weights="uniform", free=("coord_Fe5",),
                          initial=self.init)
        self.assertLess(res.rwp, 0.01)
        for name, value in true.items():
            self.assertAlmostEqual(res.params[name], value, delta=2e-4)
        wyckoff, x, y0, z = res.site_coords["Fe5"]
        self.assertEqual(wyckoff, "4c")
        self.assertAlmostEqual(x, orbit.base[0] + true["x_Fe5"], delta=2e-4)
        self.assertAlmostEqual(z, orbit.base[2] + true["z_Fe5"], delta=2e-4)
        self.assertAlmostEqual(y0, orbit.base[1], delta=1e-6)  # y 被对称性固定

    def test_special_position_has_no_coordinate(self):
        tth, y, _, _ = self._synthetic({})
        with self.assertRaises(ValueError):
            fit_pattern(self.st, tth, y, background=None,
                        weights="uniform", free=("coord_Li0",),
                        initial=self.init)

    def test_unknown_site_rejected(self):
        tth, y, _, _ = self._synthetic({})
        with self.assertRaises(ValueError):
            fit_pattern(self.st, tth, y, background=None,
                        weights="uniform", free=("coord_Fe99",),
                        initial=self.init)

    def test_duplicate_coordinate_parameters_rejected(self):
        tth, y, _, _ = self._synthetic({})
        with self.assertRaises(ValueError):
            fit_pattern(self.st, tth, y, background=None,
                        weights="uniform",
                        free=("coord_Fe5", "coord_Fe5"),
                        initial=self.init)
        with self.assertRaises(ValueError):
            fit_pattern(self.st, tth, y, background=None,
                        weights="uniform",
                        free=("coord_Fe5", "x_Fe5"),
                        initial=self.init)

    def test_le_bail_rejects_coordinates(self):
        tth, y, _, _ = self._synthetic({})
        with self.assertRaises(ValueError):
            fit_pattern(self.st, tth, y, background=None,
                        weights="uniform", refine_intensities=True,
                        free=("coord_Fe5",), initial=self.init)


if __name__ == "__main__":
    unittest.main()
