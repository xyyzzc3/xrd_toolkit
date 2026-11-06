"""已知 wt% 回收实验（A 定量验证）测试（2026-11-06）。

  * `phase_curve` 必须与 `simulate_pattern_gsas(raw=True)` 逐点一致；
  * 真背景 + 无噪声时，两相回收必须回到已知配比（公式链路）；
  * `weight_percent_esd` 的协方差传播与手算一致，Le Bail 相给 NaN。
"""
import math
import sys
import types
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.simulation import (  # noqa: E402
    MixturePhase, core, load_structure, recovery_fit, simulate_mixture,
    simulate_pattern_gsas, summarize_recovery, weight_percent_esd)
from xrd_toolkit.simulation.quantitative import (  # noqa: E402
    PhaseQuantity, phase_mass_factor, phase_weight_percent)
from xrd_toolkit.simulation.recovery import phase_curve  # noqa: E402

STRUCT_DIR = ROOT / "data" / "structures"
CU_KA = 1.5406
PROFILE = dict(wavelength=CU_KA, u=0.0, v=0.0, w=18.033,
               x=0.0, y=0.0, z=0.0)


class TestPhaseCurve(unittest.TestCase):
    def test_matches_gsas_simulator(self):
        st = load_structure(STRUCT_DIR / "LiFePO4_olivine.cif")
        lo, hi, step = 10.0, 40.0, 0.02
        tth = np.arange(lo, hi + step / 2.0, step)
        _t, want, _p, _h = simulate_pattern_gsas(
            st, wavelength=CU_KA, two_theta_range=(lo, hi), step_deg=step,
            w=18.033, size_um=None, mustrain=None, b_iso=None,
            raw=True, normalize=False, min_intensity=0.0)
        got = phase_curve(st, tth, wavelength=CU_KA,
                          two_theta_range=(lo, hi), w=18.033)
        self.assertEqual(got.shape, want.shape)
        self.assertLess(np.max(np.abs(got - want)),
                        1e-9 * float(np.max(np.abs(want))))


class TestKnownMixtureRecovery(unittest.TestCase):
    def test_oracle_background_returns_known_ratio(self):
        main = load_structure(STRUCT_DIR / "LiFePO4_olivine.cif")
        al = load_structure(STRUCT_DIR / "Al_metal.cif")
        lo, hi, step = 10.0, 40.0, 0.02
        grid = np.arange(lo, hi + step / 2.0, step)
        phases = [
            MixturePhase(main, 70.0, "LiFePO4", b_iso=0.0, size_um=0.093),
            MixturePhase(al, 30.0, "Al", b_iso=0.5, size_um=0.1),
        ]
        _truth, obs = simulate_mixture(
            phases, grid, wavelength=CU_KA, background=0.0,
            peak_max=2000.0, noise_scale=0.0, profile={
                "zero_deg": 0.0, "u": 0.0, "v": 0.0, "w": 18.033,
                "x": 0.0, "y": 0.0, "polariz": 0.5, "nmax": 10})
        res = recovery_fit(
            main, al, grid, obs, wavelength=CU_KA,
            instrument={"zero": 0.0, "u": 0.0, "v": 0.0,
                        "w": 18.033, "x": 0.0, "y": 0.0},
            main_cell=main.lattice.abc, extra_cell=al.lattice.abc,
            background=np.zeros_like(grid), main_label="LiFePO4",
            extra_label="Al", max_nfev=200)
        got = {row.label: row.weight_percent for row in res.phase_weights}
        self.assertAlmostEqual(got["LiFePO4"], 70.0, delta=0.2)
        self.assertAlmostEqual(got["Al"], 30.0, delta=0.2)


class TestWeightPercentEsd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = load_structure(STRUCT_DIR / "LiFePO4_olivine.cif")
        cls.al = load_structure(STRUCT_DIR / "Al_metal.cif")
        cls.rows = phase_weight_percent([
            PhaseQuantity("main", cls.main, 2.0),
            PhaseQuantity("Al", cls.al, 1.0),
        ])
        cls.f = [phase_mass_factor(cls.main),
                 phase_mass_factor(cls.al)]

    def _result(self, cov, label="main"):
        return types.SimpleNamespace(
            phase_weights=self.rows, phase_label=label,
            linear_names=["scale", "Al:scale"],
            linear_cov=np.asarray(cov, dtype=float),
            peak_table_norm=100.0, extra_phase_norm={"Al": 100.0})

    def test_matches_manual_propagation(self):
        cov = np.array([[0.01, 0.0], [0.0, 0.04]])
        got = weight_percent_esd(self._result(cov))["main"]
        # 手算：wt = 100·m1/T，m_p = S_p·Z_p·M_p·V_p，dS/dparam = 100/norm
        f1 = self.f[0][0] * self.f[0][1] * self.f[0][2]
        f2 = self.f[1][0] * self.f[1][1] * self.f[1][2]
        m1, m2 = 2.0 * f1, 1.0 * f2
        total = m1 + m2
        j1 = 100.0 * (f1 * total - m1 * f1) / total ** 2
        j2 = 100.0 * (-m1 * f2) / total ** 2
        want = math.sqrt(j1 * j1 * 0.01 + j2 * j2 * 0.04)
        self.assertAlmostEqual(got, want, places=10)

    def test_lebail_phase_has_no_scale_esd(self):
        rows = list(self.rows) + [types.SimpleNamespace(
            label="Lb", source="le_bail", scale=1.0, z=1.0,
            molar_mass=1.0, volume=1.0)]
        result = types.SimpleNamespace(
            phase_weights=rows, phase_label="main",
            linear_names=["scale", "Al:scale"],
            linear_cov=np.eye(2), peak_table_norm=100.0,
            extra_phase_norm={"Al": 100.0})
        got = weight_percent_esd(result)
        self.assertTrue(math.isnan(got["Lb"]))
        self.assertTrue(math.isfinite(got["main"]))


class TestSummarize(unittest.TestCase):
    def test_bias_and_rms(self):
        rows = [{"target_wt": 10.0, "recovered_wt": 11.0},
                {"target_wt": 50.0, "recovered_wt": 49.0},
                {"target_wt": 90.0, "recovered_wt": float("nan")}]
        stat = summarize_recovery(rows)
        self.assertEqual(stat["n"], 2)
        self.assertAlmostEqual(stat["bias"], 0.0)
        self.assertAlmostEqual(stat["rms"], 1.0)
        self.assertAlmostEqual(stat["max_abs"], 1.0)


if __name__ == "__main__":
    unittest.main()
