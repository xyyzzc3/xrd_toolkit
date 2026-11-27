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
from PySide6.QtWidgets import QApplication, QPushButton, QMdiSubWindow  # noqa: E402

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
                         theme.ACCENT.lower())
        self.assertEqual(palette.color(QPalette.ColorRole.Text).name(),
                         theme.TEXT.lower())

    def test_style_sheet_has_button_hierarchy(self):
        self.assertIn('[role="primary"]', theme._STYLE_SHEET)
        self.assertIn('[role="danger"]', theme._STYLE_SHEET)
        self.assertIn("QToolBar", theme._STYLE_SHEET)

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


if __name__ == "__main__":
    unittest.main()
