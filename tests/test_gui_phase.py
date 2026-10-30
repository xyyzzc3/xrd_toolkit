"""物相工作台的窗口级测试（2026-10-30）。

覆盖：
  1. [物相] 进出与四种工作台互斥；
  2. 真筛相链路：合成 LaB₆ 曲线 → [开始筛相] → 候选表/理论峰位叠加；
  3. [送去精修] 把曲线与 CIF 预选进精修工作台。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(ROOT / "tests")]

import numpy as np  # noqa: E402
from PySide6.QtCore import QItemSelectionModel  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from unittest import mock  # noqa: E402

try:
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403
except ModuleNotFoundError:  # 直接运行本文件
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403

from xrd_toolkit.simulation import b_iso_from_cif, load_structure  # noqa: E402
from xrd_toolkit.simulation.gsas_profile import (  # noqa: E402
    simulate_pattern_gsas)

LAB6_CIF = ROOT / "data" / "structures" / "LaB6_NIST660c.cif"
PANEL_PATH = "data/fake_phase.tif"


def setUpModule():
    _support.setUpModule()


def _synth(path, geom, npt):
    structure = load_structure(str(LAB6_CIF))
    b_iso = b_iso_from_cif(str(LAB6_CIF)) or None
    tth, y, _peaks, _hex = simulate_pattern_gsas(
        structure, wavelength=float(geom["wavelength_m"]) * 1e10,
        two_theta_range=(1.0, 8.0), step_deg=0.005, b_iso=b_iso,
        normalize=False)
    return tth, y + 50.0


class TestPhaseWorkbench(unittest.TestCase):
    def setUp(self):
        self._old_root = _support.stage_cache.CACHE_ROOT
        self.w = create_window()
        self.w.show()
        QApplication.processEvents()

    def tearDown(self):
        self.w.hide()
        self.w.close()
        QApplication.processEvents()
        _support.stage_cache.point_root(self._old_root)

    def _open_panel(self):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_synth):
            add_checked(self.w, [PANEL_PATH])
            _open_view(self.w, "1D")
            ok = _wait_until(
                lambda: f"1D|{PANEL_PATH}" in self.w.plot_docks
                and getattr(self.w.plot_docks[f"1D|{PANEL_PATH}"],
                            "last_tth", None) is not None,
                timeout_ms=20000)
        self.assertTrue(ok, "1D 图没算完")

    def _screen(self):
        self._open_panel()
        self.w.phase_btn.click()
        QApplication.processEvents()
        self.w.phase_editor.screen_btn.click()
        ok = _wait_until(lambda: len(self.w.phase_editor.rows) > 0,
                         timeout_ms=30000)
        self.assertTrue(ok, "筛相没有在 30 秒内完成")

    def test_enter_exit_and_mutual_exclusion(self):
        self.w.phase_btn.click()
        QApplication.processEvents()
        self.assertTrue(self.w._phase_active)
        self.assertEqual(self.w.phase_btn.text(), "退出物相")
        self.assertEqual(self.w.mode_label.text(), "物相模式")
        self.assertTrue(self.w.phase_dock.isVisible())
        self.assertFalse(self.w.param_dock.isVisible())
        self.assertFalse(self.w.log_dock.isVisible())

        self.w.refine_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.w._phase_active, "精修应把物相退掉")
        self.assertTrue(self.w._refine_active)
        self.w.refine_btn.click()
        QApplication.processEvents()

        self.w.phase_btn.click()
        QApplication.processEvents()
        self.w.mask_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.w._phase_active, "Mask 应把物相退掉")
        self.assertTrue(self.w._mask_active)
        self.w.mask_btn.click()
        QApplication.processEvents()

        self.w.phase_btn.click()
        QApplication.processEvents()
        self.w.calib_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.w._phase_active, "校准应把物相退掉")
        self.assertTrue(self.w.calib_btn.isChecked())
        self.w.calib_btn.click()
        QApplication.processEvents()

        self.w.phase_btn.click()
        QApplication.processEvents()
        self.w.entrance_buttons["1D"].click()
        QApplication.processEvents()
        self.assertFalse(self.w._phase_active, "点分析入口应退出物相")

    def test_screen_ranks_lab6_and_draws_sticks(self):
        self._screen()
        editor = self.w.phase_editor
        self.assertGreater(len(editor.rows), 0)
        self.assertTrue(editor.rows[0].path.name.startswith("LaB6"),
                        editor.rows[0].path.name)
        self.assertEqual(editor.table.rowCount(), len(editor.rows))
        self.assertEqual(editor.table.currentRow(), 0)
        self.assertIn("LaB6", editor.best_label.text())
        self.assertTrue(editor.fig.axes[0].collections,
                        "选中候选相后应画出理论峰位")
        self.assertTrue(editor.send_btn.isEnabled())

    def test_multi_select_draws_all_selected_phases(self):
        self._screen()
        editor = self.w.phase_editor
        self.assertEqual(editor.table.currentRow(), 0)
        model = editor.table.selectionModel()
        idx = editor.table.model().index(1, 0)
        model.select(idx, QItemSelectionModel.Select
                     | QItemSelectionModel.Rows)
        QApplication.processEvents()
        self.assertGreaterEqual(len(editor.fig.axes[0].collections), 2,
                                "选两个相应画出两组理论峰位")
        legend = editor.fig.axes[0].get_legend()
        self.assertIsNotNone(legend)
        texts = [text.get_text() for text in legend.get_texts()]
        self.assertGreaterEqual(
            sum("理论峰位" in text for text in texts), 2)

    def test_load_text_curve_and_screen(self):
        self.w.phase_btn.click()
        QApplication.processEvents()
        editor = self.w.phase_editor
        tth, y = _synth("", self.w.config["geometry"], 3000)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "curve.txt"
            np.savetxt(path, np.column_stack([tth, y]),
                       header="2theta intensity", comments="# ")
            self.assertTrue(editor.load_curve_file(path))
            self.assertEqual(editor.curve_combo.currentData(),
                             f"file:{path}")
            editor.screen_btn.click()
            ok = _wait_until(lambda: len(editor.rows) > 0,
                             timeout_ms=30000)
            self.assertTrue(ok, "文件曲线筛相没有完成")
            self.assertTrue(editor.rows[0].path.name.startswith("LaB6"),
                            editor.rows[0].path.name)

    def test_export_results_writes_csv(self):
        self._screen()
        editor = self.w.phase_editor
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "screen.csv"
            editor._export_results(path)
            text = path.read_text(encoding="utf-8-sig")
        self.assertIn("phase,score", text)
        self.assertIn("LaB6", text)

    def test_send_to_refine_preselects_curve_and_cif(self):
        self._screen()
        editor = self.w.phase_editor
        chosen = editor.rows[0].path
        editor.send_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.w._phase_active)
        self.assertTrue(self.w._refine_active)
        self.assertEqual(self.w.refine_editor.curve_combo.currentData(),
                         f"1D|{PANEL_PATH}")
        self.assertEqual(self.w.refine_editor.cif_combo.currentData(),
                         str(chosen))


if __name__ == "__main__":
    unittest.main()
