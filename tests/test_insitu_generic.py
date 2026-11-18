"""通用多相原位模式的纯函数、批次行与入口（2026-11-11 用户第 14 条）。"""
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403
except ModuleNotFoundError:  # 直接运行本文件
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403

from xrd_toolkit.simulation import insitu_generic as gen  # noqa: E402

RICH = ROOT / "data" / "structures" / "LiFePO4_olivine.cif"
POOR = ROOT / "data" / "structures" / "Mn0.6Fe0.4PO4_heterosite.cif"


def setUpModule():
    _support.setUpModule()


def _duck(*, rwp=2.0, success=True, rich_a=10.33,
          poor_cell=(9.80, 5.83, 4.77),
          labels=("LiFePO4_olivine", "Mn0_6Fe0_4PO4_heterosite")):
    poor = labels[1]
    return SimpleNamespace(
        rwp=rwp, rp=1.0, success=success, scale=2.0,
        peak_table_intensities=np.array([10.0, 10.0]),
        peak_table_norm=250.0,
        params={"a": rich_a, "b": 6.00, "c": 4.69,
                f"{poor}:a": poor_cell[0], f"{poor}:b": poor_cell[1],
                f"{poor}:c": poor_cell[2],
                f"{poor}:scale": 1.0, "scale": 1.0},
        extra_lebail={},
        extra_phase_table_sum={poor: 20.0},
        extra_phase_norm={poor: 250.0})


class TestGenericEngine(unittest.TestCase):
    def test_cell_free_by_symmetry(self):
        from xrd_toolkit.simulation import load_structure
        self.assertEqual(
            gen.cell_free_params(load_structure(str(RICH))),
            ("a", "b", "c"))
        al = load_structure(str(ROOT / "data" / "structures" / "Al_metal.cif"))
        self.assertEqual(gen.cell_free_params(al), ("a",))

    def test_phases_labels_unique(self):
        phases = gen.make_phases([RICH, POOR])
        self.assertEqual(len({p.label for p in phases}), 2)
        self.assertFalse(phases[0].lebail)
        self.assertFalse(phases[1].lebail)

    def test_fractions_use_absolute_amplitudes(self):
        phases = gen.make_phases([RICH, POOR])
        fracs = gen.fractions(_duck(), phases)
        self.assertAlmostEqual(fracs[phases[0].label], 100.0 / 150.0)
        self.assertAlmostEqual(fracs[phases[1].label], 50.0 / 150.0)

    def test_batch_rows_and_csv(self):
        batch = gen.GenericBatch([RICH, POOR], {}, 1.0)
        labels = [p.label for p in batch.phases]
        poor_cell = tuple(float(x) for x in
                          batch.phases[1].structure.lattice.abc)
        with mock.patch.object(
                gen, "load_frame",
                return_value=(np.array([1.0, 2.0]),
                              np.array([1.0, 2.0]), np.zeros(2),
                              {"fit": 0.1})), \
             mock.patch.object(gen.GenericBatch, "_fit_once",
                               return_value=_duck(labels=labels,
                                                  poor_cell=poor_cell)):
            row = batch.fit_frame("frame.tif")
        self.assertEqual(row["status"], "ok")
        self.assertAlmostEqual(row["f_LiFePO4_olivine"], 100.0 / 150.0,
                               places=6)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "generic.csv"
            batch.write_csv(out)
            header = out.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("f_LiFePO4_olivine", header)
        self.assertIn("Mn0_6Fe0_4PO4_heterosite_a", header)

    def test_phase_set_roles_and_fraction_denominator(self):
        """2026-11-17：rich/poor 样品相 + Al 参考相；Al 不进样品分数。"""
        from xrd_toolkit.simulation.phase_set import lmfp_phase_set
        pset = lmfp_phase_set()
        batch = gen.GenericBatch([], {}, 1.0, phase_set=pset)
        self.assertEqual([p.role for p in batch.phases],
                         ["sample", "sample", "reference"])
        self.assertEqual(batch._main_phase().label, "rich")
        labels = ["rich", "poor", "Al"]
        duck = _duck(labels=("rich", "poor"))
        duck.extra_phase_table_sum["Al"] = 10.0
        duck.extra_phase_norm["Al"] = 100.0
        duck.params["Al:scale"] = 1.0
        fracs = gen.fractions(duck, batch.phases)
        self.assertAlmostEqual(fracs["rich"], 100.0 / 150.0, places=6)
        self.assertAlmostEqual(fracs["poor"], 50.0 / 150.0, places=6)
        self.assertFalse(math.isfinite(fracs["Al"]), "参考相不进分母")

    def test_prepare_batch_from_scheme(self):
        scheme = {
            "name": "demo", "adapter": "", "policy": {}, "views": {},
            "instrument": {"mode": "none"},
            "phases": [{"path": str(RICH), "label": "demo",
                        "role": "sample", "width_free": False,
                        "size_um": 0.2}],
        }
        batch = gen.prepare_batch("lmfp1_lab6", scheme)
        self.assertIsInstance(batch, gen.GenericBatch)
        self.assertNotIsInstance(batch, gen.LMFPBatch)
        self.assertEqual(batch.instrument, {}, "mode=none 不固定仪器")
        self.assertAlmostEqual(batch.phases[0].size_um, 0.2)
        self.assertFalse(batch.phases[0].width_free,
                         "方案里的 PhaseSpec 字段要原样进批处理")

    def test_prepare_batch_uses_lmfp_adapter(self):
        scheme = {"name": "LMFP", "adapter": "lmfp", "policy": {},
                  "views": {}, "instrument": {"mode": "none"},
                  "phases": [{"path": str(RICH), "label": "rich"}]}
        batch = gen.prepare_batch("lmfp1_lab6", scheme)
        self.assertIsInstance(batch, gen.LMFPBatch)

    def test_resolve_instrument_modes(self):
        from xrd_toolkit.simulation.phase_set import InstrumentSpec
        record = {"wavelength": 1.0, "params": {"zero": 0.1}}
        self.assertEqual(
            gen.resolve_instrument(
                InstrumentSpec(mode="baseline"), {}, 1.0, record),
            {"zero": 0.1})
        self.assertIsNone(gen.resolve_instrument(
            InstrumentSpec(mode="baseline"), {}, 2.0, record),
            "λ 不符不能用旧基线")
        self.assertIsNone(gen.resolve_instrument(
            InstrumentSpec(mode="none"), {}, 1.0, record))

    def test_cell_jump_keeps_seed(self):
        batch = gen.GenericBatch([RICH, POOR], {}, 1.0)
        batch.frame_no = 1        # 第一帧不判晶胞跳变（与 LMFP 引擎同口径）
        before = dict(batch.seed)
        labels = [p.label for p in batch.phases]
        poor_cell = tuple(float(x) for x in
                          batch.phases[1].structure.lattice.abc)
        with mock.patch.object(
                gen, "load_frame",
                return_value=(np.array([1.0, 2.0]),
                              np.array([1.0, 2.0]), np.zeros(2), {})), \
             mock.patch.object(gen.GenericBatch, "_fit_once",
                               return_value=_duck(rich_a=10.80,
                                                  poor_cell=poor_cell,
                                                  labels=labels)):
            row = batch.fit_frame("frame.tif")
        self.assertEqual(row["status"], "cell_jump")
        self.assertEqual(batch.seed, before)


class TestGenericEntry(unittest.TestCase):
    def setUp(self):
        self.w = create_window()
        self.w.show()
        QApplication.processEvents()
        self.w.insitu_btn.click()
        QApplication.processEvents()
        self.editor = self.w.insitu_editor

    def tearDown(self):
        self.w.hide()
        self.w.close()
        QApplication.processEvents()

    def _pick(self, data):
        combo = self.editor.preset_combo
        combo.setCurrentIndex(combo.findData(data))
        combo.activated.emit(combo.currentIndex())
        QApplication.processEvents()

    def test_default_page_is_generic_and_empty(self):
        """默认是通用空相表（2026-11-18）。"""
        self.assertEqual(self.editor.preset_combo.currentData(), "generic")
        self.assertEqual(self.editor.phase_table.rowCount(), 0)
        self.assertEqual(self.editor._table_keys[:5],
                         ["frame", "file", "status", "rwp", "rp"])

    def test_lmfp_preset_fills_the_phase_table_in_place(self):
        self._pick("lmfp")
        table = self.editor.phase_table
        self.assertEqual(table.rowCount(), 3)
        roles = [table.cellWidget(r, 0).currentData() for r in range(3)]
        self.assertEqual(roles, ["sample", "sample", "reference"])
        self.assertEqual(table.cellWidget(2, 3).currentData(), "lebail")
        self.assertFalse(table.cellWidget(2, 4).isChecked())
        self.assertFalse(table.cellWidget(1, 5).isChecked())
        table.cellWidget(1, 5).setChecked(True)
        self.assertEqual(
            self.editor.phase_table.phases()[1]["free_extra"],
            ("b_iso", "size_um", "mustrain"))
        # 结果表列按方案（LMFP 紧凑 12 列）
        self.assertEqual(len(self.editor._table_keys), 12)
        self.assertIn("f_rich", self.editor._table_keys)
        self.assertEqual(self.editor._scheme_instrument.mode, "lab6")

    def test_switching_to_generic_clears_the_table(self):
        self._pick("lmfp")
        self._pick("generic")
        self.assertEqual(self.editor.phase_table.rowCount(), 0)
        self.assertEqual(self.editor._scheme_adapter, "")

    def test_rename_updates_plot_name_and_bragg_labels(self):
        """改名同时改图内名称（2026-11-18 用户："Li rich 这些怎么改"）。"""
        from xrd_toolkit.gui import insitu_phase_table as pt
        self._pick("lmfp")
        table = self.editor.phase_table
        table.setCurrentCell(0, 1)

        class _Dlg:
            def __init__(self, *args, **kwargs):
                pass

            def exec(self):
                return pt.QDialog.Accepted

            def values(self):
                return ("富锂相（新）", "Li-rich custom")

        with mock.patch.object(pt, "_NamesDialog", _Dlg):
            self.editor._rename_phase()
        self.assertEqual(table.item(0, 1).text(), "富锂相（新）")
        spec = table.phases()[0]
        self.assertEqual(spec["display_name"], "富锂相（新）")
        self.assertEqual(spec["plot_name"], "Li-rich custom")
        self.assertEqual(
            self.editor._preview_phase_set.phases[0].plot_name,
            "Li-rich custom")
        self.assertEqual(
            self.editor._bragg_labels()["rich"], "Li-rich custom")

    def test_phase_details_update_spec_and_color(self):
        """每相细节（2026-11-18）：B/尺寸/晶胞范围等写回相表，颜色立即生效。"""
        from xrd_toolkit.gui import insitu_phase_table as pt
        self._pick("lmfp")
        table = self.editor.phase_table
        table.setCurrentCell(0, 1)

        class _Dlg:
            def __init__(self, parent, spec):
                self.spec = dict(spec)

            def exec(self):
                return pt.QDialog.Accepted

            def values(self):
                self.spec["size_um"] = 0.5
                self.spec["cell_bounds"] = {"a": (10.3, 10.4)}
                self.spec["color"] = "#ff0000"
                return self.spec

        with mock.patch.object(pt, "_DetailsDialog", _Dlg):
            self.editor._edit_phase_details()
        spec = table.phases()[0]
        self.assertAlmostEqual(spec["size_um"], 0.5)
        self.assertEqual(spec["cell_bounds"], {"a": (10.3, 10.4)})
        self.assertEqual(spec["color"], "#ff0000")
        self.assertEqual(
            self.editor._preview_phase_set.phases[0].color, "#ff0000")

    def test_scheme_save_load_roundtrip(self):
        from xrd_toolkit.gui import insitu_workbench as wb
        self._pick("lmfp")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "demo.json"
            with mock.patch.object(
                    wb.QFileDialog, "getSaveFileName",
                    return_value=(str(out), "")):
                self.editor._save_scheme()
            self.assertTrue(out.is_file())
            self._pick("generic")
            self.assertEqual(self.editor.phase_table.rowCount(), 0)
            with mock.patch.object(
                    wb.QFileDialog, "getOpenFileName",
                    return_value=(str(out), "")):
                self.editor._load_scheme()
            self.assertEqual(self.editor.phase_table.rowCount(), 3)
            self.assertEqual(
                [p["label"] for p in self.editor.phase_table.phases()],
                ["rich", "poor", "Al"])
            self.assertEqual(self.editor._scheme_views.shade_phase, "rich")
            self.assertEqual(
                self.editor._scheme_views.peak_windows[0].tth_min, 1.18)
            self.assertEqual(self.editor._scheme_instrument.mode, "lab6")
            self.assertEqual(self.editor._scheme_adapter, "lmfp")
