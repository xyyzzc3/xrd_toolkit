"""GUI「文件 → 视图 → 出图」交互模型的集成测试（unittest）。

用 mock 替换 _compute_integration（真积分慢且依赖 data/ 数据），
验证接线本身：
  - 选文件只登记不算（点选本身轻快，不触发任何计算）；
  - 点击作图按钮 = 为当前文件开面板并计算该视图 → 出图（点一次
    算一次，按钮是纯动作不是开关）；
  - 面板按「视图 + 文件」成对创建：同一视图可同时开多张不同文件
    的图，标题 = 视图_文件名（如 1D_fake_a.tif）；
  - 计算完成的图自动成为"编辑对象"，[应用] 重算它（焦点指向
    具体面板，不是视图名）；
  - 点图面板切焦点（事件过滤器 _FocusMarker）；
  - 同面板连点两次 = 最新任务说了算，旧结果晚到被丢弃；
  - 文件列表以对号为唯一选择表达（点行 = 切对号，作图用对号文件）；
  - [保存] 弹窗勾选要保存的图 → 逐个选文件名存 PNG；关窗时若有
    未保存的图会弹窗询问（保存 / 不保存 / 取消）；
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

from types import SimpleNamespace

import numpy as np
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from PySide6.QtCore import QEvent, QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QLabel, QPushButton, QScrollArea,
    QSplitter, QVBoxLayout)

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
    """点击工具栏作图按钮（模拟用户点 [1D] 这类按钮）。"""
    w.view_buttons[name].click()


def _dock(w, view, path_str):
    """按面板键取坞（键 = f"{视图}|{路径}"，路径与 add_files 入参一致）。"""
    return w.plot_docks[f"{view}|{path_str}"]


def _axes(w, view, path_str):
    """取某 1D 面板自己的坐标轴。"""
    return _dock(w, view, path_str).widget().axes_1d


def _drop_event(w, paths, kind="drop"):
    """构造拖放事件并直接调用顶层窗口的拖放处理。

    两个环境怪癖：QDragEnterEvent 在本 PySide6 构建里只有拷贝构造
    （改用 QDropEvent + type=DragEnter）；拖放事件经 QApplication
    投递会被路由到坐标下的子控件而不是顶层窗口，直接调用处理
    函数才能测到顶层逻辑。真实冒泡由 Qt 拖放管理器保证：子控件
    不接 → 逐级上抛到接受者（顶层窗口）。
    """
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    etype = QEvent.Type.Drop if kind == "drop" else QEvent.Type.DragEnter
    ev = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime,
                    Qt.LeftButton, Qt.NoModifier, etype)
    if kind == "drop":
        w.dropEvent(ev)
    else:
        w.dragEnterEvent(ev)
    return ev


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
            self.assertIn("已添加 1 个文件", w.log_text.toPlainText())
        finally:
            w.close()


class TestViewButtonRuns(unittest.TestCase):
    """点击作图按钮 = 为当前文件开面板并计算（纯动作，点一次算一次）。"""

    def test_1d_click_computes_and_draws(self):
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                drawn = _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
                self.assertTrue(drawn, "点 1D 后应画出曲线")
            dock = _dock(w, "1D", "data/fake_b.tif")
            # 面板标题 = 视图_文件名，多张图一眼分清
            self.assertEqual(dock.windowTitle(), "1D_fake_b.tif")
            self.assertFalse(dock.isHidden())
            self.assertTrue(
                _axes(w, "1D", "data/fake_b.tif").get_title()
                .startswith("fake_b.tif"))
            self.assertIn("打开1D面板：fake_b.tif", w.log_text.toPlainText())
            self.assertIn("积分完成：fake_b.tif", w.log_text.toPlainText())
            # x 轴范围跟随参数坞的 2θ 上下限
            self.assertEqual(_axes(w, "1D", "data/fake_b.tif").get_xlim(),
                             (1.0, 8.0))
            # 计算完成的图自动成为编辑对象（指向具体面板）
            self.assertEqual(w.focus_panel, "1D|data/fake_b.tif")
            self.assertIn("1D_fake_b.tif", w.focus_label.text())
        finally:
            w.close()

    def test_view_button_is_action_not_toggle(self):
        """点两次 = 算两次。旧版勾选按钮的第二次点击会关掉面板、
        不算图，用户误以为"点击画图画不了"——回归保护。"""
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute) as fake:
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: fake.call_count >= 1))
                _open_view(w, "1D")   # 第二次点击：必须重新计算
                self.assertTrue(_wait_until(lambda: fake.call_count >= 2))
                # 同一文件重复点 = 刷新同一张面板，不是开新面板
                self.assertEqual(len(w.plot_docks), 1)
                self.assertFalse(_dock(w, "1D", "data/fake_b.tif").isHidden(),
                                 "重复点击不应关掉面板")
        finally:
            w.close()

    def test_multi_select_batch_plots_all(self):
        """多选 = 批量：勾两个文件点一次 1D → 两张图都出（旧模型
        会把慢的先算完的当过期丢弃）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])   # 默认全勾
                self.assertEqual(w.file_label.text(), "已选 2 个文件")
                _open_view(w, "1D")   # 批量：点一下，两个文件各开一张
                drawn_b = _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
                drawn_a = _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0)
                self.assertTrue(drawn_b, "B 的曲线应画出")
                self.assertTrue(drawn_a, "A 的曲线也应画出（各自的面板）")
            self.assertEqual(len(w.plot_docks), 2)
            dock_a = _dock(w, "1D", "data/fake_a.tif")
            dock_b = _dock(w, "1D", "data/fake_b.tif")
            self.assertEqual(dock_a.windowTitle(), "1D_fake_a.tif")
            self.assertEqual(dock_b.windowTitle(), "1D_fake_b.tif")
            self.assertFalse(dock_a.isHidden())
            self.assertFalse(dock_b.isHidden())
            # 各自的图挂各自的文件标题；没有结果被当过期丢弃；
            # 最后完成的图成为编辑对象
            self.assertTrue(
                _axes(w, "1D", "data/fake_a.tif").get_title()
                .startswith("fake_a.tif"))
            self.assertNotIn("已忽略", w.log_text.toPlainText())
            self.assertEqual(w.focus_panel, "1D|data/fake_a.tif")
        finally:
            w.close()

    def test_unwired_view_logs_placeholder(self):
        w = create_window()
        try:
            w.add_files(["data/fake_b.tif"])
            _open_view(w, "2D")
            self.assertIn("2D 视图尚未接线", w.log_text.toPlainText())
            dock = _dock(w, "2D", "data/fake_b.tif")
            self.assertEqual(dock.windowTitle(), "2D_fake_b.tif")
            self.assertFalse(dock.isHidden())
        finally:
            w.close()


class TestApplyAndFocus(unittest.TestCase):
    """[应用] 重算焦点面板；点图面板切焦点。"""

    def test_apply_reruns_focused_view(self):
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute) as fake:
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: fake.call_count >= 1))
                self.assertTrue(_wait_until(
                    lambda: w.focus_panel == "1D|data/fake_b.tif"))
                w.findChild(QPushButton, "apply_btn").click()
                self.assertTrue(_wait_until(lambda: fake.call_count >= 2))
                self.assertIn("[应用] 重算编辑对象：1D_fake_b.tif",
                              w.log_text.toPlainText())
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
            w.add_files(["data/fake_b.tif"])
            _open_view(w, "2D")
            self.assertIsNone(w.focus_panel)
            # 模拟点击面板内容 → 事件过滤器切焦点（焦点=具体面板）
            QTest.mouseClick(_dock(w, "2D", "data/fake_b.tif").widget(),
                             Qt.LeftButton)
            self.assertEqual(w.focus_panel, "2D|data/fake_b.tif")
            self.assertIn("2D_fake_b.tif", w.focus_label.text())
        finally:
            w.close()

    def test_same_panel_latest_task_wins(self):
        """同面板连点两次：先开的慢任务晚到 → 丢弃，不得覆盖新图。"""
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute) as fake:
                w.add_files(["data/fake_a.tif"])   # A 慢 0.2 s
                _open_view(w, "1D")   # 任务 1（慢）
                _open_view(w, "1D")   # 任务 2（即时）= 最新任务
                drawn = _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) == 1)
                ignored = _wait_until(
                    lambda: "已忽略 fake_a.tif 的过期结果"
                    in w.log_text.toPlainText())
                self.assertTrue(drawn, "最新任务的结果应画出")
                self.assertTrue(ignored, "旧任务的结果应被丢弃")
            self.assertEqual(fake.call_count, 2)
            # 旧结果不得覆盖：曲线仍只有一条
            self.assertEqual(len(_axes(w, "1D", "data/fake_a.tif").lines), 1)
        finally:
            w.close()


class TestFileCheckSelection(unittest.TestCase):
    """文件列表：对号是唯一的选择表达（点行 = 加选不取消；取消对号
    只能点对号方块；背景高亮跟随对号集合）。"""

    def test_add_files_checks_all_added(self):
        """一批选入/拖入的文件默认全部勾上（选中）。"""
        w = create_window()
        try:
            w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
            self.assertEqual(w.file_list.item(0).checkState(), Qt.Checked)
            self.assertEqual(w.file_list.item(1).checkState(), Qt.Checked)
            self.assertIs(w.file_list.currentItem(), w.file_list.item(1))
            self.assertEqual(w.file_label.text(), "已选 2 个文件")
        finally:
            w.close()

    def test_row_click_adds_check_keeps_others(self):
        """点行 = 加选：勾上这一行，其他对号不动。"""
        w = create_window()
        try:
            w.add_files(["data/fake_a.tif", "data/fake_b.tif"])   # 默认全勾
            # 先点 A 的方块取消 A → 只剩 B 勾着
            item_a = w.file_list.item(0)
            w._press_item, w._press_state = item_a, Qt.Checked
            item_a.setCheckState(Qt.Unchecked)
            w.file_list.itemClicked.emit(item_a)
            self.assertEqual(item_a.checkState(), Qt.Unchecked)
            # 再点 A 行体 → 勾回 A，B 的对号不动（Qt 原生：按下时
            # 事件过滤器记录 A 为未勾、并把当前项移到 A；手动补上）
            w._press_item, w._press_state = item_a, Qt.Unchecked
            w.file_list.setCurrentItem(item_a)
            w.file_list.itemClicked.emit(item_a)
            self.assertEqual(item_a.checkState(), Qt.Checked)
            self.assertEqual(w.file_list.item(1).checkState(), Qt.Checked)
            self.assertIs(w.file_list.currentItem(), item_a)
            self.assertEqual(w.file_label.text(), "已选 2 个文件")
        finally:
            w.close()

    def test_row_click_checked_item_is_noop(self):
        """已勾的行再点 = 没反应（点行不会取消对号）。"""
        w = create_window()
        try:
            w.add_files(["data/fake_b.tif"])   # 最后加的文件已勾
            w.file_list.itemClicked.emit(w.file_list.item(0))   # 点它
            self.assertEqual(w.file_list.item(0).checkState(), Qt.Checked)
            self.assertEqual(w.file_label.text(), "fake_b.tif")
        finally:
            w.close()

    def test_square_click_auto_toggle_honored(self):
        """点对号方块：Qt 已自动切换（按下时记着旧状态）→ 取消对号
        生效——这是取消对号的唯一途径。"""
        w = create_window()
        try:
            w.add_files(["data/fake_b.tif"])
            item = w.file_list.item(0)
            w._press_item, w._press_state = item, Qt.Checked   # 按下时勾着
            item.setCheckState(Qt.Unchecked)   # Qt 在弹起时自动取消
            w.file_list.itemClicked.emit(item)
            self.assertEqual(item.checkState(), Qt.Unchecked)
            self.assertEqual(w.file_label.text(), "未打开文件")
        finally:
            w.close()

    def test_plot_uses_checked_file(self):
        """以对号为准：取消 B 的对号（点方块），作图只用对号文件 A。"""
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])   # 默认全勾
                # 点 B 的对号方块取消 → 只剩 A 勾着
                item_b = w.file_list.item(1)
                w._press_item, w._press_state = item_b, Qt.Checked
                item_b.setCheckState(Qt.Unchecked)
                w.file_list.itemClicked.emit(item_b)
                self.assertEqual(w.file_list.item(0).checkState(), Qt.Checked)
                self.assertEqual(w.file_list.item(1).checkState(),
                                 Qt.Unchecked)
                _open_view(w, "1D")
                drawn = _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0)
                self.assertTrue(drawn, "应画对号文件 A 的图")
            self.assertEqual(len(w.plot_docks), 1)
        finally:
            w.close()

    def test_delete_removes_all_checked(self):
        """删除 = 批量：所有对号文件一起移除。"""
        w = create_window()
        try:
            w.add_files(["data/fake_a.tif", "data/fake_b.tif"])   # 默认全勾
            w.findChild(QPushButton, "delete_btn").click()
            self.assertEqual(w.file_list.count(), 0)
            self.assertEqual(w.file_label.text(), "未打开文件")
            self.assertIn("已删除 2 个文件", w.log_text.toPlainText())
        finally:
            w.close()


def _draw_one_1d(w):
    """画一张 fake_b 的 1D 图（mock 积分），返回面板。"""
    with mock.patch.object(gui_app, "_compute_integration",
                           side_effect=_fake_compute):
        w.add_files(["data/fake_b.tif"])
        _open_view(w, "1D")
        assert _wait_until(
            lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
    return _dock(w, "1D", "data/fake_b.tif")


class TestSaveFigures(unittest.TestCase):
    """[保存]：弹窗勾选要保存的图 → 逐个选文件名存 PNG（主动操作）。"""

    def test_save_with_no_figures_logs(self):
        w = create_window()
        try:
            w.add_files(["data/fake_b.tif"])   # 只选中没出图
            w.findChild(QPushButton, "save_btn").click()
            self.assertIn("没有已输出的图可保存", w.log_text.toPlainText())
        finally:
            w.close()

    def test_save_flow_saves_chosen_panel(self):
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            fig = dock.widget().figure
            with mock.patch.object(gui_app, "_choose_panels",
                                   return_value=[dock]), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("/tmp/out", "PNG 图片 (*.png)")) as dlg, \
                 mock.patch.object(fig, "savefig") as savefig:
                w.findChild(QPushButton, "save_btn").click()
                self.assertTrue(dlg.called)
                savefig.assert_called_once_with("/tmp/out.png")   # 自动补 .png
                self.assertTrue(dock.figure_saved)
                self.assertIn("已保存 1D_fake_b.tif → /tmp/out.png",
                              w.log_text.toPlainText())
        finally:
            w.close()

    def test_cancel_choice_saves_nothing(self):
        w = create_window()
        try:
            _draw_one_1d(w)
            with mock.patch.object(gui_app, "_choose_panels",
                                   return_value=None), \
                 mock.patch.object(QFileDialog, "getSaveFileName") as dlg:
                self.assertFalse(gui_app._save_figures(w))
                self.assertFalse(dlg.called)
                self.assertIn("已取消保存", w.log_text.toPlainText())
        finally:
            w.close()

    def test_ok_with_none_checked_is_not_cancel(self):
        """确定但一张都没勾 → 提示"没有勾选"，不是"取消"（修前混为一谈）。"""
        w = create_window()
        try:
            _draw_one_1d(w)
            with mock.patch.object(gui_app, "_choose_panels",
                                   return_value=[]), \
                 mock.patch.object(QFileDialog, "getSaveFileName") as dlg:
                self.assertFalse(gui_app._save_figures(w))
                self.assertFalse(dlg.called)
                self.assertIn("没有勾选要保存的图",
                              w.log_text.toPlainText())
        finally:
            w.close()

    def test_skip_one_figure_returns_false(self):
        """逐个存盘时跳过一张 → 返回 False（关窗流程应留在程序里，
        不能当成"已全部保存"静默关掉）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines)
                    > 0 and len(_axes(w, "1D", "data/fake_b.tif").lines)
                    > 0))
            d1 = _dock(w, "1D", "data/fake_a.tif")
            d2 = _dock(w, "1D", "data/fake_b.tif")
            with mock.patch.object(gui_app, "_choose_panels",
                                   return_value=[d1, d2]), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   side_effect=[("/tmp/a", ""),
                                                ("", "")]) as dlg, \
                 mock.patch.object(d1.widget().figure, "savefig"):
                self.assertFalse(gui_app._save_figures(w))
            self.assertEqual(dlg.call_count, 2)
            log = w.log_text.toPlainText()
            self.assertIn("已跳过保存 1D_fake_b.tif", log)
            self.assertIn("保存完成：1 张图", log)
        finally:
            w.close()

    def test_savefig_failure_logs_and_returns_false(self):
        """目标路径写不进去（如目录不存在）→ 报错日志 + 返回 False，
        不崩、不装成保存成功。"""
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            with mock.patch.object(gui_app, "_choose_panels",
                                   return_value=[dock]), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("/no/such/dir/out.png", "")), \
                 mock.patch.object(dock.widget().figure, "savefig",
                                   side_effect=OSError("磁盘写不进")):
                self.assertFalse(gui_app._save_figures(w))
            log = w.log_text.toPlainText()
            self.assertIn("保存失败 1D_fake_b.tif", log)
        finally:
            w.close()


class TestClosePrompt(unittest.TestCase):
    """关窗询问：有未保存的图时弹窗（保存 / 不保存 / 取消）。"""

    def test_close_cancel_stays_open(self):
        w = create_window()
        try:
            _draw_one_1d(w)
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="cancel"):
                self.assertFalse(w.close(), "取消 → 留在程序里")
        finally:
            w.close()

    def test_close_discard_closes(self):
        w = create_window()
        try:
            _draw_one_1d(w)
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard") as confirm:
                self.assertTrue(w.close())
                self.assertTrue(confirm.called)
        finally:
            w.close()

    def test_close_save_runs_save_flow(self):
        w = create_window()
        try:
            _draw_one_1d(w)
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="save"), \
                 mock.patch.object(gui_app, "_save_figures",
                                   return_value=True) as save:
                self.assertTrue(w.close())
                self.assertTrue(save.called)
        finally:
            w.close()

    def test_close_save_cancelled_stays_open(self):
        w = create_window()
        try:
            _draw_one_1d(w)
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="save"), \
                 mock.patch.object(gui_app, "_save_figures",
                                   return_value=False):
                self.assertFalse(w.close(), "保存被取消 → 留在程序里")
        finally:
            w.close()

    def test_close_saved_figures_skips_prompt(self):
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            dock.figure_saved = True   # 已存过盘
            with mock.patch.object(gui_app, "_confirm_close",
                                   side_effect=AssertionError("不应询问")) as confirm:
                self.assertTrue(w.close())
                confirm.assert_not_called()
        finally:
            w.close()


class TestDragDrop(unittest.TestCase):
    """拖文件进窗口 = 加入文件列表（tif/edf/cbf，拖到窗口任意位置）。"""

    def test_drop_adds_files(self):
        w = create_window()
        try:
            abs_a = str(Path("data/fake_a.tif").resolve())
            abs_b = str(Path("data/fake_b.tif").resolve())
            _drop_event(w, [abs_a, abs_b])
            self.assertEqual(w.file_list.count(), 2)
            self.assertEqual(w.file_list.item(0).text(), "fake_a.tif")
            self.assertEqual(w.file_list.item(1).text(), "fake_b.tif")
            # 一起拖入的文件默认全部勾上
            self.assertEqual(w.file_list.item(0).checkState(), Qt.Checked)
            self.assertEqual(w.file_list.item(1).checkState(), Qt.Checked)
            self.assertIs(w.file_list.currentItem(), w.file_list.item(1))
            self.assertEqual(w.file_label.text(), "已选 2 个文件")
            self.assertIn("已添加 2 个文件", w.log_text.toPlainText())
        finally:
            w.close()

    def test_drag_enter_accepts_supported_rejects_others(self):
        w = create_window()
        try:
            ev = _drop_event(w, ["/tmp/x.tif"], kind="enter")
            self.assertTrue(ev.isAccepted(), "支持的类型应接住")
            ev = _drop_event(w, ["/tmp/x.txt"], kind="enter")
            self.assertFalse(ev.isAccepted(), "不支持的类型应拒绝")
        finally:
            w.close()

    def test_dropped_file_can_be_plotted(self):
        w = create_window()
        try:
            abs_b = str(Path("data/fake_b.tif").resolve())
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                _drop_event(w, [abs_b])
                _open_view(w, "1D")
                drawn = _wait_until(
                    lambda: len(_axes(w, "1D", abs_b).lines) > 0)
                self.assertTrue(drawn, "拖入的文件应能直接作图")
        finally:
            w.close()


class TestDuplicateFiles(unittest.TestCase):
    """同一文件再次加入：弹窗问覆盖 / 改名 / 取消，列表不重名。"""

    def test_duplicate_overwrite_keeps_single_entry(self):
        w = create_window()
        try:
            w.add_files(["data/fake_a.tif"])
            with mock.patch.object(gui_app, "_ask_duplicate",
                                   return_value="overwrite"):
                w.add_files(["data/fake_a.tif"])   # 再拖入一次
            self.assertEqual(w.file_list.count(), 1)
            self.assertEqual(w.file_list.item(0).text(), "fake_a.tif")
            self.assertEqual(w.file_list.item(0).checkState(), Qt.Checked)
            self.assertIn("覆盖", w.log_text.toPlainText())
            # 第二次加入没有新条目 → 不会再有第二条"已添加"
            self.assertEqual(w.log_text.toPlainText().count("已添加"), 1)
        finally:
            w.close()

    def test_duplicate_rename_gets_numbered_name(self):
        """改名输入框预填编号名（测试环境直接返回预填值）。"""
        w = create_window()
        try:
            w.add_files(["data/fake_a.tif"])
            with mock.patch.object(gui_app, "_ask_duplicate",
                                   return_value="rename"):
                w.add_files(["data/fake_a.tif"])
            self.assertEqual(w.file_list.count(), 2)
            self.assertEqual(w.file_list.item(0).text(), "fake_a.tif")
            self.assertEqual(w.file_list.item(1).text(), "fake_a (1).tif")
            # 两条条目指向同一个文件；新条目勾上，标签 = 2 个
            self.assertEqual(w.file_list.item(0).data(Qt.UserRole),
                             w.file_list.item(1).data(Qt.UserRole))
            self.assertEqual(w.file_list.item(1).checkState(), Qt.Checked)
            self.assertEqual(w.file_label.text(), "已选 2 个文件")
            # 第三次加入 → 编号继续涨，不与 (1) 撞名
            with mock.patch.object(gui_app, "_ask_duplicate",
                                   return_value="rename"):
                w.add_files(["data/fake_a.tif"])
            texts = [w.file_list.item(i).text()
                     for i in range(w.file_list.count())]
            self.assertEqual(texts, ["fake_a.tif", "fake_a (1).tif",
                                     "fake_a (2).tif"])
        finally:
            w.close()

    def test_duplicate_cancel_skips(self):
        w = create_window()
        try:
            w.add_files(["data/fake_a.tif"])
            with mock.patch.object(gui_app, "_ask_duplicate",
                                   return_value="cancel"):
                w.add_files(["data/fake_a.tif"])
            self.assertEqual(w.file_list.count(), 1)
            self.assertIn("跳过", w.log_text.toPlainText())
        finally:
            w.close()

    def test_rename_dialog_custom_name_used(self):
        """改名输入框：用户自己输入的名字生效。"""
        w = create_window()
        try:
            w.add_files(["data/fake_a.tif"])
            with mock.patch.object(gui_app, "_ask_duplicate",
                                   return_value="rename"), \
                 mock.patch.object(gui_app, "_ask_rename",
                                   return_value="我的数据.tif"):
                w.add_files(["data/fake_a.tif"])
            self.assertEqual(w.file_list.count(), 2)
            self.assertEqual(w.file_list.item(1).text(), "我的数据.tif")
            # 显示名随便改，但底层仍指向同一个文件
            self.assertEqual(w.file_list.item(0).data(Qt.UserRole),
                             w.file_list.item(1).data(Qt.UserRole))
            self.assertEqual(w.file_list.item(1).checkState(), Qt.Checked)
            self.assertIn("改名加入：我的数据.tif", w.log_text.toPlainText())
        finally:
            w.close()

    def test_rename_dialog_cancel_skips(self):
        """改名输入框取消（返回 None）→ 这次不加。"""
        w = create_window()
        try:
            w.add_files(["data/fake_a.tif"])
            with mock.patch.object(gui_app, "_ask_duplicate",
                                   return_value="rename"), \
                 mock.patch.object(gui_app, "_ask_rename",
                                   return_value=None):
                w.add_files(["data/fake_a.tif"])
            self.assertEqual(w.file_list.count(), 1)
            self.assertIn("已跳过重复文件", w.log_text.toPlainText())
        finally:
            w.close()

    def test_rename_dialog_rejects_taken_name(self):
        """输入的名字已被占用 → 要求换一个，输入框重新弹。"""
        w = create_window()
        try:
            w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
            with mock.patch.object(gui_app, "_ask_duplicate",
                                   return_value="rename"), \
                 mock.patch.object(gui_app, "_ask_rename",
                                   side_effect=["fake_b.tif", "自定义.tif"]):
                w.add_files(["data/fake_a.tif"])
            self.assertEqual(w.file_list.count(), 3)
            self.assertEqual(w.file_list.item(2).text(), "自定义.tif")
            self.assertIn("显示名 fake_b.tif 已被占用", w.log_text.toPlainText())
        finally:
            w.close()

    def test_ask_rename_hidden_window_defaults_default_name(self):
        """窗口没显示（测试环境）→ 不弹模态框，返回预填的默认名。"""
        w = create_window()
        try:
            self.assertEqual(gui_app._ask_rename(w, "xxx (1).tif"),
                             "xxx (1).tif")
        finally:
            w.close()

    def test_ask_duplicate_hidden_window_defaults_overwrite(self):
        """窗口没显示（测试环境）→ 不弹模态框，默认覆盖（防挂死）。"""
        w = create_window()
        try:
            self.assertEqual(gui_app._ask_duplicate(w, "fake_a.tif"),
                             "overwrite")
        finally:
            w.close()

    def test_renamed_entry_gets_own_panel(self):
        """改名条目与原条目各自成图：点 1D 出两张面板，互不当过期。"""
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif"])
                with mock.patch.object(gui_app, "_ask_duplicate",
                                       return_value="rename"):
                    w.add_files(["data/fake_a.tif"])
                _open_view(w, "1D")
                drawn1 = _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0)
                # 改名条目的面板键 = 视图|路径|显示名
                key2 = "1D|data/fake_a.tif|fake_a (1).tif"
                drawn2 = _wait_until(
                    lambda: key2 in w.plot_docks
                    and len(w.plot_docks[key2].widget().axes_1d.lines) > 0)
                self.assertTrue(drawn1, "原条目应出图")
                self.assertTrue(drawn2, "改名条目应有自己的面板和曲线")
            self.assertEqual(len(w.plot_docks), 2)
            self.assertEqual(w.plot_docks[key2].windowTitle(),
                             "1D_fake_a (1).tif")
            self.assertNotIn("已忽略", w.log_text.toPlainText())
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


class TestAutoContrast(unittest.TestCase):
    """自动对比度：勾回自动 = 按编辑对象（焦点图）重算并填回；没
    焦点图填占位默认；[恢复默认] 也会重算。对比度只对 2D/剖面 有
    意义：焦点是 1D/对比面板时不读文件（占位默认，防主线程卡）。"""

    def _focus_2d(self, w):
        """开一张 fake_a 的 2D 占位面板并点它 → 编辑对象 = 该面板。"""
        w.add_files(["data/fake_a.tif"])
        _open_view(w, "2D")   # 2D 只开面板不计算（占位）
        QTest.mouseClick(_dock(w, "2D", "data/fake_a.tif").widget(),
                         Qt.LeftButton)
        return w.focus_panel

    def _focus_1d(self, w):
        """画一张 fake_a 的 1D 图并等它完成 → 编辑对象 = 该面板。"""
        with mock.patch.object(gui_app, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files(["data/fake_a.tif"])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0))
        return w.focus_panel

    def test_recheck_auto_restores_computed_values(self):
        """改完手动对比度再勾回自动 → 按焦点图重算，数字跳回自动值。"""
        w = create_window()
        try:
            self._focus_2d(w)   # 编辑对象 = fake_a 的 2D 面板
            auto = w.params["自动对比度"]
            auto.setChecked(False)   # 手动模式
            w.params["对比度下限"].setValue(5.0)
            w.params["对比度上限"].setValue(200.0)
            fake_image = np.linspace(0, 1000, 3000).reshape(50, 60)
            with mock.patch.object(gui_app, "load_diffraction_image",
                                   return_value=fake_image):
                auto.setChecked(True)   # 勾回自动 → 按焦点图重算
            self.assertAlmostEqual(
                w.params["对比度下限"].value(),
                float(np.percentile(fake_image, 1.0)), places=1)
            self.assertAlmostEqual(
                w.params["对比度上限"].value(),
                float(np.percentile(fake_image, 99.9)), places=1)
            self.assertFalse(w.params["对比度下限"].isEnabled())
            self.assertFalse(w.params["对比度上限"].isEnabled())
            self.assertIn("自动对比度：按 fake_a.tif 算得",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_recheck_auto_without_focus_fills_defaults(self):
        """没有编辑对象时勾回自动 → 恢复占位默认值。"""
        w = create_window()
        try:
            auto = w.params["自动对比度"]
            auto.setChecked(False)
            w.params["对比度下限"].setValue(5.0)
            w.params["对比度上限"].setValue(200.0)
            auto.setChecked(True)
            self.assertEqual(w.params["对比度下限"].value(), 1.0)
            self.assertEqual(w.params["对比度上限"].value(), 100000.0)
            self.assertIn("没有编辑对象", w.log_text.toPlainText())
        finally:
            w.close()

    def test_reset_image_recomputes_auto(self):
        """[恢复默认]：勾回自动并重算（不是填死的占位值），角度归零。"""
        w = create_window()
        try:
            self._focus_2d(w)
            w.params["自动对比度"].setChecked(False)
            w.params["对比度下限"].setValue(5.0)
            w.params["剖面角度 (°)"].setValue(45.0)
            fake_image = np.linspace(0, 1000, 3000).reshape(50, 60)
            with mock.patch.object(gui_app, "load_diffraction_image",
                                   return_value=fake_image):
                w.findChild(QPushButton, "reset_image_btn").click()
            self.assertTrue(w.params["自动对比度"].isChecked())
            self.assertAlmostEqual(
                w.params["对比度下限"].value(),
                float(np.percentile(fake_image, 1.0)), places=1)
            self.assertEqual(w.params["剖面角度 (°)"].value(), 0.0)
            self.assertIn("图像参数已恢复默认", w.log_text.toPlainText())
        finally:
            w.close()

    def test_auto_contrast_load_failure_falls_back(self):
        """焦点图读取失败 → 填占位默认并记日志，不崩溃。"""
        w = create_window()
        try:
            self._focus_2d(w)
            w.params["自动对比度"].setChecked(False)
            w.params["对比度下限"].setValue(5.0)
            with mock.patch.object(gui_app, "load_diffraction_image",
                                   side_effect=OSError("boom")):
                w.params["自动对比度"].setChecked(True)
            self.assertEqual(w.params["对比度下限"].value(), 1.0)
            self.assertEqual(w.params["对比度上限"].value(), 100000.0)
            self.assertIn("读取 fake_a.tif 失败", w.log_text.toPlainText())
        finally:
            w.close()

    def test_1d_focus_does_not_read_file(self):
        """焦点是 1D 面板时勾回自动 → 不读文件（防主线程解码大图），
        填占位默认。修前每次点焦点都读一遍文件，批量出图会卡。"""
        w = create_window()
        try:
            self._focus_1d(w)
            w.params["自动对比度"].setChecked(False)
            w.params["对比度下限"].setValue(5.0)
            with mock.patch.object(gui_app, "load_diffraction_image",
                                   side_effect=OSError("boom")) as load:
                w.params["自动对比度"].setChecked(True)
            self.assertFalse(load.called)
            self.assertEqual(w.params["对比度下限"].value(), 1.0)
            self.assertEqual(w.params["对比度上限"].value(), 100000.0)
            self.assertIn("编辑对象不是 2D/剖面 视图",
                          w.log_text.toPlainText())
        finally:
            w.close()


class TestParamSnapshot(unittest.TestCase):
    """点哪张图，参数面板就显示哪张图作图时的参数（快照）。

    快照在开图/重算时拍下：改参数 → [应用] → 快照跟着刷新。
    重复点同一面板不冲掉正在改的值。
    """

    def _plot_fake(self, w, file, params):
        """只勾这一个文件、改参数、出图并等完成 → 返回面板键。"""
        with mock.patch.object(gui_app, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files([file])
            # 只留这一个对号（其余取消），点作图按钮才只画它
            for i in range(w.file_list.count()):
                item = w.file_list.item(i)
                item.setCheckState(
                    Qt.Checked if item.data(Qt.UserRole) == file
                    else Qt.Unchecked)
            for name, value in params.items():
                w.params[name].setValue(value)
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", file).lines) > 0))
        return f"1D|{file}"

    def test_switch_focus_shows_each_panels_params(self):
        """点 A 显示 A 的参数，点 B 显示 B 的参数（不是只能改不能看）。"""
        w = create_window()
        try:
            key_a = self._plot_fake(
                w, "data/fake_a.tif",
                {"初始距离 (mm)": 1700.0, "2θ 上限 (°)": 7.0,
                 "输出点数": 2500})
            self.assertEqual(w.focus_panel, key_a)
            # 完成时焦点 = A → 参数坞已回放 A 的快照
            self.assertEqual(w.params["初始距离 (mm)"].value(), 1700.0)
            self.assertEqual(w.params["2θ 上限 (°)"].value(), 7.0)
            self.assertEqual(w.params["输出点数"].value(), 2500)
            # 打开 B（参数不同）→ 焦点切 B，参数显示 B 的值
            key_b = self._plot_fake(
                w, "data/fake_b.tif",
                {"初始距离 (mm)": 1800.0, "2θ 上限 (°)": 8.0})
            self.assertEqual(w.focus_panel, key_b)
            self.assertEqual(w.params["初始距离 (mm)"].value(), 1800.0)
            self.assertEqual(w.params["2θ 上限 (°)"].value(), 8.0)
            # 点回 A → 参数显示 A 的值（能看）
            QTest.mouseClick(_dock(w, "1D", "data/fake_a.tif").widget(),
                             Qt.LeftButton)
            self.assertEqual(w.focus_panel, key_a)
            self.assertEqual(w.params["初始距离 (mm)"].value(), 1700.0)
            self.assertEqual(w.params["2θ 上限 (°)"].value(), 7.0)
            self.assertEqual(w.params["输出点数"].value(), 2500)
            # 配置条目也回放（注册表只有一条，至少不串台）
            self.assertEqual(w.config_combo.currentData(), w.config_name)
        finally:
            w.close()

    def test_apply_updates_snapshot(self):
        """改参数 [应用] 后快照刷新：切走再切回显示新值。"""
        w = create_window()
        try:
            self._plot_fake(w, "data/fake_a.tif",
                            {"初始距离 (mm)": 1700.0})
            w.params["初始距离 (mm)"].setValue(1800.0)
            done_before = w.log_text.toPlainText().count("积分完成")
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.findChild(QPushButton, "apply_btn").click()
                self.assertTrue(_wait_until(
                    lambda: w.log_text.toPlainText().count("积分完成")
                    > done_before))
            # 快照已随 [应用] 同步刷新
            self.assertEqual(
                _dock(w, "1D", "data/fake_a.tif").params_snapshot
                ["初始距离 (mm)"], 1800.0)
            # 切到 B 再切回 A → 显示新值 1800
            self._plot_fake(w, "data/fake_b.tif",
                            {"初始距离 (mm)": 1900.0})
            QTest.mouseClick(_dock(w, "1D", "data/fake_a.tif").widget(),
                             Qt.LeftButton)
            self.assertEqual(w.params["初始距离 (mm)"].value(), 1800.0)
        finally:
            w.close()

    def test_same_panel_reclick_keeps_edits(self):
        """重复点同一面板不冲掉正在改的值（跳过快照回放）。"""
        w = create_window()
        try:
            key = self._plot_fake(w, "data/fake_a.tif",
                                  {"初始距离 (mm)": 1700.0})
            w.params["初始距离 (mm)"].setValue(1750.0)   # 未应用的编辑
            QTest.mouseClick(_dock(w, "1D", "data/fake_a.tif").widget(),
                             Qt.LeftButton)
            self.assertEqual(w.focus_panel, key)
            self.assertEqual(w.params["初始距离 (mm)"].value(), 1750.0)
        finally:
            w.close()

    def test_manual_contrast_restored_from_snapshot(self):
        """手动对比度也进快照：A 保持自动，B 图像 [应用] 改手动，
        切来切去各自回放各自的状态。"""
        w = create_window()
        try:
            # A 作图时自动对比度勾着（默认）
            self._plot_fake(w, "data/fake_a.tif",
                            {"初始距离 (mm)": 1700.0})
            # B 新开（默认自动开）；对 B 关自动、改值并图像 [应用]
            # → B 的快照 = 手动模式 + 这组值
            self._plot_fake(w, "data/fake_b.tif",
                            {"初始距离 (mm)": 1800.0})
            w.params["自动对比度"].setChecked(False)
            w.params["对比度下限"].setValue(123.0)
            w.params["对比度上限"].setValue(456.0)
            w.findChild(QPushButton, "apply_image_btn").click()
            # 切回 A：A 的快照里自动是勾着的 → 自动开、输入框置灰
            QTest.mouseClick(_dock(w, "1D", "data/fake_a.tif").widget(),
                             Qt.LeftButton)
            self.assertTrue(w.params["自动对比度"].isChecked())
            self.assertFalse(w.params["对比度下限"].isEnabled())
            # 再切回 B：手动模式 + 123/456 原样回放
            QTest.mouseClick(_dock(w, "1D", "data/fake_b.tif").widget(),
                             Qt.LeftButton)
            self.assertFalse(w.params["自动对比度"].isChecked())
            self.assertEqual(w.params["对比度下限"].value(), 123.0)
            self.assertEqual(w.params["对比度上限"].value(), 456.0)
        finally:
            w.close()


class TestPanelToggles(unittest.TestCase):
    """[文件][参数][日志] 收起开关：收起给图让地方，再点展开；
    用标题栏 × 关坞也会同步弹起按钮。"""

    def setUp(self):
        self.w = create_window()
        self.w.show()   # 坞的 isVisible 依赖主窗口可见，offscreen 也能 show

    def tearDown(self):
        # 窗口可见时关窗会弹"未保存图"模态框（offscreen 没人点会挂住）；
        # 关窗询问已有专门测试（TestClosePrompt），这里直接当作选"不保存"
        with mock.patch.object(gui_app, "_confirm_close",
                               return_value="discard"):
            self.w.close()

    def test_default_all_visible_and_checked(self):
        """初始状态：三个开关都勾着、三个坞都可见。"""
        for name, dock in (("文件", self.w.file_dock),
                           ("参数", self.w.param_dock),
                           ("日志", self.w.log_dock)):
            self.assertTrue(self.w.panel_toggles[name].isChecked())
            self.assertTrue(dock.isVisible())

    def test_toggle_hides_and_restores_each_dock(self):
        """点开关收起、再点展开，三个坞各试一遍。"""
        for name, dock in (("文件", self.w.file_dock),
                           ("参数", self.w.param_dock),
                           ("日志", self.w.log_dock)):
            self.w.panel_toggles[name].click()   # 收起
            self.assertFalse(dock.isVisible())
            self.assertFalse(self.w.panel_toggles[name].isChecked())
            self.w.panel_toggles[name].click()   # 展开
            self.assertTrue(dock.isVisible())
            self.assertTrue(self.w.panel_toggles[name].isChecked())

    def test_close_button_syncs_toggle(self):
        """点标题栏 × 关坞 → 按钮自动弹起；再点按钮还能展开。"""
        self.w.param_dock.close()   # 等价于标题栏 ×
        self.assertFalse(self.w.param_dock.isVisible())
        self.assertFalse(self.w.panel_toggles["参数"].isChecked())
        self.w.panel_toggles["参数"].click()
        self.assertTrue(self.w.param_dock.isVisible())

    def test_all_hidden_plot_still_works(self):
        """全收起来只剩绘图区，照常出图（开关不影响作图流程）。"""
        for btn in self.w.panel_toggles.values():
            btn.click()
        for dock in (self.w.file_dock, self.w.param_dock, self.w.log_dock):
            self.assertFalse(dock.isVisible())
        with mock.patch.object(gui_app, "_compute_integration",
                               side_effect=_fake_compute):
            self.w.add_files(["data/fake_b.tif"])
            _open_view(self.w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(self.w, "1D", "data/fake_b.tif").lines)
                > 0))
        self.assertEqual(self.w.focus_panel, "1D|data/fake_b.tif")


class Test1dDisplay(unittest.TestCase):
    """1D 显示参数（图像参数组里的子分组）：对数纵轴 + 纵轴范围。
    改后点图像 [应用] 用已有数据重画（不重算）；新开面板一律从
    默认（对数关/自动开）起步。"""

    def _plot_fake_b(self, w):
        """画 fake_b 的 1D 图并等完成 → 返回坐标轴（强度 1/2/3）。"""
        with mock.patch.object(gui_app, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files(["data/fake_b.tif"])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
        return _axes(w, "1D", "data/fake_b.tif")

    def test_log_y_applied_at_plot(self):
        """勾上对数纵轴 → 图像 [应用] → 曲线画在对数刻度上。"""
        w = create_window()
        try:
            ax = self._plot_fake_b(w)
            w.params["对数纵轴"].setChecked(True)
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertEqual(ax.get_yscale(), "log")
        finally:
            w.close()

    def test_manual_ylim_applied(self):
        """取消纵轴自动、手填范围 → 图像 [应用] → 曲线按手填范围画。"""
        w = create_window()
        try:
            ax = self._plot_fake_b(w)
            w.params["纵轴自动"].setChecked(False)
            w.params["纵轴下限"].setValue(10.0)
            w.params["纵轴上限"].setValue(500.0)
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertEqual(ax.get_ylim(), (10.0, 500.0))
            # 手动模式下输入框可用
            self.assertTrue(w.params["纵轴下限"].isEnabled())
        finally:
            w.close()

    def test_auto_ylim_uses_percentiles(self):
        """默认自动 → 纵轴范围 = 曲线强度的 1%/99.9% 分位。"""
        w = create_window()
        try:
            ax = self._plot_fake_b(w)   # 假强度 [1.0, 2.0, 3.0]
            ylo, yhi = ax.get_ylim()
            self.assertAlmostEqual(
                ylo, float(np.percentile([1.0, 2.0, 3.0], 1.0)), places=2)
            self.assertAlmostEqual(
                yhi, float(np.percentile([1.0, 2.0, 3.0], 99.9)), places=2)
            # 自动模式下输入框置灰（只读展示正在用的区间）
            self.assertFalse(w.params["纵轴下限"].isEnabled())
        finally:
            w.close()

    def test_log_y_off_apply_back_to_linear(self):
        """对数 → 取消勾选 → [应用] → 曲线回到线性刻度（反方向也生效）。"""
        w = create_window()
        try:
            ax = self._plot_fake_b(w)
            w.params["对数纵轴"].setChecked(True)
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertEqual(ax.get_yscale(), "log")
            w.params["对数纵轴"].setChecked(False)
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertEqual(ax.get_yscale(), "linear")
        finally:
            w.close()

    def test_reset_restores_1d_display(self):
        """[恢复默认] 把 1D 显示参数也复位（对数关、纵轴自动开）。"""
        w = create_window()
        try:
            w.params["对数纵轴"].setChecked(True)
            w.params["纵轴自动"].setChecked(False)
            w.findChild(QPushButton, "reset_image_btn").click()
            self.assertFalse(w.params["对数纵轴"].isChecked())
            self.assertTrue(w.params["纵轴自动"].isChecked())
            self.assertFalse(w.params["纵轴下限"].isEnabled())
        finally:
            w.close()


class TestLongNames(unittest.TestCase):
    """长名称不破坏参数坞布局（两个长度问题）：
    1. 编辑对象标题超长 → 单行缩略、不撑宽参数区，悬停看全名；
    2. 几何配置灰色说明 → 单行缩略，不再换行被下一行控件遮住。
    """

    LONG = "data/very_long_sample_name_that_would_stretch_the_panel.tif"

    def _plot_long(self, w):
        """画一张长文件名的 1D 图并等完成（编辑对象 = 该面板）。"""
        with mock.patch.object(gui_app, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files([self.LONG])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", self.LONG).lines) > 0))

    def test_long_focus_title_does_not_stretch_param_dock(self):
        w = create_window()
        try:
            w.show()
            width_before = w.param_dock.width()
            self._plot_long(w)
            title = f"1D_{Path(self.LONG).name}"
            # 完整文字还在（text() 返回逻辑文字），悬停有全名提示
            self.assertEqual(w.focus_label.text(), f"编辑对象：{title}")
            self.assertEqual(w.focus_label.toolTip(), f"编辑对象：{title}")
            # 参数坞不被撑宽（修前会被标题文字宽度撑到 ~600px）
            self.assertLessEqual(w.param_dock.width(), width_before + 5)
            # 显示层面是单行（不换行占两行）
            one_line = w.focus_label.fontMetrics().height()
            self.assertLessEqual(w.focus_label.height(), one_line + 4)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_config_label_single_line_not_occluded(self):
        w = create_window()
        try:
            w.show()
            label = w.config_label
            # 悬停提示 = 完整批次备注
            self.assertEqual(
                label.toolTip(),
                gui_app.CONFIGS[gui_app.DEFAULT_CONFIG]["label"])
            # 单行：不换行（换行的第二行会被 QFormLayout 压到下一行
            # 控件底下，即"被遮挡"）
            one_line = label.fontMetrics().height()
            self.assertLessEqual(label.height(), one_line + 4)
            # 超长备注也不撑宽参数坞
            width_before = w.param_dock.width()
            label.setText("这是一个非常非常非常长的批次备注，用来验证灰色"
                          "说明行在窄参数坞里是缩略显示而不是撑宽参数区")
            self.assertLessEqual(w.param_dock.width(), width_before + 5)
        finally:
            w.close()   # 没画图，关窗不会弹询问


class TestParamDockSplitLayout(unittest.TestCase):
    """参数坞上下对半分结构：编辑对象名固定在最上方，下面
    QSplitter 竖切两半（数据参数上 / 图像参数下，初始等高）；
    两半各自一个 QScrollArea（内容放不下时滚动），按钮竖排一列
    （[应用] 在上、[恢复默认] 在下）固定在各区最下方——在滚动区
    之外，滚动时按钮不跟着走。"""

    def test_split_structure(self):
        w = create_window()
        try:
            w.show()
            QApplication.processEvents()
            content = w.param_dock.widget()
            lay = content.layout()
            # 编辑对象名固定在坞内容布局最上方，分隔条紧随其后占满
            # 剩余空间（下方没有别的同级条目）
            self.assertIs(lay.itemAt(0).widget(), w.focus_label)
            splitter = lay.itemAt(1).widget()
            self.assertIsInstance(splitter, QSplitter)
            self.assertEqual(splitter.orientation(), Qt.Vertical)
            self.assertEqual(splitter.count(), 2)
            self.assertFalse(splitter.childrenCollapsible())

            data_half, img_half = splitter.widget(0), splitter.widget(1)
            self.assertEqual(data_half.title(), "数据参数")
            self.assertEqual(img_half.title(), "图像参数")
            # 初始对半分：两块等高（容差按 15% 或几个像素）
            s0, s1 = splitter.sizes()
            self.assertGreater(s0, 0)
            self.assertAlmostEqual(s0, s1, delta=max(4, s0 * 0.15))

            # 每半：滚动区在上、按钮行垫底；按钮不在滚动区内容物里
            for half, reset_name, apply_name in (
                    (data_half, "reset_data_btn", "apply_btn"),
                    (img_half, "reset_image_btn", "apply_image_btn")):
                scroll = half.findChild(QScrollArea)
                self.assertIsNotNone(scroll)
                reset = half.findChild(QPushButton, reset_name)
                apply = half.findChild(QPushButton, apply_name)
                self.assertIsNotNone(reset)
                self.assertIsNotNone(apply)
                # 滚动区内容物里找不到按钮 = 按钮固定在滚动区之外
                self.assertIsNone(
                    scroll.widget().findChild(QPushButton, apply_name))
                self.assertIsNone(
                    scroll.widget().findChild(QPushButton, reset_name))
                # 布局顺序：滚动区在上、按钮列垫底（竖排：[应用] 在上）
                v = half.layout()
                self.assertIs(v.itemAt(0).widget(), scroll)
                btn_col = v.itemAt(1)
                self.assertIsInstance(btn_col, QVBoxLayout)
                self.assertIs(btn_col.itemAt(0).widget(), apply)
                self.assertIs(btn_col.itemAt(1).widget(), reset)
        finally:
            w.close()


class TestParamFormPolish(unittest.TestCase):
    """参数面板排版细节：单位在输入框后缀、成对范围并排一行（中间
    ~ 连接）、小节灰色标题、复选框名称精简（键不动）、坞的最小
    宽高防拖动裁切。window.params 的键保持旧名——快照回放与既有
    测试都按键找控件，只改显示排版。"""

    def test_units_in_spinbox_suffix(self):
        w = create_window()
        try:
            self.assertEqual(w.params["像素尺寸 (µm)"].suffix(), " µm")
            self.assertEqual(w.params["波长 (Å)"].suffix(), " Å")
            self.assertEqual(w.params["初始距离 (mm)"].suffix(), " mm")
            self.assertEqual(w.params["剖面角度 (°)"].suffix(), " °")
            for name in ("2θ 下限 (°)", "2θ 上限 (°)"):
                self.assertEqual(w.params[name].suffix(), " °")
            # 对比度/纵轴没有单位，后缀为空
            self.assertEqual(w.params["对比度下限"].suffix(), "")
            self.assertEqual(w.params["纵轴下限"].suffix(), "")
        finally:
            w.close()

    def test_range_pairs_share_one_row(self):
        w = create_window()
        try:
            # 成对的下限/上限并排一行：同父（同一行字段容器），
            # 中间隔着 "~" 标签；2θ/对比度/纵轴三对都是这个结构
            for lo_name, hi_name in (("2θ 下限 (°)", "2θ 上限 (°)"),
                                     ("对比度下限", "对比度上限"),
                                     ("纵轴下限", "纵轴上限")):
                lo, hi = w.params[lo_name], w.params[hi_name]
                self.assertIs(lo.parent(), hi.parent())
                row = lo.parent().layout()
                self.assertEqual(row.count(), 3)
                self.assertEqual(row.itemAt(1).widget().text(), "~")
        finally:
            w.close()

    def test_section_captions_and_short_checkbox(self):
        w = create_window()
        try:
            captions = {lb.text() for lb in w.param_dock.findChildren(QLabel)}
            self.assertIn("标定几何", captions)
            self.assertIn("积分设置", captions)
            self.assertIn("2D/剖面视图（接线后生效）", captions)
            # 复选框名称精简（键仍是"对比归一化"，快照回放不认字面）
            box = w.params["对比归一化"]
            self.assertEqual(box.text(), "归一化到最强峰")
        finally:
            w.close()

    def test_dock_min_sizes_prevent_clipping(self):
        w = create_window()
        try:
            w.show()
            QApplication.processEvents()
            # 坞的最小宽高已设：左右 = 完整显示最宽一行的宽度，
            # 上下 = 固定件（编辑对象名 + 标题/按钮行）不被遮没
            self.assertGreater(w.param_dock.minimumWidth(), 150)
            self.assertLess(w.param_dock.minimumWidth(), 320)   # 窄排版：不能为"完整显示"把坞设得过宽
            self.assertGreater(w.param_dock.minimumHeight(), 200)
            # 把窗口压窄：参数坞停在最小宽度（被裁的只有中央绘图区）
            w.resize(250, 400)
            QApplication.processEvents()
            self.assertGreaterEqual(
                w.param_dock.width(), w.param_dock.minimumWidth() - 5)
        finally:
            w.close()


class TestImageApply(unittest.TestCase):
    """图像参数组的 [应用]：布局与数据参数组一致（[应用] 在上、
    恢复默认在下竖排一列）。显示参数（对数纵轴/纵轴范围/对比归一化）每张图各
    记各的：点一次 [应用] 只重画焦点那张图（不重算），别的图保持
    自己的设置；切面板随快照回放该面板自己的显示设置。两个 [应用]
    各管各的一组参数：图像 [应用] 不改数据参数，数据 [应用] 不改
    显示参数；新开面板显示参数从默认起步（不继承上一张焦点图的）。"""

    def test_layout_matches_data_params(self):
        """[应用] 在上、[恢复默认] 在下竖排一列，结构与数据参数组完全同款。"""
        w = create_window()
        try:
            reset_img = w.findChild(QPushButton, "reset_image_btn")
            apply_img = w.findChild(QPushButton, "apply_image_btn")
            reset_data = w.findChild(QPushButton, "reset_data_btn")
            apply_data = w.findChild(QPushButton, "apply_btn")
            for btn in (reset_img, apply_img, reset_data, apply_data):
                self.assertIsNotNone(btn)
            # 按钮加入分组框布局后被收编进所属分组框（同父 = 同一
            # 个小容器）；两组结构一致 = 同款布局
            self.assertEqual(reset_img.parent(), apply_img.parent())
            self.assertEqual(reset_data.parent(), apply_data.parent())
            self.assertEqual(type(reset_img.parent()), type(reset_data.parent()))
        finally:
            w.close()

    def test_apply_without_focus_prompts(self):
        """没选图面板就点 [应用] → 日志提示先点图。"""
        w = create_window()
        try:
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertIn("先点击要更新的图面板",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_apply_updates_focus_snapshot_and_redraws(self):
        """改图像参数 → [应用] → 快照更新 + 用已有数据重画（不重算）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines)
                    > 0))
            done_before = w.log_text.toPlainText().count("积分完成")
            w.params["剖面角度 (°)"].setValue(15.0)
            w.params["对数纵轴"].setChecked(True)
            w.findChild(QPushButton, "apply_image_btn").click()
            # 快照同步刷新（切走再切回显示这组值，与数据 [应用] 一致）
            dock = _dock(w, "1D", "data/fake_b.tif")
            self.assertEqual(dock.params_snapshot["剖面角度 (°)"], 15.0)
            self.assertTrue(dock.params_snapshot["对数纵轴"])
            log = w.log_text.toPlainText()
            self.assertIn("[应用] 图像参数已重画：1D_fake_b.tif", log)
            # 1D 显示参数真的落到图上（对数纵轴生效），且没有重算
            self.assertEqual(
                _axes(w, "1D", "data/fake_b.tif").get_yscale(), "log")
            self.assertEqual(log.count("积分完成"), done_before)
        finally:
            w.close()

    def test_apply_on_unwired_view_logs_pending(self):
        """编辑对象是 2D 占位面板 → [应用] 只记快照并提示尚未接线。"""
        w = create_window()
        try:
            w.add_files(["data/fake_b.tif"])
            _open_view(w, "2D")   # 2D 只开面板不计算（占位）
            QTest.mouseClick(_dock(w, "2D", "data/fake_b.tif").widget(),
                             Qt.LeftButton)
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertIn("2D 视图尚未接线", w.log_text.toPlainText())
        finally:
            w.close()

    def test_apply_only_redraws_focus_panel(self):
        """显示参数每张图各记各的：1D + 对比同开，焦点在 1D 上改参数
        [应用] → 只重画 1D 那张，对比保持自己的设置（修前是全局的，
        一张改了所有图都变）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines)
                    > 0))
                w.add_files(["data/fake_b.tif"])   # fake_a 仍勾着
                w.compare_btn.click()
                cax = [d for k, d in w.plot_docks.items()
                       if k.startswith("对比|")][0].widget().axes_1d
                self.assertTrue(_wait_until(lambda: len(cax.lines) >= 2))
            # 焦点此时在对比面板；切到 1D 面板改参数 → 应用 → 只动 1D
            QTest.mouseClick(_dock(w, "1D", "data/fake_a.tif").widget(),
                             Qt.LeftButton)
            w.params["对数纵轴"].setChecked(True)
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertEqual(
                _axes(w, "1D", "data/fake_a.tif").get_yscale(), "log")
            self.assertEqual(cax.get_yscale(), "linear")   # 对比没被波及
            # 对比面板自己的快照仍是旧设置（对数关），1D 面板已更新
            cdock = [d for k, d in w.plot_docks.items()
                     if k.startswith("对比|")][0]
            self.assertFalse(cdock.params_snapshot["对数纵轴"])
            self.assertTrue(
                _dock(w, "1D", "data/fake_a.tif").params_snapshot["对数纵轴"])
        finally:
            w.close()

    def test_display_params_replay_per_panel(self):
        """显示参数切面板随快照回放：两张 1D 一张对数一张线性，点哪张
        图参数坞就显示哪张的设置（修前切面板不回放，参数跟图对不上）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")   # 两张都出图（默认线性）
                self.assertTrue(_wait_until(
                    lambda: all(len(_axes(w, "1D", f"data/{n}.tif").lines) > 0
                                for n in ("fake_a", "fake_b"))))
            # 焦点是最后算完的那张；切到 fake_a 改成对数并应用
            QTest.mouseClick(_dock(w, "1D", "data/fake_a.tif").widget(),
                             Qt.LeftButton)
            w.params["对数纵轴"].setChecked(True)
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertEqual(
                _axes(w, "1D", "data/fake_a.tif").get_yscale(), "log")
            self.assertEqual(
                _axes(w, "1D", "data/fake_b.tif").get_yscale(), "linear")
            # 点 fake_b → 参数坞回放它自己的设置（对数关）
            QTest.mouseClick(_dock(w, "1D", "data/fake_b.tif").widget(),
                             Qt.LeftButton)
            self.assertFalse(w.params["对数纵轴"].isChecked())
            # 点回 fake_a → 显示它自己的设置（对数开）
            QTest.mouseClick(_dock(w, "1D", "data/fake_a.tif").widget(),
                             Qt.LeftButton)
            self.assertTrue(w.params["对数纵轴"].isChecked())
        finally:
            w.close()

    def test_ylim_state_replays_per_panel(self):
        """纵轴范围随面板回放：A 手动范围（输入框可改）、B 自动（输入
        框置灰），切来切去状态跟着换——修前显示参数不回放，纵轴组的
        开关/置灰状态与每张图的实际设置对不上。"""
        w = create_window()
        try:
            # A：先按默认出图，再改成手填范围并图像 [应用]
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines)
                    > 0))
            w.params["纵轴自动"].setChecked(False)
            w.params["纵轴下限"].setValue(10.0)
            w.params["纵轴上限"].setValue(500.0)
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertEqual(_axes(w, "1D", "data/fake_a.tif").get_ylim(),
                             (10.0, 500.0))
            # B：只勾 B 新开一张（新面板显示参数从默认起步 = 自动开）
            w.add_files(["data/fake_b.tif"])
            for i in range(w.file_list.count()):
                item = w.file_list.item(i)
                item.setCheckState(
                    Qt.Checked if item.data(Qt.UserRole) == "data/fake_b.tif"
                    else Qt.Unchecked)
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines)
                    > 0))
            # 焦点在 B：自动开、输入框置灰
            self.assertTrue(w.params["纵轴自动"].isChecked())
            self.assertFalse(w.params["纵轴下限"].isEnabled())
            # 点回 A → 回放它自己的：自动关、输入框可改、值 = 手填值
            QTest.mouseClick(_dock(w, "1D", "data/fake_a.tif").widget(),
                             Qt.LeftButton)
            self.assertFalse(w.params["纵轴自动"].isChecked())
            self.assertTrue(w.params["纵轴下限"].isEnabled())
            self.assertEqual(w.params["纵轴下限"].value(), 10.0)
            self.assertEqual(w.params["纵轴上限"].value(), 500.0)
        finally:
            w.close()

    def test_new_panels_start_with_default_display(self):
        """新开面板（1D / 对比）显示参数从默认起步：不受上一张焦点图
        设置的影响（修前新图会继承参数坞当前值——先把 1D 改成对数+
        手填，新开的对比图也跟着对数+手填）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines)
                    > 0))
            # 把这张 1D 图改成对数 + 手填纵轴（图像 [应用]）
            w.params["对数纵轴"].setChecked(True)
            w.params["纵轴自动"].setChecked(False)
            w.params["纵轴下限"].setValue(10.0)
            w.params["纵轴上限"].setValue(500.0)
            w.findChild(QPushButton, "apply_image_btn").click()
            # 新开对比图 → 显示参数是默认，不是上面的对数/手填
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif"])   # fake_b 仍勾着
                w.compare_btn.click()
                cdock = [d for k, d in w.plot_docks.items()
                         if k.startswith("对比|")][0]
                self.assertTrue(_wait_until(
                    lambda: len(cdock.widget().axes_1d.lines) >= 2))
            snap = cdock.params_snapshot
            self.assertFalse(snap["对数纵轴"])
            self.assertTrue(snap["纵轴自动"])
            self.assertEqual(snap["纵轴下限"], 1.0)
            self.assertEqual(snap["纵轴上限"], 100000.0)
            # 对比完成后成为焦点：置灰框显示它自己算出的自动区间
            # （输入框精度 decimals=1，与图的精确值允许 0.1 级误差）
            cax = cdock.widget().axes_1d
            self.assertAlmostEqual(w.params["纵轴下限"].value(),
                                   cax.get_ylim()[0], places=1)
            self.assertAlmostEqual(w.params["纵轴上限"].value(),
                                   cax.get_ylim()[1], places=1)
        finally:
            w.close()

    def test_recompute_keeps_panel_display(self):
        """重按视图按钮重算：显示参数保留面板自己的旧设置（修前会被
        参数坞当前值覆盖——看别的图时重算会把别的图的长相抄过来）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines)
                    > 0))
            # fake_a 改成对数并应用
            w.params["对数纵轴"].setChecked(True)
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertEqual(
                _axes(w, "1D", "data/fake_a.tif").get_yscale(), "log")
            # 加一张 fake_b 两张都勾着重按 1D：重算两张。新开的 fake_b
            # 从默认（线性）起步，fake_a 保留自己的对数设置
            w.add_files(["data/fake_b.tif"])
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: w.log_text.toPlainText().count("积分完成") >= 3))
            self.assertEqual(
                _axes(w, "1D", "data/fake_a.tif").get_yscale(), "log")
            self.assertEqual(
                _axes(w, "1D", "data/fake_b.tif").get_yscale(), "linear")
            self.assertTrue(
                _dock(w, "1D", "data/fake_a.tif").params_snapshot["对数纵轴"])
        finally:
            w.close()

    def test_each_apply_touches_only_its_group(self):
        """两个 [应用] 各管各的：图像 [应用] 不改数据参数，数据 [应用]
        不改显示参数（参数坞同时改了数据和显示也互不串改）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines)
                    > 0))
            dock = _dock(w, "1D", "data/fake_b.tif")
            # 同时改了数据（2θ 上限）和显示（对数），只点图像 [应用]
            w.params["2θ 上限 (°)"].setValue(7.0)
            w.params["对数纵轴"].setChecked(True)
            w.findChild(QPushButton, "apply_image_btn").click()
            # 显示生效、数据没动
            self.assertTrue(dock.params_snapshot["对数纵轴"])
            self.assertEqual(dock.params_snapshot["2θ 上限 (°)"], 8.0)
            self.assertEqual(_axes(w, "1D", "data/fake_b.tif").get_yscale(),
                             "log")
            self.assertEqual(_axes(w, "1D", "data/fake_b.tif").get_xlim(),
                             (1.0, 8.0))
            # 再改显示（对数关）只点数据 [应用] → 数据生效、显示仍是
            # 面板自己的旧设置（对数开）
            w.params["对数纵轴"].setChecked(False)
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.findChild(QPushButton, "apply_btn").click()
                self.assertTrue(_wait_until(
                    lambda: w.log_text.toPlainText().count("积分完成") >= 2))
            self.assertEqual(dock.params_snapshot["2θ 上限 (°)"], 7.0)
            self.assertTrue(dock.params_snapshot["对数纵轴"])
            self.assertEqual(_axes(w, "1D", "data/fake_b.tif").get_yscale(),
                             "log")
        finally:
            w.close()

    def test_auto_ylim_boxes_show_computed_range(self):
        """纵轴自动时置灰输入框 = 程序实际用的区间（画图/切面板回填，
        修前永远显示占位默认 1.0 ~ 100000.0）。"""
        w = create_window()
        try:
            # fake_b 数据 = [10, 20, 30]（_fake_compare_compute）→ 自动
            # 区间是它的 1%/99.9% 分位，不是占位默认
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compare_compute):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines)
                    > 0))
            ax_b = _axes(w, "1D", "data/fake_b.tif")
            self.assertAlmostEqual(w.params["纵轴下限"].value(),
                                   ax_b.get_ylim()[0], places=1)
            self.assertAlmostEqual(w.params["纵轴上限"].value(),
                                   ax_b.get_ylim()[1], places=1)
            self.assertGreater(w.params["纵轴下限"].value(), 5.0)   # 不是占位 1.0
            # 只勾 fake_a（数据 [1, 2, 3]）再开一张 → 焦点切到 A，
            # 置灰框按 A 的数据重算；切回 B 又变回 B 的区间
            w.add_files(["data/fake_a.tif"])
            for i in range(w.file_list.count()):
                item = w.file_list.item(i)
                item.setCheckState(
                    Qt.Checked if item.data(Qt.UserRole) == "data/fake_a.tif"
                    else Qt.Unchecked)
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compare_compute):
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines)
                    > 0))
            ax_a = _axes(w, "1D", "data/fake_a.tif")
            self.assertAlmostEqual(w.params["纵轴下限"].value(),
                                   ax_a.get_ylim()[0], places=1)
            QTest.mouseClick(_dock(w, "1D", "data/fake_b.tif").widget(),
                             Qt.LeftButton)
            self.assertAlmostEqual(w.params["纵轴下限"].value(),
                                   ax_b.get_ylim()[0], places=1)
            self.assertAlmostEqual(w.params["纵轴上限"].value(),
                                   ax_b.get_ylim()[1], places=1)
        finally:
            w.close()


def _fake_compare_compute(path_str, geom, npt):
    """假积分（对比用）：同 tth 不同强度——fake_a 慢 0.2 s、最强峰 3，
    其余文件最强峰 30。归一化开关的效果一眼可分。"""
    if path_str.endswith("fake_a.tif"):
        time.sleep(0.2)
        return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])
    return np.array([0.5, 1.0, 8.5]), np.array([10.0, 20.0, 30.0])


class TestCompare(unittest.TestCase):
    """[对比] 按钮（仅 1D）：勾选 >= 2 个文件 → 每个文件后台各算各
    的积分，曲线叠进同一张面板（自动分色 + 图例显示名）。同一勾选
    集合重复点 = 复用面板刷新；"对比归一化到最强峰"只动显示层。"""

    def _compare_axes(self, w):
        """取唯一的对比面板坐标轴（面板键以 "对比|" 开头）。"""
        keys = [k for k in w.plot_docks if k.startswith("对比|")]
        self.assertEqual(len(keys), 1)
        return w.plot_docks[keys[0]].widget().axes_1d

    def _plot_compare(self, w):
        """勾 fake_a + fake_b 点 [对比] 并等两条曲线到齐 → 返回坐标轴。"""
        with mock.patch.object(gui_app, "_compute_integration",
                               side_effect=_fake_compare_compute):
            w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
            w.compare_btn.click()
            ax = self._compare_axes(w)
            self.assertTrue(_wait_until(lambda: len(ax.lines) >= 2))
        return ax

    def test_compare_requires_two_checked_files(self):
        """只勾一个文件点 [对比] → 提示至少两个，不开面板。"""
        w = create_window()
        try:
            w.add_files(["data/fake_a.tif"])
            w.compare_btn.click()
            self.assertIn("对比至少勾选两个文件", w.log_text.toPlainText())
            self.assertFalse(
                [k for k in w.plot_docks if k.startswith("对比|")])
        finally:
            w.close()

    def test_compare_two_files_one_panel_with_legend(self):
        """两个文件 → 一张对比面板、两条曲线、图例 = 显示名。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            self.assertEqual(len(ax.lines), 2)
            self.assertEqual([line.get_label() for line in ax.lines],
                             ["fake_a.tif", "fake_b.tif"])
            self.assertIsNotNone(ax.get_legend())
            # 完成后对比面板成为编辑对象，日志报 2 条曲线
            self.assertTrue(w.focus_panel.startswith("对比|"))
            self.assertIn("对比完成：2 条曲线", w.log_text.toPlainText())
        finally:
            w.close()

    def test_normalize_on_by_default(self):
        """默认勾"对比归一化到最强峰" → 每条曲线最强峰都是 1.0。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            self.assertTrue(w.params["对比归一化"].isChecked())
            for line in ax.lines:
                self.assertAlmostEqual(float(np.max(line.get_ydata())),
                                       1.0, places=4)
        finally:
            w.close()

    def test_normalize_off_shows_raw_values(self):
        """关掉归一化点图像 [应用] → 按原始强度重画（不重算）。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            done_before = w.log_text.toPlainText().count("开始对比")
            w.params["对比归一化"].setChecked(False)
            w.findChild(QPushButton, "apply_image_btn").click()
            # 快照记下关归一化，fake_b 曲线回到原始强度（最强峰 30）
            dock = [d for k, d in w.plot_docks.items()
                    if k.startswith("对比|")][0]
            self.assertFalse(dock.params_snapshot["对比归一化"])
            ymax = max(float(np.max(line.get_ydata())) for line in ax.lines)
            self.assertAlmostEqual(ymax, 30.0, places=4)
            self.assertIn("[应用] 图像参数已重画：",
                          w.log_text.toPlainText())
            # 只重画不重算
            self.assertEqual(w.log_text.toPlainText().count("开始对比"),
                             done_before)
        finally:
            w.close()

    def test_reclick_same_selection_reuses_panel(self):
        """同一勾选集合重复点 [对比] → 还是那一张面板，重算刷新。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            dock_before = [d for k, d in w.plot_docks.items()
                           if k.startswith("对比|")][0]
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compare_compute):
                w.compare_btn.click()
                self.assertTrue(_wait_until(
                    lambda: w.log_text.toPlainText().count("对比完成") >= 2))
            self.assertEqual(len([k for k in w.plot_docks
                                  if k.startswith("对比|")]), 1)
            self.assertIs(dock_before, w.plot_docks[w.focus_panel])
            self.assertEqual(len(ax.lines), 2)
            self.assertEqual(w.log_text.toPlainText().count("开始对比"),
                             2)
        finally:
            w.close()

    def test_compare_data_apply_reruns_all(self):
        """对比面板是编辑对象时点数据 [应用] → 整组文件全部重算。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compare_compute):
                w.findChild(QPushButton, "apply_btn").click()
                self.assertTrue(_wait_until(
                    lambda: w.log_text.toPlainText().count("对比完成") >= 2))
            self.assertEqual(len(ax.lines), 2)
            self.assertIn("[应用] 重算编辑对象", w.log_text.toPlainText())
        finally:
            w.close()

    def test_compare_one_file_fails_others_still_drawn(self):
        """一个文件积分失败 → 日志报错、其余曲线照常画完。"""
        def boom(path_str, geom, npt):
            if path_str.endswith("fake_a.tif"):
                raise ValueError("积分炸了")
            return np.array([0.5, 1.0, 8.5]), np.array([10.0, 20.0, 30.0])

        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=boom):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                w.compare_btn.click()
                ax = self._compare_axes(w)
                self.assertTrue(_wait_until(
                    lambda: "对比完成" in w.log_text.toPlainText()))
            log = w.log_text.toPlainText()
            self.assertIn("积分失败", log)
            self.assertIn("对比完成：1 条曲线", log)
            self.assertEqual(len(ax.lines), 1)
            self.assertEqual(ax.lines[0].get_label(), "fake_b.tif")
        finally:
            w.close()

    def test_compare_three_files(self):
        """三个文件也能叠：三条曲线、图例齐全、标题 = A 等 3 个文件。"""
        def fake3(path_str, geom, npt):
            scale = {"fake_a.tif": 1, "fake_b.tif": 10,
                     "fake_c.tif": 100}[Path(path_str).name]
            return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0]) * scale

        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=fake3):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif",
                             "data/fake_c.tif"])
                w.compare_btn.click()
                ax = self._compare_axes(w)
                self.assertTrue(_wait_until(lambda: len(ax.lines) >= 3))
            self.assertEqual([line.get_label() for line in ax.lines],
                             ["fake_a.tif", "fake_b.tif", "fake_c.tif"])
            self.assertIn("等 3 个文件", ax.get_title())
            self.assertIn("对比完成：3 条曲线", w.log_text.toPlainText())
            # 颜色循环不重样（前三条 = C0/C1/C2）
            self.assertEqual(len({line.get_color() for line in ax.lines}), 3)
        finally:
            w.close()

    def test_compare_all_files_fail(self):
        """所有文件积分都失败 → 面板留空 + 明说失败（不装成"完成"）。"""
        def all_boom(path_str, geom, npt):
            raise ValueError("全炸了")

        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=all_boom):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                w.compare_btn.click()
                ax = self._compare_axes(w)
                self.assertTrue(_wait_until(
                    lambda: "对比失败" in w.log_text.toPlainText()))
            self.assertIn("对比失败：所有文件的积分都失败了",
                          w.log_text.toPlainText())
            self.assertEqual(len(ax.lines), 0)
        finally:
            w.close()

    def test_compare_stale_generation_dropped(self):
        """旧一轮还在飞时重复点 [对比] → 旧结果全部作废，只收新代。"""
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compare_compute):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                w.compare_btn.click()
                w.compare_btn.click()   # 第一代 fake_a 还在睡 0.2s
                ax = self._compare_axes(w)
                self.assertTrue(_wait_until(
                    lambda: w.log_text.toPlainText().count("对比完成") >= 1))
            time.sleep(0.4)   # 第一代的迟到结果此时早已落地
            QApplication.processEvents()
            # 只有第二代算数：一次"对比完成"、两条曲线
            self.assertEqual(w.log_text.toPlainText().count("对比完成"), 1)
            self.assertEqual(len(ax.lines), 2)
        finally:
            w.close()

    def test_compare_uses_1d_display_params(self):
        """1D 显示参数（对数纵轴）对对比面板同样生效（图像 [应用]）。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            w.params["对数纵轴"].setChecked(True)
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertEqual(ax.get_yscale(), "log")
        finally:
            w.close()


class TestArrangeModeClose(unittest.TestCase):
    """此前零覆盖的三个面：横排/竖排、[校准] 模式开关、带在飞任务关窗。"""

    def test_arrange_buttons_log(self):
        """横排/竖排各点一次 → 日志报告面板数，不崩。"""
        w = create_window()
        try:
            w.show()   # 面板要可见才参与重排（offscreen 下不 show 不可见）
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines)
                    > 0))
            w.arrange_buttons["横排"].click()
            self.assertIn("已横排 2 个面板", w.log_text.toPlainText())
            w.arrange_buttons["竖排"].click()
            self.assertIn("已竖排 2 个面板", w.log_text.toPlainText())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calib_mode_toggle(self):
        """[校准] 按下 = 校准模式提示，弹起 = 分析模式提示。"""
        w = create_window()
        try:
            w.calib_btn.click()
            self.assertIn("进入校准模式", w.log_text.toPlainText())
            self.assertIn("校准模式", w.mode_label.text())
            w.calib_btn.click()
            self.assertIn("回到分析模式", w.log_text.toPlainText())
            self.assertIn("分析模式", w.mode_label.text())
        finally:
            w.close()

    def test_close_with_inflight_task_discards(self):
        """后台任务还在飞时关窗 → 等任务收尾（discard），不挂死不崩溃。"""
        def slow(path_str, geom, npt):
            time.sleep(1.5)
            return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])

        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=slow):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")   # 任务立刻在后台开睡
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                self.assertTrue(w.close())   # 关窗 = 等完 1.5s 收尾
        finally:
            w.close()


def _hover_event(ax, xdata):
    """构造一个像真鼠标停在 (xdata, 线上 y) 处的 matplotlib 事件。

    x/y 填像素坐标（经 transData 换算），模拟画布真实派发的
    motion_notify_event；这样 _hover_motion 里的选线逻辑走真路径。
    """
    xd = ax.lines[0].get_xdata()
    ydata = float(np.interp(xdata, xd, ax.lines[0].get_ydata()))
    px, py = ax.transData.transform((xdata, ydata))
    return SimpleNamespace(inaxes=ax, xdata=xdata, ydata=ydata, x=px, y=py)


class TestPlotFixedSize(unittest.TestCase):
    """A：新面板固定 5:3 开局，不继承上次挤过的旧尺寸。"""

    def test_new_panel_opens_fixed_ratio(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                drawn = _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
                self.assertTrue(drawn)
            QApplication.processEvents()
            dock = _dock(w, "1D", "data/fake_b.tif")
            self.assertGreater(dock.width(), 350,
                               "新面板应有接近 500px 的固定宽度")
            self.assertLess(dock.width(), 750)
            # 高度/宽度 ≈ 3/5（标题栏算在坞里，给 ±0.15 容差）
            ratio = dock.height() / dock.width()
            self.assertGreater(ratio, 0.5, f"新面板比例走样：{ratio:.2f}")
            self.assertLess(ratio, 0.75, f"新面板比例走样：{ratio:.2f}")
        finally:
            w.hide()   # 窗口显示过关窗会弹"保存询问"模态框（offscreen 挂死）
            w.close()


class TestZoomToolbar(unittest.TestCase):
    """D：每个 1D 面板带自己的缩放工具栏；占位面板没有。"""

    def test_toolbar_present_on_1d(self):
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            widget = _dock(w, "1D", "data/fake_b.tif").widget()
            self.assertIsInstance(widget.toolbar, NavigationToolbar2QT)
            names = [t[0] for t in widget.toolbar.toolitems if t[0]]
            for tool in ("Zoom", "Pan", "Home"):
                self.assertIn(tool, names,
                              f"缩放工具栏应有 {tool} 工具")
        finally:
            w.close()

    def test_placeholder_has_no_toolbar(self):
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "2D")
                _wait_until(lambda: "2D|data/fake_b.tif" in w.plot_docks)
            widget = _dock(w, "2D", "data/fake_b.tif").widget()
            self.assertIsInstance(widget, QLabel, "占位面板仍是标签")
            self.assertFalse(hasattr(widget, "toolbar"),
                             "占位面板不应有缩放工具栏")
        finally:
            w.close()


class TestHoverDot(unittest.TestCase):
    """E：鼠标悬停 = 曲线上出白边点 + 状态栏实时坐标；离开清空。"""

    def _open_1d(self, w):
        with mock.patch.object(gui_app, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files(["data/fake_b.tif"])
            _open_view(w, "1D")
            _wait_until(
                lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
        return _dock(w, "1D", "data/fake_b.tif")

    def test_hover_shows_marker_and_coords(self):
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            gui_app._hover_motion(w, key, _hover_event(ax, 0.6))
            marker = dock.hover_marker
            self.assertIsNotNone(marker, "悬停后应出现取点标记")
            self.assertTrue(marker.get_visible())
            # 吸附最近真实数据点：假积分 x=[0.5, 1.0, 8.5] → 0.6 归 0.5
            self.assertEqual(list(marker.get_xdata()), [0.5])
            self.assertEqual(list(marker.get_ydata()), [1.0])
            # 状态栏坐标 = 面板名 + 2θ + 强度
            text = w.coord_label.text()
            self.assertIn("fake_b.tif", text)
            self.assertIn("2θ = 0.5°", text)
            self.assertIn("强度 = 1", text)
        finally:
            w.close()

    def test_hover_clears_on_leave(self):
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            gui_app._hover_motion(w, key, _hover_event(ax, 0.6))
            gui_app._hover_leave(w, key)
            self.assertFalse(dock.hover_marker.get_visible(),
                             "离开后取点标记应藏起来")
            self.assertEqual(w.coord_label.text(), "",
                             "离开后坐标标签应清空（标签本身常驻）")
        finally:
            w.close()

    def test_marker_recreated_after_redraw(self):
        """重算/[应用] 会 ax.clear() 掉旧标记 → 下次悬停懒重建。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            gui_app._hover_motion(w, key, _hover_event(ax, 0.6))
            old = dock.hover_marker
            tth, it = np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])
            gui_app._draw_1d(w, dock, tth, it)
            self.assertIsNot(old.axes, ax, "重画后旧标记应与旧轴断开")
            gui_app._hover_motion(w, key, _hover_event(ax, 1.0))
            self.assertIs(dock.hover_marker.axes, ax,
                          "再次悬停应在当前轴上重建标记")
            self.assertEqual(list(dock.hover_marker.get_xdata()), [1.0])
        finally:
            w.close()

    def test_compare_hover_shows_filename(self):
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                w.compare_btn.click()
                key = next(k for k in w.plot_docks
                           if k.startswith("对比|"))
                dock = w.plot_docks[key]
                _wait_until(
                    lambda: len(dock.widget().axes_1d.lines) >= 2)
            ax = dock.widget().axes_1d
            gui_app._hover_motion(w, key, _hover_event(ax, 0.5))
            text = w.coord_label.text()
            self.assertIn("fake_a.tif", text,
                          "对比图坐标前缀 = 曲线（文件）名")
            marker = dock.hover_marker
            self.assertEqual(marker.get_color(),
                             ax.lines[0].get_color(),
                             "取点颜色 = 所选曲线的颜色")
        finally:
            w.close()


class TestArrangeGrid(unittest.TestCase):
    """C：横排/竖排重排 = 每格 5:3 的格子，不再把图挤变形。"""

    def _open_two(self, w):
        with mock.patch.object(gui_app, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
            _open_view(w, "1D")
            both = _wait_until(
                lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            self.assertTrue(both)

    def _check_grid_ratio(self, w, mode):
        w.arrange_buttons[mode].click()
        QApplication.processEvents()
        docks = [d for d in w.plot_docks.values() if d.isVisible()]
        self.assertEqual(len(docks), 2)
        for d in docks:
            ratio = d.height() / d.width()
            self.assertGreater(ratio, 0.45,
                               f"{mode}后面板被挤扁：{ratio:.2f}")
            self.assertLess(ratio, 0.85,
                            f"{mode}后面板被拉长：{ratio:.2f}")
        self.assertIn(f"已{mode} 2 个面板", w.log_text.toPlainText())
        return docks

    def test_row_arrange_keeps_ratio(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            docks = self._check_grid_ratio(w, "横排")
            # 一行格子：两个面板顶边对齐、互不重叠
            self.assertAlmostEqual(docks[0].geometry().top(),
                                   docks[1].geometry().top(), delta=2)
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_column_arrange_keeps_ratio(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            docks = self._check_grid_ratio(w, "竖排")
            # 一列格子：左边对齐、上下不重叠
            self.assertAlmostEqual(docks[0].geometry().left(),
                                   docks[1].geometry().left(), delta=2)
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


class TestShiftAspect(unittest.TestCase):
    """B：Shift + 拖边 = 5:3 等比例缩放（纯函数 + 半集成两层验证）。"""

    def test_aspect_math_round_trip(self):
        """宽↔高换算来回一趟误差 ≤1px（容差 2px 判定才不会打转）。"""
        for w_px in (100, 457, 500, 800, 1234):
            h = gui_app._aspect_height(w_px)
            self.assertEqual(round(w_px * 3 / 5), h)
            self.assertLessEqual(abs(gui_app._aspect_width(h) - w_px), 1)

    def test_plain_resize_is_free_stretch(self):
        """没按 Shift：普通拖动不碰比例（自由拉伸）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            dock = _dock(w, "1D", "data/fake_b.tif")
            before = (dock.width(), dock.height())
            gui_app._on_dock_resized(
                w, dock, modifiers=Qt.KeyboardModifier.NoModifier)
            self.assertEqual((dock.width(), dock.height()), before)
        finally:
            w.close()

    def test_shift_resize_enforces_ratio(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            with mock.patch.object(gui_app, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                both = _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                    and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
                self.assertTrue(both)
            QApplication.processEvents()
            d1 = _dock(w, "1D", "data/fake_a.tif")
            # 人为改窄（模拟用户拖竖把手后的尺寸），并伪造"拖动前"
            # 的记录（上一帧宽度 300 → 本次 400 = 宽变了），让处理
            # 函数认出是"宽变了"这一分支
            w.inner.resizeDocks([d1], [400], Qt.Horizontal)
            QApplication.processEvents()
            d1._last_size = (300, d1.height())
            gui_app._on_dock_resized(
                w, d1, modifiers=Qt.KeyboardModifier.ShiftModifier)
            QApplication.processEvents()
            # 画布内容（不含标题栏）应回到 5:3
            ratio = d1.widget().height() / d1.width()
            self.assertGreater(ratio, 0.45, f"Shift 后画布仍扁：{ratio:.2f}")
            self.assertLess(ratio, 0.75, f"Shift 后画布仍长：{ratio:.2f}")
            # Shift 动态上限已登记；下一次普通拖动把它解除 → 自由拉伸
            self.assertIsNotNone(d1._shift_cap)
            gui_app._on_dock_resized(
                w, d1, modifiers=Qt.KeyboardModifier.NoModifier)
            self.assertIsNone(d1._shift_cap)
            self.assertEqual(d1.maximumHeight(), 16777215)
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


if __name__ == "__main__":
    unittest.main()
