"""1D 页峰形拟合的窗口级测试（2026-10-30）。

用一条合成谱（4 根高斯峰）走真实链路：出 1D 图 → 勾寻峰 → [拟合全部]
→ 拟合结果表 / 图上叠加曲线 / 改参数清空 / 只拟合选中。
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

    def test_fit_all_fills_table_and_draws_selected_overlay(self):
        self._open_panel()
        self.w.fit_all_btn.click()
        QApplication.processEvents()
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
        self.w.fit_all_btn.click()
        QApplication.processEvents()
        self.assertIsNotNone(self.dock.wh_result)
        self.assertEqual(len(self.dock.wh_result.beta_sample_deg), 4)
        self.assertIn("D =", self.w.wh_d_label.text())
        self.assertIn("4 根峰", self.w.wh_note_label.text())

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

    def test_overlay_all_draws_every_fit(self):
        self._open_panel()
        self.w.fit_all_btn.click()
        QApplication.processEvents()
        self.w.params["拟合全部叠加"].setChecked(True)
        QApplication.processEvents()
        self.assertEqual(len(self._fit_lines()), 4)

    def test_fit_selected_merges(self):
        self._open_panel()
        self.w.fit_all_btn.click()
        QApplication.processEvents()
        self.w.peak_table.selectRow(1)
        self.w.fit_selected_btn.click()
        QApplication.processEvents()
        self.assertEqual(self.w.fit_table.rowCount(), 4)   # 其余保留
        self.assertEqual(len(self.dock.fit_results), 4)

    def test_shape_change_clears_results(self):
        self._open_panel()
        self.w.fit_all_btn.click()
        QApplication.processEvents()
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
        self.w.fit_all_btn.click()
        QApplication.processEvents()
        self.assertEqual(self.w.fit_table.rowCount(), 0)


if __name__ == "__main__":
    unittest.main()
