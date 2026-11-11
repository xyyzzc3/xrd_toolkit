"""通用多相原位模式的纯函数、批次行与入口（2026-11-11 用户第 14 条）。"""
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

    def test_cell_jump_keeps_seed(self):
        batch = gen.GenericBatch([RICH, POOR], {}, 1.0)
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

    def tearDown(self):
        dlg = getattr(self.w, "_generic_insitu_dialog", None)
        if dlg is not None:
            dlg.close()
        self.w.hide()
        self.w.close()
        QApplication.processEvents()

    def test_button_opens_dialog_with_seeded_phases(self):
        self.w.insitu_btn.click()
        QApplication.processEvents()
        self.w.insitu_editor.generic_btn.click()
        QApplication.processEvents()
        dlg = self.w._generic_insitu_dialog
        self.assertIsNotNone(dlg)
        self.assertEqual(dlg.phase_list.count(), 3,
                         "默认应带入 LMFP 三相")
        self.assertEqual(dlg.phase_list.item(0).text().split("：")[0], "主相")
        dlg.close()
        QApplication.processEvents()
        self.assertIsNone(self.w._generic_insitu_dialog)
