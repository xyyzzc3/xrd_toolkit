"""通用多相原位：PhaseSpec 角色、峰窗指标与通用视图（2026-11-17）。"""
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.simulation.insitu_generic import (  # noqa: E402
    GenericBatch, window_stats)
from xrd_toolkit.simulation.phase_set import (  # noqa: E402
    PeakWindow, PhaseSet, ViewConfig, lmfp_phase_set)


def _rows(n=5):
    out = []
    for i in range(1, n + 1):
        f = 0.9 - 0.1 * i
        out.append({
            "frame": i, "status": "ok", "rwp": 2.0 + 0.1 * i,
            "f_rich": f, "f_poor": 1.0 - f, "f_Al": float("nan"),
            "rich_a": 10.35 - 0.01 * i, "rich_c": 4.70,
            "poor_a": 9.84 + 0.01 * i, "poor_c": 4.78,
            "win_0_centroid": 1.36 + 0.005 * i,
            "win_0_area": 100.0 + i, "win_0_height": 20.0 + i,
        })
    return out


class TestPhaseSet(unittest.TestCase):
    def test_lmfp_preset_roles_and_windows(self):
        pset = lmfp_phase_set()
        self.assertEqual([p.role for p in pset.phases],
                         ["sample", "sample", "reference"])
        self.assertEqual([p.display() for p in pset.phases],
                         ["富锂相", "贫锂相", "Al 参考相"])
        self.assertFalse(pset.phases[2].include_in_fraction)
        self.assertEqual(pset.views.crossover_pair, ("rich", "poor"))
        self.assertEqual(pset.views.peak_windows[0].tth_min, 1.18)

    def test_phase_set_json_roundtrip(self):
        import tempfile
        from xrd_toolkit.simulation.phase_set import (
            load_phase_set, save_phase_set)
        pset = lmfp_phase_set()
        with tempfile.TemporaryDirectory() as tmp:
            path = save_phase_set(pset, Path(tmp) / "lmfp.json")
            got = load_phase_set(path)
        self.assertEqual(got.labels(), pset.labels())
        self.assertEqual([p.role for p in got.phases],
                         [p.role for p in pset.phases])
        self.assertEqual(got.views.shade_phase, "rich")
        self.assertEqual(got.views.peak_windows[0].name, "(200) doublet")
        self.assertEqual(got.policy.joint_stage, pset.policy.joint_stage)
        # 2026-11-18：仪器来源与表格列随方案走；装到别的机器上
        # 标样帧路径可能不存在，但模式必须还是 lab6。
        self.assertEqual(got.instrument.mode, "lab6")
        self.assertEqual(got.instrument.lab6_tif,
                         pset.instrument.lab6_tif)
        self.assertEqual(got.views.table_columns,
                         pset.views.table_columns)
        self.assertIn("f_rich", got.views.table_columns)

    def test_scheme_roundtrip_keeps_every_phase_field(self):
        """对话框时代的损失回归：width_free / cell0 / 尺寸 / 边界不能丢。"""
        import tempfile
        from xrd_toolkit.simulation.phase_set import (
            load_phase_set, save_phase_set)
        pset = lmfp_phase_set()
        with tempfile.TemporaryDirectory() as tmp:
            got = load_phase_set(save_phase_set(pset, Path(tmp) / "s.json"))
        rich, poor, al = got.phases
        self.assertFalse(rich.width_free, "rich 的 width_free=False 是金标准口径")
        self.assertAlmostEqual(rich.size_um, 0.093, places=6)
        self.assertAlmostEqual(poor.b_iso, 0.5, places=6)
        self.assertEqual(al.cell0, (4.061, 4.061, 4.061))
        self.assertEqual(al.intensity, "lebail")
        self.assertFalse(al.include_in_fraction)
        self.assertEqual(rich.cell_bounds["a"],
                         pset.phases[0].cell_bounds["a"])
        self.assertEqual([p.color for p in got.phases],
                         [p.color for p in pset.phases])
        self.assertEqual(rich.plot_name, "Li-rich phase")

    def test_v1_scheme_file_gets_defaults(self):
        """v1 旧方案没有 instrument/table_columns，读取补默认值。"""
        import json
        import tempfile
        from xrd_toolkit.simulation.phase_set import (
            load_phase_set, phase_set_to_dict, save_phase_set)
        data = phase_set_to_dict(lmfp_phase_set())
        data["version"] = 1
        data.pop("instrument", None)
        data["views"].pop("table_columns", None)
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "v1.json"
            old.write_text(json.dumps(data, ensure_ascii=False),
                           encoding="utf-8")
            got = load_phase_set(old)
            new = save_phase_set(lmfp_phase_set(), Path(tmp) / "v2.json")
            written = json.loads(new.read_text(encoding="utf-8"))
        self.assertEqual(got.instrument.mode, "baseline")
        self.assertEqual(got.views.table_columns, ())
        self.assertEqual(written["version"], 2)
        self.assertIn("instrument", written)

    def test_generic_batch_uses_phase_set(self):
        pset = lmfp_phase_set()
        batch = GenericBatch([], {}, 1.0, phase_set=pset)
        self.assertEqual(batch._main_phase().label, "rich")
        self.assertEqual(batch.seed["Al"], (4.061, 4.061, 4.061))


class TestWindowStats(unittest.TestCase):
    def test_gaussian_centroid(self):
        t = np.linspace(1.0, 2.0, 1000)
        y = 100.0 + 50.0 * np.exp(-((t - 1.4) / 0.02) ** 2)
        area, centroid, height = window_stats(
            t, y, PeakWindow("p", 1.3, 1.5,
                             side_lo=(1.30, 1.35), side_hi=(1.45, 1.50)))
        self.assertAlmostEqual(centroid, 1.4, delta=1e-3)
        self.assertGreater(area, 0)
        self.assertGreater(height, 40)


class TestMultiViews(unittest.TestCase):
    def _draw(self, fn):
        from matplotlib.figure import Figure
        fig = Figure(figsize=(6, 4))
        fn(fig, _rows(), lmfp_phase_set())
        self.assertGreaterEqual(len(fig.axes), 4)

    def test_trends(self):
        from xrd_toolkit.simulation.insitu_multi_plots import (
            draw_multi_trends)
        self._draw(draw_multi_trends)

    def test_summary(self):
        from xrd_toolkit.simulation.insitu_multi_plots import (
            draw_multi_summary)
        self._draw(draw_multi_summary)

    def test_window_tracker(self):
        from xrd_toolkit.simulation.insitu_multi_plots import (
            draw_window_tracker)
        self._draw(draw_window_tracker)


if __name__ == "__main__":
    unittest.main()
