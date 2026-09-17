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

import numpy as np
from PySide6.QtCore import QEvent, QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton

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
                                   return_value=[]), \
                 mock.patch.object(QFileDialog, "getSaveFileName") as dlg:
                w.findChild(QPushButton, "save_btn").click()
                self.assertFalse(dlg.called)
                self.assertIn("已取消保存", w.log_text.toPlainText())
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


if __name__ == "__main__":
    unittest.main()
