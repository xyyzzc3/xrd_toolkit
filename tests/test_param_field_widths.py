"""参数坞数字框宽度守卫（2026-10-04）。

用户发现的问题："参数栏所有能手动填写的框都不会随参数栏总体拉伸——
即使拉伸参数栏，点数 3000 也不动。"根因：每个数字框的上限都写死
（84/72/60），是在某台机器、某个量程上量的。换平台（mac 转盘内边距
更肥）或换量程（纵轴到 1e9，最长文本 "1000000000.0"）就不够——上限
一旦低于"完整显示量程最长文本"的宽度，坞拖多宽都补不回来（实测当时
点数缺 2px、对比度/热图/纵轴上下限各缺 33px）。

规矩（改回去会红）：
  ① 每个数字框的上限 ≥ 它自己的 sizeHint（量程最长文本 + 本平台转盘
     内边距）——坞够宽时任何可能的值都能完整显示；
  ② 曾被旧上限压着的框（正是会裁长值的那批），旧上限降级为显式下限
     ——任何坞宽下都不比以前窄（0.1.1 的"窄坞能压"守卫在
     tests/test_windows_display.py，两边一起看）；
  ③ 坞拖宽时框要能长回"完整显示"——顶部数据行与长量程页面框实测。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                                  # noqa: E402
from PySide6.QtWidgets import (QApplication, QDoubleSpinBox,   # noqa: E402
                               QSpinBox)

from xrd_toolkit.gui.app import create_window                  # noqa: E402
from xrd_toolkit.services import stage_cache                   # noqa: E402


def setUpModule():
    stage_cache.CACHE_ROOT = Path(tempfile.mkdtemp(prefix="xrd_width_test_"))


def _spin_boxes(window):
    return {k: b for k, b in window.params.items()
            if isinstance(b, (QSpinBox, QDoubleSpinBox))}


class TestCapsFitLongestValue(unittest.TestCase):
    def test_every_spin_box_can_show_its_longest_value(self):
        app = QApplication.instance() or QApplication([])   # noqa: F841
        w = create_window()
        try:
            boxes = _spin_boxes(w)
            self.assertGreaterEqual(len(boxes), 10, "参数坞的数字框清点变了")
            for key, box in boxes.items():
                box.ensurePolished()
                self.assertGreaterEqual(
                    box.maximumWidth(), box.sizeHint().width(),
                    f"{key} 的上限窄于完整显示所需——长值会被裁，"
                    f"拉宽参数栏也救不回来（见 app._fit_max_width 的说明）")
        finally:
            w.hide()
            w.close()

    def test_long_range_boxes_keep_their_old_floor(self):
        """1e9 量程的那几个：旧上限 84 降级为下限（不许比以前窄）。"""
        app = QApplication.instance() or QApplication([])   # noqa: F841
        w = create_window()
        try:
            for key in ("对比度下限", "对比度上限", "纵轴下限", "纵轴上限",
                        "热图下限", "热图上限"):
                box = w.params[key]
                self.assertGreaterEqual(
                    box.minimumWidth(), 84,
                    f"{key} 丢了旧上限给的压缩底——窄坞里会比 0.1.1 时更窄")
        finally:
            w.hide()
            w.close()


class TestFieldsFollowTheDockWidth(unittest.TestCase):
    """坞拖宽 → 框能长到"完整显示"（用户要的"变宽数字框，看到完整信息"）。"""

    def _widen(self, w, target=700):
        w.resize(1900, 900)
        w.show()
        w.param_dock.show()              # 隐藏时 resizeDocks 不生效（探针验证）
        QApplication.processEvents()
        w.resizeDocks([w.param_dock], [target], Qt.Horizontal)
        QApplication.processEvents()

    def test_data_row_shows_full_values_in_a_widened_dock(self):
        app = QApplication.instance() or QApplication([])   # noqa: F841
        w = create_window()
        try:
            self._widen(w)
            for key in ("2θ 下限 (°)", "2θ 上限 (°)", "输出点数"):
                box = w.params[key]
                self.assertGreaterEqual(
                    box.width(), box.sizeHint().width(),
                    f"{key} 在拖宽后的坞里仍显示不全")
        finally:
            w.hide()
            w.close()

    def test_a_long_page_field_restores_when_the_dock_widens(self):
        """原图页纵轴框（量程到 1e9）：坞够宽时必须到完整显示宽度。"""
        app = QApplication.instance() or QApplication([])   # noqa: F841
        w = create_window()
        try:
            w.param_stack.setCurrentIndex(w.PARAM_PAGES["原图"])
            self._widen(w)
            box = w.params["纵轴下限"]
            self.assertGreaterEqual(box.width(), box.sizeHint().width(),
                                    "长量程框没有随坞长回完整显示宽度")
        finally:
            w.hide()
            w.close()


if __name__ == "__main__":
    unittest.main()
