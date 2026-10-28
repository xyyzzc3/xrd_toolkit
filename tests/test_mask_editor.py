"""独立 Mask 编辑器 GUI 测试（2026-10-27；2026-10-28 改为 Mask 2D 产物模型）。

窗口级行为：工具栏 [Mask] 能开/复用；画笔/矩形/撤销改同一张 bool mask；
[生成 Mask 2D] 在 stage cache 里写一条 manifest（原始图 + mask），文件栏
出现「Mask 后的 2D」条目；[清空] 只清编辑器，不删已生成的产物。

不测文件对话框；`.npy/.edf` 读写由 test_mask 覆盖，manifest 本身由
test_masked2d 覆盖。
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fabio  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from xrd_toolkit.gui.app import create_window  # noqa: E402
from xrd_toolkit.gui import mask_editor as gui_mask_editor  # noqa: E402
from xrd_toolkit.gui import sources as gui_sources  # noqa: E402
from xrd_toolkit.gui.plot import runners as gui_runners  # noqa: E402
from xrd_toolkit.services import masked2d, stage_cache  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _write_image(path):
    rng = np.random.default_rng(3)
    img = (rng.random((200, 200)) * 800.0 + 50.0).astype(np.float32)
    fabio.edfimage.EdfImage(data=img).write(str(path))


class TestMaskEditor(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._old_root = stage_cache.CACHE_ROOT
        stage_cache.point_root(Path(self._td.name) / "cache")
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
        stage_cache.point_root(self._old_root)
        self._td.cleanup()

    def _open(self):
        self.w.mask_btn.click()
        QApplication.processEvents()
        self.ed = self.w.mask_editor
        self.assertIsNotNone(self.ed)

    def _products(self):
        return masked2d.list_products()

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

    def test_brush_rect_undo_and_apply_creates_masked2d(self):
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
        products = self._products()
        self.assertEqual(len(products), 1)
        self.assertEqual(int(products[0]["masked_pixels"]), n_brush)
        self.assertEqual(Path(products[0]["raw_path"]),
                         self.img_path.resolve())

    def test_apply_twice_dedupes(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._fill_rect((10, 10), (20, 20), True)
        self.ed._apply_to_main()
        self.ed._apply_to_main()
        self.assertEqual(len(self._products()), 1)

    def test_apply_adds_masked2d_entry_to_file_dock(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._fill_rect((10, 10), (20, 20), True)
        self.ed._apply_to_main()
        QApplication.processEvents()
        srcs = [s for s in gui_sources.all_sources(self.w)
                if s.kind == gui_sources.MASKED2D]
        self.assertEqual(len(srcs), 1)
        self.assertTrue(Path(srcs[0].path).is_file())

    def test_empty_mask_creates_no_product(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._apply_to_main()
        self.assertEqual(self._products(), [])

    def test_clear_keeps_existing_products(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._fill_rect((10, 10), (20, 20), True)
        self.ed._apply_to_main()
        self.assertEqual(len(self._products()), 1)
        self.ed._clear()
        QApplication.processEvents()
        self.assertEqual(int(self.ed.mask.sum()), 0)
        self.assertEqual(len(self._products()), 1)

    def test_apply_to_checked_files(self):
        other = Path(self._td.name) / "frame2.edf"
        _write_image(other)
        self.w.add_files([str(self.img_path), str(other)], select=True)
        QApplication.processEvents()
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._fill_rect((10, 10), (20, 20), True)
        self.ed._apply_to_checked()
        self.assertEqual(len(self._products()), 2)

    def test_preview_draws_before_and_after(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._paint_segment((10, 10), (20, 20), True)
        self.ed._apply_and_preview()
        QApplication.processEvents()
        self.assertEqual(len(self.ed.ax_prev.lines), 2)
        self.assertEqual(len(self._products()), 1)

    def test_preview_uses_current_2theta_range(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._paint_segment((10, 10), (20, 20), True)
        with mock.patch.object(
                gui_mask_editor, "integrate_1d",
                return_value=(np.arange(3.0), np.arange(3.0))) as integ:
            self.ed._apply_and_preview()
        self.assertEqual(integ.call_count, 2)
        for call in integ.call_args_list:
            self.assertIn("tth_min_deg", call.kwargs)
            self.assertIn("tth_max_deg", call.kwargs)

    def test_auto_hot_runs_in_background_and_applies(self):
        self._open()
        self.ed.load_image(self.img_path)
        hot = np.zeros(self.ed.image.shape, dtype=bool)
        hot[5, 5] = True
        fake = (self.ed.image, hot, {"masked_components": 1,
                                    "masked_pixels": 1})
        with mock.patch.object(gui_mask_editor, "mask_hot_pixels",
                               return_value=fake):
            self.ed._auto_hot()
            self.assertFalse(self.ed.btn_hot.isEnabled())
            for _ in range(500):
                QApplication.processEvents()
                time.sleep(0.01)
                if self.ed._auto_task is None:
                    break
        QApplication.processEvents()
        self.assertTrue(self.ed.btn_hot.isEnabled())
        self.assertGreater(int(self.ed.mask.sum()), 0)

    def test_run_1d_uses_cache_for_masked2d(self):
        path = Path("/tmp/x.mask2d.npz")
        key = f"1D|{path}"
        with mock.patch.object(gui_runners.stage_cache, "load_1d",
                               return_value=None) as load, \
                mock.patch.object(gui_runners, "_spawn") as spawn:
            gui_runners._run_1d(self.w, path, key, {}, 100)
            load.assert_called_once()       # manifest 的 1D 缓存照常可用
            spawn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
