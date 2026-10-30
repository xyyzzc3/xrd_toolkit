"""自动分析辅助逻辑测试（2026-10-22）。

只测“相筛选/晶胞缩放扫描”和“Al 基片识别”这类纯计算，不跑完整 CLI
（完整 CLI 要两张真实 TIFF + 拟合，放 scripts 里手工跑）。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from pymatgen.core import Lattice, Structure  # noqa: E402

from xrd_toolkit.simulation import core  # noqa: E402
from xrd_toolkit.simulation import phase_search as ps  # noqa: E402
import analyze_pattern as ap  # noqa: E402

CU_KA = 1.5406


class TestAutoAnalysisHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lifepo4 = ap.STRUCT / "LiFePO4_olivine.cif"

    def test_phase_library_scans_directory_and_skips_al_substrate(self):
        names = [p.name for p in ps.phase_library(ap.STRUCT)]
        self.assertIn("LiFePO4_olivine.cif", names)
        self.assertIn("LaB6_NIST660c.cif", names)
        self.assertNotIn("Al_metal.cif", names,
                         "Al 只作第二相，不应进入主相候选")

    def test_phase_candidates_filters_by_allowed_elements(self):
        candidates = ps.phase_candidates(
            ap.STRUCT, ["Li", "Mn", "Fe", "P", "O"])
        names = [path.name for path, _st in candidates]
        self.assertIn("LiFePO4_olivine.cif", names)
        self.assertIn("LiMn0.6Fe0.4PO4_olivine.cif", names)
        self.assertNotIn("LaB6_NIST660c.cif", names)
        self.assertNotIn("Al_metal.cif", names)

    def test_detect_peaks_returns_positions_and_local_heights(self):
        x = np.arange(1.0, 2.0, 0.001)
        bg = np.full(x.size, 100.0)
        y = bg + 300.0 * np.exp(
            -4.0 * np.log(2.0) * ((x - 1.5) / 0.02) ** 2)
        t, h = ap.detect_peaks(x, y, bg)
        self.assertEqual(len(t), 1)
        self.assertAlmostEqual(t[0], 1.5, delta=2e-3)
        self.assertAlmostEqual(h[0], 300.0, delta=10.0)

    def test_scale_scan_finds_scaled_cell(self):
        base = core.load_structure(str(self.lifepo4))
        lat = Lattice.from_parameters(
            base.lattice.a * 1.01, base.lattice.b * 1.01,
            base.lattice.c * 1.01, *base.lattice.angles)
        scaled = Structure(
            lat, [s.species for s in base], [s.frac_coords for s in base])
        _, _, tth, inten = core.reflection_table(
            scaled, wavelength=CU_KA, two_theta_range=(10.0, 80.0), nmax=10)
        inten = inten / inten.max()
        obs = tth[inten >= 0.02]
        score = ps.score_phase(obs, base, CU_KA, 10.0, 80.0, 0.0)
        self.assertIsNotNone(score)
        _sc, scale, _cov, _pur = score
        self.assertAlmostEqual(scale, 1.01, delta=0.012)

    def test_al_substrate_detected(self):
        al_st = core.load_structure(str(ap.AL_CIF))
        al_d0, al_i = ps.reflection_data(al_st, 0.1223, 1.0, 8.0)
        al_t, al_i = ps.scaled_positions(al_d0, al_i, 0.1223, 1.0, 0.0)
        self.assertGreater(len(al_t), 0)
        j = int(np.argmax(al_i))
        self.assertGreater(al_i[j], 0.9)
        ranges = ap.al_substrate_ranges(
            al_t, al_i, 0.1223, 1.0, 8.0, 0.0, self.lifepo4, 1.01)
        self.assertTrue(ranges)
        self.assertTrue(any(lo <= al_t[j] <= hi for lo, hi in ranges))

    def test_screen_phases_ranks_lab6_first(self):
        lab6 = core.load_structure(str(ap.STRUCT / "LaB6_NIST660c.cif"))
        _, _, tth, inten = core.reflection_table(
            lab6, wavelength=CU_KA, two_theta_range=(10.0, 80.0), nmax=10)
        inten = inten / inten.max()
        obs = tth[inten >= 0.02]
        matches = ps.screen_phases(obs, ap.STRUCT, CU_KA, 10.0, 80.0, 0.0)
        self.assertTrue(matches)
        # 库里有 LaB6 的两份 CIF（1000055 与 NIST660c），分数相同，
        # 谁是第一取决于文件名；只要求认对物相。
        self.assertTrue(matches[0].path.name.startswith("LaB6"),
                        matches[0].path.name)


if __name__ == "__main__":
    unittest.main()
