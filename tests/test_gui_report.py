"""一键分析报告的窗口级测试（2026-11-08）。

用一条合成谱走真实链路：出 1D 图 → 寻峰 → 拟合（W-H 自动算）→
收集快照 → 生成 HTML。只验证"报告读到的是当前面板的结果"，不重算
积分、不重跑拟合。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(ROOT / "tests")]

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403
except ModuleNotFoundError:  # 直接运行本文件
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403

from xrd_toolkit.gui import report_export  # noqa: E402
from xrd_toolkit.services import analysis_report as ar  # noqa: E402


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


class TestReportWindow(unittest.TestCase):
    def setUp(self):
        self._old_root = _support.stage_cache.CACHE_ROOT
        self.w = create_window()
        self.w.show()
        QApplication.processEvents()
        self.path = "data/fake_report.tif"
        self.key = f"1D|{self.path}"

    def tearDown(self):
        self.w.hide()
        self.w.close()
        QApplication.processEvents()
        _support.stage_cache.point_root(self._old_root)

    def _open_panel(self):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_synth):
            add_checked(self.w, [self.path])
            _open_view(self.w, "1D")
            ok = _wait_until(
                lambda: self.key in self.w.plot_docks
                and getattr(self.w.plot_docks[self.key],
                            "last_tth", None) is not None)
        self.assertTrue(ok, "1D 图没算完")
        self.dock = self.w.plot_docks[self.key]
        self.w.entrance_buttons["1D"].click()
        QApplication.processEvents()
        self.w.params["显示峰位"].setChecked(True)
        self.assertTrue(
            _wait_until(lambda: self.w.peak_table.rowCount() >= 4),
            "寻峰没有列出 4 根")

    def _fit_all(self):
        self.w.select_all_peaks_btn.click()
        QApplication.processEvents()
        self.w.fit_selected_btn.click()
        QApplication.processEvents()

    def test_toolbar_has_report_button(self):
        self.assertEqual(self.w.report_btn.text(), "报告")

    def test_collect_snapshot_reads_current_results(self):
        self._open_panel()
        self._fit_all()
        self.assertIsNotNone(self.dock.wh_result)
        snapshot = report_export.collect_report_snapshot(
            self.w, title="测试报告", sample="fake",
            curve_key=self.key, sections=ar._ALL_KEYS)
        curve = snapshot.curve
        self.assertIsNotNone(curve)
        self.assertEqual(curve.chain, "none")
        self.assertGreaterEqual(len(curve.peaks), 4)
        self.assertGreaterEqual(len(curve.fits), 4)
        self.assertIsNotNone(curve.wh)
        self.assertGreaterEqual(curve.wh.n_used, 3)
        # 测试窗口用内置同步辐射几何（λ = 0.1223 Å），不是 Cu Kα
        self.assertAlmostEqual(curve.wavelength, 0.1223, places=4)
        html = ar.build_report_html(snapshot)
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("测试报告", html)
        self.assertIn("fake", html)
        self.assertIn("Williamson", html)

    def test_dialog_lists_curve_and_writes_html(self):
        self._open_panel()
        dialog = report_export.ReportDialog(self.w)
        self.assertGreaterEqual(dialog.curve_combo.count(), 1)
        self.assertIn("fake_report", dialog.title_edit.text())
        self.assertTrue(dialog.generate_btn.isEnabled())
        dialog.open_after.setChecked(False)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "report.html"
            dialog.path_edit.setText(str(target))
            dialog.generate_btn.click()
            QApplication.processEvents()
            self.assertTrue(target.is_file())
            text = target.read_text(encoding="utf-8")
            self.assertIn("XRD Toolkit", text)
            self.assertIn("一维衍射曲线", text)

    def test_dialog_disables_generate_when_no_section(self):
        self._open_panel()
        dialog = report_export.ReportDialog(self.w)
        for check in dialog.section_boxes.values():
            check.setChecked(False)
        QApplication.processEvents()
        self.assertFalse(dialog.generate_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
