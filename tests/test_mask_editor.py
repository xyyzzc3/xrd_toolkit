"""独立 Mask 编辑器 GUI 测试（2026-10-27）。

只测窗口级行为：工具栏 [Mask] 能开/复用独立窗口；载入图像后画笔/矩形/
撤销改的是同一张 bool mask；[应用到当前窗口] 把 mask 挂到 window。
不测文件对话框（那是 Qt 自己的事），`.npy/.edf` 读写由 test_mask 覆盖。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fabio  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from xrd_toolkit.gui.app import create_window  # noqa: E402
from xrd_toolkit.gui.plot import runners as gui_runners  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _write_image(path):
    rng = np.random.default_rng(3)
    img = (rng.random((200, 200)) * 800.0 + 50.0).astype(np.float32)
    fabio.edfimage.EdfImage(data=img).write(str(path))


class TestMaskEditor(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.img_path = Path(self._td.name) / "frame.edf"
        _write_image(self.img_path)
        self.w = create_window()
        self.w.show()
        QApplication.processEvents()
        self.ed = None

    def tearDown(self):
        if self.ed is not None:
            self.ed.close()
        self.w.close()
        QApplication.processEvents()
        self._td.cleanup()

    def _open(self):
        self.w.mask_btn.click()
        QApplication.processEvents()
        self.ed = self.w.mask_editor
        self.assertIsNotNone(self.ed)

    def test_toolbar_button_opens_and_reuses_window(self):
        self.assertIsNone(self.w.mask_editor)
        self._open()
        self.assertTrue(self.ed.isVisible())
        self.assertNotIn("Mask", self.w.entrance_buttons)
        self.ed.close()
        QApplication.processEvents()
        self.w.mask_btn.click()
        QApplication.processEvents()
        self.assertIs(self.w.mask_editor, self.ed)

    def test_brush_rect_undo_and_apply(self):
        self._open()
        self.ed.load_image(self.img_path)
        QApplication.processEvents()
        self.assertEqual(self.ed.mask.shape, self.ed.image.shape)
        self.assertEqual(int(self.ed.mask.sum()), 0)

        self.ed._paint_segment((10, 10), (20, 20), True)
        n_brush = int(self.ed.mask.sum())
        self.assertGreater(n_brush, 0)

        self.ed._push_undo()
        self.ed._fill_rect((50, 50), (60, 60), True)
        self.assertGreater(int(self.ed.mask.sum()), n_brush)
        self.ed._undo_last()
        self.assertEqual(int(self.ed.mask.sum()), n_brush)

        self.ed._apply_to_main()
        self.assertIsNotNone(self.w.current_mask)
        self.assertEqual(int(self.w.current_mask.sum()), n_brush)

    def test_preview_draws_before_and_after(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._paint_segment((10, 10), (20, 20), True)
        self.ed._apply_and_preview()
        QApplication.processEvents()
        self.assertEqual(len(self.ed.ax_prev.lines), 2)

    def test_run_1d_skips_cache_when_mask_active(self):
        path = Path("/tmp/mask_editor_x.tif")
        key = f"1D|{path}"
        with mock.patch.object(gui_runners.stage_cache, "load_1d") as load, \
                mock.patch.object(gui_runners, "_spawn") as spawn:
            self.w.current_mask = np.zeros((4, 4), bool)
            gui_runners._run_1d(self.w, path, key, {}, 100)
            load.assert_not_called()
            spawn.assert_called_once()
        with mock.patch.object(gui_runners.stage_cache, "load_1d",
                               return_value=None) as load, \
                mock.patch.object(gui_runners, "_spawn") as spawn:
            self.w.current_mask = None
            gui_runners._run_1d(self.w, path, key, {}, 100)
            load.assert_called_once()
            spawn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
