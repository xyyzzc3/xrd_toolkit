"""GSAS-II 原位对照脚本测试（2026-10-28）。

不跑 GSAS-II：只测 prepare/compare 里的纯函数，以及 P1→Pnma 对称化
（GSAS-II 精修 P1 晶胞会零除，这个护栏必须守住）。
"""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import gsas2_insitu_crosscheck as gc  # noqa: E402

try:  # pymatgen 是可选依赖，没装就跳过对称化测试
    import pymatgen.core  # noqa: F401
    HAS_PYMATGEN = True
except ImportError:  # pragma: no cover
    HAS_PYMATGEN = False


class TestPrepareHelpers(unittest.TestCase):
    def test_write_instprm(self):
        params = {"zero": -0.0011, "u": 57.26, "v": -2.84,
                  "w": 0.537, "x": 0.14, "y": 3.72}
        with tempfile.TemporaryDirectory() as td:
            path = gc.write_instprm(Path(td) / "inst.instprm", 0.1223, params)
            text = path.read_text(encoding="utf-8")
        self.assertIn("Type:PXC", text)
        self.assertIn("Lam:0.1223", text)
        for key in ("Zero", "U", "V", "W", "X", "Y"):
            self.assertIn(f"{key}:", text)

    def test_write_xye(self):
        with tempfile.TemporaryDirectory() as td:
            path = gc.write_xye(Path(td) / "d.xye",
                                np.array([1.0, 1.5, 2.0]),
                                np.array([4.0, 0.0, -9.0]))
            data = np.loadtxt(path)
        self.assertEqual(data.shape, (3, 3))
        np.testing.assert_allclose(data[:, 0], [1.0, 1.5, 2.0])
        np.testing.assert_allclose(data[:, 2], [2.0, 1.0, 1.0])

    def test_tag_parsing(self):
        self.assertEqual(gc._tag(Path("LMFP_1_atten0-00117.tif")), 117)
        self.assertEqual(gc._tag(Path("no_digits.tif")), 0)

    def test_anchor_order(self):
        self.assertEqual(gc.ANCHOR_ORDER, (45, 1, 81))


@unittest.skipUnless(HAS_PYMATGEN, "pymatgen 未安装")
class TestSymmetrize(unittest.TestCase):
    def test_p1_cif_becomes_pnma_and_keeps_peaks(self):
        src = ROOT / "data/structures/LiFePO4_olivine.cif"
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "LiFePO4_pnma.cif"
            symbol, out = gc.symmetrize_cif(src, dst)
            self.assertEqual(symbol, "Pnma")
            self.assertTrue(Path(out).exists())
            self.assertIn("Pnma", Path(out).read_text(encoding="utf-8"))


class TestCompareRows(unittest.TestCase):
    def _ours(self):
        return {1: {"f_rich": 0.90, "rwp": 2.1,
                    "rich_a": 10.3486, "rich_b": 6.0190, "rich_c": 4.7004,
                    "poor_a": 10.1680, "poor_b": 5.8197, "poor_c": 4.7805,
                    "rich_a_esd": 0.001, "rich_b_esd": 0.001,
                    "rich_c_esd": 0.001,
                    "poor_a_esd": 0.01, "poor_b_esd": 0.005,
                    "poor_c_esd": 0.005}}

    def _gsas(self):
        return {"frame": 1, "wR": 10.0, "t_total_s": 1.0,
                "f_rich_wt": 0.90, "rich_wt": 0.47, "poor_wt": 0.05,
                "Al_wt": 0.48,
                "rich_a": 10.3492, "rich_b": 6.0248, "rich_c": 4.7030,
                "poor_a": 9.8450, "poor_b": 5.7942, "poor_c": 4.7970,
                "rich_a_esd": 0.003, "rich_b_esd": 0.0017,
                "rich_c_esd": 0.0015,
                "poor_a_esd": 0.027, "poor_b_esd": 0.0135,
                "poor_c_esd": 0.0137}

    def test_delta_and_sigma(self):
        rows = gc.compare_rows(self._ours(), [self._gsas()])
        self.assertEqual(len(rows), 1)
        cell = rows[0]["cells"]["rich_b"]
        self.assertAlmostEqual(cell["delta"], 0.0058, places=6)
        self.assertAlmostEqual(cell["sigma"], 0.0058 / 0.0017, places=6)
        poor_a = rows[0]["cells"]["poor_a"]
        self.assertAlmostEqual(poor_a["delta"], -0.3230, places=6)
        self.assertAlmostEqual(poor_a["sigma"], -0.3230 / 0.027, places=6)

    def test_frame_missing_from_ours_is_skipped(self):
        gsas = self._gsas()
        gsas["frame"] = 2
        self.assertEqual(gc.compare_rows(self._ours(), [gsas]), [])

    def test_compare_text_has_both_sections(self):
        rows = gc.compare_rows(self._ours(), [self._gsas()])
        text = gc._compare_text(rows)
        self.assertIn("相分数", text)
        self.assertIn("晶胞", text)
        self.assertIn("10.3492", text)


if __name__ == "__main__":
    unittest.main()
