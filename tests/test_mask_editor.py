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
        self.assertTrue(self.w.mask_btn.isChecked())
        self.assertTrue(self.w.mask_dock.isVisible())
        self.assertNotIn("Mask", self.w.entrance_buttons)
        self.w.mask_btn.click()                  # 退出 Mask 模式
        QApplication.processEvents()
        self.assertFalse(self.w.mask_btn.isChecked())
        self.assertFalse(self.w.mask_dock.isVisible())
        self.w.mask_btn.click()                  # 再进：复用同一内容
        QApplication.processEvents()
        self.assertIs(self.w.mask_editor, self.ed)
        self.assertTrue(self.w.mask_dock.isVisible())

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

    def test_recipe_then_regenerate_updates_label(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._fill_rect((10, 10), (20, 20), True)
        saved = Path(self._td.name) / "my_mask.mask.npz"
        with mock.patch.object(gui_mask_editor.QFileDialog,
                               "getSaveFileName",
                               return_value=(str(saved), "")):
            self.ed._save_recipe()
        self.assertTrue(saved.is_file())
        self.ed._apply_to_main()
        self.assertEqual(self._products()[0]["label"], "my_mask.mask.npz")

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

    def test_preview_button_draws_before_after_without_product(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._paint_segment((10, 10), (20, 20), True)
        self.ed.btn_preview.click()
        QApplication.processEvents()
        self.assertEqual(len(self.ed.ax_prev.lines), 2)
        self.assertEqual(len(self._products()), 0)

    def test_preview_uses_current_2theta_range(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._paint_segment((10, 10), (20, 20), True)
        with mock.patch.object(
                gui_mask_editor, "integrate_1d",
                return_value=(np.arange(3.0), np.arange(3.0))) as integ:
            self.ed.btn_preview.click()
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
        self.ed.btn_zoom.click()          # 与主面板一致：放大镜点亮才滚轮缩放
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
        self.ed.btn_zoom.click()
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

    def test_generate_button_makes_product_and_goes_dirty(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.assertFalse(self.ed.btn_apply.isEnabled())   # 空 mask 不可点
        self.ed._fill_rect((10, 10), (20, 20), True)
        self.ed._update_status()                          # 画笔松手时也会走这里
        self.assertTrue(self.ed.btn_apply.isEnabled())
        self.ed.btn_apply.click()
        QApplication.processEvents()
        self.assertEqual(len(self.ed.ax_prev.lines), 0)   # 预览是独立按钮
        self.assertEqual(len(self._products()), 1)
        self.assertEqual(self.ed.btn_apply.text(), "已生成（无改动）")
        self.assertFalse(self.ed.btn_apply.isEnabled())
        self.ed._fill_rect((30, 30), (35, 35), True)      # 再改一笔
        self.ed._update_status()
        self.assertTrue(self.ed.btn_apply.isEnabled())

    def test_scope_combo_counts_checked_files(self):
        other = Path(self._td.name) / "frame2.edf"
        _write_image(other)
        self._open()
        self.ed.load_image(self.img_path)
        self.assertIn("0", self.ed.scope_combo.itemText(1))
        self.w.add_files([str(self.img_path), str(other)], select=True)
        QApplication.processEvents()
        self.assertIn("2", self.ed.scope_combo.itemText(1))
        self.ed._fill_rect((10, 10), (20, 20), True)
        self.ed.scope_combo.setCurrentIndex(1)            # 勾选范围
        self.ed.btn_apply.click()
        QApplication.processEvents()
        self.assertEqual(len(self._products()), 2)
        self.assertEqual(self.ed.btn_apply.text(), "已生成（无改动）")

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

        QTest.keyClick(self.ed.canvas, Qt.Key_Escape)   # 第二次：退出 Mask
        QApplication.processEvents()
        self.assertFalse(self.ed.isVisible())
        self.assertFalse(self.w.mask_dock.isVisible())
        self.assertFalse(self.w.mask_btn.isChecked())

    def test_workbench_collapses_and_restores_docks(self):
        self.w.param_dock.setVisible(True)
        self.w.log_dock.setVisible(True)
        self.w._dock_intent["参数"] = True
        self.w._dock_intent["日志"] = True
        self._open()
        self.assertFalse(self.w.param_dock.isVisible())
        self.assertFalse(self.w.log_dock.isVisible())
        self.assertTrue(self.w.file_dock.isVisible())
        self.assertEqual(self.w.mode_label.text(), "Mask 模式")
        self.assertEqual(self.w.mask_btn.text(), "退出 Mask")
        self.w.mask_btn.click()
        QApplication.processEvents()
        self.assertTrue(self.w.param_dock.isVisible())
        self.assertTrue(self.w.log_dock.isVisible())
        self.assertEqual(self.w.mode_label.text(), "分析模式")
        self.assertEqual(self.w.mask_btn.text(), "Mask")

    def test_mask_and_calibration_are_mutually_exclusive(self):
        self.w.calib_btn.setChecked(True)
        QApplication.processEvents()
        self.assertEqual(self.w.mode_label.text(), "校准模式")
        self._open()
        self.assertFalse(self.w.calib_btn.isChecked())
        self.assertEqual(self.w.mode_label.text(), "Mask 模式")
        self.w.calib_btn.setChecked(True)
        QApplication.processEvents()
        self.assertFalse(self.w.mask_btn.isChecked())
        self.assertFalse(self.w.mask_dock.isVisible())
        self.assertEqual(self.w.mode_label.text(), "校准模式")

    def test_switch_to_entrance_exits_mask(self):
        self._open()
        self.w.entrance_buttons["1D"].click()
        QApplication.processEvents()
        self.assertFalse(self.w.mask_btn.isChecked())
        self.assertFalse(self.w.mask_dock.isVisible())
        self.assertTrue(self.w.entrance_buttons["1D"].isChecked())

    def test_close_subwindow_exits_mask(self):
        self.w.param_dock.setVisible(True)
        self.w.log_dock.setVisible(True)
        self.w._dock_intent["参数"] = True
        self.w._dock_intent["日志"] = True
        self._open()
        self.w.mask_dock.close()
        QApplication.processEvents()
        self.assertFalse(self.w.mask_btn.isChecked())
        self.assertFalse(self.w.mask_dock.isVisible())
        self.assertTrue(self.w.param_dock.isVisible())
        self.assertTrue(self.w.log_dock.isVisible())
        self.assertEqual(self.w.mode_label.text(), "分析模式")

    def test_workbench_keeps_mask_state_across_exit(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._fill_rect((10, 10), (20, 20), True)
        n = int(self.ed.mask.sum())
        self.assertGreater(n, 0)
        self.w.mask_btn.click()                  # 退出
        QApplication.processEvents()
        self.w.mask_btn.click()                  # 再进
        QApplication.processEvents()
        self.assertTrue(self.w.mask_dock.isVisible())
        self.assertEqual(int(self.ed.mask.sum()), n)

    def test_home_shortcut_moves_to_the_workbench(self):
        self._open()
        self.assertTrue(self.ed.home_sc.isEnabled())
        self.assertFalse(self.w.home_shortcut.isEnabled())
        self.w.mask_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.ed.home_sc.isEnabled())
        self.assertTrue(self.w.home_shortcut.isEnabled())

    def test_home_shortcut_resets_the_view(self):
        """Home = 复位视野（与主窗口同一套 QShortcut）。

        QTest 在离屏环境不驱动 QShortcut（主窗口那条 Home 测试也是直接
        emit activated），这里同样测接线 + 动作。
        """
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._zoom_about(100.0, 100.0, 0.5)
        before = (self.ed.ax_img.get_xlim()[1]
                  - self.ed.ax_img.get_xlim()[0])
        self.assertLess(before, 199.0)
        self.ed.home_sc.activated.emit()
        QApplication.processEvents()
        after = (self.ed.ax_img.get_xlim()[1]
                 - self.ed.ax_img.get_xlim()[0])
        self.assertAlmostEqual(after, 200.0)

    def test_editor_autoloads_first_checked_image(self):
        other = Path(self._td.name) / "frame2.edf"
        _write_image(other)
        self.w.add_files([str(self.img_path), str(other)], select=True)
        QApplication.processEvents()
        self._open()
        self.assertEqual(Path(self.ed.image_path).resolve(),
                         self.img_path.resolve())
        self.assertEqual(self.ed.image_combo.count(), 2)
        self.assertEqual(self.ed.image_combo.currentData(),
                         str(self.img_path))

    def test_image_combo_switches_image(self):
        other = Path(self._td.name) / "frame2.edf"
        _write_image(other)
        self.w.add_files([str(self.img_path), str(other)], select=True)
        QApplication.processEvents()
        self._open()
        self.ed._on_image_combo(1)
        QApplication.processEvents()
        self.assertEqual(Path(self.ed.image_path).resolve(),
                         other.resolve())
        self.assertEqual(self.ed.mask.shape, self.ed.image.shape)

    def test_magnifier_gates_wheel_and_boxes_zoom(self):
        """放大镜与主面板同语义：熄灭时滚轮不缩放；点亮时左键框选放大。"""
        self._open()
        self.ed.load_image(self.img_path)
        self.ed.canvas.draw()
        QApplication.processEvents()
        ax = self.ed.ax_img
        span0 = ax.get_xlim()[1] - ax.get_xlim()[0]
        px, py = ax.transData.transform((100.0, 100.0))
        _fire(self.ed.canvas, "scroll_event", px, py, button="up", step=1)
        self.assertAlmostEqual(ax.get_xlim()[1] - ax.get_xlim()[0], span0)

        self.ed.btn_zoom.click()
        self.assertFalse(
            self.ed.tool_buttons[self.ed.TOOL_BRUSH].isEnabled())
        _fire(self.ed.canvas, "scroll_event", px, py, button="up", step=1)
        self.assertLess(ax.get_xlim()[1] - ax.get_xlim()[0], span0)

        px0, py0 = ax.transData.transform((40.0, 40.0))
        px1, py1 = ax.transData.transform((80.0, 80.0))
        _fire(self.ed.canvas, "button_press_event", px0, py0, button=1)
        self.assertTrue(self.ed._zooming)
        _fire(self.ed.canvas, "motion_notify_event", px1, py1,
              buttons=frozenset({1}))
        _fire(self.ed.canvas, "button_release_event", px1, py1, button=1)
        x0, x1 = ax.get_xlim()
        self.assertLess(x1 - x0, 120.0)
        self.assertGreaterEqual(x1 - x0, gui_mask_editor.MIN_VIEW_SPAN)
        self.assertEqual(int(self.ed.mask.sum()), 0)   # 左键没有画画

    def test_locate_changes_zooms_to_the_new_pixels(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed.canvas.draw()
        QApplication.processEvents()
        hot = np.zeros(self.ed.image.shape, dtype=bool)
        hot[150:160, 150:160] = True
        fake = (self.ed.image, hot, {"masked_components": 1,
                                    "masked_pixels": int(hot.sum())})
        with mock.patch.object(gui_mask_editor, "mask_hot_pixels",
                               return_value=fake):
            self.ed._auto_hot()
            _wait_task(self.ed)
        self.assertTrue(self.ed.btn_locate.isEnabled())
        self.ed.btn_locate.click()
        x0, x1 = self.ed.ax_img.get_xlim()
        self.assertLess(x1 - x0, 200.0)

    def test_recipe_roundtrip_restores_source_layers(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._fill_rect((10, 10), (20, 20), True)      # 手动 = 红
        hot = np.zeros(self.ed.image.shape, dtype=bool)
        hot[60:70, 60:70] = True
        fake = (self.ed.image, hot, {"masked_components": 1,
                                    "masked_pixels": int(hot.sum())})
        with mock.patch.object(gui_mask_editor, "mask_hot_pixels",
                               return_value=fake):
            self.ed._auto_hot()
            _wait_task(self.ed)
        saved = Path(self._td.name) / "roundtrip.mask.npz"
        with mock.patch.object(gui_mask_editor.QFileDialog,
                               "getSaveFileName",
                               return_value=(str(saved), "")):
            self.ed._save_recipe()
        self.assertTrue(saved.is_file())
        self.ed._clear()
        with mock.patch.object(gui_mask_editor.QFileDialog,
                               "getOpenFileName",
                               return_value=(str(saved), "")):
            self.ed._import_mask()
        rgba, _ext = self.ed._overlay_rgba(1)
        self.assertEqual(tuple(rgba[15, 15, :3]), gui_mask_editor.COLOR_MANUAL)
        self.assertEqual(tuple(rgba[65, 65, :3]), gui_mask_editor.COLOR_HOT)

    def test_export_generic_makes_npy_without_touching_recipe(self):
        self._open()
        self.ed.load_image(self.img_path)
        self.ed._fill_rect((10, 10), (20, 20), True)
        target = Path(self._td.name) / "generic.npy"
        with mock.patch.object(gui_mask_editor.QFileDialog,
                               "getSaveFileName",
                               return_value=(str(target), "")):
            self.ed._export_generic()
        self.assertTrue(target.is_file())
        self.assertIsNone(self.ed.mask_path)

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
