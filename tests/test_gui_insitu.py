"""原位工作台的窗口级测试（2026-10-30）。

真拟合每帧要数秒，这里只测 GUI 的编排：用假 batch 逐帧返回结果，
验证表/趋势图/CSV/摘要都按帧推进；另测进出与五种工作台互斥。
引擎本身的纯函数测试在 `tests/test_fit_insitu.py`。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(ROOT / "tests")]

from PySide6.QtWidgets import QApplication  # noqa: E402
from unittest import mock  # noqa: E402

try:
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403
except ModuleNotFoundError:  # 直接运行本文件
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403

import xrd_toolkit.gui.insitu_workbench as insitu_gui  # noqa: E402


def setUpModule():
    _support.setUpModule()


def _fake_row(i):
    return {
        "frame": i, "file": f"frame_{i:03d}.tif", "status": "ok",
        "usable": True, "rwp": 2.0 + i * 0.1, "rp": 1.5,
        "f_rich": 0.9 - i * 0.05, "f_poor": 0.1 + i * 0.05,
        "f_rich_esd": 0.01,
        "rich_a": 10.35, "rich_b": 6.02, "rich_c": 4.70,
        "rich_a_esd": 0.001, "rich_b_esd": 0.001, "rich_c_esd": 0.001,
        "poor_a": 10.17, "poor_b": 5.82, "poor_c": 4.78,
        "poor_a_esd": 0.01, "poor_b_esd": 0.01, "poor_c_esd": 0.01,
        "al_a": 4.061, "al_ref": 25.0,
        "t_load_s": 0.1, "t_integrate_s": 0.2, "t_bg_s": 0.0,
        "t_fit_s": 1.0, "t_total_s": 1.3,
    }


def _fake_result(i):
    """够 draw_fit / 模型无关检查用的鸭子类型 FitResult。"""
    tth = np.linspace(1.0, 8.0, 500)
    base = 100.0 + 5.0 * (tth - 1.0)
    y = base.copy()
    rich_amp = 220.0 * (1.0 - 0.15 * i)
    poor_amp = 40.0 + 30.0 * i
    for amp, center, width in ((rich_amp, 1.355, 0.014),
                               (poor_amp, 1.430, 0.014),
                               (300.0, 3.0, 0.02)):
        y += amp * np.exp(-0.5 * ((tth - center) / width) ** 2)
    y_calc = y * 0.98
    return SimpleNamespace(
        tth=tth, y_obs=y, y_calc=y_calc, background=base,
        residual=y - y_calc, rwp=2.0 + 0.1 * i, rp=1.5,
        peak_tth=np.array([1.355, 1.430, 3.0]),
        extra_peak_tth={"LMFP_poor": np.array([1.430, 3.2]),
                        "Al": np.array([2.0, 4.0])},
        phase_label="LMFP_rich",
        report=lambda: f"frame {i} report",
    )


class _FakeBatch:
    def __init__(self):
        self.rows = []
        self.fitted = []
        self.mask = None

    def fit_frame(self, path):
        i = len(self.rows) + 1
        row = _fake_row(i)
        row["file"] = Path(path).name
        self.rows.append(row)
        self.fitted.append((i, Path(path), _fake_result(i),
                            row["f_rich"], row["status"]))
        return row


class TestInSituWorkbench(unittest.TestCase):
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

    def test_use_checked_files_as_frames(self):
        """文件栏勾选的原始图可以当帧序列（2026-11-11）。"""
        files = _tmp_files(3, prefix="insitu_")
        add_checked(self.w, [str(p) for p in files])
        self.w.insitu_btn.click()
        QApplication.processEvents()
        editor = self.w.insitu_editor
        editor.checked_btn.click()
        QApplication.processEvents()
        self.assertTrue(editor._use_checked)
        self.assertEqual([p.name for p in editor.frames],
                         sorted(p.name for p in files))
        self.assertIn("文件栏勾选", editor.data_label.text())
        # 换回文件夹会把勾选来源清掉（不混着用）
        with mock.patch.object(
                insitu_gui.QFileDialog, "getExistingDirectory",
                return_value=str(files[0].parent)):
            editor._choose_data()
        QApplication.processEvents()
        self.assertFalse(editor._use_checked)

    def test_enter_exit_and_mutual_exclusion(self):
        self.w.insitu_btn.click()
        QApplication.processEvents()
        self.assertTrue(self.w._insitu_active)
        self.assertEqual(self.w.insitu_btn.text(), "退出原位")
        self.assertEqual(self.w.mode_label.text(), "原位模式")
        self.assertTrue(self.w.insitu_dock.isVisible())
        self.assertFalse(self.w.param_dock.isVisible())
        self.assertFalse(self.w.log_dock.isVisible())
        self.assertTrue(self.w._bottom_strip.isHidden(),
                        "工作台里底部横带应隐藏")

        self.w.refine_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.w._insitu_active, "精修应把原位退掉")
        self.assertTrue(self.w._refine_active)
        self.w.refine_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.w._bottom_strip.isHidden(),
                         "退出工作台应恢复底部横带")

        self.w.insitu_btn.click()
        QApplication.processEvents()
        self.w.phase_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.w._insitu_active, "物相应把原位退掉")
        self.assertTrue(self.w._phase_active)
        self.w.phase_btn.click()
        QApplication.processEvents()

        self.w.insitu_btn.click()
        QApplication.processEvents()
        self.w.mask_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.w._insitu_active, "掩膜应把原位退掉")
        self.assertTrue(self.w._mask_active)
        self.w.mask_btn.click()
        QApplication.processEvents()

        self.w.insitu_btn.click()
        QApplication.processEvents()
        self.w.calib_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.w._insitu_active, "校准应把原位退掉")
        self.assertTrue(self.w.calib_btn.isChecked())
        self.w.calib_btn.click()
        QApplication.processEvents()

        self.w.insitu_btn.click()
        QApplication.processEvents()
        self.w.entrance_buttons["1D"].click()
        QApplication.processEvents()
        self.assertFalse(self.w._insitu_active, "点分析入口应退出原位")

    def test_run_chains_frames_and_writes_csv(self):
        self.w.insitu_btn.click()
        QApplication.processEvents()
        editor = self.w.insitu_editor
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            data = folder / "data"
            data.mkdir()
            for i in (1, 2, 3):
                (data / f"frame_{i:03d}.tif").write_bytes(b"")
            editor.data_dir = data
            editor.outdir = folder / "out"
            editor.limit.setValue(3)
            editor._refresh_data_label()
            self.assertEqual(len(editor.frames), 3)

            with mock.patch.object(insitu_gui, "_prepare_worker",
                                   lambda *a, **k: _FakeBatch()):
                editor.start_btn.click()
                ok = _wait_until(
                    lambda: editor.start_btn.isEnabled()
                    and editor.table.rowCount() == 3, timeout_ms=10000)
            self.assertTrue(ok, "批量没有在 10 秒内完成")
            self.assertIn("完成", editor.status.text())
            self.assertEqual(len(editor.batch.rows), 3)
            self.assertEqual(editor._batch_meta["data_dir"], str(data))
            csv_text = (folder / "out" / "insitu_fit.csv").read_text(
                encoding="utf-8")
            self.assertEqual(len(csv_text.splitlines()), 4)
            self.assertTrue(
                (folder / "out" / "fit_summary.txt").is_file())
            self.assertGreaterEqual(len(editor.fig.axes), 2)

    def _run_fake_batch(self, editor, folder):
        data = folder / "data"
        data.mkdir()
        for i in (1, 2, 3):
            (data / f"frame_{i:03d}.tif").write_bytes(b"")
        editor.data_dir = data
        editor.outdir = folder / "out"
        editor.limit.setValue(3)
        editor._refresh_data_label()
        with mock.patch.object(insitu_gui, "_prepare_worker",
                               lambda *a, **k: _FakeBatch()):
            editor.start_btn.click()
            ok = _wait_until(
                lambda: editor.start_btn.isEnabled()
                and editor.table.rowCount() == 3, timeout_ms=10000)
        self.assertTrue(ok, "批量没有在 10 秒内完成")

    def test_four_views_and_single_frame_fit(self):
        self.w.insitu_btn.click()
        QApplication.processEvents()
        editor = self.w.insitu_editor
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            self._run_fake_batch(editor, folder)
            self.assertEqual(editor.view_tabs.count(), 4)
            self.assertEqual(len(editor.fig.axes), 4)

            editor.view_tabs.setCurrentIndex(1)
            QApplication.processEvents()
            self.assertEqual(len(editor.summary_fig.axes), 4)

            editor.view_tabs.setCurrentIndex(3)
            QApplication.processEvents()
            # 3 个 panel + twinx 的右轴
            self.assertEqual(len(editor.mi_fig.axes), 4)

            editor.table.cellDoubleClicked.emit(0, 0)
            QApplication.processEvents()
            self.assertIs(editor.view_tabs.currentWidget(),
                          editor.fit_canvas)
            self.assertEqual(len(editor.fit_fig.axes), 3)
            self.assertTrue(editor.copy_report_btn.isEnabled())
            editor._copy_fit_report()
            self.assertIn("frame 1 report",
                          QApplication.clipboard().text())

            out = folder / "fit.png"
            # 2026-11-11 起保存走与面板共用的选项弹窗（plot_export 里
            # 的 QFileDialog 才是真正弹框的那个）
            from xrd_toolkit.gui import plot_export as gui_plot_export
            with mock.patch.object(gui_plot_export, "_ask_save_options",
                                   return_value={"dpi": 300, "fmt": "png"}), \
                 mock.patch.object(gui_plot_export.QFileDialog,
                                   "getSaveFileName",
                                   return_value=(str(out), "PNG (*.png)")):
                editor.save_btn.click()
            self.assertTrue(out.is_file())
            self.assertGreater(out.stat().st_size, 1000)

    def test_top_row_labels_are_not_covered(self):
        """长数据路径不能把右上角的 mask 灰字压到按钮底下（2026-11-03）。"""
        self.w.resize(1600, 1000)
        self.w.show()
        self.w.insitu_btn.click()
        QApplication.processEvents()
        editor = self.w.insitu_editor
        editor.data_label.setText(
            "/Users/bryan/Data/XRD/lmfpdata（81 帧）")
        editor.mask_label.setText("掩膜：lmfp1_lab6_mask.npy")
        QApplication.processEvents()
        self.assertGreaterEqual(editor.mask_label.width(), 150)
        self.assertLessEqual(editor.data_label.width(), 420)
        self.assertFalse(
            editor.mask_label.geometry().intersects(
                editor.mask_btn.geometry()),
            "掩膜灰字不能被『选掩膜…』按钮遮住")
        self.assertFalse(
            editor.mask_label.geometry().intersects(
                editor.mask_clear_btn.geometry()),
            "mask 灰字不能被『清除』按钮遮住")

    def test_frame_without_fit_shows_message(self):
        self.w.insitu_btn.click()
        QApplication.processEvents()
        editor = self.w.insitu_editor
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            self._run_fake_batch(editor, folder)
            editor.batch.fitted.clear()
            editor.table.cellDoubleClicked.emit(0, 0)
            QApplication.processEvents()
            self.assertIn("没有拟合结果", editor.status.text())
            self.assertFalse(editor.copy_report_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
