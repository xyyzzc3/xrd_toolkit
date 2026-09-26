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

from PySide6.QtCore import Qt
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
        """启动即应用默认条目：window.config = 该条目的完整几何。

        2026-09-26 起参数坞里那三行只读字段（像素/波长/距离）撤了，
        读数改走坞顶"几何配置"那一行的悬停提示——测试跟着看提示文本。
        """
        w = create_window()
        try:
            geom = CONFIGS[DEFAULT_CONFIG]["geometry"]
            self.assertEqual(w.config, CONFIGS[DEFAULT_CONFIG])
            tip = w.geom_row.toolTip()
            self.assertIn(f"{geom['dist_m'] * 1e3:.2f} mm", tip)
            self.assertIn(f"{geom['wavelength_m'] * 1e10:.4f}", tip)
            self.assertIn(f"{geom['pixel_size_m'] * 1e6:.1f}", tip)
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
            w.config_combo.setItemData(w.config_combo.count() - 1,
                                       CONFIGS[self.FAKE_KEY]["label"],
                                       Qt.ToolTipRole)
            w.config_combo.setCurrentIndex(w.config_combo.count() - 1)
            self.assertEqual(w.config_name, self.FAKE_KEY)
            # 几何随条目换：window.config = 新条目的完整几何，坞顶那一行
            # 的悬停提示跟着换成新读数
            self.assertEqual(w.config, CONFIGS[self.FAKE_KEY])
            tip = w.geom_row.toolTip()
            self.assertIn("1200.00 mm", tip)
            self.assertIn("0.1500", tip)
            self.assertIn("150.0", tip)
            self.assertEqual(w.config["beam_center"], (100.0, 100.0))
            # 下拉框只放短 key，完整备注挂在条目的悬停提示上（说明行
            # 已删除——与用户讨论定稿，见 test_gui_integration 的
            # test_config_combo_carries_full_label_as_tooltip）
            self.assertEqual(
                w.config_combo.currentText(), self.FAKE_KEY)
            idx = w.config_combo.currentIndex()
            self.assertEqual(
                w.config_combo.itemData(idx, Qt.ToolTipRole),
                CONFIGS[self.FAKE_KEY]["label"])
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
            w.params["2θ 下限 (°)"].setValue(5.0)
            w.params["输出点数"].setValue(5000)
            w.findChild(QPushButton, "reset_data_btn").click()
            # 区间/点数回到初值；几何仍 = 当前配置条目（[恢复默认] 走的
            # 是 _apply_config，几何字段本身已不在面板上，读数看提示行）
            self.assertEqual(w.params["2θ 下限 (°)"].value(), 1.0)
            self.assertEqual(w.params["2θ 上限 (°)"].value(), 8.0)
            self.assertEqual(w.params["输出点数"].value(), 3000)
            self.assertIn(f"{cfg['dist_m'] * 1e3:.2f} mm",
                          w.geom_row.toolTip())
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
