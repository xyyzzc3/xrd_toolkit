"""峰位晶胞精修：七个晶系的合成反解（2026-10-31）。"""
import sys
import unittest
from pathlib import Path

import numpy as np
from pymatgen.core import Lattice, Structure

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.simulation import core  # noqa: E402
from xrd_toolkit.simulation.cell_refine import (  # noqa: E402
    crystal_system, d_spacing, refine_cell)

CASES = {
    "cubic": (4.0, 4.0, 4.0, 90.0, 90.0, 90.0),
    "tetragonal": (4.0, 4.0, 5.2, 90.0, 90.0, 90.0),
    "hexagonal": (3.2, 3.2, 5.0, 90.0, 90.0, 120.0),
    "rhombohedral": (5.0, 5.0, 5.0, 75.0, 75.0, 75.0),
    "orthorhombic": (4.0, 5.0, 6.0, 90.0, 90.0, 90.0),
    "monoclinic": (4.0, 5.0, 6.0, 90.0, 100.0, 90.0),
    "triclinic": (4.0, 5.0, 6.0, 80.0, 95.0, 105.0),
}


class TestCrystalSystem(unittest.TestCase):
    def test_all_seven(self):
        for name, cell in CASES.items():
            lattice = Lattice.from_parameters(*cell)
            self.assertEqual(crystal_system(lattice), name)


class TestDSpacing(unittest.TestCase):
    def test_matches_pymatgen(self):
        for cell in CASES.values():
            lattice = Lattice.from_parameters(*cell)
            for hkl in ((1, 0, 0), (1, 1, 1), (2, -1, 3)):
                self.assertAlmostEqual(
                    float(d_spacing([hkl], cell)[0]),
                    float(lattice.d_hkl(hkl)), places=10)


class TestRefineCell(unittest.TestCase):
    def _observed(self, true_cell, *, zero=0.02):
        true = Structure(Lattice.from_parameters(*true_cell),
                         ["Si"], [[0.0, 0.0, 0.0]])
        _hkl, _d, tth, _inten = core.reflection_table(
            true, wavelength=1.5406, two_theta_range=(10.0, 80.0), nmax=12)
        return tth + float(zero)

    def test_each_system_recovers_scaled_cell_and_zero(self):
        for name, cell in CASES.items():
            with self.subTest(system=name):
                initial = Structure(Lattice.from_parameters(*cell),
                                    ["Si"], [[0.0, 0.0, 0.0]])
                true = list(cell)
                true[0] *= 1.01
                true[1] *= 1.01
                true[2] *= 1.01
                res = refine_cell(
                    initial, self._observed(tuple(true)),
                    match_tol_deg=0.10, max_hkl=12)
                self.assertEqual(res.system, name)
                for key, want in zip(("a", "b", "c"), true[:3]):
                    self.assertAlmostEqual(res.cell[key], want, delta=2e-4)
                for key, want in zip(
                        ("alpha", "beta", "gamma"), true[3:]):
                    self.assertAlmostEqual(res.cell[key], want, delta=2e-3)
                self.assertAlmostEqual(res.zero_deg, 0.02, delta=5e-4)
                self.assertGreater(len(res.matches), 3)
                self.assertIn("cell refinement", res.report())

    def test_too_few_peaks_raises(self):
        cell = CASES["triclinic"]
        initial = Structure(Lattice.from_parameters(*cell),
                            ["Si"], [[0.0, 0.0, 0.0]])
        with self.assertRaises(ValueError):
            refine_cell(initial, [10.0, 20.0, 30.0])


if __name__ == "__main__":
    unittest.main()
