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


class _FakeBatch:
    def __init__(self):
        self.rows = []
        self.fitted = []
        self.mask = None

    def fit_frame(self, path):
        row = _fake_row(len(self.rows) + 1)
        row["file"] = Path(path).name
        self.rows.append(row)
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

    def test_enter_exit_and_mutual_exclusion(self):
        self.w.insitu_btn.click()
        QApplication.processEvents()
        self.assertTrue(self.w._insitu_active)
        self.assertEqual(self.w.insitu_btn.text(), "退出原位")
        self.assertEqual(self.w.mode_label.text(), "原位模式")
        self.assertTrue(self.w.insitu_dock.isVisible())
        self.assertFalse(self.w.param_dock.isVisible())
        self.assertFalse(self.w.log_dock.isVisible())

        self.w.refine_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.w._insitu_active, "精修应把原位退掉")
        self.assertTrue(self.w._refine_active)
        self.w.refine_btn.click()
        QApplication.processEvents()

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
        self.assertFalse(self.w._insitu_active, "Mask 应把原位退掉")
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
            csv_text = (folder / "out" / "insitu_fit.csv").read_text(
                encoding="utf-8")
            self.assertEqual(len(csv_text.splitlines()), 4)
            self.assertTrue(
                (folder / "out" / "fit_summary.txt").is_file())
            self.assertGreaterEqual(len(editor.fig.axes), 2)


if __name__ == "__main__":
    unittest.main()
