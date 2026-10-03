"""Windows 真机试用反馈的修复守卫（2026-10-04）。

三条都是"只有 Windows 才现形"的问题，本机复现不了，用测试把**判据**
钉住，防止将来被改回去：

  ① 汉字变方框——matplotlib 与状态栏坐标框的字体链里必须有 Windows 的
     中文字体（原来只有 Mac 字体）；
  ② 参数坞窄时「点数 3000」被裁——数据行的转盘允许被压窄，且坞的宽下限
     把「未应用」灰字也预留进去；
  ③ 运行中的应用显示默认图标——窗口图标要从随包 icon.png 显式设上
     （以及"显示桌面"回来后三个坞显隐的防御性重同步）。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import matplotlib                                          # noqa: E402
from PySide6.QtWidgets import QApplication                 # noqa: E402

from xrd_toolkit import paths                              # noqa: E402
from xrd_toolkit.gui import app as gui_app                 # noqa: E402
from xrd_toolkit.gui import watchdog as gui_watchdog       # noqa: E402
from xrd_toolkit.services import stage_cache               # noqa: E402


def setUpModule():
    stage_cache.CACHE_ROOT = Path(tempfile.mkdtemp(prefix="xrd_win_test_"))
    gui_watchdog.OUT_DIR = Path(tempfile.mkdtemp(prefix="xrd_win_watch_"))


class TestFontsCarryWindowsCJK(unittest.TestCase):
    def test_matplotlib_family_lists_a_cjk_font_for_this_platform(self):
        """字体链必须含**本平台**的中文字体（Windows 缺了就是方框）。"""
        import sys
        fams = matplotlib.rcParams["font.family"]
        want = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei"
        self.assertIn(want, fams,
                      f"字体链缺 {want} → 该平台图内中文会是方框")

    def test_status_bar_coordinate_label_falls_back_to_cjk(self):
        app = QApplication.instance() or QApplication([])
        w = gui_app.create_window()
        try:
            qss = w.coord_label.styleSheet()
            self.assertIn("Microsoft YaHei", qss,
                          "等宽字体链缺中文回退 → 悬停读数里的中文是方框")
        finally:
            w.hide()
            w.close()


class TestDataRowFitsNarrowDock(unittest.TestCase):
    def test_spin_boxes_may_shrink(self):
        app = QApplication.instance() or QApplication([])
        w = gui_app.create_window()
        try:
            for key in ("2θ 下限 (°)", "2θ 上限 (°)", "输出点数"):
                box = w.params[key]
                self.assertLess(box.minimumWidth(), 60,
                                f"{key} 没有可缩下限，窄坞里会顶出可视区")
        finally:
            w.hide()
            w.close()

    def test_dock_min_width_reserves_the_pending_label(self):
        """坞宽下限 ≥ 数据行 +「未应用」灰字——它一亮不能把「点数」挤出。"""
        app = QApplication.instance() or QApplication([])
        w = gui_app.create_window()
        try:
            need = (w.data_row.minimumSize().width()
                    + w.pending_labels["数据"].sizeHint().width() + 8)
            self.assertGreaterEqual(w.param_dock.minimumWidth(), need)
        finally:
            w.hide()
            w.close()


class TestAppIconAndDockResync(unittest.TestCase):
    def test_window_icon_is_set_from_shipped_png(self):
        app = QApplication.instance() or QApplication([])
        w = gui_app.create_window()
        try:
            # open() 把 PNG 读出来变成 QPixmap ✔；文件缺失不会崩（记日志）
            self.assertFalse(app.windowIcon().isNull(),
                             "运行时窗口图标没设上（Windows 任务栏会是默认图标）")
        finally:
            w.hide()
            w.close()

    def test_resync_restores_hidden_docks(self):
        app = QApplication.instance() or QApplication([])
        w = gui_app.create_window()
        try:
            dock = w.file_dock
            dock.setVisible(False)          # 模拟"显示桌面回来坞不见了"
            gui_app._resync_docks(w)
            # 用 isVisibleTo：测试里窗口没 show，isVisible() 会恒为假
            self.assertTrue(dock.isVisibleTo(w), "重同步应把坞按按钮状态恢复")
            gui_app._resync_docks(w)        # 幂等：再对一次不变
            self.assertTrue(dock.isVisibleTo(w))
        finally:
            w.hide()
            w.close()

    def test_resync_follows_unchecked_toggle(self):
        """用户主动收起的坞不许被"修复"顶回来——按钮是权威。"""
        app = QApplication.instance() or QApplication([])
        w = gui_app.create_window()
        try:
            w.panel_toggles["日志"].click()      # 真点击 = 记录意图
            gui_app._resync_docks(w)
            self.assertFalse(w.log_dock.isVisibleTo(w), "按钮没勾就不该显示")
        finally:
            w.hide()
            w.close()


class TestIconShipsInBundle(unittest.TestCase):
    def test_spec_ships_icon_png(self):
        spec = (Path(__file__).resolve().parents[1] / "packaging"
                / "xrd_toolkit.spec").read_text(encoding="utf-8")
        self.assertIn("icon.png", spec, "icon.png 要随包（运行时图标用它）")

    def test_shipped_file_finds_icon_in_dev(self):
        got = paths.shipped_file("icon.png", "packaging/icon.png")
        self.assertIsNotNone(got)
        self.assertTrue(got.is_file())


if __name__ == "__main__":
    unittest.main()
