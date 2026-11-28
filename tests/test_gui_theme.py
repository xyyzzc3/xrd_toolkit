"""GUI theme tokens and button roles (2026-11-27).

不调用 apply_app_theme：那会改进程级 QStyle/QPalette，单进程全量测试
里会影响后续模块的布局断言。这里只测纯函数和 token。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QFrame, QLabel, QMdiSubWindow, QPushButton)

from xrd_toolkit.gui import theme  # noqa: E402


class TestThemeTokens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_palette_is_light_and_branded(self):
        palette = theme.build_palette()
        self.assertEqual(palette.color(QPalette.ColorRole.Window).name(),
                         theme.BG.lower())
        self.assertEqual(palette.color(QPalette.ColorRole.Highlight).name(),
                         theme.SELECT.lower())
        self.assertEqual(palette.color(QPalette.ColorRole.Text).name(),
                         theme.TEXT.lower())

    def test_style_sheet_has_button_hierarchy(self):
        self.assertIn('[role="primary"]', theme._STYLE_SHEET)
        self.assertIn('[role="danger"]', theme._STYLE_SHEET)
        self.assertIn("QToolBar", theme._STYLE_SHEET)

    def test_style_sheet_draws_region_dividers(self):
        # 工作台框和 splitter 线是“补画”的，不是换色就能有的。
        self.assertIn(theme.BORDER_STRONG, theme._STYLE_SHEET)
        self.assertIn("QMdiArea", theme._STYLE_SHEET)
        self.assertIn("QSplitter::handle:horizontal", theme._STYLE_SHEET)
        self.assertIn("QSplitter::handle:vertical", theme._STYLE_SHEET)

    def test_set_role_sets_dynamic_property(self):
        button = QPushButton("开始")
        theme.set_role(button, "primary")
        self.assertEqual(button.property("role"), "primary")

    def test_brand_subwindow_does_not_require_an_icon(self):
        # 没有应用图标时不能炸；有图标时由真实入口设置（这里只测防御）。
        sub = QMdiSubWindow()
        try:
            theme.brand_subwindow(sub)
        finally:
            sub.deleteLater()

    def test_section_header_rule_only_after_first(self):
        # 第一条标题不加分隔线；后续小节标题 = 1px 线 + 标签。
        first = theme.section_header("数据", first=True)
        later = theme.section_header("背景")
        try:
            self.assertEqual(first.findChildren(QFrame, "sectionRule"), [])
            rules = later.findChildren(QFrame, "sectionRule")
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0].height(), 1)
            self.assertEqual(later.findChild(QLabel, "sectionLabel").text(),
                             "背景")
        finally:
            first.deleteLater()
            later.deleteLater()

    def test_mdi_area_uses_theme_background(self):
        # QSS 管不到 QMdiArea 的 viewport；必须走 setBackground。
        from PySide6.QtWidgets import QMainWindow
        from xrd_toolkit.gui.panel_layout import _build_center
        window = QMainWindow()
        try:
            _build_center(window)
            self.assertEqual(window.mdi.background().color().name(),
                             theme.MDI_BG.lower())
        finally:
            window.close()
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
