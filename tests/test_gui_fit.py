"""1D 页峰形拟合的窗口级测试（2026-10-30）。

用一条合成谱（4 根高斯峰）走真实链路：出 1D 图 → 勾寻峰 → [拟合全部]
→ 拟合结果表 / 图上叠加曲线 / 改参数清空 / 只拟合选中。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(ROOT / "tests")]

import numpy as np  # noqa: E402
from PySide6.QtCore import QItemSelectionModel, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from unittest import mock  # noqa: E402

try:
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403
except ModuleNotFoundError:  # 直接运行本文件
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403


def setUpModule():
    _support.setUpModule()


def _gauss(x, center, fwhm, height):
    return height * np.exp(-4.0 * np.log(2.0) * ((x - center) / fwhm) ** 2)


def _synth(path, geom, npt):
    x = np.linspace(1.001, 7.999, 2500)
    y = np.full_like(x, 100.0)
    y += _gauss(x, 2.0, 0.06, 1200) + _gauss(x, 3.0, 0.05, 900)
    y += _gauss(x, 4.5, 0.08, 700) + _gauss(x, 6.0, 0.07, 500)
    return x, y


class TestFitView(unittest.TestCase):
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
        path = "data/fake_fit.tif"
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_synth):
            add_checked(self.w, [path])
            _open_view(self.w, "1D")
            ok = _wait_until(
                lambda: f"1D|{path}" in self.w.plot_docks
                and getattr(self.w.plot_docks[f"1D|{path}"],
                            "last_tth", None) is not None)
        self.assertTrue(ok, "1D 图没算完")
        self.dock = self.w.plot_docks[f"1D|{path}"]
        self.w.entrance_buttons["1D"].click()
        QApplication.processEvents()
        self.w.params["显示峰位"].setChecked(True)
        self.assertTrue(_wait_until(lambda: self.w.peak_table.rowCount() >= 4),
                        "寻峰没列出 4 根")
        self.ax = _support.gui_panel_state._content(self.dock).axes_1d

    def _fit_lines(self):
        return [ln for ln in self.ax.lines
                if str(ln.get_gid() or "").endswith("fit_curve")]

    def _fit_all(self):
        self.w.select_all_peaks_btn.click()
        QApplication.processEvents()
        self.w.fit_selected_btn.click()
        QApplication.processEvents()

    def test_fit_all_fills_table_and_draws_selected_overlay(self):
        self._open_panel()
        self._fit_all()
        self.assertEqual(self.w.fit_table.rowCount(), 4)
        self.assertTrue(self.w.fit_table.isVisible())
        self.assertEqual(self.w.peak_tabs.currentIndex(), 1,
                         "拟合完应自动切到「拟合结果」页")
        self.assertEqual(self.w.peak_tabs.tabText(1), "拟合结果 · 4 根")
        centers = [entry.fit.center for entry in self.dock.fit_results]
        for got, want in zip(centers, (2.0, 3.0, 4.5, 6.0)):
            self.assertAlmostEqual(got, want, places=2)
        self.assertEqual(len(self._fit_lines()), 1)   # 默认只叠选中峰
        peak = self.dock.detected_peaks[0]
        self.assertAlmostEqual(self.dock.fit_results[0].fit.center,
                               peak.center, places=2)

    def test_wh_block_updates_and_saves(self):
        self._open_panel()
        self._fit_all()
        self.assertIsNotNone(self.dock.wh_result)
        self.assertEqual(len(self.dock.wh_result.beta_sample_deg), 4)
        self.assertIn("D =", self.w.wh_d_label.text())
        self.assertIn("4/4 根参与", self.w.wh_note_label.text())
        text = self.w.wh_scherrer_label.text()
        self.assertIn("Scherrer", text)
        self.assertIn("4 根", text)
        self.assertIn("未扣仪器", text)

        before = np.asarray(self.dock.wh_result.beta_sample_deg).copy()
        self.w.params["W-H 仪器 FWHM (°)"].setValue(0.01)
        QApplication.processEvents()
        after = np.asarray(self.dock.wh_result.beta_sample_deg)
        self.assertLessEqual(float(after.max()), float(before.max()) + 1e-12)

        from xrd_toolkit.gui.wh_view import save_wh_plot
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "wh.png"
            self.assertIsNotNone(save_wh_plot(self.w, self.dock, out))
            self.assertTrue(out.is_file() and out.stat().st_size > 0)

    def test_scherrer_block_tracks_instrument_and_checks(self):
        self._open_panel()
        self._fit_all()
        self.w.params["W-H 仪器 FWHM (°)"].setValue(0.02)
        QApplication.processEvents()
        text = self.w.wh_scherrer_label.text()
        self.assertNotIn("未扣仪器", text)
        self.assertIn("仪器 0.02°", self.w.wh_note_label.text())
        self.w.fit_table.item(0, 0).setCheckState(Qt.Unchecked)
        QApplication.processEvents()
        self.assertIn("3 根", self.w.wh_scherrer_label.text())

    def test_wh_uses_saved_lab6_baseline(self):
        self._open_panel()
        self._fit_all()
        from xrd_toolkit.gui.peak_view import _wavelength_for
        from xrd_toolkit.gui.wh_view import refresh_wh
        lam = _wavelength_for(self.dock)
        self.w.refine_editor = SimpleNamespace(_instrument={
            "params": {"zero": 0.0, "u": 0.0, "v": 0.0,
                       "w": 1.8, "x": 0.0, "y": 0.0},
            "wavelength": lam})
        refresh_wh(self.w, self.dock)
        QApplication.processEvents()
        combo = self.w.params["W-H 仪器来源"]
        self.assertTrue(combo.model().item(1).isEnabled())
        before = np.asarray(self.dock.wh_result.beta_sample_deg).copy()
        combo.setCurrentIndex(1)
        QApplication.processEvents()
        after = np.asarray(self.dock.wh_result.beta_sample_deg)
        self.assertTrue(np.all(after <= before + 1e-12))
        self.assertIn("LaB₆ 基线", self.w.wh_note_label.text())
        self.assertNotIn("未扣仪器", self.w.wh_scherrer_label.text())

        # 波长不符：不可选，自动退回常数
        self.w.refine_editor._instrument["wavelength"] = lam + 0.5
        refresh_wh(self.w, self.dock)
        QApplication.processEvents()
        self.assertFalse(combo.model().item(1).isEnabled())
        self.assertEqual(combo.currentData(), "constant")

    def test_wh_reads_window_baseline_without_refine_editor(self):
        """启动时落盘读回的 window.instrument_baseline 也能喂 W-H。"""
        self._open_panel()
        self._fit_all()
        from xrd_toolkit.gui.peak_view import _wavelength_for
        from xrd_toolkit.gui.wh_view import refresh_wh
        lam = _wavelength_for(self.dock)
        self.w.refine_editor = None
        self.w.instrument_baseline = {
            "params": {"zero": 0.0, "u": 0.0, "v": 0.0,
                       "w": 1.8, "x": 0.0, "y": 0.0},
            "wavelength": lam,
        }
        refresh_wh(self.w, self.dock)
        QApplication.processEvents()
        combo = self.w.params["W-H 仪器来源"]
        self.assertTrue(combo.model().item(1).isEnabled(),
                        "窗口里的基线应让 LaB₆ 项可选")
        combo.setCurrentIndex(1)
        QApplication.processEvents()
        self.assertIn("LaB₆ 基线", self.w.wh_note_label.text())

    def test_overlay_all_draws_every_fit(self):
        self._open_panel()
        self._fit_all()
        self.w.params["拟合全部叠加"].setChecked(True)
        QApplication.processEvents()
        self.assertEqual(len(self._fit_lines()), 4)

    def test_fit_selected_merges(self):
        self._open_panel()
        self._fit_all()
        self.w.peak_table.selectRow(1)
        self.w.fit_selected_btn.click()
        QApplication.processEvents()
        self.assertEqual(self.w.fit_table.rowCount(), 4)   # 其余保留
        self.assertEqual(len(self.dock.fit_results), 4)

    def test_shape_change_clears_results(self):
        self._open_panel()
        self._fit_all()
        self.assertEqual(self.w.fit_table.rowCount(), 4)
        self.w.params["拟合线型"].setCurrentIndex(1)       # 高斯
        QApplication.processEvents()
        self.assertEqual(self.w.fit_table.rowCount(), 0)
        self.assertIsNone(self.dock.fit_results)
        self.assertEqual(len(self._fit_lines()), 0)
        self.assertEqual(self.w.peak_tabs.currentIndex(), 0,
                         "结果作废后应退回「峰表」页")
        self.assertFalse(self.w.peak_tabs.isTabEnabled(1))
        self.assertEqual(self.w.peak_tabs.tabText(1), "拟合结果")

    def test_multi_select_fit_and_wh_exclude(self):
        self._open_panel()
        model = self.w.peak_table.selectionModel()
        for row in range(4):
            model.select(self.w.peak_table.model().index(row, 0),
                         QItemSelectionModel.Select
                         | QItemSelectionModel.Rows)
        self.w.fit_selected_btn.click()
        QApplication.processEvents()
        self.assertEqual(len(self.dock.fit_results), 4)
        self.w.fit_table.item(0, 0).setCheckState(Qt.Unchecked)
        QApplication.processEvents()
        self.assertFalse(self.dock.fit_results[0].wh)
        self.assertIn("3/4 根参与", self.w.wh_note_label.text())

    def test_wh_result_text_has_summary_and_peak_table(self):
        """右键 [复制 W-H 结果] 的文本：摘要 + 参与峰表（2026-11-01）。"""
        import math

        from xrd_toolkit.gui.wh_view import wh_result_text
        from xrd_toolkit.simulation.microstructure import williamson_hall

        lam = 1.5406
        d_nm, eps = 30.0, 1.0e-3
        centers = [10.0, 20.0, 30.0, 40.0]
        intercept = (0.9 * lam / (d_nm * 10.0)) ** 2
        slope = (4.0 * eps) ** 2
        fwhms = []
        for center in centers:
            theta = math.radians(center / 2.0)
            y = intercept + slope * math.sin(theta) ** 2
            fwhms.append(math.degrees(math.sqrt(y) / math.cos(theta)))
        result = williamson_hall(centers, fwhms, wavelength=lam)
        entries = [SimpleNamespace(
            wh=True,
            fit=SimpleNamespace(center=c, fwhm=f, area=100.0, rwp=1.0))
            for c, f in zip(centers, fwhms)]
        dock = SimpleNamespace(wh_result=result, fit_results=entries,
                               wh_inst_note="仪器 0.01°")
        text = wh_result_text(dock)
        self.assertIn("Williamson–Hall", text)
        self.assertIn("D (nm)", text)
        self.assertIn("30.00 nm", text)
        self.assertIn("参与\t4/4 根", text)
        self.assertIn("仪器\t仪器 0.01°", text)
        self.assertIn("2θ (°)\tFWHM (°)", text)
        for center in centers:
            self.assertIn(f"{center:.4f}", text)

    def test_delete_fit_row(self):
        self._open_panel()
        self._fit_all()
        self.w.fit_table.selectRow(0)
        from xrd_toolkit.gui.fit_view import delete_fit_rows
        self.assertEqual(delete_fit_rows(self.w), 1)
        self.assertEqual(self.w.fit_table.rowCount(), 3)
        self.assertEqual(len(self.dock.fit_results), 3)
        self.assertIn("3/3 根参与", self.w.wh_note_label.text())

    def test_curve_change_clears_fits(self):
        self._open_panel()
        self._fit_all()
        tth = np.asarray(self.dock.last_tth, dtype=float)
        y = np.asarray(self.dock.last_intensity, dtype=float) + 1.0
        from xrd_toolkit.gui.plot.drawing import _draw_1d
        _draw_1d(self.w, self.dock, tth, y)
        QApplication.processEvents()
        self.assertIsNone(self.dock.fit_results)
        self.assertEqual(self.w.fit_table.rowCount(), 0)
        self.assertFalse(self.w.peak_tabs.isTabEnabled(1))

    def test_wh_view_dialog(self):
        self._open_panel()
        self._fit_all()
        self.w.wh_view_btn.click()
        QApplication.processEvents()
        dlg = getattr(self.w, "_wh_dialog", None)
        self.assertIsNotNone(dlg)
        self.assertTrue(dlg.isVisible())
        dlg.close()
        QApplication.processEvents()

    def test_fit_requires_peaks(self):
        path = "data/fake_no_peaks.tif"
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=lambda p, g, n:
                               (np.linspace(1.0, 8.0, 500),
                                np.full(500, 10.0))):
            add_checked(self.w, [path])
            _open_view(self.w, "1D")
            _wait_until(lambda: f"1D|{path}" in self.w.plot_docks
                        and getattr(self.w.plot_docks[f"1D|{path}"],
                                    "last_tth", None) is not None)
        self.w.entrance_buttons["1D"].click()
        QApplication.processEvents()
        self.w.params["显示峰位"].setChecked(True)
        QApplication.processEvents()
        self._fit_all()
        self.assertEqual(self.w.fit_table.rowCount(), 0)


if __name__ == "__main__":
    unittest.main()
