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
