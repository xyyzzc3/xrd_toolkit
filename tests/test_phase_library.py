"""内置精选相库（2026-11-11 用户第 10 条）。

随包的 data/phase_library 要能直接给物相/精修工作台用：
  * 目录里有几十张 CIF，全部能被引擎读进来；
  * 来源/许可说明随库；
  * 物相与精修工作台的 [内置相库] 按钮切过去。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403
except ModuleNotFoundError:  # 直接运行本文件
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403

from xrd_toolkit import paths  # noqa: E402
from xrd_toolkit.simulation import load_structure  # noqa: E402


def setUpModule():
    _support.setUpModule()


class TestBundledLibrary(unittest.TestCase):
    def test_all_cifs_load(self):
        lib = paths.phase_library_dir()
        self.assertTrue(lib.is_dir(), lib)
        self.assertTrue((lib / "SOURCES.md").is_file())
        cifs = sorted(lib.glob("*.cif"))
        self.assertGreaterEqual(len(cifs), 30, "精选库至少要有几十张相")
        self.assertEqual(len(cifs), len({p.name for p in cifs}))
        names = {p.name for p in cifs}
        for expected in ("LaB6.cif", "LiFePO4_olivine.cif",
                         "Al_fcc.cif", "Si_diamond.cif"):
            self.assertIn(expected, names)
        for path in cifs:
            structure = load_structure(str(path))
            self.assertGreater(len(structure), 0, path.name)


class TestBundledLibraryButtons(unittest.TestCase):
    def setUp(self):
        self.w = create_window()
        self.w.show()
        QApplication.processEvents()

    def tearDown(self):
        self.w.hide()
        self.w.close()
        QApplication.processEvents()

    def test_phase_workbench_switches_to_bundled_library(self):
        self.w.phase_btn.click()
        QApplication.processEvents()
        editor = self.w.phase_editor
        editor.bundled_lib_btn.click()
        QApplication.processEvents()
        self.assertEqual(editor.lib_dir, paths.phase_library_dir())
        self.assertIn("phase_library", editor.lib_label.text())
        self.assertIn("内置精选相库", editor.status.text())

    def test_refine_workbench_switches_to_bundled_library(self):
        self.w.refine_btn.click()
        QApplication.processEvents()
        editor = self.w.refine_editor
        editor.bundled_lib_btn.click()
        QApplication.processEvents()
        self.assertEqual(editor.lib_dir, paths.phase_library_dir())
        self.assertIn("内置精选相库", editor.status.text())
        # 结构下拉里应有库里的 CIF（按文件名排序，LaB6 只是其中之一）
        names = {editor.cif_combo.itemText(i)
                 for i in range(editor.cif_combo.count())}
        self.assertIn("LaB6", names)
