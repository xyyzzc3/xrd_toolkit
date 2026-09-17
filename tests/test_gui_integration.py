"""GUI「文件 → 视图 → 出图」交互模型的集成测试（unittest）。

用 mock 替换 _compute_integration（真积分慢且依赖 data/ 数据），
验证接线本身：
  - 选文件只登记不算（点选本身轻快，不触发任何计算）；
  - 勾选视图按钮 = 打开面板并计算该视图 → 出图；
  - 计算完成的视图自动成为"编辑对象"，[应用] 重算它；
  - 点图面板切焦点（事件过滤器 _FocusMarker）；
  - 过期结果丢弃：A 没算完就选了 B，A 的结果不画；
  - _collect_geometry：面板输入覆盖配置条目值。

等待方式与 test_gui_tasks.py 相同：回调 + processEvents 轮询
（QSignalSpy.wait 不处理跨线程投递，见该文件说明）。

运行：python -m unittest discover -s tests -v
"""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from xrd_toolkit.gui import app as gui_app
from xrd_toolkit.gui.app import create_window

_app = QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout_ms=5000):
    """轮询处理事件直到条件成立（后台结果靠事件循环排队投递）。"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _fake_compute(path_str, geom, npt):
    """假积分：A 文件慢 0.2 s（模拟真计算），其余即时返回。"""
    if path_str.endswith("fake_a.tif"):
        time.sleep(0.2)
    return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])


def _open_view(w, name):
    """勾选视图按钮（模拟用户点击工具栏开关）。"""
    w.view_buttons[name].setChecked(True)


class TestSelectionOnly(unittest.TestCase):
    """选文件 ≠ 计算：点选只是登记当前文件。"""

    def test_selecting_file_does_not_run(self):
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute) as fake:
                w.add_files(["data/fake_b.tif"])
                time.sleep(0.6)          # 给足"防抖级"时间
                QApplication.processEvents()
                self.assertEqual(fake.call_count, 0,
                                 "选文件不应触发任何计算")
            self.assertEqual(w.file_label.text(), "fake_b.tif")
            self.assertIn("已选中 fake_b.tif", w.log_text.toPlainText())
        finally:
            w.close()


class TestViewToggleRuns(unittest.TestCase):
    """勾选视图按钮 = 打开面板并计算该视图。"""

    def test_1d_toggle_computes_and_draws(self):
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                drawn = _wait_until(
                    lambda: hasattr(w, "axes_1d")
                    and len(w.axes_1d.lines) > 0)
                self.assertTrue(drawn, "勾选 1D 后应画出曲线")
            # 面板自动打开；标题带文件名；日志有完成记录
            self.assertFalse(w.plot_docks["1D"].isHidden())
            self.assertTrue(w.axes_1d.get_title().startswith("fake_b.tif"))
            self.assertIn("积分完成：fake_b.tif", w.log_text.toPlainText())
            # x 轴范围跟随参数坞的 2θ 上下限
            self.assertEqual(w.axes_1d.get_xlim(), (1.0, 8.0))
            # 计算完成的视图自动成为编辑对象
            self.assertEqual(w.focus_view, "1D")
            self.assertIn("1D", w.focus_label.text())
        finally:
            w.close()

    def test_unwired_view_logs_placeholder(self):
        w = create_window()
        try:
            w.add_files(["data/fake_b.tif"])
            _open_view(w, "2D")
            self.assertIn("2D 视图尚未接线", w.log_text.toPlainText())
            self.assertFalse(w.plot_docks["2D"].isHidden())
        finally:
            w.close()


class TestApplyAndFocus(unittest.TestCase):
    """[应用] 重算焦点视图；点图面板切焦点。"""

    def test_apply_reruns_focused_view(self):
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute) as fake:
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: fake.call_count >= 1))
                self.assertTrue(_wait_until(
                    lambda: w.focus_view == "1D"))
                w.findChild(QPushButton, "apply_btn").click()
                self.assertTrue(_wait_until(lambda: fake.call_count >= 2))
        finally:
            w.close()

    def test_apply_without_focus_hints(self):
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute) as fake:
                w.add_files(["data/fake_b.tif"])
                w.findChild(QPushButton, "apply_btn").click()
                self.assertEqual(fake.call_count, 0)
                self.assertIn("先点击要更新的图面板",
                              w.log_text.toPlainText())
        finally:
            w.close()

    def test_clicking_placeholder_focuses_view(self):
        w = create_window()
        try:
            _open_view(w, "2D")
            self.assertIsNone(w.focus_view)
            # 模拟点击面板内容 → 事件过滤器切焦点
            QTest.mouseClick(w.plot_docks["2D"].widget(), Qt.LeftButton)
            self.assertEqual(w.focus_view, "2D")
            self.assertIn("2D", w.focus_label.text())
        finally:
            w.close()

    def test_stale_result_discarded(self):
        """A 没算完就选 B：A 的结果回来时当前文件是 B → 丢弃不画。"""
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute) as fake:
                w.add_files(["data/fake_a.tif"])        # A 开跑（慢 0.2 s）
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: fake.call_count >= 1))
                w.add_files(["data/fake_b.tif"])        # 切到 B（不算）
                # 点 1D 图切焦点（A 还没算完，焦点尚未自动设定）
                QTest.mouseClick(w.canvas_1d, Qt.LeftButton)
                w.findChild(QPushButton, "apply_btn").click()   # 算 B
                drawn = _wait_until(
                    lambda: w.axes_1d.get_title().startswith("fake_b.tif"))
                self.assertTrue(drawn, "最终应画出 B 的曲线")
                ignored = _wait_until(
                    lambda: "已忽略 fake_a.tif 的过期结果"
                    in w.log_text.toPlainText())
                self.assertTrue(ignored, "A 的过期结果应被丢弃")
            self.assertFalse(w.axes_1d.get_title().startswith("fake_a.tif"))
        finally:
            w.close()


class TestCollectGeometry(unittest.TestCase):
    """参数面板 → 积分几何：输入覆盖配置值，PONI/倾斜取配置条目。"""

    def test_overrides_and_config_fallback(self):
        w = create_window()
        try:
            cfg = w.config["geometry"]
            w.params["初始距离 (mm)"].setValue(1700.0)
            geom = gui_app._collect_geometry(w)
            self.assertAlmostEqual(geom["dist_m"], 1.7, places=9)
            self.assertAlmostEqual(geom["pixel_size_m"],
                                   cfg["pixel_size_m"])
            self.assertEqual(geom["poni1_m"], cfg["poni1_m"])
            self.assertEqual(geom["rot1_deg"], cfg["rot1_deg"])
            w.params["波长 (Å)"].setValue(0.15)
            self.assertAlmostEqual(
                gui_app._collect_geometry(w)["wavelength_m"], 0.15e-10)
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
