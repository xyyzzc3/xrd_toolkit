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

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403
except ModuleNotFoundError:  # 直接运行本文件
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403

from xrd_toolkit import paths, presets  # noqa: E402
from xrd_toolkit.config import CONFIGS  # noqa: E402
from xrd_toolkit.gui import preset_ops  # noqa: E402
from xrd_toolkit.gui.param_dock import helpers as dock_helpers  # noqa: E402
from xrd_toolkit.gui.state import processing as gui_processing  # noqa: E402
from xrd_toolkit.services.mask import save_recipe  # noqa: E402


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

    def test_create_window_does_not_migrate_legacy_recipes(self):
        """迁移只在 main() 跑：测试/脚本调 create_window 不许改用户文件。"""
        recipe_store.save("旧设置", {"mode": "auto"})
        self.addCleanup(recipe_store.delete, "旧设置")
        path = self.tmp / "presets_user.json"
        self.assertFalse(path.exists(), "前提：预设文件还不存在")
        w = create_window()
        try:
            self.assertFalse(path.exists(),
                             "create_window 不该写 presets_user.json")
            self.assertNotIn("旧设置", presets.preset_names())
        finally:
            w.close()

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
        # 诚实快照：没打开过工作台/没有第二相控件 → 留空，不借默认值
        self.assertEqual(values["insitu_dir"], "")
        self.assertEqual(values["phase_library"], "")
        self.assertEqual(values["second_phase_cif"], "")
        self.assertIsNone(values["processing"])
        self.assertEqual(values["mask_recipe"], "")
        values["second_phase_cif"] = "Si_9008565.cif"
        self.assertTrue(preset_ops.save_current_preset(
            self.w, "带第二相", values=values))
        self.assertEqual(self.w.preset["second_phase_cif"],
                         "Si_9008565.cif")
        self.assertEqual(
            presets.get_preset("带第二相")["second_phase_cif"],
            "Si_9008565.cif")

    def test_save_current_carries_processing_settings_only_on_apply(self):
        """预设保存当前处理设置；切换只显示，点 [套用当前预设] 才改控件。"""
        _prime_1d(self.w, ["data/fake_a.tif"])
        settings = {
            "mode": "anchor", "window_deg": 2.0,
            "anchor_method": "pchip", "clip": False,
            "anchors": [(3.0, 1.0), (4.0, 1.0)],
            "smooth_deg": 0.15, "smooth_method": "boxcar",
            "smooth_order": 3, "cut_ranges": [],
        }
        with mock.patch.object(gui_processing, "_proc_settings",
                               return_value=settings):
            self.assertTrue(preset_ops.save_current_preset(
                self.w, "带处理"))
        saved = presets.get_preset("带处理")
        self.assertEqual(saved["processing"]["mode"], "anchor")
        self.assertEqual(saved["processing"]["smooth_deg"], 0.15)
        self.assertNotIn("created", saved["processing"])
        # 只显示：处理页标签写明当前预设的设置，但控件没被改
        self.assertIn("当前预设「带处理」", self.w.preset_proc_lbl.text())
        self.assertNotEqual(
            self.w.params["背景扣除模式"].currentData(), "anchor")
        # 显式点 [套用当前预设] 才生效
        dock_helpers._apply_preset_processing(self.w)
        self.assertEqual(
            self.w.params["背景扣除模式"].currentData(), "anchor")

    def test_processing_recipe_is_dropped_when_all_off(self):
        """三项全关 = 没有配方可存，预设里保持未设置。"""
        _prime_1d(self.w, ["data/fake_a.tif"])
        settings = {"mode": "off", "window_deg": 0.0,
                    "anchor_method": "pchip", "clip": False, "anchors": [],
                    "smooth_deg": 0.0, "smooth_method": "boxcar",
                    "smooth_order": 3, "cut_ranges": []}
        with mock.patch.object(gui_processing, "_proc_settings",
                               return_value=settings):
            self.assertTrue(preset_ops.save_current_preset(
                self.w, "无配方"))
        self.assertIsNone(presets.get_preset("无配方")["processing"])

    def test_preset_mask_recipe_fallback(self):
        """预设只引用 .mask.npz；该图没自己的配方时用它兜底。"""
        mask = np.zeros((8, 8), dtype=bool)
        mask[0, :] = True
        recipe_path = self.tmp / "lmfp.mask.npz"
        save_recipe(recipe_path, mask,
                    meta={"source_image": "some_other_frame.tif"})
        presets.save_user_preset("带掩膜", {
            "geometry": "lmfp1_lab6", "second_phase_cif": "",
            "insitu_dir": "", "insitu_mask_name": "", "phase_library": "",
            "mask_recipe": str(recipe_path),
        })
        self.assertTrue(preset_ops.apply_preset(self.w, "带掩膜"))
        self.w.mask_btn.click()          # 打开掩膜工作台（创建时接住预设）
        QApplication.processEvents()
        editor = self.w.mask_editor
        self.assertIsNotNone(editor)
        self.assertEqual(editor.preset_mask_recipe, recipe_path)
        # 模拟载入一张同形状、但没有自己配方的图 → 预设配方兜底
        editor.chk_preview.setChecked(False)
        editor.image = np.zeros((8, 8))
        editor.image_path = Path("unrelated.tif")
        editor.apply_preset(self.w.preset)
        self.assertEqual(editor.mask_path, recipe_path)
        self.assertTrue(np.array_equal(editor.mask, mask))

    def test_empty_insitu_dir_stays_unset(self):
        """预设没写帧目录时，原位工作台显示未设置，不落回 LMFP 默认。"""
        presets.save_user_preset("无目录", {
            "geometry": "lmfp1_lab6", "second_phase_cif": "",
            "insitu_dir": "", "insitu_mask_name": "", "phase_library": "",
        })
        self.assertTrue(preset_ops.apply_preset(self.w, "无目录"))
        self.w.insitu_btn.click()
        QApplication.processEvents()
        editor = self.w.insitu_editor
        self.assertFalse(editor._data_dir_set)
        self.assertEqual(editor.frames, [])
        self.assertEqual(editor.data_label.text(), "（未选择帧目录）")

    def test_manager_marks_builtin_read_only(self):
        dialog = preset_ops.PresetManagerDialog(self.w)
        try:
            self.assertGreaterEqual(dialog.list.count(), 1)
            dialog.list.setCurrentRow(0)
            self.assertFalse(dialog.delete_btn.isEnabled())
            self.assertFalse(dialog.update_btn.isEnabled())
        finally:
            dialog.close()

    def test_geometry_drift_shows_current_item_and_recovers(self):
        """手动换了几何 → 预设下拉显示「（当前设置）」→ 点回预设可恢复。"""
        CONFIGS["_test_drift_config"] = {
            "label": "漂移测试",
            "geometry": dict(pixel_size_m=150e-6, wavelength_m=0.15e-10,
                             dist_m=1.2),
            "beam_center": (100.0, 100.0),
        }
        self.addCleanup(CONFIGS.pop, "_test_drift_config", None)
        combo = self.w.config_combo
        combo.addItem("_test_drift_config", "_test_drift_config")
        combo.setCurrentIndex(combo.count() - 1)
        QApplication.processEvents()

        self.assertEqual(self.w.config_name, "_test_drift_config")
        self.assertEqual(self.w.preset_name, presets.DEFAULT_PRESET_NAME)
        self.assertEqual(self.w.preset_combo.currentText(),
                         preset_ops.CURRENT_PRESET_TEXT)

        # 管理弹窗与主下拉一致：顶部也有「（当前设置）」，默认为它，
        # 显示实际五项值且三个动作按钮都不可用
        dialog = preset_ops.PresetManagerDialog(self.w)
        try:
            self.assertEqual(dialog.list.currentRow(), 0)
            self.assertTrue(dialog.list.item(0).text().startswith(
                preset_ops.CURRENT_PRESET_TEXT))
            self.assertIn("未保存为预设", dialog.summary.text())
            self.assertIn("_test_drift_config", dialog.summary.text())
            # 没打开过工作台 / 没有第二相控件：留空，不借用预设默认值
            self.assertIn("相库目录：（未设置）", dialog.summary.text())
            self.assertIn("原位帧目录：（未设置）", dialog.summary.text())
            self.assertIn("第二相 CIF：（无）", dialog.summary.text())
            self.assertFalse(dialog.apply_btn.isEnabled())
            self.assertFalse(dialog.update_btn.isEnabled())
            self.assertFalse(dialog.delete_btn.isEnabled())
        finally:
            dialog.close()

        idx = self.w.preset_combo.findData(presets.DEFAULT_PRESET_NAME)
        self.w.preset_combo.setCurrentIndex(idx)   # 点回真实预设
        QApplication.processEvents()
        self.assertEqual(self.w.config_name, "lmfp1_lab6")
        self.assertEqual(self.w.preset_combo.currentText(),
                         presets.DEFAULT_PRESET_NAME)

    def test_hint_geometry_outside_preset(self):
        self.w.config_name = "_not_in_preset"
        with mock.patch.object(preset_ops, "_log") as log:
            self.assertTrue(preset_ops.hint_geometry_outside_preset(self.w))
        self.assertIn("内置预设不可改", log.call_args.args[1])

        presets.save_user_preset("用户项目", {
            "geometry": "lmfp1_lab6", "second_phase_cif": "",
            "insitu_dir": "", "insitu_mask_name": "", "phase_library": "",
        })
        preset_ops.apply_preset(self.w, "用户项目")
        self.w.config_name = "_not_in_preset"
        with mock.patch.object(preset_ops, "_log") as log:
            self.assertTrue(preset_ops.hint_geometry_outside_preset(self.w))
        self.assertIn("用当前设置更新", log.call_args.args[1])

        self.w.config_name = "lmfp1_lab6"
        self.assertFalse(preset_ops.hint_geometry_outside_preset(self.w))

    def test_manager_update_uses_current_values(self):
        presets.save_user_preset("可更新", {
            "geometry": "lmfp1_lab6", "second_phase_cif": "Al_metal.cif",
            "insitu_dir": str(self.tmp / "old"), "insitu_mask_name": "",
            "phase_library": "",
        })
        preset_ops.apply_preset(self.w, "可更新")

        library = self.tmp / "newlib"
        library.mkdir()
        frames = self.tmp / "newframes"
        frames.mkdir()
        self.w.phase_btn.click()
        QApplication.processEvents()
        self.w.phase_editor.lib_dir = library
        self.w.insitu_btn.click()
        QApplication.processEvents()
        self.w.insitu_editor.data_dir = frames
        self.w.insitu_editor.mask_path = None

        dialog = preset_ops.PresetManagerDialog(self.w)
        try:
            row = next(i for i in range(dialog.list.count())
                       if dialog.list.item(i).data(Qt.UserRole) == "可更新")
            dialog.list.setCurrentRow(row)
            self.assertTrue(dialog.update_btn.isEnabled())
            with mock.patch.object(
                    preset_ops.QMessageBox, "question",
                    return_value=preset_ops.QMessageBox.Yes):
                dialog._update()
        finally:
            dialog.close()

        updated = presets.get_preset("可更新")
        self.assertEqual(updated["phase_library"], str(library))
        self.assertEqual(updated["insitu_dir"], str(frames))
        self.assertEqual(updated["second_phase_cif"], "Al_metal.cif")


if __name__ == "__main__":
    unittest.main()
