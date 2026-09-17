"""GUI 配置选择器的单元测试（unittest，环境无 pytest）。

不弹真实窗口：QT_QPA_PLATFORM=offscreen 让 Qt 在内存里画（不显示），
create_window() 与事件循环分离，测试里只建窗口、不进 app.exec()。
验证：
  - 下拉框条目 = config.py CONFIGS 的全部 key，默认选中 DEFAULT_CONFIG；
  - 启动即把默认条目的标定几何填进参数坞（距离 1595.8 mm 等）；
  - 切换条目触发参数同步 + window.config 更新（含 beam_center）。

运行：python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication, QPushButton

from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG
from xrd_toolkit.gui.app import create_window

# QApplication 是进程级单例：模块加载时建一次，所有测试共用
_app = QApplication.instance() or QApplication([])


class TestConfigSelector(unittest.TestCase):
    """下拉框内容与默认选中（不修改注册表，只读检查）。"""

    def test_entries_match_registry(self):
        w = create_window()
        try:
            combo = w.config_combo
            self.assertEqual(combo.count(), len(CONFIGS))
            keys = [combo.itemData(i) for i in range(combo.count())]
            self.assertEqual(set(keys), set(CONFIGS))
        finally:
            w.close()

    def test_default_selected(self):
        w = create_window()
        try:
            self.assertEqual(w.config_name, DEFAULT_CONFIG)
        finally:
            w.close()

    def test_startup_applies_default_geometry(self):
        w = create_window()
        try:
            geom = CONFIGS[DEFAULT_CONFIG]["geometry"]
            # 启动即应用：参数坞初值 = 注册表标定值（距离 1595.8 mm）
            self.assertAlmostEqual(
                w.params["初始距离 (mm)"].value(), geom["dist_m"] * 1e3,
                places=3)
            self.assertAlmostEqual(
                w.params["波长 (Å)"].value(), geom["wavelength_m"] * 1e10,
                places=4)
            self.assertAlmostEqual(
                w.params["像素尺寸 (µm)"].value(),
                geom["pixel_size_m"] * 1e6, places=3)
        finally:
            w.close()


class TestConfigSwitch(unittest.TestCase):
    """切换条目：信号触发 _apply_config，参数与 window.config 同步。

    用临时假条目测切换路径（注册表在测试进程内加一条，不写回文件；
    测完恢复原状，避免影响同进程的其他测试）。
    """

    FAKE_KEY = "_test_fake_config"

    def setUp(self):
        self._saved = CONFIGS.get(self.FAKE_KEY, None)
        CONFIGS[self.FAKE_KEY] = {
            "label": "测试条目",
            "geometry": dict(pixel_size_m=150e-6, wavelength_m=0.15e-10,
                             dist_m=1.2),
            "beam_center": (100.0, 100.0),
        }

    def tearDown(self):
        if self._saved is None:
            CONFIGS.pop(self.FAKE_KEY, None)
        else:
            CONFIGS[self.FAKE_KEY] = self._saved

    def test_switch_applies_new_geometry(self):
        w = create_window()
        try:
            w.config_combo.addItem(self.FAKE_KEY, self.FAKE_KEY)
            w.config_combo.setCurrentIndex(w.config_combo.count() - 1)
            self.assertEqual(w.config_name, self.FAKE_KEY)
            self.assertAlmostEqual(
                w.params["初始距离 (mm)"].value(), 1200.0, places=3)
            self.assertAlmostEqual(
                w.params["波长 (Å)"].value(), 0.15, places=4)
            self.assertAlmostEqual(
                w.params["像素尺寸 (µm)"].value(), 150.0, places=3)
            self.assertEqual(w.config["beam_center"], (100.0, 100.0))
            # 下拉框只放短 key，完整备注在下方说明行随选择更新
            self.assertEqual(
                w.config_combo.currentText(), self.FAKE_KEY)
            self.assertEqual(w.config_label.text(), "测试条目")
        finally:
            w.close()


class TestImageParams(unittest.TestCase):
    """图像参数组：只看图不参与计算的那几个控件。"""

    def test_auto_contrast_default_and_locks_boxes(self):
        w = create_window()
        try:
            # 默认自动对比度开 → 两个输入框置灰（对应 CLI 不给
            # --vmin/--vmax 时的自动分位数行为）
            self.assertTrue(w.params["自动对比度"].isChecked())
            self.assertFalse(w.params["对比度下限"].isEnabled())
            self.assertFalse(w.params["对比度上限"].isEnabled())
            # 取消勾选 → 输入框放开，可手填
            w.params["自动对比度"].setChecked(False)
            self.assertTrue(w.params["对比度下限"].isEnabled())
            self.assertTrue(w.params["对比度上限"].isEnabled())
            # 重新勾回 → 再次置灰
            w.params["自动对比度"].setChecked(True)
            self.assertFalse(w.params["对比度下限"].isEnabled())
        finally:
            w.close()

    def test_profile_angle_default_horizontal(self):
        w = create_window()
        try:
            # 默认 0° = 水平剖面线（view_diffraction --angle 默认值）
            self.assertEqual(w.params["剖面角度 (°)"].value(), 0.0)
            w.params["剖面角度 (°)"].setValue(90.0)   # 输入范围不报错
            self.assertEqual(w.params["剖面角度 (°)"].value(), 90.0)
        finally:
            w.close()


class TestResetButtons(unittest.TestCase):
    """两个参数组各自的 [恢复默认]：只复位参数，不触发计算。"""

    def test_reset_data_params(self):
        w = create_window()
        try:
            cfg = w.config["geometry"]
            w.params["初始距离 (mm)"].setValue(1700.0)
            w.params["2θ 下限 (°)"].setValue(5.0)
            w.params["输出点数"].setValue(5000)
            w.findChild(QPushButton, "reset_data_btn").click()
            # 几何回到当前配置条目，区间/点数回到初值
            self.assertAlmostEqual(
                w.params["初始距离 (mm)"].value(), cfg["dist_m"] * 1e3,
                places=3)
            self.assertEqual(w.params["2θ 下限 (°)"].value(), 1.0)
            self.assertEqual(w.params["2θ 上限 (°)"].value(), 8.0)
            self.assertEqual(w.params["输出点数"].value(), 3000)
            self.assertIn("数据参数已恢复默认", w.log_text.toPlainText())
        finally:
            w.close()

    def test_reset_image_params(self):
        w = create_window()
        try:
            w.params["自动对比度"].setChecked(False)
            w.params["对比度下限"].setValue(500.0)
            w.params["剖面角度 (°)"].setValue(45.0)
            w.findChild(QPushButton, "reset_image_btn").click()
            self.assertTrue(w.params["自动对比度"].isChecked())
            self.assertEqual(w.params["对比度下限"].value(), 1.0)
            self.assertEqual(w.params["对比度上限"].value(), 100000.0)
            self.assertEqual(w.params["剖面角度 (°)"].value(), 0.0)
            # 自动对比度回到开 → 两个输入框重新置灰
            self.assertFalse(w.params["对比度下限"].isEnabled())
            self.assertIn("图像参数已恢复默认", w.log_text.toPlainText())
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
