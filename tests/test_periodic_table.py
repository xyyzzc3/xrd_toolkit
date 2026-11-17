"""元素周期表选择器与物相工作台 [周期表…] 接线（2026-11-11）。

用户第 11 条：元素筛选从"手输一行元素符号"改成"点元素周期表"。
输入框保留（仍是筛相与报告的唯一取值处），周期表是它的图形入口。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from PySide6.QtWidgets import QApplication, QDialog, QPushButton  # noqa: E402

try:
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403
except ModuleNotFoundError:  # 直接运行本文件
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403

from xrd_toolkit.gui.periodic_table import (  # noqa: E402
    PeriodicTableDialog, SYMBOLS, atomic_number, sort_elements)

_app = QApplication.instance() or QApplication([])


def setUpModule():
    _support.setUpModule()


class TestPeriodicTableData(unittest.TestCase):
    def test_symbols_complete_and_unique(self):
        self.assertEqual(len(SYMBOLS), 118)
        self.assertEqual(len(set(SYMBOLS)), 118)
        self.assertEqual(SYMBOLS[0], "H")
        self.assertEqual(SYMBOLS[-1], "Og")

    def test_atomic_number_and_sort(self):
        self.assertEqual(atomic_number("Fe"), 26)
        self.assertEqual(atomic_number("fe"), 26)
        self.assertEqual(atomic_number("不认得"), 999)
        self.assertEqual(sort_elements({"Fe", "Li", "O"}), ["Li", "O", "Fe"])
        self.assertEqual(sort_elements(["Fe", "Fe"]), ["Fe"])


class TestPeriodicTableDialog(unittest.TestCase):
    def test_every_element_has_a_button(self):
        dlg = PeriodicTableDialog()
        try:
            self.assertEqual(len(dlg._buttons), len(SYMBOLS))
            for symbol in SYMBOLS:
                self.assertIn(symbol, dlg._buttons)
        finally:
            dlg.deleteLater()

    def test_initial_selection_and_toggle(self):
        """两态：点一下 = 一定有，再点 = 可能有，再点 = 取消（2026-11-17）。"""
        dlg = PeriodicTableDialog(["Fe", "Li", "不认识的"], possible=["Al"])
        try:
            self.assertEqual(dlg.required_elements(), ["Li", "Fe"])
            self.assertEqual(dlg.possible_elements(), ["Al"])
            self.assertEqual(dlg.selected_elements(), ["Li", "Al", "Fe"])
            dlg._buttons["O"].click()                 # 未选 → 一定有
            self.assertIn("O", dlg.required_elements())
            dlg._buttons["Li"].click()                # 一定有 → 可能有
            self.assertEqual(dlg.required_elements(), ["O", "Fe"])
            self.assertEqual(dlg.possible_elements(), ["Li", "Al"])
            dlg._buttons["Li"].click()                # 可能有 → 取消
            self.assertNotIn("Li", dlg.selected_elements())
            self.assertIn("一定有 2 个", dlg.summary.text())
            self.assertIn("可能有 1 个", dlg.summary.text())
        finally:
            dlg.deleteLater()

    def test_clear(self):
        dlg = PeriodicTableDialog(["Fe"], possible=["Al"])
        try:
            dlg._clear()
            self.assertEqual(dlg.selected_elements(), [])
            self.assertEqual(dlg.required_elements(), [])
            self.assertEqual(dlg.possible_elements(), [])
            self.assertIn("未选元素", dlg.summary.text())
        finally:
            dlg.deleteLater()

    def test_screen_button_requests_screening(self):
        dlg = PeriodicTableDialog(["Fe"], allow_screen=True)
        try:
            dlg.findChild(QPushButton, "periodic_screen_btn").click()
            self.assertTrue(dlg.screen_requested)
        finally:
            dlg.deleteLater()


class TestPhaseWorkbenchPeriodicWiring(unittest.TestCase):
    def setUp(self):
        self.w = create_window()
        self.w.show()
        QApplication.processEvents()

    def tearDown(self):
        self.w.hide()
        self.w.close()
        QApplication.processEvents()

    def test_button_writes_sorted_selection(self):
        self.w.phase_btn.click()
        QApplication.processEvents()
        editor = self.w.phase_editor

        class _FakeDialog:
            def __init__(self, selected, possible=(), parent=None,
                         allow_screen=False):
                self.initial = list(selected)
                self.initial_possible = list(possible)
                self.allow_screen = allow_screen
                self.screen_requested = False

            def exec(self):
                return QDialog.Accepted

            def required_elements(self):
                return ["Fe", "O", "Li"]

            def possible_elements(self):
                return ["Al"]

        from xrd_toolkit.gui import phase_workbench as gui_phase
        with mock.patch.object(gui_phase, "PeriodicTableDialog", _FakeDialog):
            editor.periodic_btn.click()
            QApplication.processEvents()
        self.assertEqual(editor.elements_edit.text(), "Li O Fe",
                         "写回输入框时应按原子序数排序")
        self.assertEqual(editor.elements_possible_edit.text(), "Al")

    def test_screen_button_starts_screening(self):
        """[确定并筛相]：关窗后直接开始筛相（不必再回页面点按钮）。"""
        self.w.phase_btn.click()
        QApplication.processEvents()
        editor = self.w.phase_editor

        class _FakeDialog:
            screen_requested = True

            def __init__(self, *a, **k):
                pass

            def exec(self):
                return QDialog.Accepted

            def required_elements(self):
                return []

            def possible_elements(self):
                return []

        from xrd_toolkit.gui import phase_workbench as gui_phase
        with mock.patch.object(gui_phase, "PeriodicTableDialog", _FakeDialog), \
                mock.patch.object(editor, "_start_screen") as start:
            editor.periodic_btn.click()
            QApplication.processEvents()
        start.assert_called_once()
