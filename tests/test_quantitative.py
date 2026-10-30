"""定量相分数（wt%）测试（2026-10-31）。

两部分：
  * 公式本身：W_p ∝ S_p·Z_p·M_p·V_p，用已知重量分数反推 scale；
  * 全谱链路：两相合成谱（绝对相对强度）→ 拟合 → wt% 回到已知比例，
    结构模式与 Le Bail 额外相各一条。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.simulation import (  # noqa: E402
    core, fit_pattern, phase_mass_factor, phase_weight_percent,
    simulate_pattern_gsas)
from xrd_toolkit.simulation.quantitative import (  # noqa: E402
    PhaseQuantity, cell_volume)

STRUCT_DIR = ROOT / "data" / "structures"
CU_KA = 1.5406


class TestFormula(unittest.TestCase):
    def test_round_trip_known_weight_fractions(self):
        main = core.load_structure(STRUCT_DIR / "LiFePO4_olivine.cif")
        al = core.load_structure(STRUCT_DIR / "Al_metal.cif")
        z1, m1, v1, _ = phase_mass_factor(main)
        z2, m2, v2, _ = phase_mass_factor(al)
        want = (80.0, 20.0)
        factors = (z1 * m1 * v1, z2 * m2 * v2)
        phases = [
            PhaseQuantity("main", main, want[0] / factors[0]),
            PhaseQuantity("Al", al, want[1] / factors[1]),
        ]
        got = phase_weight_percent(phases)
        self.assertAlmostEqual(got[0].weight_percent, want[0], places=6)
        self.assertAlmostEqual(got[1].weight_percent, want[1], places=6)
        self.assertEqual(got[0].z, 4.0)
        self.assertEqual(got[1].z, 4.0)

    def test_missing_structure_is_excluded_from_denominator(self):
        main = core.load_structure(STRUCT_DIR / "LiFePO4_olivine.cif")
        bad = type("Bad", (), {"lattice": None})
        got = phase_weight_percent([
            PhaseQuantity("main", main, 1.0),
            PhaseQuantity("bad", bad, 1.0),
        ])
        self.assertAlmostEqual(got[0].weight_percent, 100.0, places=6)
        self.assertTrue(np.isnan(got[1].weight_percent))
        self.assertTrue(got[1].warning)

    def test_cell_volume_general(self):
        self.assertAlmostEqual(cell_volume((2.0, 3.0, 4.0)), 24.0)
        # 六方：a=b=2, c=3 → V = a²c·sin60°
        self.assertAlmostEqual(
            cell_volume((2.0, 2.0, 3.0), (90.0, 90.0, 120.0)),
            4.0 * 3.0 * np.sqrt(3.0) / 2.0, places=6)


class TestFullPatternWeights(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = core.load_structure(STRUCT_DIR / "LiFePO4_olivine.cif")
        cls.al = core.load_structure(STRUCT_DIR / "Al_metal.cif")
        cls.kw = dict(wavelength=CU_KA, two_theta_range=(10.0, 80.0),
                      step_deg=0.02, w=18.033, size_um=1e5,
                      mustrain=0.0, normalize=False, raw=True)
        cls.tth, y1, _, _ = simulate_pattern_gsas(cls.main, **cls.kw)
        _, y2, _, _ = simulate_pattern_gsas(cls.al, **cls.kw)
        cls.obs = y1 + y2
        z1, m1, v1, _ = phase_mass_factor(cls.main)
        z2, m2, v2, _ = phase_mass_factor(cls.al)
        total = z1 * m1 * v1 + z2 * m2 * v2
        cls.expected = (100.0 * z1 * m1 * v1 / total,
                        100.0 * z2 * m2 * v2 / total)
        cls.fit_kw = dict(
            background=None, weights="uniform", free=("zero", "w"),
            initial={"zero": 0.0, "u": 0.0, "v": 0.0, "w": 18.033})

    def _extra(self, lebail=False):
        return [{"structure": self.al, "label": "Al",
                 "a": 4.049, "b": 4.049, "c": 4.049,
                 "size_um": 1e4, "mustrain": 0.0, "b_iso": None,
                 "refine_intensities": bool(lebail)}]

    def test_structure_phase_weights(self):
        res = fit_pattern(self.main, self.tth, self.obs,
                          extra_phases=self._extra(), **self.fit_kw)
        got = {row.label: row.weight_percent for row in res.phase_weights}
        self.assertAlmostEqual(got["LiFePO4"], self.expected[0], delta=0.2)
        self.assertAlmostEqual(got["Al"], self.expected[1], delta=0.2)
        self.assertIn("quantitative phase fractions", res.report())

    def test_lebail_extra_phase_weights(self):
        res = fit_pattern(self.main, self.tth, self.obs,
                          extra_phases=self._extra(lebail=True),
                          **self.fit_kw)
        got = {row.label: row.weight_percent for row in res.phase_weights}
        self.assertAlmostEqual(got["LiFePO4"], self.expected[0], delta=0.3)
        self.assertAlmostEqual(got["Al"], self.expected[1], delta=0.3)
        self.assertEqual(
            next(row.source for row in res.phase_weights
                 if row.label == "Al"), "le_bail")


if __name__ == "__main__":
    unittest.main()
