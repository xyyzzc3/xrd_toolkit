"""GUI 寻峰（1D 页）：标记、峰表、双击放大、参数按面板存（2026-10-23）。"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
from PySide6.QtWidgets import QApplication, QPushButton

from xrd_toolkit.services import stage_cache
from xrd_toolkit.gui import watchdog as gui_watchdog
from xrd_toolkit.gui import panel_state
from xrd_toolkit.gui import peak_view
from xrd_toolkit.gui.app import create_window
from xrd_toolkit.gui.plot import spawn as gui_plot_spawn

_app = QApplication.instance() or QApplication([])
_CACHE_TMP = tempfile.mkdtemp(prefix="xrd_peak_cache_")


def setUpModule():
    stage_cache.CACHE_ROOT = Path(_CACHE_TMP)
    gui_watchdog.OUT_DIR = Path(tempfile.mkdtemp(prefix="xrd_peak_hang_"))


def _wait_until(predicate, timeout_ms=5000):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _curve():
    """两个孤立高斯峰：1.500°（FWHM 0.020）与 2.200°（0.030）。"""
    x = np.arange(1.0, 3.0, 0.002)
    y = 100.0 + 400.0 * np.exp(
        -4.0 * np.log(2.0) * ((x - 1.5) / 0.02) ** 2)
    y += 200.0 * np.exp(-4.0 * np.log(2.0) * ((x - 2.2) / 0.03) ** 2)
    y += np.random.default_rng(0).normal(0.0, 0.5, x.size)
    return x, y


def _open_1d(w, path="data/fake_peak.tif"):
    with mock.patch.object(gui_plot_spawn, "_compute_integration",
                           return_value=_curve()):
        w.add_files([path], select=True)
        w.view_buttons["1D"].click()
        ok = _wait_until(
            lambda: f"1D|{path}" in w.plot_docks
            and getattr(w.plot_docks[f"1D|{path}"], "last_tth", None)
            is not None)
    assert ok, "1D 面板没算完"
    QApplication.processEvents()
    return w.plot_docks[f"1D|{path}"]


def _markers(ax):
    return [ln for ln in ax.lines
            if str(ln.get_gid() or "").startswith("bg:peak_marker")]


class TestGuiPeakFinding(unittest.TestCase):
    def test_find_peaks_draws_markers_and_table(self):
        w = create_window()
        try:
            dock = _open_1d(w)
            ax = panel_state._content(dock).axes_1d
            self.assertTrue(w.peak_box.isHidden())
            self.assertEqual(len(_markers(ax)), 0)

            w.findChild(QPushButton, "find_peaks_btn").click()
            QApplication.processEvents()
            self.assertTrue(w.params["显示峰位"].isChecked())
            peaks = dock.detected_peaks
            self.assertIsNotNone(peaks)
            self.assertEqual(len(peaks), 2)
            self.assertAlmostEqual(peaks[0].center, 1.5, delta=2e-3)
            self.assertAlmostEqual(peaks[1].center, 2.2, delta=2e-3)
            self.assertEqual(len(_markers(ax)), 2)
            self.assertFalse(w.peak_box.isHidden())
            self.assertEqual(w.peak_table.rowCount(), 2)
            self.assertIn("寻峰：2 根", w.log_text.toPlainText())
        finally:
            w.close()

    def test_uncheck_hides_markers_and_table(self):
        w = create_window()
        try:
            dock = _open_1d(w)
            ax = panel_state._content(dock).axes_1d
            w.findChild(QPushButton, "find_peaks_btn").click()
            QApplication.processEvents()
            self.assertEqual(len(_markers(ax)), 2)

            w.params["显示峰位"].setChecked(False)
            QApplication.processEvents()
            self.assertEqual(len(_markers(ax)), 0)
            self.assertTrue(w.peak_box.isHidden())
        finally:
            w.close()

    def test_double_click_zooms_to_peak(self):
        w = create_window()
        try:
            dock = _open_1d(w)
            w.findChild(QPushButton, "find_peaks_btn").click()
            QApplication.processEvents()
            peak_view.zoom_to_peak(w, 0)
            QApplication.processEvents()
            snap = dock.params_snapshot
            center = dock.detected_peaks[0].center
            self.assertLess(snap["视图 2θ 下限 (°)"], center)
            self.assertGreater(snap["视图 2θ 上限 (°)"], center)
            lo, hi = panel_state._content(dock).axes_1d.get_xlim()
            self.assertLess(lo, center)
            self.assertGreater(hi, center)
        finally:
            w.close()

    def test_no_focus_logs_guidance(self):
        w = create_window()
        try:
            w.findChild(QPushButton, "find_peaks_btn").click()
            QApplication.processEvents()
            self.assertIn("先点开一张 1D 图", w.log_text.toPlainText())
        finally:
            w.close()

    def test_peak_settings_are_display_params(self):
        # 每张图各记各的：快照里必须有这几个键，默认关
        w = create_window()
        try:
            dock = _open_1d(w)
            snap = dock.params_snapshot
            self.assertFalse(snap["显示峰位"])
            self.assertAlmostEqual(snap["寻峰显著度"], 5.0)
            self.assertAlmostEqual(snap["寻峰最小峰距 (°)"], 0.008)
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
