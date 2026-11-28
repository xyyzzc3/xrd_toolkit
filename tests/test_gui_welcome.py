"""空态欢迎卡 + 参数坞折叠说明（2026-11-28 视觉第五轮）。

欢迎卡是 MDI viewport 的普通子控件，不是子窗口：
  - 没有子窗口时显示并居中；
  - 开一张图/工作台后隐藏；
  - 全部关掉后再出现。
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(ROOT / "tests")]

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QToolButton, QWidget)

from tests import _gui_integration_support as _support  # noqa: E402


def setUpModule():
    _support.setUpModule()


class TestWelcomeCard(unittest.TestCase):
    def setUp(self):
        self.w = _support.create_window()
        self.w.resize(1600, 1000)
        self.w.show()
        self._settle()

    def tearDown(self):
        self.w.close()
        self.w.deleteLater()
        QApplication.processEvents()

    def _settle(self):
        # 子窗口增删的同步是 singleShot(0) 投递的，多转两圈
        for _ in range(3):
            QApplication.processEvents()

    def test_card_tracks_subwindow_count(self):
        card = self.w.welcome_card
        self.assertIsNotNone(card)
        self.assertFalse(card.isHidden())
        self.assertFalse(self.w.mdi.subWindowList())

        sub = self.w.mdi.addSubWindow(QWidget())
        sub.resize(200, 150)
        sub.show()
        self._settle()
        self.assertTrue(card.isHidden(),
                        "有子窗口时欢迎卡应隐藏")

        sub.close()
        sub.deleteLater()
        self._settle()
        self.assertFalse(card.isHidden(),
                         "关掉全部子窗口后欢迎卡应回来")
        self.assertFalse(self.w.mdi.subWindowList())

    def test_long_page_hint_is_collapsed_by_default(self):
        toggle = self.w.findChild(QToolButton, "paramHintToggle")
        self.assertIsNotNone(toggle, "1D 页的「说明」按钮没建出来")
        self.assertEqual(toggle.text(), "说明")
        label = next((lb for lb in self.w.findChildren(QLabel)
                      if "2θ 范围与点数在参数面板顶部" in lb.text()), None)
        self.assertIsNotNone(label)
        self.assertTrue(label.isHidden(), "长说明默认应收起")

        toggle.click()
        self._settle()
        self.assertFalse(label.isHidden(), "点开后说明应显示")


if __name__ == "__main__":
    unittest.main()
