"""[帮助] 菜单与「关于」框（2026-10-03，按桌面软件惯例加的入口）。

盯三件事：菜单真的挂上了、关于框里版本与许可都对、"打开文件"这条路
在文件缺失时不崩（记日志而不是弹错）。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                              # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox    # noqa: E402

from xrd_toolkit import __version__                        # noqa: E402
from xrd_toolkit.gui import app as gui_app                 # noqa: E402
from xrd_toolkit.gui import watchdog as gui_watchdog       # noqa: E402
from xrd_toolkit.services import stage_cache               # noqa: E402


def setUpModule():
    # 与其余 GUI 测试同款隔离：别把缓存与崩溃现场写进仓库 outputs/
    stage_cache.CACHE_ROOT = Path(tempfile.mkdtemp(prefix="xrd_help_test_"))
    gui_watchdog.OUT_DIR = Path(tempfile.mkdtemp(prefix="xrd_help_watch_"))


class TestHelpMenu(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.w = gui_app.create_window()

    def tearDown(self):
        self.w.hide()
        self.w.close()

    def test_menu_has_help_with_both_entries(self):
        menus = [a.text() for a in self.w.menuBar().actions()]
        self.assertIn("帮助", menus)
        acts = self.w.menu_actions
        self.assertEqual(acts["manual"].text(), "使用说明")
        self.assertIn("关于", acts["about"].text())

    def test_menu_bar_hidden_off_macos(self):
        """菜单栏平台分叉（用户 2026-10-07："windows 是在功能栏的上方多出
        一行，仅有帮助一个选项"）：Windows/Linux 上隐藏那条窗口内横条
        （帮助全走工具栏 [帮助▾]）；macOS 的系统菜单保留（不占窗口，
        Apple 惯例的「关于」位）。CI 两个平台都会真跑到各自的分支。"""
        import sys
        if sys.platform == "darwin":
            self.assertFalse(self.w.menuBar().isHidden(),
                             "macOS 上菜单栏该保留（系统级、不占窗口）")
        else:
            self.assertTrue(self.w.menuBar().isHidden(),
                            "Windows/Linux 上那条只装「帮助」的横条该隐藏")
            self.assertIn(self.w.menu_actions["manual"], self.w.actions(),
                          "F1 的动作要挂到窗口上保底")

    def test_f1_still_works_with_the_menu_bar_hidden(self):
        """菜单栏隐藏后 F1 照常开说明书（真按键事件，实测过的组合）。"""
        from PySide6.QtTest import QTest
        self.w.show()
        self.w.menuBar().setVisible(False)   # 模拟 Windows/Linux 侧
        self.w.addAction(self.w.menu_actions["manual"])
        QApplication.processEvents()
        with mock.patch.object(gui_app, "_open_manual") as opened:
            QTest.keyClick(self.w, Qt.Key_F1)
            QApplication.processEvents()
        opened.assert_called_once()

    def test_toolbar_help_button_shares_the_same_actions(self):
        """[帮助] 按钮挨着 [日志]（用户 2026-10-07："把帮助放到日志旁边"）。

        点开的小菜单里是**同一批 QAction 对象**（菜单栏那条照旧保留：
        macOS 的「关于」必须住系统菜单、F1 也归它管）——两条入口、一份
        行为，改一边两边都跟。
        """
        from PySide6.QtWidgets import QPushButton
        btn = self.w.help_btn
        self.assertIsInstance(btn, QPushButton)   # 与工具栏其余按钮同款外观
        self.assertEqual(btn.text(), "帮助")
        self.assertIsNotNone(btn.menu(), "该带弹出菜单")
        acts = btn.menu().actions()
        self.assertIs(acts[0], self.w.menu_actions["manual"])
        self.assertIs(acts[1], self.w.menu_actions["about"])
        # 确实挂在工具栏上、在 [日志] 开关的右边
        from PySide6.QtWidgets import QToolBar
        tb = self.w.findChild(QToolBar)
        widgets = [tb.widgetForAction(a) for a in tb.actions()]
        self.assertIn(btn, widgets)
        self.assertLess(widgets.index(self.w.panel_toggles["日志"]),
                        widgets.index(btn), "[帮助] 该在 [日志] 右边")

    def test_about_dialog_shows_version_license_and_entries(self):
        seen = {}

        def fake_exec(box):
            seen["text"] = box.text()
            seen["info"] = box.informativeText()
            seen["buttons"] = [b.text() for b in box.buttons()]
            return 0                       # 一个按钮都不点 = 直接关掉

        with mock.patch.object(QMessageBox, "exec", new=fake_exec):
            gui_app._show_about(self.w)
        self.assertIn(__version__, seen["text"], "关于框必须写版本号")
        self.assertIn("MIT", seen["info"])
        self.assertIn("使用说明", seen["buttons"])
        self.assertIn("第三方许可", seen["buttons"])

    def test_manual_action_opens_the_shipped_html(self):
        with mock.patch.object(gui_app.QDesktopServices, "openUrl") as op:
            gui_app._open_manual(self.w)
        self.assertTrue(op.called, "应当交给系统浏览器打开")
        url = op.call_args[0][0].toLocalFile()
        self.assertTrue(url.endswith("使用说明.html"), url)

    def test_missing_shipped_file_logs_instead_of_crashing(self):
        with mock.patch.object(gui_app.paths, "shipped_file",
                               return_value=None):
            gui_app._open_notices(self.w)
        self.assertIn("第三方许可声明读不到", self.w.log_text.toPlainText())


if __name__ == "__main__":
    unittest.main()
