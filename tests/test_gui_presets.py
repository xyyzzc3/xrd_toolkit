"""项目预设的窗口级测试（B1，2026-11-10）。

真实链路：create_window → 参数坞预设下拉 → 打开物相/原位工作台改目录 →
[保存…]（这里直接调 save_current_preset，跳过输入框）→ 切换预设，
检查几何/相库/原位目录/mask 都跟着走。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(ROOT / "tests")]

from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403
except ModuleNotFoundError:  # 直接运行本文件
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403

from xrd_toolkit import paths, presets  # noqa: E402
from xrd_toolkit.gui import preset_ops  # noqa: E402


def setUpModule():
    _support.setUpModule()


class TestPresetWindow(unittest.TestCase):
    def setUp(self):
        self._old_presets_path = presets.USER_PRESETS_PATH
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        presets.USER_PRESETS_PATH = self.tmp / "presets_user.json"
        self.w = create_window()
        self.w.show()
        QApplication.processEvents()

    def tearDown(self):
        self.w.hide()
        self.w.close()
        QApplication.processEvents()
        presets.USER_PRESETS_PATH = self._old_presets_path
        self._tmp.cleanup()

    def test_default_preset_applied_at_startup(self):
        self.assertEqual(self.w.preset_name, presets.DEFAULT_PRESET_NAME)
        self.assertEqual(self.w.config_name, "lmfp1_lab6")
        self.assertGreaterEqual(self.w.preset_combo.count(), 1)
        self.assertEqual(self.w.preset_combo.currentText(),
                         presets.DEFAULT_PRESET_NAME)

    def test_save_and_switch_user_preset_updates_workbenches(self):
        frames = self.tmp / "frames"
        frames.mkdir()
        library = self.tmp / "library"
        library.mkdir()

        self.w.insitu_btn.click()
        QApplication.processEvents()
        insitu = self.w.insitu_editor
        insitu.data_dir = frames
        insitu.mask_path = None
        insitu._refresh_data_label()

        self.w.phase_btn.click()
        QApplication.processEvents()
        phase = self.w.phase_editor
        phase.lib_dir = library
        phase._refresh_lib_label()

        self.assertTrue(preset_ops.save_current_preset(self.w, "测试项目"))
        self.assertEqual(self.w.preset_name, "测试项目")
        self.assertEqual(insitu.data_dir, frames)
        self.assertEqual(phase.lib_dir, library)
        self.assertIn("测试项目",
                      [self.w.preset_combo.itemText(i)
                       for i in range(self.w.preset_combo.count())])

        # 切回内置：几何/相库/原位目录都回默认
        self.assertTrue(preset_ops.apply_preset(
            self.w, presets.DEFAULT_PRESET_NAME))
        self.assertEqual(self.w.preset_name, presets.DEFAULT_PRESET_NAME)
        self.assertEqual(phase.lib_dir, paths.structures_dir())
        self.assertNotEqual(insitu.data_dir, frames)

        # 再切回用户预设
        self.assertTrue(preset_ops.apply_preset(self.w, "测试项目"))
        self.assertEqual(insitu.data_dir, frames)
        self.assertEqual(phase.lib_dir, library)

    def test_active_preset_restored_in_new_window(self):
        presets.save_user_preset("启动项目", {
            "geometry": "lmfp1_lab6", "second_phase_cif": "",
            "insitu_dir": str(self.tmp / "frames"),
            "insitu_mask_name": "", "phase_library": "",
        })
        presets.set_active_preset("启动项目")
        w2 = create_window()
        try:
            self.assertEqual(w2.preset_name, "启动项目")
            self.assertEqual(w2.preset["insitu_dir"], str(self.tmp / "frames"))
            w2.insitu_btn.click()
            QApplication.processEvents()
            self.assertEqual(w2.insitu_editor.data_dir,
                             self.tmp / "frames")
        finally:
            w2.hide()
            w2.close()
            QApplication.processEvents()

    def test_busy_workbench_blocks_switch(self):
        self.w.insitu_btn.click()
        QApplication.processEvents()
        editor = self.w.insitu_editor
        editor._task = object()
        try:
            with mock.patch.object(preset_ops.QMessageBox,
                                   "information") as info:
                self.assertFalse(preset_ops.apply_preset(
                    self.w, presets.DEFAULT_PRESET_NAME))
                info.assert_called_once()
        finally:
            editor._task = None

    def test_combo_switch_applies_preset(self):
        presets.save_user_preset("下拉项目", {
            "geometry": "lmfp1_lab6", "second_phase_cif": "",
            "insitu_dir": str(self.tmp / "frames"),
            "insitu_mask_name": "", "phase_library": "",
        })
        preset_ops.refresh_preset_combo(self.w)   # 仍选中内置默认
        idx = self.w.preset_combo.findData("下拉项目")
        self.assertGreaterEqual(idx, 0)
        self.w.preset_combo.setCurrentIndex(idx)   # 下拉换用户预设
        QApplication.processEvents()
        self.assertEqual(self.w.preset_name, "下拉项目")
        idx = self.w.preset_combo.findData(presets.DEFAULT_PRESET_NAME)
        self.w.preset_combo.setCurrentIndex(idx)   # 再切回内置
        QApplication.processEvents()
        self.assertEqual(self.w.preset_name, presets.DEFAULT_PRESET_NAME)

    def test_save_current_with_second_phase_override(self):
        values = preset_ops.current_preset_values(self.w)
        values["second_phase_cif"] = "Si_9008565.cif"
        self.assertTrue(preset_ops.save_current_preset(
            self.w, "带第二相", values=values))
        self.assertEqual(self.w.preset["second_phase_cif"],
                         "Si_9008565.cif")
        self.assertEqual(
            presets.get_preset("带第二相")["second_phase_cif"],
            "Si_9008565.cif")

    def test_manager_marks_builtin_read_only(self):
        dialog = preset_ops.PresetManagerDialog(self.w)
        try:
            self.assertGreaterEqual(dialog.list.count(), 1)
            dialog.list.setCurrentRow(0)
            self.assertFalse(dialog.delete_btn.isEnabled())
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
