"""原位三相批量脚本的纯函数测试（2026-10-27）。

不跑真数据（真数据 81 帧要 ~10 分钟），只钉：
  * 帧排序/limit/stride；
  * 相对相分数口径 A_p = scale × Σtable × norm / 100；
  * 异常判据（不收敛/高 Rwp/晶胞跳变/无相信号）；
  * 额外相规格（poor 晶胞自由、Al 逐反射）。
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import fit_insitu as fi  # noqa: E402


def _result(*, success=True, rwp=2.0, rich_a=10.35):
    return SimpleNamespace(
        success=success, rwp=rwp,
        scale=2.0,
        peak_table_intensities=np.array([10.0, 10.0]),
        peak_table_norm=250.0,
        params={"LMFP_poor:scale": 1.0},
        extra_phase_table_sum={"LMFP_poor": 20.0},
        extra_phase_norm={"LMFP_poor": 250.0},
        extra_lebail={"Al": {"i_fit": np.array([5.0]),
                             "norm": 500.0}},
        # _cell 读这三个键
        **{"a": rich_a})


class TestFrames(unittest.TestCase):
    def test_tag_and_order(self):
        tmp = Path(self.id().replace(".", "_"))
        # 用临时目录更干净
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            for tag in (33, 29, 31):
                (Path(td) / f"LMFP_1_atten0-{tag:05d}.tif").write_bytes(b"")
            files = fi._frames(Path(td), 0, 1)
            self.assertEqual([fi._tag(p) for p in files], [29, 31, 33])
            self.assertEqual([fi._tag(p) for p in fi._frames(Path(td), 2, 1)],
                             [29, 31])
            self.assertEqual([fi._tag(p) for p in fi._frames(Path(td), 0, 2)],
                             [29, 33])


class TestFractions(unittest.TestCase):
    def test_relative_scattering_fraction(self):
        # rich = 2*20*250/100 = 100；poor = 1*20*250/100 = 50
        # f_rich = 100/150；Al = 5*500/100 = 25
        A_rich, A_poor, A_al, f_rich = fi._fractions(_result())
        self.assertAlmostEqual(A_rich, 100.0)
        self.assertAlmostEqual(A_poor, 50.0)
        self.assertAlmostEqual(A_al, 25.0)
        self.assertAlmostEqual(f_rich, 100.0 / 150.0)

    def test_missing_extra_phase_gives_nan_fraction(self):
        res = _result()
        del res.extra_phase_table_sum
        _a, _b, _al, f_rich = fi._fractions(res)
        self.assertTrue(np.isnan(f_rich))


class TestStatus(unittest.TestCase):
    def setUp(self):
        self.seed = {"rich": (10.35, 6.02, 4.70),
                     "poor": (10.17, 5.82, 4.78)}

    def _status(self, res, f_rich=0.9, rich_cell=None, poor_cell=None,
                check_cells=True):
        return fi._status(
            res, f_rich,
            rich_cell or self.seed["rich"],
            poor_cell or self.seed["poor"],
            self.seed, 20.0, check_cells=check_cells)

    def test_ok(self):
        self.assertEqual(self._status(_result()), "ok")

    def test_not_converged_and_high_rwp(self):
        self.assertEqual(self._status(_result(success=False)),
                         "not_converged")
        self.assertIn("high_rwp", self._status(_result(rwp=25.0)))

    def test_no_phase_signal(self):
        self.assertEqual(self._status(_result(), f_rich=float("nan")),
                         "no_phase_signal")

    def test_cell_jump_and_first_frame_exemption(self):
        moved = (10.55, 6.02, 4.70)
        self.assertEqual(self._status(_result(), rich_cell=moved),
                         "cell_jump")
        self.assertEqual(self._status(_result(), rich_cell=moved,
                                      check_cells=False), "ok")


class TestExtraPhases(unittest.TestCase):
    def test_poor_cell_free_and_al_per_reflection(self):
        poor = SimpleNamespace(lattice=SimpleNamespace(
            a=9.79, b=5.83, c=4.769))
        al = object()
        specs = fi._extra_phases(poor, al, (10.17, 5.82, 4.78))
        by_label = {s["label"]: s for s in specs}
        self.assertTrue(by_label["LMFP_poor"]["refine_cell"])
        self.assertFalse(by_label["LMFP_poor"]["refine_intensities"])
        self.assertTrue(by_label["Al"]["refine_intensities"])
        self.assertNotIn("refine_cell", by_label["Al"])
        self.assertEqual(by_label["LMFP_poor"]["a"], 10.17)


if __name__ == "__main__":
    unittest.main()
