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

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

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
        dlg = PeriodicTableDialog(["Fe", "Li", "不认识的"])
        try:
            self.assertEqual(dlg.selected_elements(), ["Li", "Fe"])
            dlg._buttons["O"].click()
            self.assertEqual(dlg.selected_elements(), ["Li", "O", "Fe"])
            dlg._buttons["Li"].click()
            self.assertEqual(dlg.selected_elements(), ["O", "Fe"])
            self.assertIn("已选 2 个", dlg.summary.text())
        finally:
            dlg.deleteLater()

    def test_clear(self):
        dlg = PeriodicTableDialog(["Fe"])
        try:
            dlg._clear()
            self.assertEqual(dlg.selected_elements(), [])
            self.assertIn("未选元素", dlg.summary.text())
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
            def __init__(self, selected, parent=None):
                self.initial = list(selected)

            def exec(self):
                return QDialog.Accepted

            def selected_elements(self):
                return ["Fe", "O", "Li"]

        from xrd_toolkit.gui import phase_workbench as gui_phase
        with mock.patch.object(gui_phase, "PeriodicTableDialog", _FakeDialog):
            editor.periodic_btn.click()
            QApplication.processEvents()
        self.assertEqual(editor.elements_edit.text(), "Li O Fe",
                         "写回输入框时应按原子序数排序")
