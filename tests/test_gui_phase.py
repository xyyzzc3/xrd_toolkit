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
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QPushButton  # noqa: E402
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
        self.assertTrue(self.w._bottom_strip.isHidden(),
                        "工作台里底部横带应隐藏")

        self.w.refine_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.w._phase_active, "精修应把物相退掉")
        self.assertTrue(self.w._refine_active)
        self.w.refine_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.w._bottom_strip.isHidden(),
                         "退出工作台应恢复底部横带")

        self.w.phase_btn.click()
        QApplication.processEvents()
        self.w.mask_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.w._phase_active, "掩膜应把物相退掉")
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

    def test_curve_list_includes_file_bar_products(self):
        """文件栏里的 1D 产物不打开面板也能选（2026-11-02）。"""
        import types

        from xrd_toolkit.gui import phase_workbench as gui_phase
        self.w.phase_btn.click()
        QApplication.processEvents()
        editor = self.w.phase_editor
        source = types.SimpleNamespace(
            kind=gui_phase.gui_sources.ONED, display="fake_phase.tif",
            path=PANEL_PATH, key="deadbeef")
        curve = (np.linspace(1.0, 8.0, 500), np.ones(500))
        key = f"product:{gui_phase.gui_sources.source_id(source)}"
        with mock.patch.object(gui_phase.gui_sources, "all_sources",
                               return_value=[source]), \
                mock.patch.object(gui_phase.gui_sources, "load_product",
                                  return_value=curve):
            editor.refresh_curves()
            keys = [editor.curve_combo.itemData(i)
                    for i in range(editor.curve_combo.count())]
            self.assertIn(key, keys)
            editor.curve_combo.setCurrentIndex(
                editor.curve_combo.findData(key))
            self.assertIsNotNone(editor._selected_curve())

    def test_export_without_screening_explains_why(self):
        """没筛相点导出要给提示，不能没反应（2026-11-02）。"""
        self.w.phase_btn.click()
        QApplication.processEvents()
        editor = self.w.phase_editor
        self.assertTrue(editor.export_default_btn.isEnabled())
        editor.export_default_btn.click()
        QApplication.processEvents()
        self.assertIn("先点 [开始筛相]", editor.status.text())

    def test_curve_shows_on_open(self):
        """进入物相工作台就该看到选中的 1D 曲线（2026-11-02）。"""
        self._open_panel()
        self.w.phase_btn.click()
        QApplication.processEvents()
        editor = self.w.phase_editor
        self.assertIsNotNone(editor.curve)
        self.assertGreater(len(editor.fig.axes[0].lines), 0)
        # 隐藏再显示（切走再回来）：showEvent 要补画，不能变空白
        self.w.phase_dock.hide()
        QApplication.processEvents()
        self.w.phase_dock.show()
        QApplication.processEvents()
        self.assertGreater(len(editor.fig.axes[0].lines), 0)

    def test_refine_cell_dialog(self):
        self._screen()
        editor = self.w.phase_editor
        self.assertTrue(editor.cell_btn.isEnabled())
        self.assertEqual(editor.cell_btn.text(), "峰位精修晶胞")
        editor.cell_btn.click()
        QApplication.processEvents()
        dlg = getattr(editor, "_cell_dialog", None)
        self.assertIsNotNone(dlg)
        self.assertTrue(dlg.isVisible())
        text = dlg.findChild(QPlainTextEdit).toPlainText()
        self.assertIn("不写回 CIF", text)
        self.assertIn("cell refinement", text)
        self.assertIn("cubic", text)
        from xrd_toolkit.gui import phase_workbench as gui_phase
        save = next(b for b in dlg.findChildren(QPushButton)
                    if b.text() == "保存报告…")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cell_refine.txt"
            with mock.patch.object(gui_phase.QFileDialog,
                                   "getSaveFileName",
                                   return_value=(str(path), "")):
                save.click()
            self.assertTrue(path.is_file())
            self.assertIn("cell refinement",
                          path.read_text(encoding="utf-8"))
        dlg.close()
        QApplication.processEvents()

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

    def test_copy_candidate_table(self):
        self._screen()
        editor = self.w.phase_editor
        n = editor._copy_table(False)
        self.assertEqual(n, len(editor.rows))
        text = QApplication.clipboard().text()
        self.assertIn("score", text)
        self.assertIn("LaB6", text)

    def test_export_results_writes_csv(self):
        self._screen()
        editor = self.w.phase_editor
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "screen.csv"
            editor._export_results(path)
            text = path.read_text(encoding="utf-8-sig")
        self.assertIn("phase,score", text)
        self.assertIn("LaB6", text)

    def test_export_falls_back_to_default_outputs(self):
        """保存位置不可写时回退到默认输出目录（2026-11-02）。"""
        self._screen()
        editor = self.w.phase_editor
        from xrd_toolkit.gui import phase_workbench as gui_phase
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            blocker = root / "blocker"
            blocker.write_text("not a folder", encoding="utf-8")
            bad = blocker / "screen.csv"       # parent 是文件，mkdir 会失败
            fallback = root / "fallback"
            with mock.patch.object(gui_phase.paths, "default_outputs_dir",
                                   return_value=fallback):
                editor._export_results(str(bad))
            files = list(fallback.glob("phase_screen_*.csv"))
            self.assertTrue(files, editor.status.text())
            self.assertIn("已导出", editor.status.text())

    def test_export_default_button_writes_without_dialog(self):
        """一键导出到默认位置，不弹保存框（2026-11-02）。"""
        self._screen()
        editor = self.w.phase_editor
        from xrd_toolkit.gui import phase_workbench as gui_phase
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(gui_phase.paths, "default_outputs_dir",
                                   return_value=Path(folder)):
                editor.export_default_btn.click()
                QApplication.processEvents()
            files = list(Path(folder).glob("phase_screen_*.csv"))
            self.assertTrue(files, editor.status.text())

    def test_ebadf_error_is_explained(self):
        import errno

        from xrd_toolkit.gui.tasks import user_error_text
        msg = user_error_text(OSError(errno.EBADF, "Bad file descriptor"))
        self.assertIn("保存位置", msg)

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
