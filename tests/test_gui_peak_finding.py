"""GUI 寻峰（1D 页）：标记、峰表、双击放大、参数按面板存（2026-10-23）。"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QHeaderView, QPushButton,
                               QSizePolicy)

from xrd_toolkit.services import stage_cache
from xrd_toolkit.gui import watchdog as gui_watchdog
from xrd_toolkit.gui import panel_state
from xrd_toolkit.gui import peak_view
from xrd_toolkit.gui.plot.interaction import _hover_label, _hover_motion
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


def _curve_close():
    """两个挨得很近的峰（1.500 / 1.504）+ 一个远处的峰（2.200）。"""
    x = np.arange(1.0, 3.0, 0.002)
    y = 100.0
    for center, height, fwhm in ((1.500, 300.0, 0.006),
                                 (1.512, 300.0, 0.006),
                                 (2.200, 200.0, 0.030)):
        y += height * np.exp(
            -4.0 * np.log(2.0) * ((x - center) / fwhm) ** 2)
    y += np.random.default_rng(1).normal(0.0, 0.5, x.size)
    return x, y


def _open_1d(w, path="data/fake_peak.tif", curve=None):
    with mock.patch.object(gui_plot_spawn, "_compute_integration",
                           return_value=(curve or _curve)()):
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


def _labels(ax):
    return [txt for txt in ax.texts
            if str(txt.get_gid() or "").startswith("bg:peak_label")]


def _label_boxes(ax):
    renderer = ax.figure.canvas.get_renderer()
    return [txt.get_window_extent(renderer) for txt in _labels(ax)]


class TestGuiPeakFinding(unittest.TestCase):
    def test_find_peaks_draws_markers_and_table(self):
        w = create_window()
        try:
            dock = _open_1d(w)
            ax = panel_state._content(dock).axes_1d
            self.assertTrue(w.peak_box.isHidden())
            self.assertEqual(len(_markers(ax)), 0)

            w.params["显示峰位"].setChecked(True)
            QApplication.processEvents()
            self.assertTrue(w.params["显示峰位"].isChecked())
            peaks = dock.detected_peaks
            self.assertIsNotNone(peaks)
            self.assertEqual(len(peaks), 2)
            self.assertAlmostEqual(peaks[0].center, 1.5, delta=2e-3)
            self.assertAlmostEqual(peaks[1].center, 2.2, delta=2e-3)
            self.assertEqual(len(_labels(ax)), 2)
            self.assertEqual(len(_markers(ax)), 0,
                             "有标签的峰不再画橙线")
            self.assertFalse(w.peak_box.isHidden())
            self.assertEqual(w.peak_table.rowCount(), 2)
            self.assertEqual(w.peak_table.columnCount(), 4)
            headers = [w.peak_table.horizontalHeaderItem(i).text()
                       for i in range(4)]
            self.assertEqual(headers, ["2θ (°)", "d (Å)", "FWHM (°)", "面积"])
            self.assertIn("峰高", w.peak_table.item(0, 0).toolTip())
            self.assertNotEqual(w.peak_table.item(0, 1).text(), "—")
            # 数值排序：默认 2θ 升序，点表头降序后第一行是 2.2°
            self.assertTrue(w.peak_table.item(0, 0).text().startswith("1.5"))
            w.peak_table.sortItems(0, Qt.DescendingOrder)
            self.assertTrue(w.peak_table.item(0, 0).text().startswith("2.2"))
            self.assertAlmostEqual(
                w.peak_table.item(0, 0).data(Qt.UserRole)[0], 2.2,
                delta=2e-3)
            tsv = peak_view.peak_table_tsv(w)
            self.assertEqual(len(tsv.splitlines()), 3)
            self.assertIn("面积", tsv.splitlines()[0])
            self.assertEqual(peak_view.copy_peak_table(w, False), 2)
            self.assertIn("面积", QApplication.clipboard().text())
            w.peak_table.selectRow(0)
            self.assertEqual(peak_view.copy_peak_table(w, True), 1)
            self.assertEqual(w.peak_caption.text(), "峰表 · 2 根")
            self.assertIn("双击", w.peak_table.toolTip())
            self.assertLessEqual(w.peak_table.maximumWidth(), 320,
                                 "全屏时表格不许被拉宽")
            self.assertLessEqual(w.peak_box.maximumWidth(), 320)
            self.assertEqual(w.peak_table.sizePolicy().verticalPolicy(),
                             QSizePolicy.Expanding, "峰表要占满剩余高度")
            self.assertEqual(w.peak_box.sizePolicy().verticalPolicy(),
                             QSizePolicy.Expanding)
            for col in range(4):
                self.assertEqual(
                    w.peak_table.horizontalHeader().sectionResizeMode(col),
                    QHeaderView.Stretch, "三列应当平均分宽度")
            self.assertIn("寻峰：2 根", w.log_text.toPlainText())
        finally:
            w.close()

    def test_uncheck_hides_markers_and_table(self):
        w = create_window()
        try:
            dock = _open_1d(w)
            ax = panel_state._content(dock).axes_1d
            w.params["显示峰位"].setChecked(True)
            QApplication.processEvents()
            self.assertEqual(len(_labels(ax)), 2)

            w.params["显示峰位"].setChecked(False)
            QApplication.processEvents()
            self.assertEqual(len(_labels(ax)), 0)
            self.assertEqual(len(_markers(ax)), 0)
            self.assertTrue(w.peak_box.isHidden())
        finally:
            w.close()

    def test_double_click_zooms_to_peak(self):
        w = create_window()
        try:
            dock = _open_1d(w)
            w.params["显示峰位"].setChecked(True)
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
            w.params["显示峰位"].setChecked(True)
            QApplication.processEvents()
            self.assertIn("先点开一张 1D 图", w.log_text.toPlainText())
        finally:
            w.close()


    def test_peak_spin_steps_are_fine(self):
        """滚轮/箭头步长要细：显著度 0.1、峰距/FWHM 0.001（2026-10-24）。"""
        w = create_window()
        try:
            self.assertAlmostEqual(
                w.params["寻峰显著度"].singleStep(), 0.1)
            self.assertAlmostEqual(
                w.params["寻峰最小峰距 (°)"].singleStep(), 0.001)
            self.assertAlmostEqual(
                w.params["寻峰最小 FWHM (°)"].singleStep(), 0.001)
            self.assertIsNone(w.findChild(QPushButton, "find_peaks_btn"),
                              "寻峰按钮已并入「寻峰并显示峰位」复选框")
        finally:
            w.close()

    def test_labels_avoid_overlap(self):
        w = create_window()
        try:
            dock = _open_1d(w, curve=_curve_close)
            ax = panel_state._content(dock).axes_1d
            w.params["显示峰位"].setChecked(True)
            QApplication.processEvents()
            boxes = _label_boxes(ax)
            self.assertGreaterEqual(len(boxes), 1)
            # 密集的两根峰只标一根：默认模式不再纵向错层（没有长引线）
            self.assertLess(len(boxes), len(dock.detected_peaks))
            self.assertEqual(len(_markers(ax)),
                             len(dock.detected_peaks) - len(boxes),
                             "只有没标签的峰留短刻度")
            for bb in boxes:
                self.assertGreaterEqual(bb.x0, ax.bbox.x0 - 1)
                self.assertLessEqual(bb.x1, ax.bbox.x1 + 1)
                self.assertGreaterEqual(bb.y0, ax.bbox.y0 - 1)
                self.assertLessEqual(bb.y1, ax.bbox.y1 + 1)
            for i in range(len(boxes)):
                for j in range(i + 1, len(boxes)):
                    a, b = boxes[i], boxes[j]
                    self.assertFalse(
                        a.x1 > b.x0 and b.x1 > a.x0
                        and a.y1 > b.y0 and b.y1 > a.y0,
                        "默认模式标签不许重叠")
            # 标签也不许压在曲线上（用全部点，不抽稀）
            renderer = ax.figure.canvas.get_renderer()
            for line in ax.lines:
                x = np.asarray(line.get_xdata(), dtype=float)
                y = np.asarray(line.get_ydata(), dtype=float)
                if x.size < 5 or x.size != y.size:
                    continue
                disp = ax.transData.transform(np.column_stack([x, y]))
                for bb in boxes:
                    inside = ((disp[:, 0] >= bb.x0 - 2)
                              & (disp[:, 0] <= bb.x1 + 2)
                              & (disp[:, 1] >= bb.y0 - 2)
                              & (disp[:, 1] <= bb.y1 + 2))
                    self.assertFalse(np.any(inside),
                                     "标签压到曲线上了")

            near_peak = min(dock.detected_peaks,
                            key=lambda p: abs(p.center - 1.512))
            text = _hover_label(dock, "", near_peak.center, 0.0,
                                near_peak)
            self.assertIn("峰 2θ", text)
            self.assertIn("FWHM", text)
            self.assertIn("峰高", text)
            self.assertIn(f"{near_peak.center:.4f}", text)

        finally:
            w.close()


    def test_tall_narrow_peak_stays_inside_and_labelled(self):
        """99.9% 分位以下的矮峰不能把最高峰挤出框（2026-10-24 实测）。"""
        def spike():
            x = np.arange(1.0, 3.0, 0.002)
            y = 100.0 + 5000.0 * np.exp(
                -4.0 * np.log(2.0) * ((x - 1.5) / 0.002) ** 2)
            y += 200.0 * np.exp(
                -4.0 * np.log(2.0) * ((x - 2.2) / 0.03) ** 2)
            return x, y

        w = create_window()
        try:
            dock = _open_1d(w, curve=spike)
            ax = panel_state._content(dock).axes_1d
            w.params["显示峰位"].setChecked(True)
            QApplication.processEvents()
            y = np.asarray(dock.last_intensity, dtype=float)
            self.assertGreaterEqual(ax.get_ylim()[1], float(y.max()))
            centres = [round(p.center, 3) for p in dock.detected_peaks]
            self.assertIn(1.5, centres)
            labels = [txt.get_text() for txt in _labels(ax)]
            self.assertIn("1.500", labels)
        finally:
            w.close()


    def test_hover_snaps_to_peak_apex(self):
        """光标靠近峰顶：读数直接报这根峰，不混鼠标点的 2θ。"""
        w = create_window()
        try:
            dock = _open_1d(w, curve=_curve_close)
            ax = panel_state._content(dock).axes_1d
            w.params["显示峰位"].setChecked(True)
            QApplication.processEvents()
            peak = dock.detected_peaks[0]
            i = int(np.searchsorted(dock.last_tth, peak.center))
            px, py = ax.transData.transform(
                (peak.center, dock.last_intensity[i]))
            event = SimpleNamespace(
                inaxes=ax, xdata=peak.center,
                ydata=dock.last_intensity[i], x=px, y=py)
            _hover_motion(w, w.focus_panel, event)
            text = w.coord_label.text()
            self.assertIn("峰 2θ", text)
            self.assertIn(f"{peak.center:.4f}", text)
            self.assertIn("FWHM", text)
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
