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
from matplotlib.backend_bases import MouseEvent  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from xrd_toolkit.gui.app import create_window  # noqa: E402
from xrd_toolkit.gui import mask_editor as gui_mask_editor  # noqa: E402
from xrd_toolkit.gui.image_utils import downsample_max  # noqa: E402
from xrd_toolkit.gui import sources as gui_sources  # noqa: E402
from xrd_toolkit.gui.plot import runners as gui_runners  # noqa: E402
from xrd_toolkit.services import masked2d, stage_cache  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _write_image(path):
    rng = np.random.default_rng(3)
    img = (rng.random((200, 200)) * 800.0 + 50.0).astype(np.float32)
    fabio.edfimage.EdfImage(data=img).write(str(path))


def _fire(canvas, name, x, y, **kw):
    """发一个真鼠标事件（与 scripts/check_gui.py 同一条路径）。"""
    canvas.callbacks.process(name, MouseEvent(name, canvas, x, y, **kw))


def _wait_task(editor):
    for _ in range(500):
        QApplication.processEvents()
        time.sleep(0.01)
        if editor._auto_task is None:
            break
    QApplication.processEvents()


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

    def test_save_then_regenerate_updates_label(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._fill_rect((10, 10), (20, 20), True)
        saved = Path(self._td.name) / "my_mask.npy"
        with mock.patch.object(gui_mask_editor.QFileDialog,
                               "getSaveFileName",
                               return_value=(str(saved), "")):
            self.ed._save_mask()
        self.ed._apply_to_main()
        self.assertEqual(self._products()[0]["label"], "my_mask.npy")

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

    def test_downsample_helpers_keep_hot_pixels(self):
        img = np.zeros((5, 6), dtype=float)
        img[3, 4] = 9.0
        small = downsample_max(img, 2)
        self.assertEqual(small.shape, (3, 3))       # 边缘补齐到 ds 的倍数
        self.assertEqual(float(small.max()), 9.0)   # 孤立亮斑不被平均掉

        mask = np.zeros((5, 6), dtype=bool)
        mask[3, 4] = True
        small_mask = downsample_max(mask, 2)
        self.assertTrue(small_mask[1, 2])            # 块里有任一像素被遮就算
        self.assertEqual(int(small_mask.sum()), 1)

    def test_overlay_extent_stays_in_image_coordinates(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._fill_rect((10, 10), (20, 20), True)
        h, w = self.ed.image.shape
        for ds in (1, 2, 3):
            rgba, ext = self.ed._overlay_rgba(ds)
            self.assertAlmostEqual(ext[0], -0.5)
            self.assertAlmostEqual(ext[2], -0.5)
            self.assertGreaterEqual(ext[1], w - 0.5)
            self.assertLess(ext[1], w - 0.5 + ds)   # 最多多补一个块
            self.assertEqual(rgba.shape[0], int(np.ceil(h / ds)))

    def test_scroll_zoom_and_home_reset(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed.canvas.draw()
        QApplication.processEvents()
        span0 = self.ed.ax_img.get_xlim()[1] - self.ed.ax_img.get_xlim()[0]
        px, py = self.ed.ax_img.transData.transform((100.0, 100.0))
        _fire(self.ed.canvas, "scroll_event", px, py, button="up", step=1)
        xlim = self.ed.ax_img.get_xlim()
        span1 = xlim[1] - xlim[0]
        self.assertLess(span1, span0)
        self.assertGreaterEqual(span1, gui_mask_editor.MIN_VIEW_SPAN)
        self.ed.btn_home.click()
        xlim = self.ed.ax_img.get_xlim()
        self.assertAlmostEqual(xlim[1] - xlim[0], 200.0)

    def test_zoom_keeps_square_pixels(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed.canvas.draw()
        QApplication.processEvents()
        px, py = self.ed.ax_img.transData.transform((30.0, 170.0))
        for _ in range(6):
            _fire(self.ed.canvas, "scroll_event", px, py, button="up", step=1)
        x0, x1 = self.ed.ax_img.get_xlim()
        y0, y1 = self.ed.ax_img.get_ylim()
        self.assertAlmostEqual((x1 - x0), (y1 - y0))

    def test_middle_drag_pans_view(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed.canvas.draw()
        QApplication.processEvents()
        self.ed._zoom_about(100.0, 100.0, 0.25)     # 先放大，才有地方平移
        x0, x1 = self.ed.ax_img.get_xlim()
        y0, y1 = self.ed.ax_img.get_ylim()
        px, py = self.ed.ax_img.transData.transform(
            ((x0 + x1) / 2.0, (y0 + y1) / 2.0))
        _fire(self.ed.canvas, "button_press_event", px, py, button=2)
        _fire(self.ed.canvas, "motion_notify_event", px - 40.0, py,
              buttons=frozenset({2}))
        _fire(self.ed.canvas, "button_release_event", px - 40.0, py, button=2)
        x0b, x1b = self.ed.ax_img.get_xlim()
        self.assertAlmostEqual(x1b - x0b, x1 - x0)  # 跨度不变 = 只是平移
        self.assertGreater(x0b, x0)                 # 拖左 → 内容往右走

    def test_overlay_colors_by_source(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._fill_rect((10, 10), (20, 20), True)     # 手动 = 红
        hot = np.zeros(self.ed.image.shape, dtype=bool)
        hot[60:70, 60:70] = True
        fake = (self.ed.image, hot, {"masked_components": 1,
                                     "masked_pixels": int(hot.sum())})
        with mock.patch.object(gui_mask_editor, "mask_hot_pixels",
                               return_value=fake):
            self.ed._auto_hot()
            _wait_task(self.ed)

        rgba, _ext = self.ed._overlay_rgba(1)
        self.assertEqual(tuple(rgba[15, 15, :3]), gui_mask_editor.COLOR_MANUAL)
        self.assertEqual(tuple(rgba[65, 65, :3]), gui_mask_editor.COLOR_HOT)
        self.assertEqual(int(rgba[5, 5, 3]), 0)          # 没遮的地方透明

        # 撤销只回退自动坏点那一步；手动方块本来就该留着
        self.ed._undo_last()
        rgba, _ext = self.ed._overlay_rgba(1)
        self.assertEqual(tuple(rgba[15, 15, :3]), gui_mask_editor.COLOR_MANUAL)
        self.assertEqual(int(rgba[65, 65, 3]), 0)

    def test_generate_respects_preview_checkbox(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._fill_rect((10, 10), (20, 20), True)
        self.ed.chk_preview.setChecked(False)
        self.ed.btn_apply.click()
        QApplication.processEvents()
        self.assertEqual(len(self.ed.ax_prev.lines), 0)
        self.assertEqual(len(self._products()), 1)

        self.ed.chk_preview.setChecked(True)
        self.ed.btn_apply.click()
        QApplication.processEvents()
        self.assertEqual(len(self.ed.ax_prev.lines), 2)

    def test_apply_button_shows_checked_count(self):
        other = Path(self._td.name) / "frame2.edf"
        _write_image(other)
        self._open()
        self.assertNotIn("（", self.ed.btn_apply_checked.text())
        self.w.add_files([str(self.img_path), str(other)], select=True)
        QApplication.processEvents()
        self.assertIn("2", self.ed.btn_apply_checked.text())
        self.assertEqual(self._products(), [])

    def test_flush_overlay_blits_without_error(self):
        self._open()
        self.ed.load_image(self.img_path)
        QApplication.processEvents()
        self.ed._flush_overlay()               # 抓背景 + blit
        self.assertIsNotNone(self.ed._bg)      # 支持 blit 的 canvas 才走快路径
        self.ed._paint_segment((10, 10), (20, 20), True)
        self.ed._update_overlay()
        self.ed._flush_overlay()
        self.assertGreater(int(self.ed.mask.sum()), 0)
        self.ed._clear()
        self.ed._flush_overlay()
        self.assertEqual(int(self.ed.mask.sum()), 0)

    def test_escape_cancels_the_stroke_before_hiding(self):
        """用户 2026-10-28：拖矩形时按 Esc 想取消，整个编辑器藏了。"""
        self._open()
        self.ed.load_image(self.img_path)
        self.ed.canvas.draw()
        QApplication.processEvents()
        self.ed._fill_rect((10, 10), (20, 20), True)
        before = int(self.ed.mask.sum())

        self.ed.tool_buttons[self.ed.TOOL_RECT].click()
        px, py = self.ed.ax_img.transData.transform((40.0, 40.0))
        _fire(self.ed.canvas, "button_press_event", px, py, button=1)
        mx, my = self.ed.ax_img.transData.transform((80.0, 80.0))
        _fire(self.ed.canvas, "motion_notify_event", mx, my,
              buttons=frozenset({1}))
        self.assertTrue(self.ed._drawing)

        QTest.keyClick(self.ed.canvas, Qt.Key_Escape)   # 焦点在画布上
        QApplication.processEvents()
        self.assertTrue(self.ed.isVisible())            # 第一次：只取消
        self.assertFalse(self.ed._drawing)
        self.assertIsNone(self.ed._rect_patch)
        self.assertEqual(int(self.ed.mask.sum()), before)

        QTest.keyClick(self.ed.canvas, Qt.Key_Escape)   # 第二次：才隐藏
        QApplication.processEvents()
        self.assertFalse(self.ed.isVisible())

    def test_status_shows_zoom_factor(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.assertNotIn("缩放", self.ed.lbl_status.text())
        self.ed._zoom_about(100.0, 100.0, 0.5)
        for _ in range(80):
            QApplication.processEvents()
            time.sleep(0.01)
            if "缩放" in self.ed.lbl_status.text():
                break
        self.assertIn("缩放", self.ed.lbl_status.text())
        self.ed.btn_home.click()
        QApplication.processEvents()
        self.assertNotIn("缩放", self.ed.lbl_status.text())

    def test_erase_clears_overlay_pixels(self):
        """blit 背景必须不含 overlay，否则橡皮擦不掉旧色块。"""
        self._open()
        self.ed.load_image(self.img_path)
        self.ed.canvas.draw()
        QApplication.processEvents()
        px, py = self.ed.ax_img.transData.transform((100.0, 100.0))
        hpx = self.ed.canvas.buffer_rgba().shape[0]
        row, col = hpx - 1 - int(round(py)), int(round(px))

        def rgb():
            buf = np.asarray(self.ed.canvas.buffer_rgba())
            return buf[row, col, :3].copy()

        before = rgb()
        self.ed._fill_rect((95, 95), (105, 105), True)
        self.ed._update_overlay()
        self.ed._flush_overlay()
        painted = rgb()
        self.assertFalse(np.array_equal(before, painted))

        self.ed._clear()
        self.ed._update_overlay()
        self.ed._flush_overlay()
        self.assertTrue(np.array_equal(before, rgb()))   # 擦干净，无残留

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
