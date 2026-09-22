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
  - _collect_geometry：面板输入覆盖配置条目值；2θ 上下限（积分设置）
    经 geom 传进计算链路（引擎侧区间约束见 test_partial_ring.py）；
  - 绘图区 = MDI 子窗口（每图一窗）：自由缩放（拖过 = 记画布比例，
    主窗口缩放不牵动子窗口）、开新图不动旧图、[弹出]/[收回] 搬进
    搬出独立 OS 窗口；
  - 自装抓手：内容四边 5px 抓取带 + 四角 16px 抓取区 + 右下角
    把手（▙），悬停换方向光标、按住拖 = 拉伸容器；
  - 横排/竖排 = 按类型分层摆位置（开图先后排序）不缩放，溢出靠
    QMdiArea 滚动条兜底；摆图前滚动自动归零（滚动状态下的 move
    会混入滚动偏移、图越排越漂）；点面板窗口任何位置 = 选中该
    面板；滚轮 = 只滚动绘图区；放大镜开关点亮时滚轮以光标为中心
    缩放每格 10% + 左键拖框放大，熄灭时左键 = 平移；总缩放 =
    Ctrl+滚轮 / 底部 − 100% + 按钮，绘图区全体同比缩放
    （50%–200%），弹出去的不参与、平铺不碰它；新图左上角
    24px 小错位级联（6 档循环）、按当前总缩放开；
  - 关闭面板 = 关闭即遗忘：重开全新默认，关窗询问只算开着的图，
    在飞任务/旧代对比结果迟到即作废；
  - 校准工作台（TestCalibration / TestSaveCalibConfig）：[校准] 进
    模式 = 参数坞换页 + 中央校准图面板开出（勾选的第一个文件；没勾
    文件只提示）；自动校准后台跑（mock 引擎）→ 自动列 + 保存区提示；
    校准图点环判环吸附/拒点、撤销/清空、手动校准 → 手动列 + Δ 列；
    面板关/退出模式后迟到结果作废；连点重跑旧任务过期；[保存为配置]
    = key/label 校验 + 落盘临时用户文件 + 下拉框同步自动选中 +
    覆盖确认；校准页 [返回分析模式] 出口 + 开关文字随状态变
    （校准 ↔ 退出校准）。
  - 配置条目删除（TestDeleteConfig）：[删除] 只删用户条目（内置
    置灰 + 处理函数双保险）、确认框取消保留、删后回退默认条目、
    磁盘同步（config.remove_user_config 单测）。

等待方式与 test_gui_tasks.py 相同：回调 + processEvents 轮询
（QSignalSpy.wait 不处理跨线程投递，见该文件说明）。

运行：python -m unittest discover -s tests -v
"""
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from types import SimpleNamespace

import numpy as np
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QDropEvent, QPointingDevice, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea,
    QSplitter, QSpinBox, QVBoxLayout)

from xrd_toolkit import config as config_mod
from xrd_toolkit.services.integrator import lab6_theoretical_2theta
from xrd_toolkit.gui import app as gui_app
from xrd_toolkit.gui import customize as gui_customize
from xrd_toolkit.gui.app import create_window
# 拆分后 patch 目标 = 调用点所在的模块（gui_app 只是兼容再导出，
# 打它的名字截不住别的模块里的裸名查找）
from xrd_toolkit.gui import calib as gui_calib
from xrd_toolkit.gui import panel_state as gui_state
from xrd_toolkit.gui import panels as gui_panels
from xrd_toolkit.gui import plot_views as gui_views

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


def _hover_until(widget, pos, expect, timeout_ms=5000):
    """反复发悬停移动事件直到光标到期望形状。

    QTest.mouseMove 的悬停移动（没有按住鼠标的隐式抓取）走窗口
    系统投递，全套件负载下单发一次 + 一轮事件循环会漏投（实测
    过滤器根本没收到）。真实用户鼠标是连续移动流，重发贴近真实
    且稳定。每次先挪到旁边点再挪到目标点：保证每个目标移动都有
    非零位移（连续两次发同一位置可能被窗口系统当静止吞掉），
    也和真实鼠标"一路滑过去"的路径一致。按住拖拽中的移动事件
    有隐式抓取、直接投递，不受影响。
    """
    # 先把上一测试关窗遗留的延迟删除处理干净：在鼠标事件泵送中途
    # 销毁旧窗口会把窗口系统的鼠标移动投递整断（探针实证：之后所有
    # 移动事件都送不到过滤器，悬停光标再也不会变）
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    deadline = time.time() + timeout_ms / 1000
    jitter = QPoint(max(0, pos.x() - 60), pos.y())
    while time.time() < deadline:
        QTest.mouseMove(widget, jitter)
        QApplication.processEvents()
        QTest.mouseMove(widget, pos)
        QApplication.processEvents()
        if expect():
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
    """取某 1D 面板自己的坐标轴（容器 = 子窗口或弹出窗口，经 _content 取内容）。"""
    return gui_app._content(_dock(w, view, path_str)).axes_1d


def _resize_panel(w, dock, wpx, hpx):
    """模拟用户拖面板边框：改容器尺寸并泵干事件（画布跟着变，
    比例记忆经 _on_canvas_resized 记录）。"""
    dock.resize(wpx, hpx)
    for _ in range(5):
        QApplication.processEvents()


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
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
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
            a_done = threading.Event()

            def fake_ordered(path_str, geom, npt):
                # A 慢 0.2 s；B 等 A 完成再返回：完成顺序固定 A 先
                # B 后。不能靠"B 快所以 B 先完成"——线程启动时机在
                # 全套件负载下不可控（实测 B 的线程晚 0.4 s 才起，
                # 反而最后完成），焦点断言会飘
                if path_str.endswith("fake_a.tif"):
                    time.sleep(0.2)
                    a_done.set()
                elif path_str.endswith("fake_b.tif"):
                    a_done.wait(timeout=5)
                    # 再垫 0.2 s：a_done 在 A 返回之前就置位了，B
                    # 光等事件可能比 A 更早投递完成信号（跨线程排队
                    # 投递不保证顺序），垫一下让 B 确定最后完成
                    time.sleep(0.2)
                return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])

            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=fake_ordered):
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
            # 最后完成的图成为编辑对象（B 被安排等 A 完成再返回）
            self.assertTrue(
                _axes(w, "1D", "data/fake_a.tif").get_title()
                .startswith("fake_a.tif"))
            self.assertNotIn("已忽略", w.log_text.toPlainText())
            self.assertEqual(w.focus_panel, "1D|data/fake_b.tif")
        finally:
            w.close()

    def test_four_views_registered(self):
        """四个视图按钮全部接线：注册表齐套（旧版 2D 是占位面板）。

        热图只占 builder 表（多文件 → 一张面板，走 _plot_heatmap，
        不占 runner 表——它复用 1D 的 _spawn 后台积分）。
        """
        self.assertEqual(set(gui_views._VIEW_RUNNERS),
                         {"2D", "剖面", "1D", "瀑布"})
        self.assertEqual(set(gui_views._VIEW_BUILDERS),
                         {"2D", "剖面", "1D", "瀑布", "热图"})


class TestNewViews(unittest.TestCase):
    """2D / 剖面 / 瀑布 视图接线：各自后台计算 → 画进面板 →
    图像参数 [应用]（只重画，只有剖面角度变过才重算）。"""

    def test_2d_view_draws_image_and_caches(self):
        w = create_window()
        try:
            fake_image = np.arange(400, dtype=float).reshape(20, 20)
            with mock.patch.object(gui_views, "load_diffraction_image",
                                   return_value=fake_image):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "2D")
                drawn = _wait_until(lambda: len(gui_app._content(
                    _dock(w, "2D", "data/fake_b.tif")).axes_2d.images) > 0)
                self.assertTrue(drawn, "点 2D 后应画出图像")
            dock = _dock(w, "2D", "data/fake_b.tif")
            self.assertEqual(dock.windowTitle(), "2D_fake_b.tif")
            ax = gui_app._content(dock).axes_2d
            self.assertEqual(len(ax.images), 1)
            # 束心十字画在当前配置的 beam_center 上（(行, 列) → x=列, y=行）
            cy, cx = w.config["beam_center"]
            self.assertAlmostEqual(ax.lines[0].get_xdata()[0], cx)
            self.assertAlmostEqual(ax.lines[0].get_ydata()[0], cy)
            self.assertIn("读取完成：fake_b.tif", w.log_text.toPlainText())
            self.assertEqual(w.focus_panel, "2D|data/fake_b.tif")
            # 图进了路径键缓存（自动对比度直接复用，不重复解码）
            self.assertIn("data/fake_b.tif", w._image_cache)
        finally:
            w.close()

    def test_profile_view_uses_snapshot_angle_and_recomputes_on_apply(self):
        w = create_window()
        try:
            profile_calls = []

            def fake_profile(image, center, angle_deg=0.0):
                profile_calls.append((center, angle_deg))
                t = np.linspace(-100.0, 100.0, 21)
                return t, 10.0 * np.ones_like(t)

            with mock.patch.object(gui_views, "load_diffraction_image",
                                   return_value=np.zeros((10, 10))), \
                 mock.patch.object(gui_views, "line_profile",
                                   side_effect=fake_profile):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "剖面")
                drawn = _wait_until(lambda: len(gui_app._content(
                    _dock(w, "剖面", "data/fake_b.tif")).axes_profile.lines) > 0)
                self.assertTrue(drawn, "点 剖面 后应画出曲线")
            dock = _dock(w, "剖面", "data/fake_b.tif")
            self.assertEqual(dock.windowTitle(), "剖面_fake_b.tif")
            # 首画：角度 = 面板快照默认 0，中心 = 配置 beam_center
            self.assertEqual(profile_calls[0][1], 0.0)
            self.assertEqual(profile_calls[0][0], tuple(w.config["beam_center"]))
            self.assertIn("剖面完成：fake_b.tif", w.log_text.toPlainText())
            # 改角度点图像 [应用] → 按新角度重算（不是只重画）
            w.params["剖面角度 (°)"].setValue(45.0)
            with mock.patch.object(gui_views, "load_diffraction_image",
                                   return_value=np.zeros((10, 10))), \
                 mock.patch.object(gui_views, "line_profile",
                                   side_effect=fake_profile):
                w.findChild(QPushButton, "apply_image_btn").click()
                recomputed = _wait_until(lambda: len(profile_calls) == 2)
                self.assertTrue(recomputed, "角度变了应重算剖面")
            self.assertEqual(profile_calls[1][1], 45.0)
            self.assertIn("剖面角度改为 45°，重新计算",
                          w.log_text.toPlainText())
            # 角度没变再点 [应用] → 只重画，不再算
            with mock.patch.object(gui_views, "load_diffraction_image",
                                   return_value=np.zeros((10, 10))), \
                 mock.patch.object(gui_views, "line_profile",
                                   side_effect=fake_profile):
                w.findChild(QPushButton, "apply_image_btn").click()
                QApplication.processEvents()
            self.assertEqual(len(profile_calls), 2, "角度没变不应重算")
            self.assertIn("[应用] 图像参数已重画：剖面_fake_b.tif",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_waterfall_stacks_36_sectors(self):
        w = create_window()
        try:
            tth = np.linspace(1.0, 8.0, 30)
            i2d = np.ones((30, 36))
            chi = np.linspace(-175.0, 175.0, 36)   # 真引擎惯例：χ 从 -175° 起

            def fake_sectors(image, **kw):
                self.assertEqual(kw["n_sectors"], 36)
                return tth, i2d, chi

            with mock.patch.object(gui_views, "load_diffraction_image",
                                   return_value=np.zeros((10, 10))), \
                 mock.patch.object(gui_views, "integrate_sectors",
                                   side_effect=fake_sectors):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "瀑布")
                drawn = _wait_until(lambda: len(gui_app._content(
                    _dock(w, "瀑布", "data/fake_b.tif")).axes_waterfall.lines) > 0)
                self.assertTrue(drawn, "点 瀑布 后应画出堆叠曲线")
            dock = _dock(w, "瀑布", "data/fake_b.tif")
            ax = gui_app._content(dock).axes_waterfall
            self.assertEqual(len(ax.lines), 36, "36 条扇区曲线")
            # 每行基线标 χ（第一条 -175°），曲线名 = 扇区名（悬停读数）
            self.assertEqual(ax.get_yticklabels()[0].get_text(), "-175°")
            self.assertEqual(ax.lines[0].get_label(), "-175°")
            self.assertIn("扇形积分完成：fake_b.tif（36 扇区 × 30 点）",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_image_params_apply_redraws_2d_contrast(self):
        """2D 对比度参数：关自动 + 手填 → [应用] 用已有图按手填值重画。"""
        w = create_window()
        try:
            fake_image = np.arange(400, dtype=float).reshape(20, 20)
            with mock.patch.object(gui_views, "load_diffraction_image",
                                   return_value=fake_image):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "2D")
                self.assertTrue(_wait_until(lambda: len(gui_app._content(
                    _dock(w, "2D", "data/fake_b.tif")).axes_2d.images) > 0))
            ax = gui_app._content(_dock(w, "2D", "data/fake_b.tif")).axes_2d
            self.assertGreater(ax.images[0].norm.vmin, 1.0, "自动 = 分位值")
            w.params["自动对比度"].setChecked(False)
            w.params["对比度下限"].setValue(5.0)
            w.params["对比度上限"].setValue(50.0)
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertEqual(ax.images[0].norm.vmin, 5.0)
            self.assertEqual(ax.images[0].norm.vmax, 50.0)
            self.assertIn("[应用] 图像参数已重画：2D_fake_b.tif",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_waterfall_apply_redraws_without_recompute(self):
        """瀑布面板图像 [应用]：只重画已有结果，不重新扇形积分。"""
        w = create_window()
        try:
            tth = np.linspace(1.0, 8.0, 10)
            i2d = np.ones((10, 36))
            chi = np.linspace(5.0, 355.0, 36)
            with mock.patch.object(gui_views, "load_diffraction_image",
                                   return_value=np.zeros((10, 10))), \
                 mock.patch.object(
                    gui_views, "integrate_sectors",
                    return_value=(tth, i2d, chi)) as fake_sectors:
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "瀑布")
                self.assertTrue(_wait_until(lambda: len(gui_app._content(
                    _dock(w, "瀑布", "data/fake_b.tif")).axes_waterfall.lines) > 0))
                w.findChild(QPushButton, "apply_image_btn").click()
                QApplication.processEvents()
                self.assertEqual(fake_sectors.call_count, 1,
                                 "[应用] 不应重新积分")
        finally:
            w.close()


class TestApplyAndFocus(unittest.TestCase):
    """[应用] 重算焦点面板；点图面板切焦点。"""

    def test_apply_reruns_focused_view(self):
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
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
            QTest.mouseClick(gui_app._content(_dock(w, "2D", "data/fake_b.tif")),
                             Qt.LeftButton)
            self.assertEqual(w.focus_panel, "2D|data/fake_b.tif")
            self.assertIn("2D_fake_b.tif", w.focus_label.text())
        finally:
            w.close()

    def test_same_panel_latest_task_wins(self):
        """同面板连点两次：先开的慢任务晚到 → 丢弃，不得覆盖新图。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
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
    with mock.patch.object(gui_views, "_compute_integration",
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
            fig = gui_app._content(dock).figure
            with mock.patch.object(gui_app, "_choose_panels",
                                   return_value=[dock]), \
                 mock.patch.object(gui_app, "_ask_save_options",
                                   return_value={"dpi": 300, "fmt": "png"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("/tmp/out", "PNG 图片 (*.png)")) as dlg, \
                 mock.patch.object(fig, "savefig") as savefig:
                w.findChild(QPushButton, "save_btn").click()
                self.assertTrue(dlg.called)
                savefig.assert_called_once_with("/tmp/out.png", dpi=300)   # 自动补 .png + 300 dpi
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
            with mock.patch.object(gui_views, "_compute_integration",
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
                 mock.patch.object(gui_app, "_ask_save_options",
                                   return_value={"dpi": 300, "fmt": "png"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   side_effect=[("/tmp/a", ""),
                                                ("", "")]) as dlg, \
                 mock.patch.object(gui_app._content(d1).figure, "savefig"):
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
                 mock.patch.object(gui_app, "_ask_save_options",
                                   return_value={"dpi": 300, "fmt": "png"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("/no/such/dir/out.png", "")), \
                 mock.patch.object(gui_app._content(dock).figure, "savefig",
                                   side_effect=OSError("磁盘写不进")):
                self.assertFalse(gui_app._save_figures(w))
            log = w.log_text.toPlainText()
            self.assertIn("保存失败 1D_fake_b.tif", log)
        finally:
            w.close()


class TestSaveOptions(unittest.TestCase):
    """保存选项弹窗：分辨率 dpi + 格式（PNG/TIF）贯穿单张与批量保存。"""

    def test_dialog_defaults(self):
        """默认 300 dpi + PNG（论文/报告印刷的常用起步值）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views.QDialog, "exec",
                                   return_value=QDialog.Accepted):
                self.assertEqual(gui_views._ask_save_options(w),
                                 {"dpi": 300, "fmt": "png"})
        finally:
            w.close()

    def test_dialog_custom_values(self):
        """改 600 dpi + TIF → 返回对应值（fmt 同时当扩展名用）。"""
        w = create_window()
        try:
            def fake_exec(dlg):
                dlg.findChild(QSpinBox, "save_dpi_spin").setValue(600)
                dlg.findChild(QComboBox, "save_fmt_combo").setCurrentIndex(1)
                return QDialog.Accepted
            # new= 放普通函数：函数是描述符，实例访问自动绑定 dlg；
            # return_value 的 MagicMock 不绑定（Shiboken 方法也不吃 autospec）
            with mock.patch.object(gui_views.QDialog, "exec", new=fake_exec):
                self.assertEqual(gui_views._ask_save_options(w),
                                 {"dpi": 600, "fmt": "tif"})
        finally:
            w.close()

    def test_dialog_cancel_returns_none(self):
        w = create_window()
        try:
            with mock.patch.object(gui_views.QDialog, "exec",
                                   return_value=QDialog.Rejected):
                self.assertIsNone(gui_views._ask_save_options(w))
        finally:
            w.close()

    def test_batch_cancel_options_aborts_save(self):
        """批量保存：选项弹窗取消 → 不弹文件名框、返回 False（关窗留在程序里）。"""
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            with mock.patch.object(gui_app, "_choose_panels",
                                   return_value=[dock]), \
                 mock.patch.object(gui_app, "_ask_save_options",
                                   return_value=None), \
                 mock.patch.object(QFileDialog, "getSaveFileName") as dlg:
                self.assertFalse(gui_app._save_figures(w))
                self.assertFalse(dlg.called)
                self.assertIn("已取消保存", w.log_text.toPlainText())
        finally:
            w.close()

    def test_batch_tif_saves_with_extension_and_dpi(self):
        """批量保存 TIF：文件名过滤器带 TIF、自动补 .tif + savefig 收到 dpi。"""
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            fig = gui_app._content(dock).figure
            with mock.patch.object(gui_app, "_choose_panels",
                                   return_value=[dock]), \
                 mock.patch.object(gui_app, "_ask_save_options",
                                   return_value={"dpi": 600, "fmt": "tif"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("/tmp/b", "TIF 图片 (*.tif)")) as dlg, \
                 mock.patch.object(fig, "savefig") as savefig:
                self.assertTrue(gui_app._save_figures(w))
                self.assertIn("TIF", dlg.call_args[0][3])
                savefig.assert_called_once_with("/tmp/b.tif", dpi=600)
        finally:
            w.close()

    def test_panel_save_cancel_options_keeps_unsaved(self):
        """单面板工具栏 [Save]：选项弹窗取消 → 不弹文件名框、记账不动。"""
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            with mock.patch.object(gui_views, "_ask_save_options",
                                   return_value=None), \
                 mock.patch.object(QFileDialog, "getSaveFileName") as dlg:
                gui_app._content(dock).toolbar.save_figure()
                self.assertFalse(dlg.called)
                self.assertFalse(dock.figure_saved)
        finally:
            w.close()

    def test_panel_save_tif_dpi(self):
        """单面板保存 TIF 600 dpi：自动补 .tif + savefig 收到 dpi。"""
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            fig = gui_app._content(dock).figure
            with mock.patch.object(gui_views, "_ask_save_options",
                                   return_value={"dpi": 600, "fmt": "tif"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("/tmp/panel_tif", "TIF 图片 (*.tif)")) as dlg, \
                 mock.patch.object(fig, "savefig") as savefig:
                gui_app._content(dock).toolbar.save_figure()
                self.assertIn("TIF", dlg.call_args[0][3])
                savefig.assert_called_once_with("/tmp/panel_tif.tif", dpi=600)
                self.assertTrue(dock.figure_saved)
        finally:
            w.close()


class TestCurveColors(unittest.TestCase):
    """任务六·色彩：曲线配色参数（固定色序色板）+ Customize 逐条自定义色。"""

    # 高对比色板 8 槽（OKLab 校验色盲安全；颜色跟着文件走不跟排序走）
    _SLOTS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100",
              "#e87ba4", "#008300", "#4a3aa7", "#e34948")

    def test_curve_color_slots(self):
        """高对比固定顺序、槽尽回槽 0 复用；默认 = C 循环/单曲线蓝；
        未知色板兜底高对比。"""
        self.assertEqual([gui_state._curve_color("高对比", i)
                          for i in range(8)], list(self._SLOTS))
        self.assertEqual(gui_state._curve_color("高对比", 8),
                         self._SLOTS[0])   # 第 9 条回槽 0（靠图例文字分辨）
        self.assertEqual(gui_state._curve_color("默认", 0), "C0")
        self.assertEqual(gui_state._curve_color("默认", 10), "C0")   # 越界复用
        self.assertEqual(gui_state._curve_color("高对比", 0, single=True),
                         self._SLOTS[0])
        self.assertEqual(gui_state._curve_color("默认", 0, single=True), "b")
        self.assertEqual(gui_state._curve_color("乱写的", 2),
                         self._SLOTS[2])   # 未知色板兜底

    def test_draw_1d_follows_palette_param(self):
        """1D 单曲线：默认高对比 = 第 1 槽蓝；快照切"默认"重画 = 传统蓝 b。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
            ax = _axes(w, "1D", "data/fake_b.tif")
            self.assertEqual(ax.lines[0].get_color(), self._SLOTS[0])
            dock = _dock(w, "1D", "data/fake_b.tif")
            dock.params_snapshot["曲线配色"] = "默认"
            gui_views._draw_1d(w, dock, dock.last_tth, dock.last_intensity)
            self.assertEqual(ax.lines[0].get_color(), "b")
        finally:
            w.close()

    def _plot_compare(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compare_compute):
            w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
            w.compare_btn.click()
            keys = [k for k in w.plot_docks if k.startswith("对比|")]
            self.assertEqual(len(keys), 1)
            ax = gui_app._content(w.plot_docks[keys[0]]).axes_1d
            self.assertTrue(_wait_until(lambda: len(ax.lines) >= 2))
        return keys[0], ax

    def test_compare_uses_palette_slots_and_override(self):
        """对比曲线按色板槽着色；自定义色优先；换色板重画不被打回旧色。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            self.assertEqual([line.get_color() for line in ax.lines],
                             [self._SLOTS[0], self._SLOTS[1]])
            # 逐条自定义：第一条换红 → 重画时自定义优先
            dock.curve_colors = {"fake_a.tif": "#ff0000"}
            gui_views._redraw_compare(w, key)
            self.assertEqual(ax.lines[0].get_color(), "#ff0000")
            self.assertEqual(ax.lines[1].get_color(), self._SLOTS[1])
            # 换"默认"配色 → 颜色按新参数重画（旧色不被套回）
            dock.params_snapshot["曲线配色"] = "默认"
            gui_views._redraw_compare(w, key)
            self.assertEqual([line.get_color() for line in ax.lines],
                             ["#ff0000", "C1"])
        finally:
            w.close()

    def test_palette_widget_and_reset(self):
        """参数坞有配色下拉框；[恢复默认] 回高对比；默认表收录新参数。"""
        w = create_window()
        try:
            w.params["曲线配色"].setCurrentIndex(
                w.params["曲线配色"].findData("默认"))
            w.findChild(QPushButton, "reset_image_btn").click()
            self.assertEqual(w.params["曲线配色"].currentData(), "高对比")
            self.assertEqual(gui_state._DISPLAY_DEFAULTS["曲线配色"], "高对比")
        finally:
            w.close()

    def test_customize_compare_color_section(self):
        """对比面板 Customize：曲线颜色小节逐条换色（应用才写回）、
        取消不动图、恢复默认配色清掉自定义。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            content = gui_app._content(dock)
            dlg = gui_app._build_customize_dialog(w, dock, content.axes_1d,
                                                  content.figure)
            swatches = [dlg.findChild(QPushButton, f"swatch_{i}")
                        for i in range(2)]
            self.assertTrue(all(s is not None for s in swatches),
                            "对比面板应有逐条色块")
            with mock.patch.object(gui_customize.QColorDialog, "getColor",
                                   return_value=QColor("#00ff00")):
                swatches[0].click()
            self.assertIn("fake_a.tif", dlg._color_picks)
            # [恢复默认配色] 清空暂存
            dlg.findChild(QPushButton, "clear_colors_btn").click()
            self.assertEqual(dlg._color_picks, {})
            # 重新选红 → 应用 → 写回 dock + 重画
            with mock.patch.object(gui_customize.QColorDialog, "getColor",
                                   return_value=QColor("#ff0000")):
                swatches[0].click()
            gui_app._apply_customize(w, dock, content.axes_1d,
                                     content.figure, dlg)
            self.assertEqual(dock.curve_colors, {"fake_a.tif": "#ff0000"})
            self.assertEqual(ax.lines[0].get_color(), "#ff0000")
            self.assertEqual(ax.lines[1].get_color(), self._SLOTS[1])
            # 再开对话框：色块预填自定义色；清空后应用 → 回色板色
            dlg2 = gui_app._build_customize_dialog(w, dock, content.axes_1d,
                                                   content.figure)
            self.assertEqual(dlg2._color_picks, {"fake_a.tif": "#ff0000"})
            dlg2.findChild(QPushButton, "clear_colors_btn").click()
            gui_app._apply_customize(w, dock, content.axes_1d,
                                     content.figure, dlg2)
            self.assertFalse(hasattr(dock, "curve_colors"))
            self.assertEqual(ax.lines[0].get_color(), self._SLOTS[0])
        finally:
            w.close()


class TestCompareStackAndHeatLink(unittest.TestCase):
    """任务六·对比增强：瀑布式堆叠显示 + 热图行点击联动对比面板。"""

    def _plot_compare(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compare_compute):
            w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
            w.compare_btn.click()
            keys = [k for k in w.plot_docks if k.startswith("对比|")]
            self.assertEqual(len(keys), 1)
            ax = gui_app._content(w.plot_docks[keys[0]]).axes_1d
            self.assertTrue(_wait_until(lambda: len(ax.lines) >= 2))
        return keys[0], ax

    def _synthetic_event(self, ax, ydata, x=50, y=50, button=1):
        ev = mock.Mock()
        ev.inaxes = ax
        ev.button = button
        ev.xdata = 1.0
        ev.ydata = ydata
        ev.x = x
        ev.y = y
        return ev

    def test_stack_offsets_curves_like_waterfall(self):
        """堆叠：第 2 条按第 1 条峰值 ×0.7 抬行，y 刻度 = 样品名，
        无图例；取消堆叠回到平铺 + 图例回来。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            y_flat = [np.asarray(l.get_ydata()).copy() for l in ax.lines]
            dock.params_snapshot["对比堆叠"] = True
            gui_views._redraw_compare(w, key)
            y_stack = [np.asarray(l.get_ydata()).copy() for l in ax.lines]
            peak0 = np.nanmax(y_flat[0])   # fake_a 最强峰 = 3
            np.testing.assert_allclose(y_stack[0], y_flat[0])   # 第一条不动
            np.testing.assert_allclose(y_stack[1], y_flat[1] + peak0 * 0.7)
            self.assertEqual([t.get_text() for t in ax.get_yticklabels()],
                             ["fake_a.tif", "fake_b.tif"])
            self.assertIsNone(ax.get_legend(), "堆叠下 y 刻度即样品名，无图例")
            dock.params_snapshot["对比堆叠"] = False
            gui_views._redraw_compare(w, key)
            np.testing.assert_allclose(ax.lines[0].get_ydata(), y_flat[0])
            self.assertIsNotNone(ax.get_legend())
        finally:
            w.close()

    def test_stack_ignores_log_and_ylim(self):
        """堆叠下纵轴保持线性、范围由行偏移决定（对数/纵轴参数不适用）。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            dock.params_snapshot["对数纵轴"] = True
            dock.params_snapshot["纵轴自动"] = False
            dock.params_snapshot["纵轴下限"] = 2.0
            dock.params_snapshot["纵轴上限"] = 3.0
            dock.params_snapshot["对比堆叠"] = True
            gui_views._redraw_compare(w, key)
            self.assertEqual(ax.get_yscale(), "linear")
            lo, hi = ax.get_ylim()   # 手填 2~3 不生效：行基线决定范围
            self.assertTrue(lo <= 0.0 and hi > 3.0,
                            f"范围应按行偏移算，实际 {lo}~{hi}")
        finally:
            w.close()

    def test_stack_widget_and_reset(self):
        w = create_window()
        try:
            w.params["对比堆叠"].setChecked(True)
            w.findChild(QPushButton, "reset_image_btn").click()
            self.assertFalse(w.params["对比堆叠"].isChecked())
            self.assertEqual(gui_state._DISPLAY_DEFAULTS["对比堆叠"], False)
        finally:
            w.close()

    def test_heat_row_click_toggles_compare_curve(self):
        """热图行点击 = 该样品在对比面板隐藏/显示切换（颜色序号不乱）。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            # 给面板挂 heat_files（行→文件），行 1 = fake_b.tif
            dock.heat_files = [("data/fake_a.tif", "fake_a.tif"),
                               ("data/fake_b.tif", "fake_b.tif")]
            gui_views._heat_row_press(w, key, self._synthetic_event(ax, 1))
            gui_views._heat_row_release(w, key, self._synthetic_event(ax, 1))
            self.assertEqual(dock.compare_hidden, {"fake_b.tif"})
            self.assertEqual([l.get_label() for l in ax.lines],
                             ["fake_a.tif"])
            self.assertIn("对比面板隐藏该曲线", w.log_text.toPlainText())
            # 再点一次 → 恢复，颜色序号不变（fake_b 仍是第 2 槽）
            gui_views._heat_row_press(w, key, self._synthetic_event(ax, 1))
            gui_views._heat_row_release(w, key, self._synthetic_event(ax, 1))
            self.assertEqual(dock.compare_hidden, set())
            self.assertEqual(len(ax.lines), 2)
            self.assertEqual(ax.lines[1].get_color(), "#eb6834")
        finally:
            w.close()

    def test_heat_drag_is_not_a_click(self):
        """按下后拖走（>5 px）→ 平移手势，不切换。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            dock.heat_files = [("data/fake_a.tif", "fake_a.tif"),
                               ("data/fake_b.tif", "fake_b.tif")]
            gui_views._heat_row_press(w, key,
                                      self._synthetic_event(ax, 1, x=50, y=50))
            gui_views._heat_row_release(w, key,
                                        self._synthetic_event(ax, 1,
                                                              x=200, y=200))
            self.assertFalse(hasattr(dock, "compare_hidden"))
            self.assertEqual(len(ax.lines), 2)
        finally:
            w.close()

    def test_heat_click_no_compare_panel_only_logs(self):
        """没有含该文件的对比面板 → 只提示，不炸。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
            dock1 = _dock(w, "1D", "data/fake_b.tif")
            dock1.heat_files = [("data/fake_b.tif", "fake_b.tif")]
            ax1 = gui_app._content(dock1).axes_1d
            gui_views._heat_row_press(w, "1D|data/fake_b.tif",
                                      self._synthetic_event(ax1, 0))
            gui_views._heat_row_release(w, "1D|data/fake_b.tif",
                                        self._synthetic_event(ax1, 0))
            self.assertIn("没有含该文件的对比面板", w.log_text.toPlainText())
        finally:
            w.close()

    def test_compare_shown_curves_filters_hidden(self):
        """_compare_shown_curves 跳过 compare_hidden：图例/颜色同口径。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            dock.compare_hidden = {"fake_a.tif"}
            curves = gui_state._compare_shown_curves(w, dock)
            self.assertEqual([d for _, _, d, _ in curves], ["fake_b.tif"])
            self.assertEqual([i for _, _, _, i in curves], [1],
                             "颜色序号跟文件走（隐藏第一条，第二条仍是 1 号）")
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
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                _drop_event(w, [abs_b])
                _open_view(w, "1D")
                drawn = _wait_until(
                    lambda: len(_axes(w, "1D", abs_b).lines) > 0)
                self.assertTrue(drawn, "拖入的文件应能直接作图")
        finally:
            w.close()


class TestFolderDrag(unittest.TestCase):
    """拖文件夹进窗口 = 扫描加入（与 [打开文件夹] 同一条逻辑）。"""

    def _tmp_folder(self, names):
        folder = tempfile.mkdtemp()
        for name in names:
            Path(folder, name).touch()
        return folder

    def test_drop_folder_scans_and_adds(self):
        folder = self._tmp_folder(("b.edf", "a.tif", "c.txt", "d.TIF"))
        w = create_window()
        try:
            _drop_event(w, [folder])
            names = [w.file_list.item(i).text()
                     for i in range(w.file_list.count())]
            self.assertEqual(names, ["a.tif", "b.edf", "d.TIF"])
            self.assertIn("文件夹扫描", w.log_text.toPlainText())
        finally:
            w.close()

    def test_drop_folder_twice_skips_duplicates(self):
        folder = self._tmp_folder(("a.tif",))
        w = create_window()
        try:
            _drop_event(w, [folder])
            _drop_event(w, [folder])
            self.assertEqual(w.file_list.count(), 1)
            self.assertIn("已跳过重复文件", w.log_text.toPlainText())
        finally:
            w.close()

    def test_drop_empty_folder_logs_hint(self):
        folder = tempfile.mkdtemp()
        w = create_window()
        try:
            _drop_event(w, [folder])
            self.assertIn("文件夹里没有支持的数据文件",
                          w.log_text.toPlainText())
            self.assertEqual(w.file_list.count(), 0)
        finally:
            w.close()

    def test_drop_mixed_files_and_dir(self):
        folder = self._tmp_folder(("a.tif",))
        w = create_window()
        try:
            abs_b = str(Path("data/fake_b.tif").resolve())
            _drop_event(w, [abs_b, "/tmp/x.txt", folder])
            names = [w.file_list.item(i).text()
                     for i in range(w.file_list.count())]
            self.assertEqual(names, ["fake_b.tif", "a.tif"])
        finally:
            w.close()

    def test_drag_enter_accepts_dir(self):
        folder = tempfile.mkdtemp()
        w = create_window()
        try:
            ev = _drop_event(w, [folder], kind="enter")
            self.assertTrue(ev.isAccepted(), "拖文件夹应接住（扫描加入）")
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
            with mock.patch.object(gui_views, "_compute_integration",
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
                    and len(gui_app._content(w.plot_docks[key2]).axes_1d.lines) > 0)
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
            # 2θ 上下限 = 积分设置，随面板走（改了就进 geom）
            self.assertAlmostEqual(geom["tth_min_deg"], 1.0)
            self.assertAlmostEqual(geom["tth_max_deg"], 8.0)
            w.params["2θ 下限 (°)"].setValue(2.5)
            w.params["2θ 上限 (°)"].setValue(7.5)
            geom2 = gui_app._collect_geometry(w)
            self.assertAlmostEqual(geom2["tth_min_deg"], 2.5)
            self.assertAlmostEqual(geom2["tth_max_deg"], 7.5)
        finally:
            w.close()


class TestTthRangeFlowsToCompute(unittest.TestCase):
    """2θ 上下限进计算链路：点 [1D]/[应用] 时 geom 带当前区间值。

    引擎按区间重积分由 TestIntegrate1DRange（test_partial_ring.py）
    兜底；这里只验 GUI 把参数传对。
    """

    def test_range_values_reach_compute_and_rerun(self):
        w = create_window()
        try:
            calls = []
            def recorder(path_str, geom, npt):
                calls.append(dict(geom))
                return np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.0])
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=recorder):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: len(calls) >= 1))
                self.assertAlmostEqual(calls[0]["tth_min_deg"], 1.0)
                self.assertAlmostEqual(calls[0]["tth_max_deg"], 8.0)
                # 先等首轮计算的焦点回放落地（回放把快照写回参数坞，
                # 会覆盖"在飞"的控件改动）——真实用户也是等出图后
                # 再改参数
                self.assertTrue(
                    _wait_until(lambda: w.focus_panel == "1D|data/fake_b.tif"),
                    "出图后焦点面板应就位")
                w.params["2θ 下限 (°)"].setValue(2.5)
                w.params["2θ 上限 (°)"].setValue(7.5)
                w.findChild(QPushButton, "apply_btn").click()
                self.assertTrue(
                    _wait_until(lambda: len(calls) >= 2),
                    f"[应用] 未触发重算；calls 区间值 = "
                    f"{[c['tth_min_deg'] for c in calls]}；"
                    f"日志 = {w.log_text.toPlainText().splitlines()[-2:]}")
                self.assertAlmostEqual(calls[-1]["tth_min_deg"], 2.5)
                self.assertAlmostEqual(calls[-1]["tth_max_deg"], 7.5)
        finally:
            w.close()


class TestAutoContrast(unittest.TestCase):
    """自动对比度：勾回自动 = 按编辑对象（焦点图）重算并填回；没
    焦点图填占位默认；[恢复默认] 也会重算。对比度只对 2D/剖面 有
    意义：焦点是 1D/对比面板时不读文件（占位默认，防主线程卡）。"""

    def _focus_2d(self, w):
        """开一张 fake_a 的 2D 面板并等它画出来 → 编辑对象 = 该面板。"""
        fake_image = np.linspace(0, 1000, 3000).reshape(50, 60)
        with mock.patch.object(gui_views, "load_diffraction_image",
                               return_value=fake_image):
            w.add_files(["data/fake_a.tif"])
            _open_view(w, "2D")
            self.assertTrue(_wait_until(lambda: len(gui_app._content(
                _dock(w, "2D", "data/fake_a.tif")).axes_2d.images) > 0))
        QTest.mouseClick(gui_app._content(_dock(w, "2D", "data/fake_a.tif")),
                         Qt.LeftButton)
        return w.focus_panel

    def _focus_1d(self, w):
        """画一张 fake_a 的 1D 图并等它完成 → 编辑对象 = 该面板。"""
        with mock.patch.object(gui_views, "_compute_integration",
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
            w._image_cache.clear()   # 2D 面板已缓存其图：清掉让重算走 mock
            with mock.patch.object(gui_state, "load_diffraction_image",
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
            w._image_cache.clear()   # 2D 面板已缓存其图：清掉让重算走 mock
            with mock.patch.object(gui_state, "load_diffraction_image",
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
            w._image_cache.clear()   # 2D 面板已缓存其图：清掉让读图真的失败
            with mock.patch.object(gui_state, "load_diffraction_image",
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
            with mock.patch.object(gui_state, "load_diffraction_image",
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
        with mock.patch.object(gui_views, "_compute_integration",
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
            QTest.mouseClick(gui_app._content(_dock(w, "1D", "data/fake_a.tif")),
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
            with mock.patch.object(gui_views, "_compute_integration",
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
            QTest.mouseClick(gui_app._content(_dock(w, "1D", "data/fake_a.tif")),
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
            QTest.mouseClick(gui_app._content(_dock(w, "1D", "data/fake_a.tif")),
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
            QTest.mouseClick(gui_app._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            self.assertTrue(w.params["自动对比度"].isChecked())
            self.assertFalse(w.params["对比度下限"].isEnabled())
            # 再切回 B：手动模式 + 123/456 原样回放
            QTest.mouseClick(gui_app._content(_dock(w, "1D", "data/fake_b.tif")),
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
        with mock.patch.object(gui_views, "_compute_integration",
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
        with mock.patch.object(gui_views, "_compute_integration",
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
        with mock.patch.object(gui_views, "_compute_integration",
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

    def test_config_combo_carries_full_label_as_tooltip(self):
        """几何配置说明行已删除（与用户讨论定稿：不单独占一行）——
        完整批次备注改为挂在配置下拉框各项的悬停提示上
        （悬停闭合下拉框 = 显示当前项的提示）。"""
        w = create_window()
        try:
            w.show()
            combo = w.config_combo
            idx = combo.currentIndex()
            self.assertEqual(combo.itemData(idx), gui_app.DEFAULT_CONFIG)
            self.assertEqual(
                combo.itemData(idx, Qt.ToolTipRole),
                gui_app.CONFIGS[gui_app.DEFAULT_CONFIG]["label"])
        finally:
            w.close()   # 没画图，关窗不会弹询问


class TestParamDockSplitLayout(unittest.TestCase):
    """参数坞上下对半分结构：编辑对象名固定在最上方，下面
    QSplitter 竖切两半（数据参数上 / 图像参数下，初始等高）；
    两半各自一个 QScrollArea（内容放不下时滚动），按钮并排一行
    （[恢复默认] 在左、[应用] 在右，各占一半宽度）固定在各区最
    下方——在滚动区之外，滚动时按钮不跟着走。"""

    def test_split_structure(self):
        w = create_window()
        try:
            w.show()
            QApplication.processEvents()
            content = w.param_dock.widget()
            lay = content.layout()
            # 参数坞 = QStackedWidget 两页翻面（页 0 = 分析工作台、
            # 页 1 = 校准工作台），顶层层布局唯一的条目就是翻页栈
            self.assertIs(lay.itemAt(0).widget(), w.param_stack)
            self.assertEqual(w.param_stack.count(), 2)
            # 分析页（页 0）：编辑对象名固定在最上方，分隔条紧随其后
            # 占满剩余空间（下方没有别的同级条目）
            page_lay = w.param_stack.widget(0).layout()
            self.assertIs(page_lay.itemAt(0).widget(), w.focus_label)
            splitter = page_lay.itemAt(1).widget()
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
                # 布局顺序：滚动区在上、按钮行垫底（并排：
                # [恢复默认] 在左、[应用] 在右，各占一半宽度）
                v = half.layout()
                self.assertIs(v.itemAt(0).widget(), scroll)
                btn_row = v.itemAt(1)
                self.assertIsInstance(btn_row, QHBoxLayout)
                self.assertIs(btn_row.itemAt(0).widget(), reset)
                self.assertIs(btn_row.itemAt(1).widget(), apply)
                self.assertEqual(btn_row.stretch(0), 1)
                self.assertEqual(btn_row.stretch(1), 1)
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

    def test_section_captions_and_normalize_combo(self):
        w = create_window()
        try:
            captions = {lb.text() for lb in w.param_dock.findChildren(QLabel)}
            self.assertIn("标定几何", captions)
            self.assertIn("积分设置", captions)
            self.assertIn("2D/剖面视图", captions)
            # 归一化四选一下拉框（键仍是"对比归一化"，快照回放认 data
            # 不认字面）：各自最强峰 / 全图最强峰 / 指定数据… / 不归一化
            combo = w.params["对比归一化"]
            self.assertEqual(
                [combo.itemText(i) for i in range(combo.count())],
                ["各自最强峰", "全图最强峰", "指定数据…", "不归一化"])
            self.assertEqual(
                [combo.itemData(i) for i in range(combo.count())],
                ["each", "global", "file", "off"])
            self.assertEqual(combo.currentData(), "off", "默认 = 不归一化")
            # "指定数据" 未选中时，旁边的目标文件下拉框置灰
            self.assertFalse(w.params["归一化目标"].isEnabled())
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
            with mock.patch.object(gui_views, "_compute_integration",
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

    def test_apply_before_result_logs_pending(self):
        """编辑对象是没算出结果的 2D 面板 → [应用] 提示先等计算结果。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views, "load_diffraction_image",
                                   side_effect=OSError("boom")):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "2D")
                self.assertTrue(_wait_until(
                    lambda: "读取失败" in w.log_text.toPlainText()))
            QTest.mouseClick(gui_app._content(_dock(w, "2D", "data/fake_b.tif")),
                             Qt.LeftButton)
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertIn("还没有计算结果（读取完成后再试）",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_apply_only_redraws_focus_panel(self):
        """显示参数每张图各记各的：1D + 对比同开，焦点在 1D 上改参数
        [应用] → 只重画 1D 那张，对比保持自己的设置（修前是全局的，
        一张改了所有图都变）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines)
                    > 0))
                w.add_files(["data/fake_b.tif"])   # fake_a 仍勾着
                w.compare_btn.click()
                cax = gui_app._content([d for k, d in w.plot_docks.items()
                                        if k.startswith("对比|")][0]).axes_1d
                self.assertTrue(_wait_until(lambda: len(cax.lines) >= 2))
            # 焦点此时在对比面板；切到 1D 面板改参数 → 应用 → 只动 1D
            QTest.mouseClick(gui_app._content(_dock(w, "1D", "data/fake_a.tif")),
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
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")   # 两张都出图（默认线性）
                self.assertTrue(_wait_until(
                    lambda: all(len(_axes(w, "1D", f"data/{n}.tif").lines) > 0
                                for n in ("fake_a", "fake_b"))))
            # 焦点是最后算完的那张；切到 fake_a 改成对数并应用
            QTest.mouseClick(gui_app._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            w.params["对数纵轴"].setChecked(True)
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertEqual(
                _axes(w, "1D", "data/fake_a.tif").get_yscale(), "log")
            self.assertEqual(
                _axes(w, "1D", "data/fake_b.tif").get_yscale(), "linear")
            # 点 fake_b → 参数坞回放它自己的设置（对数关）
            QTest.mouseClick(gui_app._content(_dock(w, "1D", "data/fake_b.tif")),
                             Qt.LeftButton)
            self.assertFalse(w.params["对数纵轴"].isChecked())
            # 点回 fake_a → 显示它自己的设置（对数开）
            QTest.mouseClick(gui_app._content(_dock(w, "1D", "data/fake_a.tif")),
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
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines)
                    > 0))
            # 焦点在 B：自动开、输入框置灰
            self.assertTrue(w.params["纵轴自动"].isChecked())
            self.assertFalse(w.params["纵轴下限"].isEnabled())
            # 点回 A → 回放它自己的：自动关、输入框可改、值 = 手填值
            QTest.mouseClick(gui_app._content(_dock(w, "1D", "data/fake_a.tif")),
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
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif"])   # fake_b 仍勾着
                w.compare_btn.click()
                cdock = [d for k, d in w.plot_docks.items()
                         if k.startswith("对比|")][0]
                self.assertTrue(_wait_until(
                    lambda: len(gui_app._content(cdock).axes_1d.lines) >= 2))
            snap = cdock.params_snapshot
            self.assertFalse(snap["对数纵轴"])
            self.assertTrue(snap["纵轴自动"])
            self.assertEqual(snap["纵轴下限"], 1.0)
            self.assertEqual(snap["纵轴上限"], 100000.0)
            # 对比完成后成为焦点：置灰框显示它自己算出的自动区间
            # （输入框精度 decimals=1，与图的精确值允许 0.1 级误差）
            cax = gui_app._content(cdock).axes_1d
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
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compare_compute):
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines)
                    > 0))
            ax_a = _axes(w, "1D", "data/fake_a.tif")
            self.assertAlmostEqual(w.params["纵轴下限"].value(),
                                   ax_a.get_ylim()[0], places=1)
            QTest.mouseClick(gui_app._content(_dock(w, "1D", "data/fake_b.tif")),
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
        return gui_app._content(w.plot_docks[keys[0]]).axes_1d

    def _plot_compare(self, w):
        """勾 fake_a + fake_b 点 [对比] 并等两条曲线到齐 → 返回坐标轴。"""
        with mock.patch.object(gui_views, "_compute_integration",
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

    def _set_norm_mode(self, w, mode):
        """把归一化下拉框切到某模式（按 data 找条目，找不到就失败）。"""
        combo = w.params["对比归一化"]
        idx = combo.findData(mode)
        self.assertGreaterEqual(idx, 0, f"模式 {mode} 应在下拉框里")
        combo.setCurrentIndex(idx)

    def test_normalize_off_by_default(self):
        """默认 = 不归一化 → 曲线按原始强度画（fake_b 最强峰 30）；
        切到各自最强峰 → 每条曲线最强峰都是 1.0（each 分支覆盖）。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            self.assertEqual(w.params["对比归一化"].currentData(), "off")
            ymax = max(float(np.max(line.get_ydata())) for line in ax.lines)
            self.assertAlmostEqual(ymax, 30.0, places=4)
            self._set_norm_mode(w, "each")
            w.findChild(QPushButton, "apply_image_btn").click()
            for line in ax.lines:
                self.assertAlmostEqual(float(np.max(line.get_ydata())),
                                       1.0, places=4)
        finally:
            w.close()

    def test_normalize_off_shows_raw_values(self):
        """切到不归一化点图像 [应用] → 按原始强度重画（不重算）。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            done_before = w.log_text.toPlainText().count("开始对比")
            self._set_norm_mode(w, "off")
            w.findChild(QPushButton, "apply_image_btn").click()
            # 快照记下关归一化，fake_b 曲线回到原始强度（最强峰 30）
            dock = [d for k, d in w.plot_docks.items()
                    if k.startswith("对比|")][0]
            self.assertEqual(dock.params_snapshot["对比归一化"], "off")
            ymax = max(float(np.max(line.get_ydata())) for line in ax.lines)
            self.assertAlmostEqual(ymax, 30.0, places=4)
            self.assertIn("[应用] 图像参数已重画：",
                          w.log_text.toPlainText())
            # 只重画不重算
            self.assertEqual(w.log_text.toPlainText().count("开始对比"),
                             done_before)
        finally:
            w.close()

    def test_normalize_global_divides_by_strongest_of_all(self):
        """全图最强峰 → 所有曲线除以全部曲线里最高的峰。
        fake_a 最强峰 3、fake_b 最强峰 30 → 除数 30：
        fake_a 峰 0.1、fake_b 峰 1.0。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            self._set_norm_mode(w, "global")
            w.findChild(QPushButton, "apply_image_btn").click()
            peaks = sorted(float(np.max(line.get_ydata()))
                           for line in ax.lines)
            self.assertEqual(len(peaks), 2)
            self.assertAlmostEqual(peaks[0], 3.0 / 30.0, places=4)
            self.assertAlmostEqual(peaks[1], 1.0, places=4)
        finally:
            w.close()

    def test_normalize_file_divides_by_chosen_file(self):
        """指定数据 → 所有曲线除以目标文件的最强峰；目标下拉框 =
        对比面板的文件列表。选 fake_a（峰 3）→ fake_a 峰 1.0、
        fake_b 峰 30/3 = 10.0。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            # 对比面板成为焦点后，目标下拉框已按它的文件列表填充
            target = w.params["归一化目标"]
            self.assertEqual(
                [str(target.itemData(i)) for i in range(target.count())],
                ["data/fake_a.tif", "data/fake_b.tif"])
            self._set_norm_mode(w, "file")
            self.assertTrue(target.isEnabled(), "指定数据模式应启用目标下拉框")
            # 条目 data = 字符串路径，按字符串找
            target.setCurrentIndex(target.findData("data/fake_a.tif"))
            w.findChild(QPushButton, "apply_image_btn").click()
            dock = [d for k, d in w.plot_docks.items()
                    if k.startswith("对比|")][0]
            self.assertEqual(str(dock.params_snapshot["归一化目标"]),
                             "data/fake_a.tif")
            peaks = sorted(float(np.max(line.get_ydata()))
                           for line in ax.lines)
            self.assertEqual(len(peaks), 2)
            self.assertAlmostEqual(peaks[0], 1.0, places=4)    # fake_a 3/3
            self.assertAlmostEqual(peaks[1], 10.0, places=4)   # fake_b 30/3
        finally:
            w.close()

    def test_norm_mode_survives_reclick(self):
        """重复点 [对比]（重算）= 显示参数保留：归一化模式还是
        "不归一化"，重画后仍按原始强度。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            self._set_norm_mode(w, "off")
            w.findChild(QPushButton, "apply_image_btn").click()
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compare_compute):
                w.compare_btn.click()
                self.assertTrue(_wait_until(
                    lambda: w.log_text.toPlainText().count("对比完成") >= 2))
            self.assertEqual(w.params["对比归一化"].currentData(), "off")
            ymax = max(float(np.max(line.get_ydata())) for line in ax.lines)
            self.assertAlmostEqual(ymax, 30.0, places=4)
        finally:
            w.close()

    def test_reclick_same_selection_reuses_panel(self):
        """同一勾选集合重复点 [对比] → 还是那一张面板，重算刷新。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            dock_before = [d for k, d in w.plot_docks.items()
                           if k.startswith("对比|")][0]
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
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
        """[校准] 按下 = 校准模式提示，弹起 = 分析模式提示。

        开关文字随状态变（校准 ↔ 退出校准）：按钮自己说明怎么回来。
        """
        w = create_window()
        try:
            w.calib_btn.click()
            self.assertIn("进入校准模式", w.log_text.toPlainText())
            self.assertIn("校准模式", w.mode_label.text())
            self.assertEqual(w.calib_btn.text(), "退出校准")
            w.calib_btn.click()
            self.assertIn("回到分析模式", w.log_text.toPlainText())
            self.assertIn("分析模式", w.mode_label.text())
            self.assertEqual(w.calib_btn.text(), "校准")
        finally:
            w.close()

    def test_close_with_inflight_task_discards(self):
        """后台任务还在飞时关窗 → 等任务收尾（discard），不挂死不崩溃。"""
        def slow(path_str, geom, npt):
            time.sleep(1.5)
            return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])

        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
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


def _scroll_event(ax, button="up", xdata=4.0, ydata=2.0):
    """构造滚轮事件（button = "up" 放大 / "down" 缩小）。"""
    return SimpleNamespace(inaxes=ax, button=button,
                           xdata=xdata, ydata=ydata)


def _press_event(ax, button=1, x=100, y=100, xdata=4.0, ydata=2.0):
    """构造鼠标按/拖事件（x/y 像素坐标 = 平移手势用的量）。"""
    return SimpleNamespace(inaxes=ax, button=button, x=x, y=y,
                           xdata=xdata, ydata=ydata)


class TestPlotFixedSize(unittest.TestCase):
    """A：新面板固定 5:3 开局，不继承上次挤过的旧尺寸。"""

    def test_new_panel_opens_fixed_ratio(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            with mock.patch.object(gui_views, "_compute_integration",
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
            # 画布 5:3 之外还有标题栏+工具栏的壳（实测子窗口 508×383），
            # 所以整窗比例 ≈ 0.75 而画布仍真 5:3（见 TestCanvasTrue53）
            ratio = dock.height() / dock.width()
            self.assertGreater(ratio, 0.5, f"新面板比例走样：{ratio:.2f}")
            self.assertLess(ratio, 0.85, f"新面板比例走样：{ratio:.2f}")
        finally:
            w.hide()   # 窗口显示过关窗会弹"保存询问"模态框（offscreen 挂死）
            w.close()


class TestZoomToolbar(unittest.TestCase):
    """D：每个 1D 面板带自己的精简工具栏 [Home][Zoom][Customize][Save]；
    放大镜 = 开关（点亮滚轮缩放/拖框放大，熄灭滚轮滚动/拖平移），
    抓手/前进后退/子图按钮退休；占位面板没有。"""

    def test_toolbar_present_on_1d(self):
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            widget = gui_app._content(_dock(w, "1D", "data/fake_b.tif"))
            self.assertIsInstance(widget.toolbar, NavigationToolbar2QT)
            names = [t[0] for t in widget.toolbar.toolitems if t[0]]
            self.assertEqual(names, ["Home", "Zoom", "Customize", "Save"],
                             "工具栏应精简为 Home/Zoom/Customize/Save")
            for gone in ("Pan", "Back", "Forward", "Subplots"):
                self.assertNotIn(gone, names, f"{gone} 按钮应已砍掉")
        finally:
            w.close()

    def test_unregistered_view_still_placeholder(self):
        """未注册视图（分发骨架的防御路径）：占位标签面板，无工具栏。"""
        w = create_window()
        try:
            gui_views._open_plot_panel(w, "不存在", "不存在|data/fake_b.tif",
                                       "不存在_fake_b.tif")
            widget = gui_app._content(_dock(w, "不存在", "data/fake_b.tif"))
            self.assertIsInstance(widget, QLabel, "占位面板仍是标签")
            self.assertFalse(hasattr(widget, "toolbar"),
                             "占位面板不应有缩放工具栏")
        finally:
            w.close()


class TestHoverDot(unittest.TestCase):
    """E：鼠标悬停 = 曲线上出白边点 + 状态栏实时坐标；离开清空。"""

    def _open_1d(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
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
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                w.compare_btn.click()
                key = next(k for k in w.plot_docks
                           if k.startswith("对比|"))
                dock = w.plot_docks[key]
                _wait_until(
                    lambda: len(gui_app._content(dock).axes_1d.lines) >= 2)
            ax = gui_app._content(dock).axes_1d
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
    """C：横排/竖排重排 = 按类型分层摆位置（图保持各自大小，默认
    5:3 不被缩放），同类型同行/列、顶/左对齐，放不下靠滚动条兜底。"""

    def _open_two(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
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


class TestTilingGroups(unittest.TestCase):
    """平铺按类型分层：横排每类一行、竖排每类一列；类型顺序与层内
    顺序 = 开图先后；只摆位置绝不缩放（溢出靠 QMdiArea 滚动条）。"""

    def _check_only(self, w, keep):
        """文件列表只勾 keep（作图用对号文件，见 TestFileCheckSelection）。"""
        for i in range(w.file_list.count()):
            item = w.file_list.item(i)
            item.setCheckState(
                Qt.Checked if item.text() == keep else Qt.Unchecked)

    def _open_mixed(self, w):
        """按开图先后：1D(fake_a) → 2D(fake_c) → 1D(fake_b)。"""
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files(["data/fake_a.tif", "data/fake_b.tif",
                         "data/fake_c.tif"])
            for keep, view in (("fake_a.tif", "1D"),
                               ("fake_c.tif", "2D"),
                               ("fake_b.tif", "1D")):
                self._check_only(w, keep)
                _open_view(w, view)
                QApplication.processEvents()
        # 1D 计算异步，等两张 1D 都出线（2D 只开面板不算）
        self.assertTrue(_wait_until(
            lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
            and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))

    def _sizes(self, w):
        return {k: (d.width(), d.height()) for k, d in w.plot_docks.items()}

    def test_row_groups_by_type_keeps_sizes(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_mixed(w)
            before = self._sizes(w)
            w.arrange_buttons["横排"].click()
            QApplication.processEvents()
            d_a = _dock(w, "1D", "data/fake_a.tif")
            d_b = _dock(w, "1D", "data/fake_b.tif")
            d_c = _dock(w, "2D", "data/fake_c.tif")
            # 同类型同一行（顶对齐）
            self.assertAlmostEqual(d_a.geometry().top(),
                                   d_b.geometry().top(), delta=2)
            # 不同类型分层：2D 单独一行
            self.assertGreater(d_c.geometry().top(), d_a.geometry().top())
            # 层内顺序 = 开图先后：fake_a 在 fake_b 左边
            self.assertLess(d_a.geometry().left(), d_b.geometry().left())
            # 只摆位置不缩放
            self.assertEqual(self._sizes(w), before)
            self.assertIn("已横排 3 个面板", w.log_text.toPlainText())
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_column_groups_by_type_keeps_sizes(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_mixed(w)
            before = self._sizes(w)
            w.arrange_buttons["竖排"].click()
            QApplication.processEvents()
            d_a = _dock(w, "1D", "data/fake_a.tif")
            d_b = _dock(w, "1D", "data/fake_b.tif")
            d_c = _dock(w, "2D", "data/fake_c.tif")
            # 同类型同一列（左对齐）
            self.assertAlmostEqual(d_a.geometry().left(),
                                   d_b.geometry().left(), delta=2)
            # 不同类型分层：2D 单独一列
            self.assertGreater(d_c.geometry().left(), d_a.geometry().left())
            # 层内顺序 = 开图先后：fake_a 在 fake_b 上边
            self.assertLess(d_a.geometry().top(), d_b.geometry().top())
            self.assertEqual(self._sizes(w), before)
            self.assertIn("已竖排 3 个面板", w.log_text.toPlainText())
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_overflow_scrolls_without_scaling(self):
        """放不下 → 横向滚动条兜底，图不缩放。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            # 三张同类型 1D 挤在同一行，窗口收窄后必然放不下
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif",
                             "data/fake_c.tif"])
                _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", "data/fake_c.tif").lines) > 0))
            before = self._sizes(w)
            w.resize(600, 500)
            QApplication.processEvents()
            w.arrange_buttons["横排"].click()
            QApplication.processEvents()
            self.assertGreater(w.mdi.horizontalScrollBar().maximum(), 0,
                               "放不下 → 横向滚动条兜底")
            self.assertEqual(self._sizes(w), before, "溢出也不缩放图")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


class TestFreeResize(unittest.TestCase):
    """B：手动缩放完全自由：拖成什么样就停在什么样（不弹回任何比例，
    旧规则里的普通拖/Shift 拖之分随停靠分栏一起退场）；每拖一次 =
    记住当前画布比例；主窗口缩放不牵动子窗口。"""

    def _open_two(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
            _open_view(w, "1D")
            both = _wait_until(
                lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            self.assertTrue(both)
        QApplication.processEvents()

    def test_resize_records_canvas_pref(self):
        """拖面板边框 → 停在拖成的尺寸，当前画布尺寸记成新偏好比例。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            d1 = _dock(w, "1D", "data/fake_a.tif")
            _resize_panel(w, d1, 400, 450)   # 模拟用户拖成 400×450
            canvas = gui_app._content(d1).canvas
            self.assertEqual((d1.width(), d1.height()), (400, 450),
                             "自由缩放：拖成什么样就停在什么样")
            self.assertTrue(d1._dragged, "拖过 = 记比例")
            self.assertEqual(d1._canvas_pref,
                             (canvas.width(), canvas.height()),
                             "记住的画布比例 = 拖成时的画布尺寸")
            # 不再有任何比例弹回：尺寸原样保持
            QApplication.processEvents()
            self.assertEqual((d1.width(), d1.height()), (400, 450))
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_main_window_resize_leaves_panels_alone(self):
        """拉大拉小主窗口：子窗口原地不动、不误标"拖过"。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            d1 = _dock(w, "1D", "data/fake_a.tif")
            geo_before = d1.geometry()
            w.resize(900, 600)
            QApplication.processEvents()
            self.assertEqual(d1.geometry(), geo_before,
                             "主窗口缩放不牵动子窗口")
            self.assertFalse(d1._dragged, "主窗口缩放不算拖过图")
            self.assertEqual(d1._canvas_pref, (500, 300),
                             "比例记忆保持默认")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


class TestResizeGrips(unittest.TestCase):
    """自装抓手：内容四边 8px 抓取带 + 四角 24px 抓取区 + 右下角
    可见把手（▙）；悬停换方向光标（应用级覆盖光标，macOS 上部件级
    setCursor 会被带过期坐标的合成事件打回原形），按住拖 = 拉伸
    容器（子窗口/弹出窗口都可用），拖完照常记"拖过"比例。"""

    def _open_one(self, w, view="1D", path_str="data/fake_b.tif"):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files([path_str])
            _open_view(w, view)
        if view == "1D":
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, view, path_str).lines) > 0))
        QApplication.processEvents()

    def test_hover_swaps_cursor(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            content = gui_app._content(_dock(w, "1D", "data/fake_b.tif"))
            # 方向光标 = 应用级覆盖光标（macOS 上部件级 setCursor 会被
            # 带过期坐标的合成事件打回原形，见 _PanelGripFilter docstring）
            # 右边缘中部（避开右下角把手）→ 水平双箭头
            self.assertTrue(_hover_until(
                content, QPoint(content.width() - 3, 200),
                lambda: QApplication.overrideCursor() is not None
                and QApplication.overrideCursor().shape() == Qt.SizeHorCursor))
            # 画布中央 → 撤销覆盖光标（回到普通箭头）
            self.assertTrue(_hover_until(
                content, QPoint(300, 200),
                lambda: QApplication.overrideCursor() is None))
            # 右下角被把手挡着（把手盖在画布上）→ 角区斜向双箭头
            grip = content._resize_grip
            self.assertTrue(_hover_until(
                content, QPoint(content.width() - 5, content.height() - 5),
                lambda: QApplication.overrideCursor() is not None
                and QApplication.overrideCursor().shape()
                == Qt.SizeFDiagCursor))
            # 顶边抓取带落在工具栏条上：真实落点 = 工具栏 → 垂直双箭头
            self.assertTrue(_hover_until(
                content, QPoint(250, 2),
                lambda: QApplication.overrideCursor() is not None
                and QApplication.overrideCursor().shape() == Qt.SizeVerCursor))
        finally:
            # 弹空覆盖光标栈：别把方向光标留给后面的测试
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_drag_corner_grip_resizes_and_records(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            dock = _dock(w, "1D", "data/fake_b.tif")
            content = gui_app._content(dock)
            grip = content._resize_grip
            w0, h0 = dock.width(), dock.height()
            # 真实用户行为：按住 ▙ 把手拖（不是直接投递给内容）
            QTest.mousePress(grip, Qt.LeftButton, Qt.NoModifier, QPoint(8, 8))
            QTest.mouseMove(grip, QPoint(58, 38))
            QTest.mouseRelease(grip, Qt.LeftButton, Qt.NoModifier,
                               QPoint(58, 38))
            QApplication.processEvents()
            self.assertEqual((dock.width(), dock.height()),
                             (w0 + 50, h0 + 30), "按住把手拖 = 拉伸右下角")
            canvas = content.canvas
            self.assertTrue(dock._dragged, "拖过 = 记比例")
            self.assertEqual(dock._canvas_pref,
                             (canvas.width(), canvas.height()),
                             "记住的画布比例 = 拖成时的画布尺寸")
            # 把手钉回右下角
            self.assertAlmostEqual(grip.pos().x(), content.width() - 18,
                                   delta=2)
            self.assertAlmostEqual(grip.pos().y(), content.height() - 18,
                                   delta=2)
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_drag_top_edge_resizes(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            dock = _dock(w, "1D", "data/fake_b.tif")
            content = gui_app._content(dock)
            h0 = dock.height()
            # 顶边抓取带真实落点 = 工具栏条（内容上边 5px）
            QTest.mousePress(content.toolbar, Qt.LeftButton, Qt.NoModifier,
                             QPoint(content.width() // 2, 2))
            QTest.mouseMove(content.toolbar,
                            QPoint(content.width() // 2, 2 - 40))
            QTest.mouseRelease(content.toolbar, Qt.LeftButton, Qt.NoModifier,
                               QPoint(content.width() // 2, 2 - 40))
            QApplication.processEvents()
            self.assertEqual(dock.height(), h0 + 40, "顶边往上拖 = 容器变高")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_placeholder_panel_also_has_grip(self):
        """2D 面板（画布内容）同样有把手和抓手——把手装在内容上，
        与视图类型无关。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w, view="2D")
            dock = _dock(w, "2D", "data/fake_b.tif")
            content = gui_app._content(dock)
            grip = content._resize_grip
            w0, h0 = dock.width(), dock.height()
            QTest.mousePress(grip, Qt.LeftButton, Qt.NoModifier, QPoint(8, 8))
            QTest.mouseMove(grip, QPoint(38, 28))
            QTest.mouseRelease(grip, Qt.LeftButton, Qt.NoModifier,
                               QPoint(38, 28))
            QApplication.processEvents()
            self.assertEqual((dock.width(), dock.height()), (w0 + 30, h0 + 20))
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


class TestCustomizeDialog(unittest.TestCase):
    """自绘 Customize 轴属性对话框（替换 mpl 子图配置器）：标题/
    轴标签/纵轴刻度/图边距，表单标签左对齐 + [恢复默认][取消]
    [应用]；应用后用户改动受保护记账（重画不覆盖），边距接管 =
    摘掉 tight layout 引擎（否则 draw 时布局引擎把用户边距算回去）。"""

    def _open_one(self, w, path_str="data/fake_b.tif"):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files([path_str])
            _open_view(w, "1D")
        self.assertTrue(_wait_until(
            lambda: len(_axes(w, "1D", path_str).lines) > 0))
        QApplication.processEvents()

    def _build(self, w, path_str="data/fake_b.tif"):
        dock = _dock(w, "1D", path_str)
        content = gui_app._content(dock)
        return dock, gui_app._build_customize_dialog(
            w, dock, content.axes_1d, content.figure)

    def test_dialog_prefills_current_axis_state(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            dock, dlg = self._build(w)
            content = gui_app._content(dock)
            f = dlg._fields
            self.assertEqual(f["title"].text(), content.axes_1d.get_title())
            self.assertEqual(f["xlabel"].text(), content.axes_1d.get_xlabel())
            self.assertEqual(f["ylabel"].text(), content.axes_1d.get_ylabel())
            self.assertEqual(f["scale"].currentData(),
                             content.axes_1d.get_yscale())
            sp = content.figure.subplotpars
            self.assertAlmostEqual(f["left"].value(), sp.left, places=3)
            self.assertAlmostEqual(f["right"].value(), sp.right, places=3)
            # 表单左对齐（用户点名要的）：macOS 风格默认把表单内容
            # 整块水平居中，formAlignment 显式设左
            text_box = next(b for b in dlg.findChildren(QGroupBox)
                            if b.title() == "标题与轴标签")
            self.assertEqual(text_box.layout().labelAlignment(),
                             Qt.AlignLeft | Qt.AlignVCenter)
            self.assertEqual(text_box.layout().formAlignment(),
                             Qt.AlignLeft | Qt.AlignTop)
            # 标题输入框加长（用户点名要的）
            self.assertGreaterEqual(f["title"].minimumWidth(), 240)
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_apply_sets_title_labels_scale_and_margins(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            dock, dlg = self._build(w)
            f = dlg._fields
            f["title"].setText("我的衍射图")
            f["xlabel"].setText("角度")
            f["ylabel"].setText("计数")
            f["scale"].setCurrentIndex(f["scale"].findData("log"))
            f["left"].setValue(0.2)
            f["bottom"].setValue(0.15)
            f["right"].setValue(0.85)
            f["top"].setValue(0.8)
            content = gui_app._content(dock)
            gui_app._apply_customize(w, dock, content.axes_1d,
                                     content.figure, dlg)
            ax = content.axes_1d
            self.assertEqual(ax.get_title(), "我的衍射图")
            self.assertEqual(ax.get_xlabel(), "角度")
            self.assertEqual(ax.get_ylabel(), "计数")
            self.assertEqual(ax.get_yscale(), "log")
            sp = content.figure.subplotpars
            for name, value in (("left", 0.2), ("bottom", 0.15),
                                ("right", 0.85), ("top", 0.8)):
                self.assertAlmostEqual(getattr(sp, name), value, places=3)
            self.assertIsNone(content.figure.get_layout_engine(),
                              "边距由用户接管：tight layout 引擎退场")
        finally:
            w.hide()
            w.close()

    def test_custom_edits_survive_redraw(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            dock, dlg = self._build(w)
            f = dlg._fields
            f["title"].setText("我的衍射图")
            f["xlabel"].setText("角度")
            f["ylabel"].setText("计数")
            f["scale"].setCurrentIndex(f["scale"].findData("log"))
            f["left"].setValue(0.2)
            content = gui_app._content(dock)
            gui_app._apply_customize(w, dock, content.axes_1d,
                                     content.figure, dlg)
            # 程序重画（等价于图像参数 [应用] 走的 _draw_1d 路径）
            gui_app._draw_1d(w, dock, dock.last_tth, dock.last_intensity)
            ax = content.axes_1d
            self.assertEqual(ax.get_title(), "我的衍射图", "标题：用户为准")
            self.assertEqual(ax.get_xlabel(), "角度", "X 标签：用户为准")
            self.assertEqual(ax.get_ylabel(), "计数", "Y 标签：用户为准")
            self.assertEqual(ax.get_yscale(), "log", "刻度：用户为准")
            self.assertAlmostEqual(
                content.figure.subplotpars.left, 0.2, places=3,
                msg="边距不被重画覆盖")
        finally:
            w.hide()
            w.close()

    def test_dialog_flow_accept_applies_cancel_keeps(self):
        """[应用]/[取消] 两条完整路径（exec 打补丁，不真弹模态框）。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            key = "1D|data/fake_b.tif"
            content = gui_app._content(_dock(w, "1D", "data/fake_b.tif"))
            before = (content.axes_1d.get_title(),
                      content.axes_1d.get_xlabel(),
                      content.axes_1d.get_ylabel(),
                      content.axes_1d.get_yscale())
            # 取消路：exec 拒绝 → 图保持原样
            with mock.patch.object(QDialog, "exec",
                                   lambda self: QDialog.Rejected):
                gui_app._open_customize_dialog(w, key)
            self.assertEqual(
                (content.axes_1d.get_title(), content.axes_1d.get_xlabel(),
                 content.axes_1d.get_ylabel(), content.axes_1d.get_yscale()),
                before, "取消不动图")
            # 接受路：exec 里改字段再接受 → 改动生效
            def _accept(self):
                self._fields["title"].setText("流程标题")
                self._fields["scale"].setCurrentIndex(
                    self._fields["scale"].findData("log"))
                return QDialog.Accepted
            with mock.patch.object(QDialog, "exec", _accept):
                gui_app._open_customize_dialog(w, key)
            self.assertEqual(content.axes_1d.get_title(), "流程标题",
                             "应用生效")
            self.assertEqual(content.axes_1d.get_yscale(), "log")
        finally:
            w.hide()
            w.close()

    def test_toolbar_customize_button_opens_our_dialog(self):
        """工具栏 [Customize] 已从 mpl 子图配置器改接自绘对话框。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            content = gui_app._content(_dock(w, "1D", "data/fake_b.tif"))
            with mock.patch.object(gui_views, "_open_customize_dialog") as m:
                content.toolbar._actions["edit_parameters"].trigger()
            m.assert_called_once_with(w, "1D|data/fake_b.tif")
        finally:
            w.hide()
            w.close()


class TestWindowClickFocus(unittest.TestCase):
    """点面板窗口任何位置 = 选中该面板（标题栏/边框/图/工具栏都
    算，所有图都一样），参数坞跟着切。实现 = 应用级事件过滤器
    _PanelClickTracker：QWidget 的父过滤器收不到子部件事件（探针
    实证），从落点沿 parentWidget 链向上找面板容器。"""

    def _open_two(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))

    def test_click_canvas_selects_panel(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            w.focus_panel = None
            # 点画布（子部件，mpl 会 accept 鼠标按下）→ 也选中
            canvas = gui_app._content(
                _dock(w, "1D", "data/fake_a.tif")).canvas
            QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier,
                             QPoint(100, 100))
            self.assertEqual(w.focus_panel, "1D|data/fake_a.tif")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_click_title_bar_selects_panel(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            w.focus_panel = None
            # 点子窗口标题栏（不必点图本体）
            sub = _dock(w, "1D", "data/fake_b.tif")
            QTest.mouseClick(sub, Qt.LeftButton, Qt.NoModifier, QPoint(10, 10))
            self.assertEqual(w.focus_panel, "1D|data/fake_b.tif")
            # 占位面板也一样：点内容即选中
            w.focus_panel = None
            QTest.mouseClick(gui_app._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            self.assertEqual(w.focus_panel, "1D|data/fake_a.tif")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_click_floated_window_selects_panel(self):
        """弹出窗口里点内容 → 也选中（parent 链终点 = _FloatedWindow）。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            key = "1D|data/fake_a.tif"
            dock = _dock(w, "1D", "data/fake_a.tif")
            gui_app._content(dock).popout_btn.click()
            QApplication.processEvents()
            floated = w.plot_docks[key]
            self.assertNotIsInstance(floated, gui_app._PlotSubWindow)
            w.focus_panel = None
            QTest.mouseClick(gui_app._content(floated).canvas,
                             Qt.LeftButton, Qt.NoModifier, QPoint(100, 100))
            self.assertEqual(w.focus_panel, key)
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


class TestCanvasTrue53(unittest.TestCase):
    """新面板的"画布"真 5:3：画布 500×300（面板总高 = 画布 +
    工具栏 + 标题栏的壳，比旧版 500×300 面板多出来的正是壳）。"""

    def test_new_panel_canvas_is_500x300(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            QApplication.processEvents()
            canvas = gui_app._content(_dock(w, "1D", "data/fake_b.tif")).canvas
            self.assertGreater(canvas.width(), 450)
            self.assertLess(canvas.width(), 550)
            self.assertGreater(canvas.height(), 270)
            self.assertLess(canvas.height(), 330)
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


class TestCustomRatioMemory(unittest.TestCase):
    """拖过 = 永远按拖成的比例缩放：开新图、横竖排列、别的图拖动，
    都保持同比例缩放；没拖过的按默认 5:3。"""

    def _open_two(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
            _open_view(w, "1D")
            both = _wait_until(
                lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            self.assertTrue(both)
        QApplication.processEvents()

    def test_new_panel_does_not_move_existing(self):
        """开新图：旧图原地不动（这正是"不再连在一起"的核心）。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            d2 = _dock(w, "1D", "data/fake_b.tif")
            geo = d2.geometry()
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_c.tif"])
                _open_view(w, "1D")
                _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_c.tif").lines) > 0)
            QApplication.processEvents()
            d2 = _dock(w, "1D", "data/fake_b.tif")
            self.assertEqual(d2.geometry(), geo, "开新图不应挪动已有面板")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_arrange_keeps_dragged_ratio(self):
        """横排/竖排按钮：拖过的图按自己的比例、没拖过的按默认 5:3。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            d2 = _dock(w, "1D", "data/fake_b.tif")
            _resize_panel(w, d2, 700, 400)   # 模拟用户拖成 700×400
            pref = d2._canvas_pref
            for mode in ("横排", "竖排"):
                w.arrange_buttons[mode].click()
                QApplication.processEvents()
                c = gui_app._content(d2).canvas
                now = c.height() / c.width()
                expected = pref[1] / pref[0]
                self.assertAlmostEqual(now, expected, delta=0.06,
                                       msg=f"{mode}后拖过的图比例变了")
                # 没拖过的图保持默认 5:3
                d1 = _dock(w, "1D", "data/fake_a.tif")
                c1 = gui_app._content(d1).canvas
                self.assertAlmostEqual(
                    c1.height() / c1.width(), 3 / 5,
                    delta=0.06, msg=f"{mode}后默认图比例变了")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_resizing_one_panel_leaves_neighbor(self):
        """拖一张图的边框：邻居完全不动，自己记住拖成比例。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            d1 = _dock(w, "1D", "data/fake_a.tif")
            d2 = _dock(w, "1D", "data/fake_b.tif")
            geo2 = d2.geometry()
            _resize_panel(w, d1, 400, 450)
            self.assertEqual(d2.geometry(), geo2, "拖一张图牵动了邻居")
            self.assertTrue(d1._dragged)
            c1 = gui_app._content(d1).canvas
            self.assertEqual(d1._canvas_pref, (c1.width(), c1.height()),
                             "拖过的图应记住拖成时的画布比例")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


class TestViewLimitSync(unittest.TestCase):
    """缩放/平移写回参数：视图 2θ 范围 + 纵轴窗口（动纵轴才关自动）。"""

    def _open_1d(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files(["data/fake_b.tif"])
            _open_view(w, "1D")
            drawn = _wait_until(
                lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            self.assertTrue(drawn)
        return _dock(w, "1D", "data/fake_b.tif")

    def test_x_zoom_writes_view_range_back(self):
        """纯 x 缩放：视图 2θ 范围写回快照 + 参数坞框，纵轴自动不动。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = _axes(w, "1D", "data/fake_b.tif")
            ax.set_xlim(2.0, 3.0)   # 模拟缩放后的范围改动
            QApplication.processEvents()
            snap = dock.params_snapshot
            self.assertAlmostEqual(snap["视图 2θ 下限 (°)"], 2.0, places=4)
            self.assertAlmostEqual(snap["视图 2θ 上限 (°)"], 3.0, places=4)
            # 焦点面板 → 参数坞视图范围框同步显示
            self.assertAlmostEqual(
                w.params["视图 2θ 下限 (°)"].value(), 2.0, places=4)
            # 纯 x 缩放没动纵轴 → 自动仍开着
            self.assertTrue(snap["纵轴自动"])
        finally:
            w.close()

    def test_y_zoom_turns_auto_ylim_off(self):
        """动纵轴：纵轴窗口写回 + 纵轴自动关掉（窗口由用户接管）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = _axes(w, "1D", "data/fake_b.tif")
            ax.set_ylim(5.0, 10.0)
            QApplication.processEvents()
            snap = dock.params_snapshot
            self.assertFalse(snap["纵轴自动"], "动过纵轴后自动应关掉")
            self.assertAlmostEqual(snap["纵轴下限"], 5.0, places=4)
            self.assertAlmostEqual(snap["纵轴上限"], 10.0, places=4)
            # 焦点面板 → 参数坞复选框与输入框同步
            self.assertFalse(w.params["纵轴自动"].isChecked())
            self.assertAlmostEqual(w.params["纵轴下限"].value(), 5.0, places=4)
            self.assertTrue(w.params["纵轴下限"].isEnabled(),
                            "自动关掉后上下限框应解除置灰")
        finally:
            w.close()

    def test_view_range_controls_redraw(self):
        """视图 2θ 范围参与重画：改完 [应用] → 图按新窗口重画。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            w.params["视图 2θ 下限 (°)"].setValue(1.0)
            w.params["视图 2θ 上限 (°)"].setValue(5.0)
            w.findChild(QPushButton, "apply_image_btn").click()
            QApplication.processEvents()
            xlo, xhi = ax.get_xlim()
            self.assertAlmostEqual(xlo, 1.0, places=4)
            self.assertAlmostEqual(xhi, 5.0, places=4)
        finally:
            w.close()

    def test_reset_image_returns_to_follow_integration(self):
        """恢复默认：视图范围回到跟随积分范围（快照里的显式值清掉）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            ax.set_xlim(2.0, 3.0)   # 先缩放 → 快照有了显式视图范围
            QApplication.processEvents()
            w.findChild(QPushButton, "reset_image_btn").click()
            snap = dock.params_snapshot
            self.assertIsNone(snap.get("视图 2θ 下限 (°)"),
                              "恢复默认后应回到跟随积分范围")
            self.assertIsNone(snap.get("视图 2θ 上限 (°)"))
            # 输入框显示回数据组的积分范围
            self.assertAlmostEqual(
                w.params["视图 2θ 下限 (°)"].value(),
                w.params["2θ 下限 (°)"].value(), places=4)
        finally:
            w.close()


class TestGestures(unittest.TestCase):
    """手势：左键拖 = 平移；滚轮 = 只滚动绘图区；放大镜点亮时滚轮
    以光标为中心缩放（每格 10%）+ 左键拖框放大，熄灭时让位。"""

    def _open_1d(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files(["data/fake_b.tif"])
            _open_view(w, "1D")
            drawn = _wait_until(
                lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            self.assertTrue(drawn)
        return _dock(w, "1D", "data/fake_b.tif")

    def _magnifier(self, w, display, on):
        """点放大镜开关（触发 QAction = 用户点按钮），断言模式到位。"""
        content = gui_app._content(_dock(w, "1D", display))
        content.toolbar._actions["zoom"].trigger()
        QApplication.processEvents()
        self.assertEqual(gui_app._magnifier_on(_dock(w, "1D", display)), on,
                         f"放大镜应已{'点亮' if on else '熄灭'}")

    def test_magnifier_toggle_switches_mode(self):
        """放大镜 = 纯开关：点亮点灭只翻转按钮，mpl 模式永远停在 NONE
        （框选放大已删除，不再切 ZOOM 模式）。"""
        w = create_window()
        try:
            self._open_1d(w)
            content = gui_app._content(_dock(w, "1D", "data/fake_b.tif"))
            action = content.toolbar._actions["zoom"]
            self.assertTrue(action.isCheckable(), "放大镜按钮应可亮灭")
            self.assertFalse(action.isChecked())
            self._magnifier(w, "data/fake_b.tif", True)
            self.assertTrue(action.isChecked(), "点亮后按钮应亮起")
            self.assertEqual(content.toolbar.mode.name, "NONE",
                             "点亮放大镜不应切进 mpl 框选模式")
            self._magnifier(w, "data/fake_b.tif", False)
            self.assertFalse(action.isChecked(), "熄灭后按钮应熄灭")
            self.assertEqual(content.toolbar.mode.name, "NONE")
        finally:
            w.close()

    def test_wheel_ignored_until_magnifier_on(self):
        """放大镜熄灭 = 滚轮不缩图（事件穿透给绘图区滚动）；点亮才缩放。"""
        w = create_window()
        try:
            self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            ax.set_xlim(1.0, 8.0)
            gui_app._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
            self.assertEqual(ax.get_xlim(), (1.0, 8.0),
                             "放大镜熄灭时滚轮不应改范围")
            self._magnifier(w, "data/fake_b.tif", True)
            gui_app._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
            xlo, xhi = ax.get_xlim()
            self.assertAlmostEqual(xlo, 4.0 - 3.0 / 1.1, places=3,
                                   msg="点亮后滚轮应缩放")
            self.assertAlmostEqual(xhi, 4.0 + 4.0 / 1.1, places=3)
            self._magnifier(w, "data/fake_b.tif", False)
            ax.set_xlim(1.0, 8.0)
            gui_app._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
            self.assertEqual(ax.get_xlim(), (1.0, 8.0),
                             "再熄灭后滚轮应再次失效")
        finally:
            w.close()

    def test_pan_always_records_start(self):
        """左键拖 = 平移（框选放大已删除）：放大镜点不点亮都记平移起点。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            self._magnifier(w, "data/fake_b.tif", True)
            gui_app._pan_press(w, key, _press_event(ax, x=100, y=120))
            self.assertIsNotNone(getattr(dock, "_pan_start", None),
                                 "放大镜点亮时左键拖也应是平移")
            dock._pan_start = None
            self._magnifier(w, "data/fake_b.tif", False)
            gui_app._pan_press(w, key, _press_event(ax, x=100, y=120))
            self.assertIsNotNone(getattr(dock, "_pan_start", None),
                                 "放大镜熄灭时左键拖同样是平移")
        finally:
            w.close()

    def test_wheel_zoom_centers_on_cursor(self):
        """放大镜点亮时滚轮向上：光标点钉在原地，范围按每格 10% 向光标收拢。"""
        w = create_window()
        try:
            self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            ax.set_xlim(1.0, 8.0)
            self._magnifier(w, "data/fake_b.tif", True)
            gui_app._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
            xlo, xhi = ax.get_xlim()
            # 系数 1.1（每格 10%）：两侧各收拢 1/1.1
            self.assertAlmostEqual(xlo, 4.0 - 3.0 / 1.1, places=3)
            self.assertAlmostEqual(xhi, 4.0 + 4.0 / 1.1, places=3)
            # 光标对着的点"钉在原地"= 它在范围内的相对位置不变
            # （4.0 不是 (1,8) 的中点，所以中点不守恒、分数守恒）
            frac = (4.0 - xlo) / (xhi - xlo)
            self.assertAlmostEqual(frac, 3 / 7, places=3,
                                   msg="光标点的相对位置缩放后应不变")
            gui_app._wheel_zoom(w, key, _scroll_event(ax, "down", 4.0, 2.0))
            xlo2, xhi2 = ax.get_xlim()
            self.assertAlmostEqual(xlo2, 1.0, places=3)
            self.assertAlmostEqual(xhi2, 8.0, places=3)
        finally:
            w.close()

    def test_wheel_zoom_log_axis_never_freezes(self):
        """放大镜点亮时对数纵轴连续缩小：下限一路走低且始终 > 0
        （加性缩放会把下限算到 0 以下 → matplotlib 忽略整次设置，
        纵轴卡死）。"""
        w = create_window()
        try:
            self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            ax.set_yscale("log")
            ax.set_ylim(0.5, 3.0)
            self._magnifier(w, "data/fake_b.tif", True)
            prev_lo = 0.5
            for _ in range(6):
                gui_app._wheel_zoom(
                    w, key, _scroll_event(ax, "down", 4.0, 1.0))
                ylo, yhi = ax.get_ylim()
                self.assertGreater(ylo, 0.0,
                                   "对数轴下限不应撞到 0 以下")
                self.assertLess(ylo, prev_lo,
                                "每次缩小下限都应继续走低")
                prev_lo = ylo
        finally:
            w.close()

    def test_home_returns_after_wheel_zoom(self):
        """滚轮缩放后 Home 回到初始视图（滚轮绕过 mpl 手势，需自己
        补记账——否则账本空着，Home 无事可做）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            content = gui_app._content(dock)
            self._magnifier(w, "data/fake_b.tif", True)
            x0 = ax.get_xlim()
            gui_app._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
            self.assertNotEqual(ax.get_xlim(), x0, "滚轮缩放应先改范围")
            content.toolbar._actions["home"].trigger()
            QApplication.processEvents()
            xlo, xhi = ax.get_xlim()
            self.assertAlmostEqual(xlo, x0[0], places=6,
                                   msg="Home 应回到滚轮缩放前的视图")
            self.assertAlmostEqual(xhi, x0[1], places=6)
        finally:
            w.close()

    def test_home_refreshes_after_program_redraw(self):
        """程序重画后"家"刷新成新画的视图（Home 不回重画前的老视图）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            content = gui_app._content(dock)
            self._magnifier(w, "data/fake_b.tif", True)
            gui_app._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
            # 程序重画（等价：参数面板改视图范围后点 [应用]）
            dock.params_snapshot["视图 2θ 下限 (°)"] = 3.0
            dock.params_snapshot["视图 2θ 上限 (°)"] = 6.0
            gui_app._draw_1d(w, dock, dock.last_tth, dock.last_intensity)
            QApplication.processEvents()
            self.assertEqual(ax.get_xlim(), (3.0, 6.0))
            gui_app._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
            self.assertNotEqual(ax.get_xlim(), (3.0, 6.0),
                                "第二次滚轮缩放应先改范围")
            content.toolbar._actions["home"].trigger()
            QApplication.processEvents()
            xlo, xhi = ax.get_xlim()
            self.assertAlmostEqual(xlo, 3.0, places=6,
                                   msg="Home 应回到重画后的视图，不是开图时")
            self.assertAlmostEqual(xhi, 6.0, places=6)
        finally:
            w.close()

    def test_drag_pans_plot(self):
        """按住左键拖动 = 整图平移（范围随拖拽位移）。"""
        w = create_window()
        try:
            self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            ax.set_xlim(1.0, 8.0)
            ax.set_ylim(1.0, 3.0)
            # (100,120) 像素按下 → 拖到 (150,100)：matplotlib 事件的
            # y 从画布底边起算（鼠标在屏幕上向下 = y 变小）。图跟着
            # 鼠标走：向右下拖 → 数据范围整体向左上移动
            gui_app._pan_press(w, key, _press_event(ax, x=100, y=120))
            gui_app._pan_motion(w, key, _press_event(ax, x=150, y=100))
            gui_app._pan_release(w, key, _press_event(ax, x=150, y=100))
            xlo, xhi = ax.get_xlim()
            self.assertLess(xlo, 1.0, "向右拖图 → 数据范围应左移")
            self.assertLess(xhi, 8.0)
            ylo, yhi = ax.get_ylim()
            self.assertGreater(ylo, 1.0, "向下拖图 → 数据范围应上移")
            self.assertGreater(yhi, 3.0)
        finally:
            w.close()


class TestCustomizeProtection(unittest.TestCase):
    """Customize 对话框改动保护（与用户讨论定稿的规则）：标题/轴标签/
    曲线样式/纵轴刻度以用户为准，重画（应用/恢复默认/对比刷新）一律
    不覆盖；参数面板里又改了一遍（显示名 → 标题、[对数纵轴] → 刻度）
    才由参数接管。"""

    def _open_1d(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files(["data/fake_a.tif"])
            _open_view(w, "1D")
            drawn = _wait_until(
                lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0)
            self.assertTrue(drawn)
        return _dock(w, "1D", "data/fake_a.tif")

    def _redraw(self, w, dock):
        """等价于点 [应用] 的程序重画路径。"""
        gui_app._draw_1d(w, dock, dock.last_tth, dock.last_intensity)
        QApplication.processEvents()

    def test_custom_title_survives_redraw(self):
        """Customize 改过的标题重画不覆盖（连续重画也保留）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = _axes(w, "1D", "data/fake_a.tif")
            ax.set_title("我的手改标题")   # 模拟在 Customize 里改
            self._redraw(w, dock)
            self.assertEqual(ax.get_title(), "我的手改标题",
                             "Customize 改过的标题不应被重画覆盖")
            self._redraw(w, dock)
            self.assertEqual(ax.get_title(), "我的手改标题",
                             "连续重画也应一直保留")
        finally:
            w.close()

    def test_title_follows_display_change(self):
        """显示名（参数源）改过 → 标题跟新的默认（参数接管）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = _axes(w, "1D", "data/fake_a.tif")
            ax.set_title("我的手改标题")
            self._redraw(w, dock)
            self.assertEqual(ax.get_title(), "我的手改标题")
            dock.panel_display = "新名字"   # 参数源改了
            self._redraw(w, dock)
            self.assertEqual(ax.get_title(),
                             "新名字: full azimuthal integration",
                             "显示名改过 → 标题应跟新的默认")
        finally:
            w.close()

    def test_axis_labels_survive_redraw(self):
        """Customize 改过的轴标签重画不覆盖（无参数源 → 永远用户为准）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = _axes(w, "1D", "data/fake_a.tif")
            ax.set_xlabel("我的 X")
            ax.set_ylabel("我的 Y")
            self._redraw(w, dock)
            self.assertEqual(ax.get_xlabel(), "我的 X")
            self.assertEqual(ax.get_ylabel(), "我的 Y")
            self._redraw(w, dock)
            self.assertEqual(ax.get_xlabel(), "我的 X", "连续重画也应保留")
        finally:
            w.close()

    def test_curve_style_survives_redraw(self):
        """改过的线型/线宽重画不覆盖；颜色跟"曲线配色"参数走
        （任务六起颜色有了参数入口，不再按旧快照保护）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = _axes(w, "1D", "data/fake_a.tif")
            line = ax.lines[0]
            line.set_color("red")
            line.set_linestyle("--")
            line.set_linewidth(3)
            self._redraw(w, dock)
            new = ax.lines[0]
            self.assertEqual(new.get_color(), "#2a78d6",
                             "颜色跟配色参数（默认高对比第 1 槽）")
            self.assertEqual(new.get_linestyle(), "--")
            self.assertEqual(new.get_linewidth(), 3)
            self._redraw(w, dock)
            self.assertEqual(ax.lines[0].get_linestyle(), "--",
                             "连续重画也应保留线型")
        finally:
            w.close()

    def test_scale_user_wins_until_param_toggled(self):
        """Customize 改的纵轴刻度以用户为准，直到 [对数纵轴] 又改过。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = _axes(w, "1D", "data/fake_a.tif")
            ax.set_yscale("log")   # 模拟在 Customize 里改刻度
            self._redraw(w, dock)
            self.assertEqual(ax.get_yscale(), "log",
                             "Customize 改的刻度不应被重画覆盖")
            dock.params_snapshot["对数纵轴"] = True   # 参数又改了一遍
            self._redraw(w, dock)
            self.assertEqual(ax.get_yscale(), "log")
            dock.params_snapshot["对数纵轴"] = False   # 再改一遍
            self._redraw(w, dock)
            self.assertEqual(ax.get_yscale(), "linear",
                             "参数又改过 → 参数接管刻度")
        finally:
            w.close()


class TestTileScrollReset(unittest.TestCase):
    """回归：绘图区滚动状态下点横排/竖排，Qt 会把滚动偏移混进子
    窗口 move 坐标——图被多推一段、灰区越排越多（探针实证：横滚
    628 时再排列，桌面多出 628px）。修复 = 摆图前滚动归零。"""

    def _open_six(self, w):
        for i in range(3):
            gui_app._open_plot_panel(w, "1D", f"1D|fake{i}.tif", f"1D_{i}")
        for i in range(3):
            gui_app._open_plot_panel(w, "2D", f"2D|fake{i}.tif", f"2D_{i}")
        for _ in range(10):
            QApplication.processEvents()

    @staticmethod
    def _scroll(w):
        h = w.mdi.horizontalScrollBar()
        v = w.mdi.verticalScrollBar()
        return h.value(), h.maximum(), v.value(), v.maximum()

    def test_arrange_after_scrolling_resets_and_never_drifts(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_six(w)
            w.arrange_buttons["横排"].click()
            for _ in range(10):
                QApplication.processEvents()
            _, hmax1, _, vmax1 = self._scroll(w)
            self.assertGreater(hmax1, 0, "两行三列应溢出视口出滚动条")
            # 用户滚到右下角看第 6 张图
            w.mdi.horizontalScrollBar().setValue(hmax1)
            w.mdi.verticalScrollBar().setValue(
                w.mdi.verticalScrollBar().maximum())
            for _ in range(10):
                QApplication.processEvents()
            # 滚动状态下点竖排：视图应归零、布局不漂移
            w.arrange_buttons["竖排"].click()
            for _ in range(10):
                QApplication.processEvents()
            hv, _, vv, _ = self._scroll(w)
            self.assertEqual((hv, vv), (0, 0),
                             "排列后视图应回到左上角（滚动归零）")
            # 再横排：桌面最大值应与第一次横排一致（无灰区增长）。
            # 与第一次横排比而不是写死数值：面板内容尺寸会随视图
            # 接线升级（2D 从占位标签变真画布面板），写死就不稳了
            w.arrange_buttons["横排"].click()
            for _ in range(10):
                QApplication.processEvents()
            _, hmax2, _, vmax2 = self._scroll(w)
            self.assertEqual((hmax2, vmax2), (hmax1, vmax1),
                             "反复排列后滚动范围不应增长（回归：滚动偏移混入 move）")
        finally:
            w.hide()
            w.close()


class TestCascadeCap(unittest.TestCase):
    """新图落点 = 左上角小错位级联：24px 一档、6 档循环回起点
    （下面几张的标题栏露出来，永远待在左上角区域）。"""

    def test_seventh_panel_cycles_back_to_origin(self):
        w = create_window()
        try:
            subs = []
            for i in range(7):
                subs.append(gui_app._open_plot_panel(
                    w, "1D", f"1D|fake{i}.tif", f"fake{i}"))
            for _ in range(10):
                QApplication.processEvents()
            # 24px 一档：第 2 张在 (40,40)，第 6 张在 (136,136)
            self.assertEqual((subs[1].x(), subs[1].y()), (40, 40))
            self.assertEqual((subs[5].x(), subs[5].y()), (136, 136))
            # 6 档循环：第 7 张回到第 1 张的落点
            self.assertEqual((subs[6].x(), subs[6].y()),
                             (subs[0].x(), subs[0].y()),
                             "第 7 张应循环回左上角起点")
        finally:
            w.close()


class TestAreaZoom(unittest.TestCase):
    """总缩放：Ctrl+滚轮（视口过滤器 / 图上方 mpl 层拦截）与底部
    − 100% + 按钮，绘图区全体围绕视口中心同比缩放（50%–200%，
    每格 10%）；弹出去的不参与、平铺不碰它；新开/收回的图按当前
    总缩放落位；缩放不记成"用户拖过"。"""

    def _open_two(self, w):
        a = gui_app._open_plot_panel(w, "1D", "1D|a.tif", "a")
        b = gui_app._open_plot_panel(w, "2D", "2D|b.tif", "b")
        for _ in range(10):
            QApplication.processEvents()
        return a, b

    def _ctrl_wheel(self, target, pos, delta=120):
        """构造真实 QWheelEvent（Ctrl 按住）投给目标部件。"""
        ev = QWheelEvent(
            QPointF(pos), QPointF(pos), QPoint(0, 0), QPoint(0, delta),
            Qt.NoButton, Qt.ControlModifier, Qt.ScrollPhase.ScrollUpdate,
            False, Qt.MouseEventNotSynthesized,
            QPointingDevice.primaryPointingDevice())
        QApplication.sendEvent(target, ev)
        QApplication.processEvents()

    def test_apply_scales_subs_around_center(self):
        """全体同比缩放：尺寸 ×k、位置围绕视口中心缩放、比例记忆不碰。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            a, b = self._open_two(w)
            geo = {d: (d.x(), d.y(), d.width(), d.height()) for d in (a, b)}
            # 锚点 = 缩放动手时的视口中心（缩放会触发滚动条出现/
            # 消失，事后量视口会和动手时差 18px）
            cx = w.mdi.viewport().width() / 2
            cy = w.mdi.viewport().height() / 2
            gui_app._apply_area_zoom(w, 1.1)
            for _ in range(10):
                QApplication.processEvents()
            for d, (x, y, wd, ht) in geo.items():
                self.assertEqual(d.width(), round(wd * 1.1),
                                 "总缩放后宽度应 ×1.1")
                self.assertEqual(d.height(), round(ht * 1.1),
                                 "总缩放后高度应 ×1.1")
                self.assertEqual(d.x(), round(cx + (x - cx) * 1.1),
                                 "位置应围绕视口中心缩放")
                self.assertEqual(d.y(), round(cy + (y - cy) * 1.1),
                                 "位置应围绕视口中心缩放")
                self.assertFalse(getattr(d, "_dragged", False),
                                 "程序性缩放不应记成用户拖过")
            self.assertEqual(w.zoom_label.text(), "110%")
        finally:
            w.hide()
            w.close()

    def test_ctrl_wheel_on_viewport_zooms(self):
        """Ctrl+滚轮（灰底上）= 总缩放每格 10%。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            self._ctrl_wheel(w.mdi.viewport(), QPoint(50, 50))
            self.assertAlmostEqual(w._area_zoom, 1.1, places=6,
                                   msg="Ctrl+滚轮向上应放大 10%")
            self._ctrl_wheel(w.mdi.viewport(), QPoint(50, 50), delta=-120)
            self.assertAlmostEqual(w._area_zoom, 1.0, places=6,
                                   msg="Ctrl+滚轮向下应缩小回 100%")
        finally:
            w.hide()
            w.close()

    def test_ctrl_wheel_over_canvas_zooms_once(self):
        """光标在图上方 Ctrl+滚轮 = 总缩放（mpl 层拦截），且 accept
        掉 Qt 事件——否则传播到视口过滤器会再缩一次（双倍）。"""
        w = create_window()
        try:
            a, _ = self._open_two(w)
            ax = gui_app._content(a).axes_1d
            ax.set_xlim(1.0, 8.0)
            accepted = []
            gui = SimpleNamespace(
                modifiers=lambda: Qt.ControlModifier,
                angleDelta=lambda: QPoint(0, 120),
                accept=lambda: accepted.append(True))
            gui_app._wheel_zoom(
                w, "1D|a.tif",
                SimpleNamespace(inaxes=ax, guiEvent=gui,
                                xdata=4.0, ydata=2.0))
            self.assertAlmostEqual(w._area_zoom, 1.1, places=6,
                                   msg="图上方 Ctrl+滚轮应触发总缩放")
            self.assertEqual(accepted, [True],
                             "应 accept 掉 Qt 事件防止传播到视口再缩一次")
            self.assertEqual(ax.get_xlim(), (1.0, 8.0),
                             "Ctrl+滚轮不应缩放图本身（放大镜也没点亮）")
        finally:
            w.close()

    def test_clamp_50_200(self):
        """总缩放夹逼在 50%–200%。"""
        w = create_window()
        try:
            self._open_two(w)
            gui_app._apply_area_zoom(w, 0.01)
            self.assertAlmostEqual(w._area_zoom, 0.5, places=6)
            self.assertEqual(w.zoom_label.text(), "50%")
            gui_app._apply_area_zoom(w, 99.0)
            self.assertAlmostEqual(w._area_zoom, 2.0, places=6)
            self.assertEqual(w.zoom_label.text(), "200%")
        finally:
            w.close()

    def test_buttons_step_10_percent(self):
        """底部 − / + 按钮：每点一次 10%。"""
        w = create_window()
        try:
            self._open_two(w)
            w.zoom_buttons["+"].click()
            QApplication.processEvents()
            self.assertAlmostEqual(w._area_zoom, 1.1, places=6)
            w.zoom_buttons["−"].click()
            QApplication.processEvents()
            self.assertAlmostEqual(w._area_zoom, 1.0, places=6)
        finally:
            w.close()

    def test_new_panel_opens_at_current_zoom(self):
        """新图按当前总缩放开（和周围的图大小一致）。"""
        w = create_window()
        try:
            w.show()   # 显示后布局才会激活（隐藏窗口的 resize 不挤画布）
            w.resize(1400, 900)
            a, _ = self._open_two(w)
            gui_app._apply_area_zoom(w, 0.8)
            for _ in range(10):
                QApplication.processEvents()
            p = gui_app._open_plot_panel(w, "1D", "1D|c.tif", "c")
            for _ in range(10):
                QApplication.processEvents()
            # 契约 = 新图和周围已缩放的图一样大；画布被标题栏/工具栏
            # 壳吃掉固定高度，不会正好是 500×0.8，所以与邻居对比
            self.assertEqual((p.width(), p.height()), (a.width(), a.height()),
                             msg="新图子窗口应和周围已缩放的图一样大")
            canvas = gui_app._content(p).canvas
            canvas_a = gui_app._content(a).canvas
            self.assertEqual((canvas.width(), canvas.height()),
                             (canvas_a.width(), canvas_a.height()),
                             msg="新图画布应和周围图一致")
        finally:
            w.hide()
            w.close()

    def test_dock_back_lands_at_current_zoom(self):
        """弹出的图收回时按当前总缩放落位。"""
        w = create_window()
        try:
            w.show()   # 见 test_new_panel_opens_at_current_zoom
            w.resize(1400, 900)
            a, _ = self._open_two(w)
            ref = gui_app._open_plot_panel(w, "1D", "1D|ref.tif", "ref")
            for _ in range(10):
                QApplication.processEvents()
            gui_app._toggle_pop_out(w, "1D|a.tif")
            for _ in range(10):
                QApplication.processEvents()
            gui_app._apply_area_zoom(w, 0.8)
            for _ in range(10):
                QApplication.processEvents()
            gui_app._toggle_pop_out(w, "1D|a.tif")
            for _ in range(10):
                QApplication.processEvents()
            sub = _dock(w, "1D", "a.tif")
            canvas2 = gui_app._content(sub).canvas
            canvas_ref = gui_app._content(ref).canvas
            # 收回 = 按当前总缩放落位：弹出时 100%、收回时 80%，
            # 应和没弹出过的 1D 邻居 ref 一致（弹出时的画布已带
            # 100% 缩放，直接乘 80% 会双重缩，见 _pop_zoom）
            self.assertEqual((sub.width(), sub.height()),
                             (ref.width(), ref.height()),
                             msg="收回的子窗口应和周围的图一样大")
            self.assertEqual((canvas2.width(), canvas2.height()),
                             (canvas_ref.width(), canvas_ref.height()),
                             msg="收回的画布应和周围的图一致")
        finally:
            w.hide()
            w.close()

    def test_popped_window_not_touched(self):
        """总缩放只动绘图区里的图，弹出的独立窗口大小不变。"""
        w = create_window()
        try:
            a, b = self._open_two(w)
            gui_app._toggle_pop_out(w, "1D|a.tif")
            for _ in range(10):
                QApplication.processEvents()
            floated = w.plot_docks["1D|a.tif"]
            fw, fh = floated.width(), floated.height()
            bw = b.width()
            gui_app._apply_area_zoom(w, 0.5)
            for _ in range(10):
                QApplication.processEvents()
            self.assertEqual((floated.width(), floated.height()), (fw, fh),
                             "弹出窗口不应被总缩放带动")
            self.assertEqual(b.width(), round(bw * 0.5),
                             "绘图区内的图应被缩放")
        finally:
            w.close()

    def test_tile_keeps_zoom(self):
        """平铺不碰总缩放（各管各的）。"""
        w = create_window()
        try:
            self._open_two(w)
            gui_app._apply_area_zoom(w, 1.1)
            w.arrange_buttons["横排"].click()
            for _ in range(10):
                QApplication.processEvents()
            self.assertAlmostEqual(w._area_zoom, 1.1, places=6,
                                   msg="平铺不应重置总缩放")
            self.assertEqual(w.zoom_label.text(), "110%")
        finally:
            w.close()


class TestStatusBarPartition(unittest.TestCase):
    """状态栏分区：坐标框（等宽字体 + 凹槽）| 竖分隔线 | 文件标签。"""

    def test_coord_frame_and_separator(self):
        w = create_window()
        try:
            self.assertEqual(w.coord_label.frameShape(), QFrame.StyledPanel)
            self.assertIn("monospace", w.coord_label.styleSheet())
            vlines = [c for c in w.statusBar().findChildren(QFrame)
                      if c.frameShape() == QFrame.VLine]
            self.assertEqual(len(vlines), 1,
                             "坐标与文件信息之间应有竖分隔线")
        finally:
            w.close()


class TestPopOut(unittest.TestCase):
    """[弹出]/[收回]：面板搬进独立 OS 窗口再收回，内容与状态不丢；
    弹出窗口 × = 关闭即遗忘；平铺只排主窗口内的子窗口。"""

    def _open_1d(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files(["data/fake_b.tif"])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
        QApplication.processEvents()

    def test_pop_out_and_back_keeps_content(self):
        """弹出 → 内容同一个对象、曲线还在、按钮变"收回"；收回反向。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_1d(w)
            key = "1D|data/fake_b.tif"
            content = gui_app._content(_dock(w, "1D", "data/fake_b.tif"))
            gui_app._toggle_pop_out(w, key)
            QApplication.processEvents()
            floated = w.plot_docks[key]
            self.assertIsInstance(floated, gui_app._FloatedWindow)
            self.assertIs(gui_app._content(floated), content,
                          "弹出后内容应是同一个对象")
            self.assertEqual(content.popout_btn.text(), "收回")
            self.assertEqual(len(content.axes_1d.lines), 1, "曲线应保留")
            gui_app._toggle_pop_out(w, key)
            QApplication.processEvents()
            back = w.plot_docks[key]
            self.assertIsInstance(back, gui_app.QMdiSubWindow)
            self.assertIs(gui_app._content(back), content)
            self.assertEqual(content.popout_btn.text(), "弹出")
            self.assertEqual(len(content.axes_1d.lines), 1)
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_pop_out_view_sync_still_writes(self):
        """弹出后缩放/平移仍写回参数快照（回调按 key 找容器）。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_1d(w)
            key = "1D|data/fake_b.tif"
            gui_app._toggle_pop_out(w, key)
            QApplication.processEvents()
            floated = w.plot_docks[key]
            ax = gui_app._content(floated).axes_1d
            ax.set_xlim(2.0, 3.0)
            QApplication.processEvents()
            self.assertAlmostEqual(
                float(floated.params_snapshot["视图 2θ 下限 (°)"]), 2.0,
                places=4, msg="弹出后视图范围应仍写回参数快照")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_pop_out_close_forgets(self):
        """弹出窗口 × = 关闭即遗忘：登记移除。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_1d(w)
            key = "1D|data/fake_b.tif"
            gui_app._toggle_pop_out(w, key)
            QApplication.processEvents()
            w.plot_docks[key].close()
            QApplication.processEvents()
            self.assertNotIn(key, w.plot_docks, "弹出窗口 × 应抹掉登记")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_arrange_excludes_floated(self):
        """平铺只排主窗口内的子窗口，弹出去的窗口原地不动。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                    and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
            QApplication.processEvents()
            key_b = "1D|data/fake_b.tif"
            gui_app._toggle_pop_out(w, key_b)
            QApplication.processEvents()
            floated = w.plot_docks[key_b]
            geo_before = floated.geometry()
            w.arrange_buttons["横排"].click()
            QApplication.processEvents()
            self.assertIn("已横排 1 个面板", w.log_text.toPlainText())
            self.assertEqual(floated.geometry(), geo_before,
                             "平铺不应挪动弹出窗口")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


class TestPanelClose(unittest.TestCase):
    """关闭面板（子窗口 ×）= 关闭即遗忘：登记移除、焦点移交、重开
    全新默认；在飞任务迟到结果静默丢弃、旧代结果不串新图。"""

    def _open_two(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
        QApplication.processEvents()

    def test_close_removes_and_hands_over_focus(self):
        """关掉焦点面板 → 编辑对象移给下一张；全关 → 未选中。"""
        w = create_window()
        try:
            self._open_two(w)
            focus = w.focus_panel
            other = next(k for k in w.plot_docks if k != focus)
            gui_app._close_panel(w, focus)
            QApplication.processEvents()
            self.assertNotIn(focus, w.plot_docks)
            self.assertEqual(w.focus_panel, other, "焦点应移交给剩余面板")
            gui_app._close_panel(w, other)
            QApplication.processEvents()
            self.assertFalse(w.plot_docks)
            self.assertIsNone(w.focus_panel)
            self.assertEqual(w.focus_label.text(), "编辑对象：未选中图面板")
        finally:
            w.close()

    def test_reopen_is_fresh_default(self):
        """关掉前拖过/动过显示参数 → 重开 = 全新的默认图。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            key_a = "1D|data/fake_a.tif"
            _resize_panel(w, _dock(w, "1D", "data/fake_a.tif"), 700, 400)
            _axes(w, "1D", "data/fake_a.tif").set_ylim(1.0, 2.0)
            gui_app._close_panel(w, key_a)
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                _open_view(w, "1D")   # 文件仍在列表里，重新出图
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0))
            QApplication.processEvents()
            new = _dock(w, "1D", "data/fake_a.tif")
            self.assertFalse(new._dragged, "重开不应继承拖过状态")
            self.assertEqual(new._canvas_pref, (500, 300),
                             "重开应回到默认画布比例")
            canvas = gui_app._content(new).canvas
            self.assertGreater(canvas.width(), 450)
            self.assertLess(canvas.width(), 550)
            ylo, yhi = _axes(w, "1D", "data/fake_a.tif").get_ylim()
            self.assertNotAlmostEqual(ylo, 1.0,
                                      msg="重开不应继承旧显示范围")
            self.assertNotAlmostEqual(yhi, 2.0,
                                      msg="重开不应继承旧显示范围")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_close_with_inflight_task_drops_late_result(self):
        """后台还在算时关掉面板：迟到结果静默丢弃，不崩不串。"""
        def slow(path_str, geom, npt):
            time.sleep(0.3)
            return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])

        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=slow):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")   # 后台开算（0.3s）
                key = "1D|data/fake_b.tif"
                self.assertTrue(_wait_until(lambda: key in w.plot_docks))
                gui_app._close_panel(w, key)
            for _ in range(50):   # 等迟到结果送达（不崩 = 通过）
                QApplication.processEvents()
                time.sleep(0.01)
            self.assertNotIn("积分完成", w.log_text.toPlainText())
        finally:
            w.close()

    def test_compare_epoch_prevents_stale_lines(self):
        """对比在飞时关掉重开：旧代结果作废，新图只有 2 条曲线。"""
        def slow(path_str, geom, npt):
            time.sleep(0.5)
            if path_str.endswith("fake_a.tif"):
                return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])
            return np.array([0.5, 1.0, 8.5]), np.array([10.0, 20.0, 30.0])

        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=slow):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                w.compare_btn.click()
                keys = [k for k in w.plot_docks if k.startswith("对比|")]
                self.assertTrue(_wait_until(lambda: keys))
                gui_app._close_panel(w, keys[0])   # 旧代任务还在飞
                w.compare_btn.click()              # 重开新面板（新代）
                keys = [k for k in w.plot_docks if k.startswith("对比|")]
                self.assertTrue(_wait_until(lambda: keys))
                ax = gui_app._content(w.plot_docks[keys[0]]).axes_1d
                self.assertTrue(_wait_until(lambda: len(ax.lines) == 2))
            QApplication.processEvents()
            self.assertEqual(len(ax.lines), 2, "旧代结果不应混进新图")
            self.assertEqual(w.log_text.toPlainText().count("对比完成"), 1,
                             "只有新一代算完，旧代应被作废")
        finally:
            w.close()


class TestToolbarSave(unittest.TestCase):
    """面板自带工具栏 [Save]：存 PNG 并置 figure_saved（关窗不再问）。"""

    def test_toolbar_save_sets_figure_saved(self):
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            fig = gui_app._content(dock).figure
            with mock.patch.object(gui_views, "_ask_save_options",
                                   return_value={"dpi": 300, "fmt": "png"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("/tmp/panel_out", "PNG 图片 (*.png)")) as dlg, \
                 mock.patch.object(fig, "savefig") as savefig:
                gui_app._content(dock).toolbar.save_figure()
                self.assertTrue(dlg.called)
                savefig.assert_called_once_with("/tmp/panel_out.png", dpi=300)
                self.assertTrue(dock.figure_saved)
                self.assertIn("已保存 1D_fake_b.tif → /tmp/panel_out.png",
                              w.log_text.toPlainText())
        finally:
            w.close()


class TestSavePromptExcludesClosed(unittest.TestCase):
    """关软件只问当时开着的图：已关闭的面板不进入询问。"""

    def test_close_prompt_counts_only_open_panels(self):
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                    and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
            gui_app._close_panel(w, "1D|data/fake_b.tif")
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard") as confirm:
                self.assertTrue(w.close())
            self.assertEqual(confirm.call_args.args[1], 1,
                             "已关闭的面板不应再询问")
        finally:
            w.close()


class TestCalibration(unittest.TestCase):
    """校准工作台：模式切换 / 自动校准 / 手动选点 / 结果与模板 / 守卫。

    patch 目标 = gui_calib 模块里的裸名（后台线程也读模块全局）；
    引擎函数全部 mock（pyFAI 慢且非确定），只验接线与状态机。
    """

    FAKE_AUTO = {
        "dist_m": 1.5958, "poni1_px": 1045.2, "poni2_px": 1022.0,
        "offset_px": (1.0, 2.0), "rot1_deg": -0.005, "rot2_deg": -0.163,
        "rot3_deg": 0.0, "residual_deg": 0.004,
        "control_points": [(1050.0, 1020.0, 2), (1060.0, 1030.0, 4)],
    }
    FAKE_MANUAL = {
        "dist_m": 1.5962, "poni1_px": 1044.0, "poni2_px": 1021.0,
        "offset_px": (0.0, 0.0), "rot1_deg": -0.004, "rot2_deg": -0.160,
        "rot3_deg": 0.0, "residual_deg": 0.012,
    }
    FAKE_CENTER = {"cx": 1022.0, "cy": 1021.5}

    def _click_rings(self, w, specs):
        """按 lmfp1 配置几何在指定 (环号, 方位角) 处模拟校准图点击
        （等价于点图时点中该环：吸附判环应判回同一环）。

        行列注意：配置里 poni1_m↔行/y、poni2_m↔列/x，而点击坐标是
        (x=列, y=行)——x 用 poni2、y 用 poni1。
        """
        cfg = gui_app.CONFIGS["lmfp1_lab6"]["geometry"]
        theo = lab6_theoretical_2theta(cfg["wavelength_m"])
        for ring, ang in specs:
            r = cfg["dist_m"] * np.tan(np.radians(theo[ring])) \
                / cfg["pixel_size_m"]
            a = np.radians(ang)
            x = cfg["poni2_m"] / cfg["pixel_size_m"] + r * np.cos(a)
            y = cfg["poni1_m"] / cfg["pixel_size_m"] + r * np.sin(a)
            gui_calib._on_calib_click(w, w.calib_key,
                                      SimpleNamespace(xdata=x, ydata=y,
                                                      inaxes=w.calib_ax))

    def _logs(self, w):
        return w.log_text.toPlainText()

    def _enter_with_fake_a(self, w):
        """加 fake_a.tif 并进校准模式（图像读取需调用方已 mock）。

        注意：自动/手动校准的后台 worker 还会再读一次图，调用方要
        自己把 load_diffraction_image 的 patch 罩住整个测试体。
        """
        w.add_files(["data/fake_a.tif"])
        w.calib_btn.click()
        self.assertEqual(w.param_stack.currentIndex(), 1)
        self.assertIsNotNone(w.calib_dock)

    def test_enter_mode_opens_panel_and_exit_restores(self):
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            self.assertEqual(w.calib_key, "校准|data/fake_a.tif")
            self.assertIn("打开校准面板", self._logs(w))
            self.assertIn("校准模式", w.mode_label.text())
            # 16 条理论环路径按当前几何画上（青线；精确反解后不再是
            # Circle patch，见 theoretical_ring_paths）
            rings = [ln for ln in w.calib_ax.lines
                     if str(ln.get_color()).lower()
                     == gui_calib.RING_COLOR.lower()]
            self.assertGreaterEqual(len(rings), 16)
            # 退出：面板关、参数坞还原
            w.calib_btn.click()
            self.assertEqual(w.param_stack.currentIndex(), 0)
            self.assertIsNone(w.calib_dock)
            self.assertIn("回到分析模式", self._logs(w))
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calib_page_exit_button_returns_to_analysis(self):
        """校准页底部 [返回分析模式] = 把工具栏开关弹起（同源切换）。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            self.assertEqual(w.calib_btn.text(), "退出校准")
            self.assertTrue(w.calib_btn.isChecked())
            # 页面出口：点 [返回分析模式] → 开关弹起 → 翻回分析页
            w.calib_exit_btn.click()
            self.assertFalse(w.calib_btn.isChecked())
            self.assertEqual(w.calib_btn.text(), "校准")
            self.assertEqual(w.param_stack.currentIndex(), 0)
            self.assertIsNone(w.calib_dock)
            self.assertIn("回到分析模式", self._logs(w))
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_enter_mode_without_file_logs_hint(self):
        w = create_window()
        try:
            w.calib_btn.click()
            self.assertEqual(w.param_stack.currentIndex(), 1)
            self.assertIsNone(getattr(w, "calib_dock", None))
            self.assertIn("请先在文件列表勾选标样文件", self._logs(w))
        finally:
            w.close()

    def test_auto_calib_fills_result_and_enables_save(self):
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib, "calibrate_lab6",
                                   return_value=self.FAKE_AUTO) as fake:
                self._enter_with_fake_a(w)
                w.calib_start_auto.click()
                self.assertTrue(_wait_until(
                    lambda: w.calib_state["auto"] is not None, 8000))
            # 引擎初值 = 自动定环心 (cx, cy) + 参数面板几何
            self.assertEqual(fake.call_args.kwargs["center0_px"],
                             (self.FAKE_CENTER["cx"], self.FAKE_CENTER["cy"]))
            self.assertAlmostEqual(fake.call_args.kwargs["dist0_m"], 1.5958)
            # 自动列 + 状态行日志
            vals = w.calib_vals["auto"]
            self.assertEqual(vals["dist"].text(), "1595.80")
            self.assertEqual(vals["poni"].text(), "(1045.20, 1022.00)")
            self.assertEqual(vals["resid"].text(), "0.0040")
            self.assertIn("自动校准完成", self._logs(w))
            # 图按新几何重画：控制点绿点（一条 2 点散点线）画上
            self.assertTrue(any(len(line.get_xdata()) == 2
                                for line in w.calib_ax.lines))
            # 保存区：来源提示 + 保存按钮启用（保存流程另测）
            self.assertTrue(w.calib_save_btn.isEnabled())
            self.assertEqual(w.calib_save_hint.text(),
                             "将保存：自动校准（距离 1595.80 mm，残差 0.0040°）")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_rings_off_image_guard_expands_view_and_logs_once(self):
        """守卫：几何把理论环全推出图像时，明说 + 视野放大到看得见
        （静默画一圈看不见的青线是最坏的失败方式）。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                # 距离按"米"填进"毫米"框 → 大 1000 倍 → 16 条环全在外
                w.params["初始距离 (mm)"].setValue(1595800.0)
                self._enter_with_fake_a(w)
            self.assertIn("全部落在图像外", self._logs(w))
            self.assertGreater(w.calib_ax.get_xlim()[1], 256.0)
            # 同一几何重画（撤销/清空选点）不重复刷屏
            n = self._logs(w).count("全部落在图像外")
            gui_calib._redraw_calib(w)
            self.assertEqual(self._logs(w).count("全部落在图像外"), n)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_rings_and_snapping_follow_result_geometry(self):
        """青线与判环同源：都按"最近一次校准结果"的几何画/判。

        结果几何（PONI 980/1060、rot2 −0.5°）与配置条目（1045.2/1022.0、
        −0.163°）明显不同：用前者画的环上点，若按后者判会滑到隔壁环号
        （实测环 2/6/9 → 3/7/10）。"""
        w = create_window()
        res = dict(self.FAKE_AUTO, dist_m=1.58, poni1_px=980.0,
                   poni2_px=1060.0, rot1_deg=0.02, rot2_deg=-0.50)
        try:
            w.show()
            with mock.patch.object(gui_calib, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            calls = []
            real_paths = gui_calib.theoretical_ring_paths

            def spy(**kw):
                calls.append(kw)
                return real_paths(**kw)

            with mock.patch.object(gui_calib, "theoretical_ring_paths",
                                   side_effect=spy):
                gui_calib._on_auto_done(w, res)
            self.assertEqual(calls[-1]["poni1_px"], 980.0)
            self.assertEqual(calls[-1]["poni2_px"], 1060.0)
            self.assertAlmostEqual(calls[-1]["rot2_deg"], -0.50)
            # 按结果几何取环上的点 → 点它应判回同一环
            paths = real_paths(
                pixel_size_m=calls[-1]["pixel_size_m"],
                wavelength_m=calls[-1]["wavelength_m"], dist_m=res["dist_m"],
                poni1_px=res["poni1_px"], poni2_px=res["poni2_px"],
                rot1_deg=res["rot1_deg"], rot2_deg=res["rot2_deg"],
                image_shape=(256, 256))
            for ring in (2, 6, 9):
                x, y = paths["rings"][ring][1][0]
                gui_calib._on_calib_click(
                    w, w.calib_key,
                    SimpleNamespace(xdata=float(x), ydata=float(y),
                                    inaxes=w.calib_ax))
                self.assertEqual(w.calib_state["points"][-1][2], ring)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_manual_points_and_refine(self):
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            # 初始：0 点，手动按钮置灰
            self.assertEqual(w.calib_points_label.text(), "已选 0 个点 / 0 个环")
            self.assertFalse(w.calib_start_manual.isEnabled())
            # 点环 2/4/6（自动判环吸附回同一环）
            self._click_rings(w, ((2, 0), (4, 90), (6, 180)))
            self.assertEqual(len(w.calib_state["points"]), 3)
            self.assertEqual(w.calib_points_label.text(), "已选 3 个点 / 3 个环")
            self.assertTrue(w.calib_start_manual.isEnabled())
            self.assertIn("已记录第 3 个点", self._logs(w))
            # 束心附近点（离首环 1.7°）吸不上：日志忽略、列表不变
            gui_calib._on_calib_click(
                w, w.calib_key,
                SimpleNamespace(xdata=1022.0, ydata=1022.0,
                                inaxes=w.calib_ax))
            self.assertEqual(len(w.calib_state["points"]), 3)
            self.assertIn("已忽略", self._logs(w))
            # 撤销 → 2 点回灰；清空 → 0 点、撤销按钮回灰
            w.calib_undo_btn.click()
            self.assertEqual(w.calib_points_label.text(), "已选 2 个点 / 2 个环")
            self.assertFalse(w.calib_start_manual.isEnabled())
            w.calib_clear_btn.click()
            self.assertEqual(w.calib_points_label.text(), "已选 0 个点 / 0 个环")
            self.assertFalse(w.calib_undo_btn.isEnabled())
            # 重新点 3 点 → 手动校准（mock 引擎）
            self._click_rings(w, ((2, 0), (4, 90), (6, 180)))
            with mock.patch.object(gui_calib, "refine_lab6_from_points",
                                   return_value=self.FAKE_MANUAL) as fake:
                w.calib_start_manual.click()
                self.assertTrue(_wait_until(
                    lambda: w.calib_state["manual"] is not None, 8000))
            # 引擎输入：3 点 + 环号 + 束心 (行,列) 换序 + 面板几何
            self.assertEqual(len(fake.call_args.args[0]), 3)
            self.assertEqual(fake.call_args.args[1], [2, 4, 6])
            self.assertEqual(fake.call_args.kwargs["center0_px"],
                             (1022.3, 1022.0))
            # 手动列 + 点数少的可信度提示
            self.assertEqual(w.calib_vals["manual"]["dist"].text(), "1596.20")
            self.assertIn("手动校准完成", self._logs(w))
            self.assertIn("点数较少", self._logs(w))
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_delta_column_and_last_save_source(self):
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib, "calibrate_lab6",
                                   return_value=self.FAKE_AUTO), \
                 mock.patch.object(gui_calib, "refine_lab6_from_points",
                                   return_value=self.FAKE_MANUAL):
                self._enter_with_fake_a(w)
                self._click_rings(w, ((2, 0), (4, 90), (6, 180)))
                # 只跑手动 → Δ 列保持 "—"
                w.calib_start_manual.click()
                self.assertTrue(_wait_until(
                    lambda: w.calib_state["manual"] is not None, 8000))
                self.assertEqual(w.calib_vals["delta"]["dist"].text(), "—")
                # 再跑自动 → Δ = 手动 − 自动（PONI = 两点距离 px）
                w.calib_start_auto.click()
                self.assertTrue(_wait_until(
                    lambda: w.calib_state["auto"] is not None, 8000))
                d = w.calib_vals["delta"]
                self.assertEqual(d["dist"].text(), "+0.40")
                self.assertEqual(d["poni"].text(), "1.56")
                self.assertEqual(d["resid"].text(), "+0.0080")
                # 保存来源 = 最近一次完成的模式（自动后跑 → 自动结果）
                self.assertIn("将保存：自动校准",
                              w.calib_save_hint.text())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_late_result_after_exit_is_dropped(self):
        """任务在飞时退出模式：迟到结果作废（不崩、不填列）。"""
        started = threading.Event()
        release = threading.Event()

        def slow_calib(*args, **kwargs):
            started.set()
            release.wait(10)
            return self.FAKE_AUTO

        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib, "calibrate_lab6",
                                   side_effect=slow_calib):
                self._enter_with_fake_a(w)
                w.calib_start_auto.click()
                self.assertTrue(started.wait(5))
                w.calib_btn.click()   # 退出模式：面板关、代 +1
                self.assertIsNone(w.calib_dock)
                release.set()
                # 等任务收尾投递后：状态与结果区仍为空（作废不炸）
                self.assertTrue(_wait_until(lambda: not w._tasks, 5000))
                self.assertIsNone(w.calib_state["auto"])
                self.assertEqual(w.calib_vals["auto"]["dist"].text(), "—")
        finally:
            release.set()
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_restart_drops_stale_auto_result(self):
        """连点 [开始自动校准]：旧任务慢、新任务快 → 旧结果晚到作废。"""
        calls = {"n": 0}
        slow_entered = threading.Event()
        release = threading.Event()
        fast = dict(self.FAKE_AUTO, dist_m=1.6000, residual_deg=0.009)

        def two_speed_calib(*args, **kwargs):
            i = calls["n"]
            calls["n"] += 1
            if i == 0:
                slow_entered.set()
                release.wait(10)
                return self.FAKE_AUTO
            return fast

        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib, "calibrate_lab6",
                                   side_effect=two_speed_calib):
                self._enter_with_fake_a(w)
                w.calib_start_auto.click()
                self.assertTrue(slow_entered.wait(5))
                w.calib_start_auto.click()   # 连点重跑：新任务说了算
                self.assertTrue(_wait_until(
                    lambda: w.calib_state["auto"] is not None, 8000))
                self.assertEqual(w.calib_vals["auto"]["dist"].text(),
                                 "1600.00")
                release.set()   # 旧任务这时才完成 → 迟到作废
                self.assertTrue(_wait_until(lambda: not w._tasks, 5000))
                self.assertEqual(w.calib_vals["auto"]["dist"].text(),
                                 "1600.00", "旧结果不得覆盖新结果")
                self.assertIs(w.calib_state["auto"], fast)
        finally:
            release.set()
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_closing_panel_forgets_state(self):
        """× 关校准面板 = 选点/结果全清（关闭即遗忘，重开全新）。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                self._click_rings(w, ((2, 0), (4, 90), (6, 180)))
                self.assertEqual(len(w.calib_state["points"]), 3)
                dock = w.calib_dock
                dock.close()   # × 按钮
                QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.assertIsNone(w.calib_dock)
                self.assertIn("已关闭校准面板", self._logs(w))
                self.assertEqual(w.calib_state["points"], [])
                self.assertIsNone(w.calib_state["auto"])
                self.assertEqual(w.calib_points_label.text(),
                                 "已选 0 个点 / 0 个环")
                self.assertFalse(w.calib_start_manual.isEnabled())
                self.assertFalse(w.calib_save_btn.isEnabled())
                self.assertEqual(w.calib_save_hint.text(), "尚未有校准结果")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()


class TestSaveCalibConfig(unittest.TestCase):
    """[保存为配置]：校验 / 落盘 / 下拉框同步选中 / 覆盖确认 / worker 束心。

    save 走真 config.save_user_config（写临时目录的真文件），
    USER_CONFIG_PATH 指向临时路径（不碰仓库真文件），收尾还原
    USER_CONFIGS / CONFIGS 内存字典。引擎照旧 mock。
    """

    FAKE_AUTO = dict(TestCalibration.FAKE_AUTO)
    FAKE_MANUAL = dict(TestCalibration.FAKE_MANUAL)

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._path = Path(self._tmpdir) / "config_user.json"
        patcher = mock.patch.object(config_mod, "USER_CONFIG_PATH",
                                    self._path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._user_backup = dict(config_mod.USER_CONFIGS)
        self._confs_backup = dict(config_mod.CONFIGS)
        # 清空用户条目再测：真 config_user.json 里若有同名 key（本地标定
        # 存档，如 lmfp2_lab6），保存/删除会撞上"覆盖确认"等模态框——
        # offscreen 下没人应答，测试原地挂死。隔离不能依赖真文件内容
        # （test_config_user 已踩过同一个坑）。tearDown 负责还原。
        config_mod.USER_CONFIGS.clear()
        for key in self._user_backup:
            config_mod.CONFIGS.pop(key, None)

    def tearDown(self):
        config_mod.USER_CONFIGS.clear()
        config_mod.USER_CONFIGS.update(self._user_backup)
        config_mod.CONFIGS.clear()
        config_mod.CONFIGS.update(self._confs_backup)

    def _auto_calib(self, w):
        """进校准模式跑一次 mock 自动校准（worker 真跑、引擎 mock）。"""
        with mock.patch.object(gui_calib, "load_diffraction_image",
                               return_value=np.ones((256, 256)) * 10), \
             mock.patch.object(gui_calib, "fit_center_from_rings",
                               return_value=TestCalibration.FAKE_CENTER), \
             mock.patch.object(gui_calib, "calibrate_lab6",
                               return_value=dict(self.FAKE_AUTO)):
            w.add_files(["data/fake_a.tif"])
            w.calib_btn.click()
            w.calib_start_auto.click()
            self.assertTrue(_wait_until(
                lambda: w.calib_state["auto"] is not None, 8000))

    def test_save_adds_to_combo_and_selects(self):
        """保存 → 下拉框出现新条目并自动选中（几何填进参数坞 + 落盘）。"""
        w = create_window()
        try:
            w.show()
            self._auto_calib(w)
            w.calib_key_edit.setText("lmfp2_lab6")
            w.calib_label_edit.setText("lmfp 第 2 批（LaB₆ 标样标定）")
            w.calib_save_btn.click()
            # 下拉框：新条目出现 + 自动选中
            idx = w.config_combo.findData("lmfp2_lab6")
            self.assertGreaterEqual(idx, 0)
            self.assertEqual(w.config_combo.currentIndex(), idx)
            self.assertEqual(w.config_name, "lmfp2_lab6")
            # 几何填进参数坞：距离 = 校准结果（1595.80，不是初值 1600）
            self.assertAlmostEqual(w.params["初始距离 (mm)"].value(),
                                   1595.8, places=1)
            # 条目内容：结果几何 + 参数坞像素/波长 + 新拟合束心 B
            cfg = w.config
            self.assertEqual(cfg["label"], "lmfp 第 2 批（LaB₆ 标样标定）")
            self.assertAlmostEqual(cfg["geometry"]["dist_m"], 1.5958)
            self.assertAlmostEqual(cfg["geometry"]["poni1_m"],
                                   1045.2 * 200e-6)
            self.assertEqual(cfg["beam_center"],
                             (TestCalibration.FAKE_CENTER["cy"],
                              TestCalibration.FAKE_CENTER["cx"]))
            # 落盘真文件 + 内存注册表 + 日志
            self.assertTrue(self._path.is_file())
            self.assertIn("lmfp2_lab6", config_mod.CONFIGS)
            self.assertIn("已保存新配置条目", w.log_text.toPlainText())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_save_validation_errors(self):
        """key/label 校验 + 内置重名拒绝：只记日志、不落盘。"""
        w = create_window()
        try:
            w.show()
            self._auto_calib(w)
            # 非法 key
            w.calib_key_edit.setText("lmfp 2")
            w.calib_label_edit.setText("某批次")
            w.calib_save_btn.click()
            self.assertIn("key 无效", w.log_text.toPlainText())
            # 内置重名
            w.calib_key_edit.setText("lmfp1_lab6")
            w.calib_save_btn.click()
            self.assertIn("与内置条目重名", w.log_text.toPlainText())
            # 空 label
            w.calib_key_edit.setText("lmfp2_lab6")
            w.calib_label_edit.setText("  ")
            w.calib_save_btn.click()
            self.assertIn("请先填写批次备注", w.log_text.toPlainText())
            self.assertFalse(self._path.exists())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_save_overwrite_asks_and_can_confirm(self):
        """已存用户条目重名：弹确认，No 不动 / Yes 覆盖。"""
        w = create_window()
        try:
            w.show()
            self._auto_calib(w)
            w.calib_key_edit.setText("lmfp2_lab6")
            w.calib_label_edit.setText("第 2 批")
            w.calib_save_btn.click()
            self.assertIn("lmfp2_lab6", config_mod.USER_CONFIGS)
            # 同 key 再存 → 确认框 No：不覆盖
            with mock.patch.object(gui_calib.QMessageBox, "question",
                                   return_value=QMessageBox.No):
                w.calib_save_btn.click()
            self.assertEqual(config_mod.USER_CONFIGS["lmfp2_lab6"]["label"],
                             "第 2 批")
            # 确认框 Yes：覆盖
            w.calib_label_edit.setText("第 2 批（重标定）")
            with mock.patch.object(gui_calib.QMessageBox, "question",
                                   return_value=QMessageBox.Yes):
                w.calib_save_btn.click()
            self.assertEqual(config_mod.USER_CONFIGS["lmfp2_lab6"]["label"],
                             "第 2 批（重标定）")
            self.assertIn("已覆盖配置条目", w.log_text.toPlainText())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_suggest_config_key(self):
        """key 建议名：第一个数字段 +1，后缀数字（_lab6 的 6）不动。"""
        self.assertEqual(gui_calib._suggest_config_key("lmfp1_lab6"),
                         "lmfp2_lab6")
        self.assertEqual(gui_calib._suggest_config_key("lmfp12_lab6"),
                         "lmfp13_lab6")
        self.assertEqual(gui_calib._suggest_config_key("n7m3"), "n8m3")
        self.assertEqual(gui_calib._suggest_config_key("no_digits"),
                         "lab6_calib")

    def test_save_requires_result(self):
        """没有校准结果：按钮置灰 + 直调处理函数记日志拒绝。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                w.add_files(["data/fake_a.tif"])
                w.calib_btn.click()
            self.assertFalse(w.calib_save_btn.isEnabled())
            self.assertEqual(w.calib_save_hint.text(), "尚未有校准结果")
            gui_calib._save_calib_config(w)   # 按钮置灰点不到：直调处理函数
            self.assertIn("没有可保存的校准结果", w.log_text.toPlainText())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_workers_attach_beam_center(self):
        """worker 附带 beam_center_rc：自动 = 新拟合环心，手动 = 初值 B。"""
        image = np.ones((256, 256)) * 10
        geom = {"pixel_size_m": 200e-6, "wavelength_m": 0.1223e-10,
                "dist_m": 1.5958}   # worker 先读几何键再调引擎（引擎 mock）
        with mock.patch.object(gui_calib, "load_diffraction_image",
                               return_value=image), \
             mock.patch.object(gui_calib, "fit_center_from_rings",
                               return_value=TestCalibration.FAKE_CENTER), \
             mock.patch.object(gui_calib, "calibrate_lab6",
                               return_value=dict(self.FAKE_AUTO)):
            res = gui_calib._auto_calib_worker("data/fake_a.tif", geom)
        self.assertEqual(res["beam_center_rc"],
                         (TestCalibration.FAKE_CENTER["cy"],
                          TestCalibration.FAKE_CENTER["cx"]))
        # 取点拟合失败 → FFT 兜底同样附带
        with mock.patch.object(gui_calib, "load_diffraction_image",
                               return_value=image), \
             mock.patch.object(gui_calib, "fit_center_from_rings",
                               return_value=None), \
             mock.patch.object(gui_calib, "find_ring_center",
                               return_value=(1021.0, 1023.0)), \
             mock.patch.object(gui_calib, "calibrate_lab6",
                               return_value=dict(self.FAKE_AUTO)):
            res = gui_calib._auto_calib_worker("data/fake_a.tif", geom)
        self.assertEqual(res["beam_center_rc"], (1021.0, 1023.0))
        # 手动 worker：束心 = 初值 (列, 行) → (行, 列)
        with mock.patch.object(gui_calib, "refine_lab6_from_points",
                               return_value=dict(self.FAKE_MANUAL)):
            res = gui_calib._manual_calib_worker(
                [(1, 2), (3, 4), (5, 6)], [0, 1, 2], geom, (1022.3, 1022.0))
        self.assertEqual(res["beam_center_rc"], (1022.0, 1022.3))


class TestBatchProgress(unittest.TestCase):
    """批量进度计数：多文件一次出图，完成/失败日志末尾贴（k/n）。"""

    def test_batch_logs_progress_suffix_and_clears(self):
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                # fake_a 慢 0.2 s → fake_b 先完成 =（1/2），fake_a =（2/2）
                self.assertTrue(_wait_until(
                    lambda: all(
                        getattr(_dock(w, "1D", p), "last_tth", None)
                        is not None
                        for p in ("data/fake_a.tif", "data/fake_b.tif"))))
            log = w.log_text.toPlainText()
            self.assertIn("积分完成：fake_b.tif（3 点，2θ 0.500~8.500°）"
                          "（1/2）", log)
            self.assertIn("积分完成：fake_a.tif（3 点，2θ 0.500~8.500°）"
                          "（2/2）", log)
            self.assertFalse(hasattr(w, "_batch"),
                             "批走完应清账（之后零散任务不再计数）")
        finally:
            w.close()

    def test_single_file_has_no_progress_suffix(self):
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: getattr(_dock(w, "1D", "data/fake_b.tif"),
                                    "last_tth", None) is not None))
            log = w.log_text.toPlainText()
            self.assertIn("积分完成：fake_b.tif（3 点", log)
            self.assertNotIn("（1/1）", log)
        finally:
            w.close()

    def test_error_counts_toward_progress(self):
        """一个文件积分失败：错误日志同样计数，批照常走完。"""
        def flaky(path_str, geom, npt):
            if path_str.endswith("fake_a.tif"):
                raise RuntimeError("解码失败")
            return _fake_compute(path_str, geom, npt)

        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=flaky):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: not hasattr(w, "_batch")))
            log = w.log_text.toPlainText()
            self.assertIn("积分失败：fake_a.tif — RuntimeError: 解码失败",
                          log)
            self.assertIn("积分完成：fake_b.tif（3 点，2θ 0.500~8.500°）"
                          "（2/2）", log)
        finally:
            w.close()


class TestFolderImport(unittest.TestCase):
    """[文件夹]：遍历目录（支持格式白名单，与 CLI 一致）+ 重复自动跳过。"""

    def test_folder_scan_adds_supported_and_skips_duplicates(self):
        folder = tempfile.mkdtemp()
        for name in ("a.tif", "b.edf", "c.TIF", "d.txt", "e.cbf"):
            Path(folder, name).touch()
        w = create_window()
        try:
            with mock.patch.object(QFileDialog, "getExistingDirectory",
                                   return_value=folder):
                w.findChild(QPushButton, "folder_btn").click()
            names = [w.file_list.item(i).text()
                     for i in range(w.file_list.count())]
            self.assertEqual(names, ["a.tif", "b.edf", "c.TIF", "e.cbf"])
            self.assertIn("找到 4 个数据文件", w.log_text.toPlainText())
            # 整目录重导：全部重复 → 逐个跳过不弹窗，列表不长
            with mock.patch.object(QFileDialog, "getExistingDirectory",
                                   return_value=folder):
                w.findChild(QPushButton, "folder_btn").click()
            self.assertEqual(w.file_list.count(), 4)
            self.assertIn("已跳过重复文件",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_empty_folder_logs_hint(self):
        folder = tempfile.mkdtemp()
        w = create_window()
        try:
            with mock.patch.object(QFileDialog, "getExistingDirectory",
                                   return_value=folder):
                w.findChild(QPushButton, "folder_btn").click()
            self.assertIn("文件夹里没有支持的数据文件",
                          w.log_text.toPlainText())
            self.assertEqual(w.file_list.count(), 0)
        finally:
            w.close()

    def test_folder_dialog_cancelled_does_nothing(self):
        w = create_window()
        try:
            with mock.patch.object(QFileDialog, "getExistingDirectory",
                                   return_value=""):
                w.findChild(QPushButton, "folder_btn").click()
            self.assertEqual(w.file_list.count(), 0)
        finally:
            w.close()


class TestExportData(unittest.TestCase):
    """[导出数据]：1D 结果批量落盘（镜像 CLI 格式）+ 取消/跳过/单文件失败。"""

    def _two_1d_results(self, w):
        """勾两个文件出 1D（mock 计算），等结果进面板缓存。"""
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: all(
                    getattr(_dock(w, "1D", p), "last_tth", None)
                    is not None
                    for p in ("data/fake_a.tif", "data/fake_b.tif"))))

    def test_export_writes_cli_format_files(self):
        w = create_window()
        try:
            self._two_1d_results(w)
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_app, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".txt",
                                                 "csv": False}):
                gui_app._run_export(w)
            target = outdir / "fake_a" / "integrated_2th.txt"
            self.assertTrue(target.is_file())
            lines = target.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "# 2theta(deg)  intensity")
            data = np.loadtxt(str(target))
            np.testing.assert_allclose(data[:, 0], [0.5, 1.0, 8.5])
            np.testing.assert_allclose(data[:, 1], [1.0, 2.0, 3.0])
            self.assertTrue(
                (outdir / "fake_b" / "integrated_2th.txt").is_file())
            log = w.log_text.toPlainText()
            self.assertIn("导出完成：2 个文件", log)
        finally:
            w.close()

    def test_export_chi_suffix(self):
        w = create_window()
        try:
            self._two_1d_results(w)
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_app, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".chi",
                                                 "csv": False}):
                gui_app._run_export(w)
            self.assertTrue(
                (outdir / "fake_b" / "integrated_2th.chi").is_file())
        finally:
            w.close()

    def test_export_cancel_writes_nothing(self):
        w = create_window()
        try:
            self._two_1d_results(w)
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_app, "_build_export_dialog",
                                   return_value=None):
                gui_app._run_export(w)
            self.assertIn("已取消导出", w.log_text.toPlainText())
            self.assertEqual(list(outdir.iterdir()), [])
        finally:
            w.close()

    def test_export_without_1d_results_logs_hint(self):
        """勾了文件但没出过 1D：逐条提示先点 [1D]，不弹导出设置框。"""
        w = create_window()
        try:
            w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
            with mock.patch.object(gui_app, "_build_export_dialog") as dlg:
                gui_app._run_export(w)
            dlg.assert_not_called()
            log = w.log_text.toPlainText()
            self.assertIn("跳过 fake_a.tif：还没有 1D 结果", log)
            self.assertIn("跳过 fake_b.tif：还没有 1D 结果", log)
            self.assertIn("没有可导出的 1D 结果", log)
        finally:
            w.close()

    def test_export_one_failure_does_not_abort_batch(self):
        """单个文件写盘失败只记日志，其余照常导出。"""
        w = create_window()
        try:
            self._two_1d_results(w)
            outdir = Path(tempfile.mkdtemp())
            real_write = gui_app._write_export
            calls = {"n": 0}

            def flaky_write(target, tth, intensity):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("磁盘已满")
                real_write(target, tth, intensity)

            with mock.patch.object(gui_app, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".txt",
                                                 "csv": False}), \
                 mock.patch.object(gui_app, "_write_export",
                                   side_effect=flaky_write):
                gui_app._run_export(w)
            log = w.log_text.toPlainText()
            self.assertIn("导出失败 fake_a", log)
            self.assertIn("导出完成：1 个文件", log)
            self.assertEqual(sorted(p.name for p in outdir.iterdir()),
                             ["fake_b"])
        finally:
            w.close()


class TestExportCsv(unittest.TestCase):
    """CSV 总表：同网格直拼 / 网格不一致三路（交集/跳过/取消）。"""

    def test_same_grid_csv(self):
        w = create_window()
        try:
            tth = np.array([0.5, 1.0, 8.5])
            results = [("a", tth, np.array([1.0, 2.0, 3.0])),
                       ("b", tth, np.array([4.0, 5.0, 6.0]))]
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_app, "_ask_csv_range") as ask:
                gui_app._write_csv_summary(w, results, outdir)
            ask.assert_not_called()   # 网格一致不打扰用户
            target = outdir / "1d_summary.csv"
            self.assertEqual(target.read_text().splitlines()[0],
                             "2theta(deg),a,b")
            data = np.loadtxt(str(target), delimiter=",", skiprows=1)
            self.assertEqual(data.shape, (3, 3))
            np.testing.assert_allclose(data[:, 0], tth)
            np.testing.assert_allclose(data[:, 1], [1, 2, 3])
            self.assertIn("已生成 CSV 总表", w.log_text.toPlainText())
        finally:
            w.close()

    def test_mismatch_intersect_reinterpolates(self):
        w = create_window()
        try:
            results = [("a", np.array([1.0, 2.0, 3.0]),
                        np.array([1.0, 2.0, 3.0])),
                       ("b", np.array([2.0, 3.0, 4.0]),
                        np.array([2.0, 3.0, 4.0]))]
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_app, "_ask_csv_range",
                                   return_value="intersect"):
                gui_app._write_csv_summary(w, results, outdir)
            data = np.loadtxt(str(outdir / "1d_summary.csv"),
                              delimiter=",", skiprows=1)
            # 公共交集 2~3° 按最大点数均匀取样，两列都重插到公共网格
            self.assertEqual(data.shape, (3, 3))
            np.testing.assert_allclose(data[:, 0], [2.0, 2.5, 3.0])
            np.testing.assert_allclose(data[:, 1], [2.0, 2.5, 3.0])
            np.testing.assert_allclose(data[:, 2], [2.0, 2.5, 3.0])
            self.assertIn("取公共交集", w.log_text.toPlainText())
        finally:
            w.close()

    def test_mismatch_skip_drops_different(self):
        w = create_window()
        try:
            results = [("a", np.array([1.0, 2.0, 3.0]),
                        np.array([1.0, 1.0, 1.0])),
                       ("b", np.array([0.0, 1.0]),
                        np.array([2.0, 2.0]))]
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_app, "_ask_csv_range",
                                   return_value="skip"):
                gui_app._write_csv_summary(w, results, outdir)
            data = np.loadtxt(str(outdir / "1d_summary.csv"),
                              delimiter=",", skiprows=1)
            self.assertEqual(data.shape, (3, 2))   # 只留网格一致的文件
            self.assertIn("跳过 1 个", w.log_text.toPlainText())
        finally:
            w.close()

    def test_mismatch_cancel_writes_nothing(self):
        w = create_window()
        try:
            results = [("a", np.array([1.0, 2.0]), np.array([1.0, 1.0])),
                       ("b", np.array([3.0, 4.0]), np.array([2.0, 2.0]))]
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_app, "_ask_csv_range",
                                   return_value="cancel"):
                gui_app._write_csv_summary(w, results, outdir)
            self.assertIn("已取消 CSV 总表", w.log_text.toPlainText())
            self.assertEqual(list(outdir.iterdir()), [])
        finally:
            w.close()

    def test_ask_csv_range_defaults_to_skip_when_not_visible(self):
        """窗口未显示（测试环境）：问询对话框不弹，直接按"跳过"处理。"""
        w = create_window()
        try:
            self.assertEqual(gui_app._ask_csv_range(w), "skip")
        finally:
            w.close()

    def test_export_flow_writes_csv_when_checked(self):
        """导出勾选 CSV → 总表与逐文件 txt 一起落盘。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: getattr(_dock(w, "1D", "data/fake_b.tif"),
                                    "last_tth", None) is not None))
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_app, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".txt",
                                                 "csv": True}):
                gui_app._run_export(w)
            self.assertTrue((outdir / "fake_b" / "integrated_2th.txt")
                            .is_file())
            csv = outdir / "1d_summary.csv"
            self.assertEqual(csv.read_text().splitlines()[0],
                             "2theta(deg),fake_b")
            data = np.loadtxt(str(csv), delimiter=",", skiprows=1)
            self.assertEqual(data.shape, (3, 2))
        finally:
            w.close()


class TestPoniImport(unittest.TestCase):
    """[加载参数]：读 pyFAI 几何 → 用户条目落盘 + 下拉框自动选中。

    _import_poni 真调 pyFAI.load（读临时 .poni 文件）；USER_CONFIG_PATH
    指向临时路径（同 TestSaveCalibConfig），收尾还原内存字典。
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._path = Path(self._tmpdir) / "config_user.json"
        patcher = mock.patch.object(config_mod, "USER_CONFIG_PATH",
                                    self._path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._user_backup = dict(config_mod.USER_CONFIGS)
        self._confs_backup = dict(config_mod.CONFIGS)
        # 清空用户条目再测：真 config_user.json 里若有同名 key（本地标定
        # 存档，如 lmfp2_lab6），保存/删除会撞上"覆盖确认"等模态框——
        # offscreen 下没人应答，测试原地挂死。隔离不能依赖真文件内容
        # （test_config_user 已踩过同一个坑）。tearDown 负责还原。
        config_mod.USER_CONFIGS.clear()
        for key in self._user_backup:
            config_mod.CONFIGS.pop(key, None)

    def tearDown(self):
        config_mod.USER_CONFIGS.clear()
        config_mod.USER_CONFIGS.update(self._user_backup)
        config_mod.CONFIGS.clear()
        config_mod.CONFIGS.update(self._confs_backup)

    def _poni_file(self, name, **over):
        lines = {
            "Version": "2.1",
            "Detector": "Pilatus",
            "Detector_config":
                '{"pixel1": 0.0002, "pixel2": 0.0002,'
                ' "max_shape": [2048, 2048]}',
            "Distance": "1.5958",
            "Poni1": str(1045.2 * 200e-6),
            "Poni2": str(1022.0 * 200e-6),
            "Rot1": str(np.radians(-0.005)),
            "Rot2": str(np.radians(-0.163)),
            "Rot3": "0",
            "Wavelength": "1.223e-11",
        }
        lines.update(over)
        p = Path(self._tmpdir, name)
        p.write_text("\n".join(f"{k}: {v}" for k, v in lines.items())
                     + "\n", encoding="utf-8")
        return p

    def _click_poni(self, w, path):
        with mock.patch.object(QFileDialog, "getOpenFileName",
                               return_value=(str(path),
                                             "pyFAI 几何 (*.poni)")):
            w.findChild(QPushButton, "poni_btn").click()

    def test_import_adds_entry_and_selects(self):
        w = create_window()
        try:
            p = self._poni_file("mygeom.poni")
            self._click_poni(w, p)
            idx = w.config_combo.findData("mygeom")
            self.assertGreaterEqual(idx, 0)
            self.assertEqual(w.config_combo.currentIndex(), idx)
            self.assertEqual(w.config_name, "mygeom")
            cfg = w.config
            self.assertAlmostEqual(cfg["geometry"]["dist_m"], 1.5958)
            self.assertAlmostEqual(cfg["geometry"]["pixel_size_m"], 200e-6)
            self.assertAlmostEqual(cfg["geometry"]["rot1_deg"], -0.005)
            self.assertAlmostEqual(cfg["geometry"]["rot2_deg"], -0.163)
            # 束心 = pyFAI getFit2D 直射束落点（含倾斜修正，B ≠ PONI）
            import pyFAI
            f = pyFAI.load(str(p)).getFit2D()
            self.assertAlmostEqual(cfg["beam_center"][0], f.centerY,
                                   places=4)
            self.assertAlmostEqual(cfg["beam_center"][1], f.centerX,
                                   places=4)
            # 落盘真文件 + 日志
            self.assertTrue(self._path.is_file())
            self.assertIn("已导入 .poni → 配置条目 mygeom",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_key_sanitized_and_collision_suffixed(self):
        w = create_window()
        try:
            self._click_poni(w, self._poni_file("123 weird name!.poni"))
            self.assertIn("poni_123_weird_name_", config_mod.USER_CONFIGS)
            # 与内置条目撞名：自动补 _poni1
            self._click_poni(w, self._poni_file("lmfp1_lab6.poni"))
            self.assertIn("lmfp1_lab6_poni1", config_mod.USER_CONFIGS)
            self.assertEqual(w.config_name, "lmfp1_lab6_poni1")
            self.assertIn("lmfp1_lab6_poni1", w.log_text.toPlainText())
        finally:
            w.close()

    def test_missing_fields_rejected(self):
        """旧版 v1 .poni 不带探测器 → 像素缺失 → 拒绝导入。"""
        w = create_window()
        try:
            v1 = Path(self._tmpdir, "old.poni")
            v1.write_text(
                "Distance: 1.5958\nPoni1: 0.20904\nPoni2: 0.2044\n"
                "Rot1: -8.7e-05\nRot2: -0.0028\nRot3: 0\n"
                "Wavelength: 1.223e-11\n", encoding="utf-8")
            self._click_poni(w, v1)
            log = w.log_text.toPlainText()
            self.assertIn("缺少几何字段", log)
            self.assertIn("pixel", log)
            self.assertNotIn("old", config_mod.USER_CONFIGS)
        finally:
            w.close()


class TestDeleteConfig(unittest.TestCase):
    """[删除] 几何配置条目：只删用户条目（内置置灰 + 处理函数双保险）、
    确认框、删后回退默认、磁盘同步。

    remove 走真 config.remove_user_config（写临时目录的真文件），
    USER_CONFIG_PATH 指向临时路径（不碰仓库真文件），收尾还原
    USER_CONFIGS / CONFIGS 内存字典。
    """

    ENTRY = {
        "label": "tmp 删除测试条目",
        "geometry": {"pixel_size_m": 200e-6, "wavelength_m": 1.223e-10,
                     "dist_m": 1.6, "poni1_m": 0.21, "poni2_m": 0.20,
                     "rot1_deg": 0.0, "rot2_deg": -0.16},
        "beam_center": (1022.0, 1022.3),
    }

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._path = Path(self._tmpdir) / "config_user.json"
        patcher = mock.patch.object(config_mod, "USER_CONFIG_PATH",
                                    self._path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._user_backup = dict(config_mod.USER_CONFIGS)
        self._confs_backup = dict(config_mod.CONFIGS)
        # 清空用户条目再测：真 config_user.json 里若有同名 key（本地标定
        # 存档，如 lmfp2_lab6），保存/删除会撞上"覆盖确认"等模态框——
        # offscreen 下没人应答，测试原地挂死。隔离不能依赖真文件内容
        # （test_config_user 已踩过同一个坑）。tearDown 负责还原。
        config_mod.USER_CONFIGS.clear()
        for key in self._user_backup:
            config_mod.CONFIGS.pop(key, None)

    def tearDown(self):
        config_mod.USER_CONFIGS.clear()
        config_mod.USER_CONFIGS.update(self._user_backup)
        config_mod.CONFIGS.clear()
        config_mod.CONFIGS.update(self._confs_backup)

    def _close(self, w):
        with mock.patch.object(gui_app, "_confirm_close",
                               return_value="discard"):
            w.close()

    def test_remove_user_config_unit(self):
        """config.remove_user_config：删除 / 再删无操作 / 内置报错 / 落盘。"""
        config_mod.save_user_config("tmp_del", dict(self.ENTRY))
        self.assertTrue(config_mod.remove_user_config("tmp_del"))
        self.assertNotIn("tmp_del", config_mod.USER_CONFIGS)
        self.assertNotIn("tmp_del", config_mod.CONFIGS)
        # 再删同一条：无操作返回 False
        self.assertFalse(config_mod.remove_user_config("tmp_del"))
        # 内置条目：人工登记注册表，报错拒绝
        with self.assertRaises(ValueError):
            config_mod.remove_user_config("lmfp1_lab6")
        # 磁盘文件同步：不含已删条目
        self.assertNotIn("tmp_del", self._path.read_text(encoding="utf-8"))

    def test_delete_button_removes_and_falls_back_to_default(self):
        """选中用户条目 → [删除] 确认 → 条目消失 + 回退默认 + 按钮置灰。"""
        config_mod.save_user_config("tmp_del", dict(self.ENTRY))
        w = create_window()
        try:
            w.show()
            idx = w.config_combo.findData("tmp_del")
            self.assertGreaterEqual(idx, 0)
            w.config_combo.setCurrentIndex(idx)   # 选中用户条目 → 按钮可用
            self.assertTrue(w.del_config_btn.isEnabled())
            with mock.patch.object(gui_app.QMessageBox, "question",
                                   return_value=QMessageBox.Yes) as ask:
                w.del_config_btn.click()
            ask.assert_called_once()
            # 注册表 + 下拉框 + 磁盘：条目消失
            self.assertNotIn("tmp_del", config_mod.USER_CONFIGS)
            self.assertEqual(w.config_combo.findData("tmp_del"), -1)
            self.assertNotIn("tmp_del", self._path.read_text(encoding="utf-8"))
            # 回退默认条目（内置）→ [删除] 重新置灰
            self.assertEqual(w.config_combo.currentData(),
                             config_mod.DEFAULT_CONFIG)
            self.assertFalse(w.del_config_btn.isEnabled())
            self.assertIn("已删除配置条目 tmp_del", w.log_text.toPlainText())
        finally:
            self._close(w)

    def test_builtin_selected_button_disabled_and_handler_refuses(self):
        """内置条目选中：按钮置灰；直调处理函数也不弹框、注册表不动。"""
        w = create_window()
        try:
            w.show()
            self.assertIn(w.config_combo.currentData(),
                          config_mod.BUILTIN_CONFIGS)
            self.assertFalse(w.del_config_btn.isEnabled())
            # 置灰是体验层，处理函数是安全层：直调也被拒
            with mock.patch.object(gui_app.QMessageBox, "question") as ask:
                gui_app._delete_config(w)
            ask.assert_not_called()
            self.assertIn("内置条目", w.log_text.toPlainText())
        finally:
            self._close(w)

    def test_delete_cancel_keeps_entry(self):
        """确认框选 No：条目原样保留，选中不变。"""
        config_mod.save_user_config("tmp_del", dict(self.ENTRY))
        w = create_window()
        try:
            w.show()
            w.config_combo.setCurrentIndex(w.config_combo.findData("tmp_del"))
            with mock.patch.object(gui_app.QMessageBox, "question",
                                   return_value=QMessageBox.No):
                w.del_config_btn.click()
            self.assertIn("tmp_del", config_mod.USER_CONFIGS)
            self.assertEqual(w.config_combo.currentData(), "tmp_del")
        finally:
            self._close(w)


class TestSavePoni(unittest.TestCase):
    """[保存参数]：当前选中配置 → 标准 .poni 文件（pyFAI Geometry.save）。

    真调 pyFAI 写读往返：写出的文件能被 pyFAI.load 读回，几何 7 字段
    与原配置一致（保存内容 = 距离/中心/像素/波长/倾斜角）。
    """

    def test_buttons_labelled_save_and_load(self):
        """作业规格按钮名：[加载参数] + [保存参数]。"""
        w = create_window()
        try:
            self.assertEqual(w.findChild(QPushButton, "poni_btn").text(),
                             "加载参数")
            self.assertEqual(
                w.findChild(QPushButton, "save_poni_btn").text(),
                "保存参数")
        finally:
            w.close()

    def test_save_writes_poni_roundtrip(self):
        w = create_window()
        try:
            out = Path(tempfile.mkdtemp()) / "sub" / "lmfp1_lab6.poni"
            with mock.patch.object(
                    QFileDialog, "getSaveFileName",
                    return_value=(str(out), "pyFAI 几何 (*.poni)")):
                w.findChild(QPushButton, "save_poni_btn").click()
            self.assertTrue(out.is_file())
            import pyFAI
            g = pyFAI.load(str(out))
            cfg = w.config["geometry"]
            self.assertAlmostEqual(g.dist, cfg["dist_m"])
            self.assertAlmostEqual(g.poni1, cfg["poni1_m"])
            self.assertAlmostEqual(g.poni2, cfg["poni2_m"])
            self.assertAlmostEqual(g.rot1, np.radians(cfg["rot1_deg"]))
            self.assertAlmostEqual(g.rot2, np.radians(cfg["rot2_deg"]))
            self.assertAlmostEqual(g.pixel1, cfg["pixel_size_m"])
            self.assertAlmostEqual(g.wavelength, cfg["wavelength_m"])
            log = w.log_text.toPlainText()
            self.assertIn("已保存几何参数", log)
            self.assertIn("距离", log)
            self.assertIn("中心 poni1", log)
            self.assertIn("倾斜 rot1", log)
        finally:
            w.close()

    def test_save_cancel_writes_nothing(self):
        w = create_window()
        try:
            with mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("", "")):
                w.findChild(QPushButton, "save_poni_btn").click()
            self.assertNotIn("已保存几何参数", w.log_text.toPlainText())
        finally:
            w.close()


class TestHeatmap(unittest.TestCase):
    """[热图]：勾选文件的 1D 曲线拼成 2θ×样品 强度热图。

    已算好的 1D 面板缓存直接复用，缺的后台补积分（进度计数同
    批量管线）；显示参数（色图/归一化/对数/范围）走面板快照，
    图像 [应用] 只重画、数据 [应用] 全部重积分。
    """

    def _heat_dock(self, w):
        """找到热图面板（键 = "热图|路径串"）。"""
        return next(d for k, d in w.plot_docks.items()
                    if k.startswith("热图|"))

    def _wait_heat(self, w):
        return _wait_until(
            lambda: any(k.startswith("热图|")
                        and getattr(d, "heat_data", None) is not None
                        for k, d in w.plot_docks.items()))

    def test_assemble_heatmap_same_grid_no_interp(self):
        r = [("a", np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.0])),
             ("b", np.array([1.0, 2.0, 3.0]), np.array([4.0, 5.0, 6.0]))]
        tth, matrix, stems, interp = gui_views._assemble_heatmap(r)
        self.assertFalse(interp)
        self.assertEqual(stems, ["a", "b"])
        np.testing.assert_array_equal(matrix, [[1, 2, 3], [4, 5, 6]])

    def test_assemble_heatmap_mismatched_grids_interp(self):
        """网格不一致（点数/区间不同）→ 重插值到第一个文件的网格。"""
        r = [("a", np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.0])),
             ("b", np.array([1.0, 3.0]), np.array([10.0, 30.0]))]
        tth, matrix, stems, interp = gui_views._assemble_heatmap(r)
        self.assertTrue(interp)
        np.testing.assert_allclose(matrix[1], [10.0, 20.0, 30.0])

    def test_assemble_heatmap_empty_returns_none(self):
        self.assertIsNone(gui_views._assemble_heatmap([]))
        self.assertIsNone(gui_views._assemble_heatmap(
            [("a", np.array([]), np.array([]))]))

    def test_heat_shown_modes(self):
        m = np.array([[1.0, 2.0], [3.0, 4.0]])
        np.testing.assert_array_equal(gui_state._heat_shown(m, "off"), m)
        np.testing.assert_allclose(gui_state._heat_shown(m, "each"),
                                   [[0.5, 1.0], [0.75, 1.0]])
        np.testing.assert_allclose(gui_state._heat_shown(m, "global"),
                                   m / 4.0)

    def test_heatmap_uses_cached_1d_and_draws(self):
        """1D 已算好 → [热图] 零后台任务直接出图（复用面板缓存）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute) as c:
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: all(
                        getattr(_dock(w, "1D", p), "last_tth", None)
                        is not None
                        for p in ("data/fake_a.tif", "data/fake_b.tif"))))
                calls = c.call_count
                w.heat_btn.click()
                self.assertTrue(self._wait_heat(w))
            self.assertEqual(c.call_count, calls)   # 复用缓存：零新任务
            log = w.log_text.toPlainText()
            self.assertIn("复用已有 1D 结果，后台积分 0 个", log)
            self.assertIn("热图完成：2 个样品 × 3 点", log)
            # 图真的画上去了：imshow + 颜色条 + 行标签 = 文件名
            dock = self._heat_dock(w)
            ax = gui_app._content(dock).axes_heat
            self.assertEqual(len(ax.images), 1)
            self.assertIsNotNone(dock._heat_colorbar)
            self.assertEqual([t.get_text() for t in ax.get_yticklabels()],
                             ["fake_a", "fake_b"])
            self.assertTrue(w.focus_panel.startswith("热图|"))
        finally:
            w.close()

    def test_heatmap_integrates_missing_with_progress(self):
        """没算过 1D → 后台补积分，进度计数（1/2）（2/2），完成出图。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                w.heat_btn.click()
                self.assertTrue(self._wait_heat(w))
            log = w.log_text.toPlainText()
            self.assertIn("开始热图：2 个文件（复用已有 1D 结果，"
                          "后台积分 2 个）", log)
            self.assertIn("热图：fake_b.tif 积分完成（3 点）", log)
            self.assertIn("热图：fake_a.tif 积分完成（3 点）", log)
            # 两条完成回调跑在独立 QThread，先后由调度决定（教训 12）：
            # 只断言两条都计数、合计 (1/2)+(2/2)
            k = re.findall(r"热图：fake_[ab]\.tif 积分完成（3 点）（(\d)/2）",
                           log)
            self.assertEqual(set(k), {"1", "2"})
            self.assertIn("热图完成：2 个样品 × 3 点", log)
            self.assertFalse(hasattr(w, "_batch"),
                             "批走完应清账（之后零散任务不再计数）")
        finally:
            w.close()

    def test_heatmap_single_file_hint(self):
        w = create_window()
        try:
            w.add_files(["data/fake_a.tif"])
            w.heat_btn.click()
            self.assertIn("热图至少勾选两个文件", w.log_text.toPlainText())
        finally:
            w.close()

    def test_heatmap_error_still_draws_others(self):
        """一个文件失败：错误日志计数，其余照样成图。"""
        def flaky(path_str, geom, npt):
            if path_str.endswith("fake_a.tif"):
                raise RuntimeError("解码失败")
            return _fake_compute(path_str, geom, npt)

        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=flaky):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                w.heat_btn.click()
                self.assertTrue(self._wait_heat(w))
            log = w.log_text.toPlainText()
            self.assertIn("热图：fake_a.tif 积分失败 — RuntimeError: 解码失败",
                          log)
            self.assertIn("热图：fake_b.tif 积分完成（3 点）", log)
            # 完成/失败回调各跑在独立 QThread，谁先到主线程由调度决定
            # （教训 12）——不写死先后，只断言"失败也参与计数、两条
            # 合计 (1/2)+(2/2)"
            k = re.findall(r"热图：(?:fake_a|fake_b)\.tif 积分(?:完成|失败)"
                           r".*?（(\d)/2）", log)
            self.assertEqual(set(k), {"1", "2"})
            self.assertIn("热图完成：1 个样品 × 3 点", log)
        finally:
            w.close()

    def test_heatmap_mismatched_grids_logs_interp_note(self):
        """各文件网格不一致 → 出图 + 日志提示重插值。"""
        def grids(path_str, geom, npt):
            if path_str.endswith("fake_a.tif"):
                return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])
            return np.array([0.5, 8.5]), np.array([1.0, 3.0])

        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=grids):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                w.heat_btn.click()
                self.assertTrue(self._wait_heat(w))
            log = w.log_text.toPlainText()
            self.assertIn("各文件 2θ 网格不一致，已重插值到"
                          "第一个文件的网格", log)
            dock = self._heat_dock(w)
            self.assertEqual(dock.heat_data[1].shape, (2, 3))
        finally:
            w.close()

    def test_heatmap_image_apply_redraws_with_new_colormap(self):
        """图像 [应用]：改色图 → 重画（不重新积分）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute) as c:
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                w.heat_btn.click()
                self.assertTrue(self._wait_heat(w))
                calls = c.call_count
            dock = self._heat_dock(w)
            ax = gui_app._content(dock).axes_heat
            self.assertEqual(ax.images[0].get_cmap().name, "magma")
            w.params["热图色图"].setCurrentIndex(
                w.params["热图色图"].findData("viridis"))
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertEqual(ax.images[0].get_cmap().name, "viridis")
            self.assertEqual(c.call_count, calls)   # 只重画不重算
        finally:
            w.close()

    def test_heatmap_repeated_apply_does_not_shrink_axes(self):
        """图像 [应用] 反复重画：主坐标轴宽度不缩（教训 13——
        remove+重建颜色条每次让 20% 宽度且不退还；颜色条改为
        update_normal 复用后几何只算一次）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                w.heat_btn.click()
                self.assertTrue(self._wait_heat(w))
            dock = self._heat_dock(w)
            ax = gui_app._content(dock).axes_heat
            widths = []
            for _ in range(3):
                w.findChild(QPushButton, "apply_image_btn").click()
                widths.append(float(ax.get_position().width))
            self.assertLess(max(widths) - min(widths), 1e-3,
                            f"热图宽度在反复[应用]后缩小：{widths}")
        finally:
            w.close()

    def test_heatmap_data_apply_reintegrates_all(self):
        """数据 [应用]：改了数据参数 → 全部文件重新积分（无视缓存）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute) as c:
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                w.heat_btn.click()
                self.assertTrue(self._wait_heat(w))
                calls = c.call_count
                w.findChild(QPushButton, "apply_btn").click()
                self.assertTrue(_wait_until(
                    lambda: c.call_count >= calls + 2))
        finally:
            w.close()


class Test2DColorbar(unittest.TestCase):
    """2D 视图带颜色条（作业规格"带颜色条"）：每画一次恰好一条，
    颜色条只建一次、重画 update_normal 复用（教训 13）。"""

    def test_2d_has_single_colorbar_and_redraw_does_not_stack(self):
        w = create_window()
        try:
            with mock.patch.object(gui_views, "load_diffraction_image",
                                   return_value=np.ones((64, 64)) * 5.0):
                w.add_files(["data/fake_a.tif"])
                _open_view(w, "2D")
                self.assertTrue(_wait_until(
                    lambda: getattr(_dock(w, "2D", "data/fake_a.tif"),
                                    "last_image", None) is not None))
            dock = _dock(w, "2D", "data/fake_a.tif")
            fig = gui_app._content(dock).figure
            self.assertEqual(len(fig.axes), 2)   # 图像轴 + 颜色条轴
            # 图像 [应用] 重画 → 颜色条仍恰好一条
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertEqual(len(fig.axes), 2)
            self.assertIsNotNone(dock._colorbar_2d)
        finally:
            w.close()

    def test_2d_repeated_apply_does_not_shrink_axes(self):
        """图像 [应用] 反复重画：主坐标轴宽度不缩（与热图同病，
        教训 13——remove+重建颜色条每次让 20% 宽度且不退还）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views, "load_diffraction_image",
                                   return_value=np.ones((64, 64)) * 5.0):
                w.add_files(["data/fake_a.tif"])
                _open_view(w, "2D")
                self.assertTrue(_wait_until(
                    lambda: getattr(_dock(w, "2D", "data/fake_a.tif"),
                                    "last_image", None) is not None))
            dock = _dock(w, "2D", "data/fake_a.tif")
            ax = gui_app._content(dock).axes_2d
            widths = []
            for _ in range(3):
                w.findChild(QPushButton, "apply_image_btn").click()
                widths.append(float(ax.get_position().width))
            self.assertLess(max(widths) - min(widths), 1e-3,
                            f"2D 宽度在反复[应用]后缩小：{widths}")
        finally:
            w.close()


def _fake_bg_compute(path_str, geom, npt):
    """假积分（背景扣除用）：200 点的"陡升背景 + 一个尖峰"曲线。

    3 点的玩具数据（_fake_compute）下窗口参数没有分辨力（1° 和 3° 都
    退化成一个窗口），基线估计算法动不起来，测不出东西。
    """
    tth = np.linspace(0.5, 8.5, 200)
    bg = 100.0 + 900.0 * np.exp(-tth / 2.0)
    return tth, bg + 800.0 * np.exp(-0.5 * ((tth - 3.0) / 0.08) ** 2)


def _fake_waterfall_compute(path_str, geom, npt):
    """假扇区积分（4 个扇区）：同一背景形状 × 各扇区自己的倍率。

    倍率不同是为了让"扇区之间的强度差"可测——共同基线扣完，这些差值
    必须原样保留。
    """
    tth = np.linspace(0.5, 8.5, 200)
    bg = 100.0 + 900.0 * np.exp(-tth / 2.0)
    i2d = bg[:, None] * np.array([1.0, 1.5, 2.0, 2.5])[None, :]
    return tth, i2d, np.array([-175.0, -85.0, 5.0, 95.0])


def _bg_click(ax, xdata, y=None, drag=(0, 0)):
    """构造"按下-松手"一对事件，模拟在 (xdata, y) 处点一下。

    像素坐标由 transData 真算出来（数据坐标 → 屏幕位置），松手点再加
    drag 的位移：位移 >5 px 就是拖拽（平移手势），不算点击。
    """
    xd = ax.lines[0].get_xdata()
    ydata = (float(np.interp(xdata, xd, ax.lines[0].get_ydata()))
             if y is None else y)
    px, py = ax.transData.transform((xdata, ydata))
    press = SimpleNamespace(inaxes=ax, button=1, x=px, y=py,
                            xdata=xdata, ydata=ydata)
    release = SimpleNamespace(inaxes=ax, button=1, x=px + drag[0],
                              y=py + drag[1], xdata=xdata, ydata=ydata)
    return press, release


def _bg_lines(ax):
    """该轴上的数据曲线（排除背景扣除辅助线）与辅助线。"""
    aux = [ln for ln in ax.lines if gui_views._is_aux_line(ln)]
    data = [ln for ln in ax.lines if not gui_views._is_aux_line(ln)]
    return data, aux


class TestBackgroundSubtraction(unittest.TestCase):
    """任务六·背景扣除：三种模式 + 实时预览 + 辅助线隔离。

    用 _fake_bg_compute（200 点陡升背景 + 尖峰）——3 点的玩具曲线下
    基线算法会退化，窗口参数也失去分辨力。
    """

    PATH = "data/fake_bg.tif"
    KEY = "1D|data/fake_bg.tif"

    def _open_1d(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_bg_compute):
            w.add_files([self.PATH])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", self.PATH).lines) > 0))
        return _dock(w, "1D", self.PATH)

    def _set_mode(self, w, mode):
        cb = w.params["背景扣除模式"]
        cb.setCurrentIndex(cb.findData(mode))

    def _anchors_of(self, w, dock):
        """当前作用文件的锚点列表（键 = 面板文件的路径字符串）。"""
        return w.bg_anchors.get(str(gui_views._bg_path_of(dock)), [])

    def _click_anchor(self, w, ax, x, y=None):
        """在一个锚点位置按一下再松手（像素不位移 = 点击）。

        y 省略 = 点在屏幕上那条曲线（原始数据）上；要删已有关键点时得
        点在**标记**上——扣完背景后曲线已经落到 0 附近，而锚点标记画在
        原始尺度上，两者不是一个位置。
        """
        press, release = _bg_click(ax, x, y=y)
        gui_views._anchor_press(w, self.KEY, press)
        gui_views._anchor_release(w, self.KEY, release)

    def test_off_by_default_draws_single_line(self):
        """默认关闭 = 零行为变化：1D 仍只画一条曲线，没有辅助线。

        这是整个功能的回归护栏——不扣背景时画布必须和从前一模一样。
        """
        w = create_window()
        try:
            self._open_1d(w)
            ax = _axes(w, "1D", self.PATH)
            data, aux = _bg_lines(ax)
            self.assertEqual(len(data), 1)
            self.assertEqual(aux, [], "关闭模式不该画任何辅助线")
            self.assertEqual(w.params["背景扣除模式"].currentData(), "off")
        finally:
            w.close()

    def test_auto_mode_draws_curve_baseline_and_raw(self):
        """自动模式：扣除后曲线 + 基线（点线）+ 原始曲线（虚线），
        三条线都带 bg: gid（辅助线才不会被快照/悬停当成曲线）。"""
        w = create_window()
        try:
            self._open_1d(w)
            self._set_mode(w, "auto")
            ax = _axes(w, "1D", self.PATH)
            data, aux = _bg_lines(ax)
            self.assertEqual(len(data), 1, "数据曲线仍只有一条")
            self.assertEqual(len(aux), 2, "基线 + 原始曲线")
            self.assertEqual(sorted(ln.get_gid() for ln in aux),
                             ["bg:baseline", "bg:raw"])
            # 基线落在原始数据的量级内（既不是 0，也不高到追上峰顶）
            base = next(ln for ln in aux if ln.get_gid() == "bg:baseline")
            raw = next(ln for ln in aux if ln.get_gid() == "bg:raw")
            yb = np.asarray(base.get_ydata(), dtype=float)
            y_raw = np.asarray(raw.get_ydata(), dtype=float)
            self.assertGreater(float(yb.max()), 0.2 * float(y_raw.max()))
            self.assertLess(float(yb.max()), 0.9 * float(y_raw.max()))
            # 陡升段不该被削掉：基线在低角端应贴近背景量级（不该塌向 0）
            self.assertGreater(float(yb[0]), 0.5 * float(y_raw[0]))
            self.assertIn("背景扣除：自动基线", w.log_text.toPlainText())
        finally:
            w.close()

    def test_show_raw_off_hides_original_curve(self):
        """取消"显示原始曲线对比"只剩扣除后曲线 + 基线。"""
        w = create_window()
        try:
            self._open_1d(w)
            self._set_mode(w, "auto")
            w.params["背景显示原始"].setChecked(False)
            ax = _axes(w, "1D", self.PATH)
            _, aux = _bg_lines(ax)
            self.assertEqual([ln.get_gid() for ln in aux], ["bg:baseline"])
        finally:
            w.close()

    def test_live_preview_redraws_without_reintegrating(self):
        """实时预览：改窗口宽度立刻重画，但**不重新积分**（调用次数不变）。

        这是本功能唯一的"改控件即重画"通路，靠的是扣除发生在绘制层
        （缓存里的原始曲线不动）。
        """
        w = create_window()
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_bg_compute) as compute:
            try:
                dock = self._open_1d(w)
                self._set_mode(w, "auto")
                ax = _axes(w, "1D", self.PATH)
                before = np.array(_bg_lines(ax)[0][0].get_ydata())
                n_calls = compute.call_count
                w.params["背景窗口 (°)"].setValue(3.0)
                after = np.array(_bg_lines(ax)[0][0].get_ydata())
                self.assertFalse(np.allclose(before, after),
                                 "窗口一变，扣除后曲线应跟着变")
                self.assertEqual(compute.call_count, n_calls,
                                 "实时预览不该触发重新积分")
                self.assertIsNotNone(dock.last_tth)
            finally:
                w.close()

    def test_anchor_click_adds_and_removes(self):
        """锚点点选：点一下加一个（≤5 px 算点击），再点同处删掉。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            self._click_anchor(w, ax, 1.0)
            anchors = self._anchors_of(w, dock)
            self.assertEqual(len(anchors), 1)
            # 吸附到最近的真实数据点（网格步长 0.04°，不是鼠标原始位置）
            self.assertLess(abs(anchors[0][0] - 1.0), 0.05)
            self.assertIn("加锚点", w.log_text.toPlainText())
            # 点回同一个锚点标记上 = 删除（按标记的像素位置点，而不是
            # 屏幕上那条已经扣到 0 附近的曲线）
            ax_, ay_ = anchors[0]
            self._click_anchor(w, ax, ax_, y=ay_)
            self.assertEqual(self._anchors_of(w, dock), [])
            self.assertIn("删除锚点", w.log_text.toPlainText())
        finally:
            w.close()

    def test_anchor_drag_is_not_a_click(self):
        """按下后拖走（>5 px）→ 平移手势，不加锚点。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            press, release = _bg_click(ax, 1.0, drag=(150, 150))
            gui_views._anchor_press(w, self.KEY, press)
            gui_views._anchor_release(w, self.KEY, release)
            self.assertEqual(self._anchors_of(w, dock), [])
        finally:
            w.close()

    def test_anchor_needs_pick_toggle(self):
        """模式是锚点但没开 [拾取锚点] → 点图不出锚点（不误点）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(False)
            ax = _axes(w, "1D", self.PATH)
            self._click_anchor(w, ax, 1.0)
            self.assertEqual(self._anchors_of(w, dock), [])
        finally:
            w.close()

    def test_anchor_baseline_follows_points(self):
        """锚点基线过点：在两点上各打一锚，基线在这两点处**恰好等于**
        当时点到的曲线值（这是手动锚点的核心承诺：你点哪它走哪）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            for x in (1.0, 8.0):
                self._click_anchor(w, ax, x)
            anchors = self._anchors_of(w, dock)
            self.assertEqual(len(anchors), 2)
            _, aux = _bg_lines(ax)
            base = next(ln for ln in aux if ln.get_gid() == "bg:baseline")
            self.assertEqual(len(base.get_xdata()), 200)
            for ax_, ay_ in anchors:
                self.assertAlmostEqual(
                    float(np.interp(ax_, base.get_xdata(), base.get_ydata())),
                    ay_, places=6, msg=f"基线应过锚点 2θ={ax_:.3f}°")
        finally:
            w.close()

    def test_anchor_mode_without_anchors_draws_nothing_extra(self):
        """锚点模式但一个锚点都没点 → 不扣也不画辅助线（不是画一条 0 基线）。"""
        w = create_window()
        try:
            self._open_1d(w)
            self._set_mode(w, "anchor")
            ax = _axes(w, "1D", self.PATH)
            data, aux = _bg_lines(ax)
            self.assertEqual(aux, [])
            np.testing.assert_allclose(
                np.asarray(data[0].get_ydata(), dtype=float),
                _fake_bg_compute("", {}, 0)[1])
        finally:
            w.close()

    def test_clear_anchors_button(self):
        """[清空锚点] 清掉当前 1D 面板对应文件的锚点。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            self._click_anchor(w, ax, 1.0)
            self.assertEqual(len(self._anchors_of(w, dock)), 1)
            w.bg_clear_btn.click()
            self.assertEqual(self._anchors_of(w, dock), [])
            self.assertIn("已清空", w.log_text.toPlainText())
        finally:
            w.close()

    def test_anchors_are_per_file(self):
        """锚点按文件各记各的：另一个文件不会套用这份锚点。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            self._click_anchor(w, ax, 1.0)
            self.assertEqual(len(self._anchors_of(w, dock)), 1)
            other = gui_state._bg_params(w, dock, "data/other.tif")
            self.assertEqual(other["anchors"], [])
        finally:
            w.close()

    def test_blank_without_image_warns_and_does_not_subtract(self):
        """选了空扫模式但还没挑空扫图 → 提示 + 不扣（不画辅助线）。"""
        w = create_window()
        try:
            self._open_1d(w)
            self._set_mode(w, "blank")
            ax = _axes(w, "1D", self.PATH)
            data, aux = _bg_lines(ax)
            self.assertEqual(aux, [])
            self.assertIn("还没选空扫图", w.log_text.toPlainText())
        finally:
            w.close()

    def test_blank_subtracts_after_pick(self):
        """挑好空扫图（缓存里放好）→ 切到空扫模式即按系数扣除。"""
        w = create_window()
        try:
            self._open_1d(w)
            tth = np.linspace(0.5, 8.5, 200)
            blank = 0.5 * (100.0 + 900.0 * np.exp(-tth / 2.0))
            w.bg_blank = {"path": "data/blank.tif", "tth": tth,
                          "intensity": blank, "geom": ""}
            self._set_mode(w, "blank")
            ax = _axes(w, "1D", self.PATH)
            data, aux = _bg_lines(ax)
            expect = _fake_bg_compute("", {}, 0)[1] - blank
            np.testing.assert_allclose(
                np.asarray(data[0].get_ydata(), dtype=float), expect)
            self.assertEqual(len(aux), 2, "基线 + 原始")
            # 归一化系数作为倍率：×2 之后扣掉的是两倍
            w.params["空扫归一化"].setValue(2.0)
            data, _ = _bg_lines(ax)
            np.testing.assert_allclose(
                np.asarray(data[0].get_ydata(), dtype=float),
                _fake_bg_compute("", {}, 0)[1] - 2.0 * blank)
        finally:
            w.close()

    def test_negative_values_kept_by_default_and_clipped_on_demand(self):
        """扣完的负值默认保留（噪声地板露出），勾上"负值截断为 0"才切零。"""
        w = create_window()
        try:
            self._open_1d(w)
            tth = np.linspace(0.5, 8.5, 200)
            # 空扫给得比样品还高 → 扣完处处为负
            w.bg_blank = {"path": "b.tif", "tth": tth,
                          "intensity": np.full(200, 5000.0), "geom": ""}
            self._set_mode(w, "blank")
            ax = _axes(w, "1D", self.PATH)
            data, _ = _bg_lines(ax)
            self.assertLess(float(np.asarray(data[0].get_ydata()).min()), -1.0)
            w.params["负值截断为 0"].setChecked(True)
            data, _ = _bg_lines(ax)
            self.assertGreaterEqual(
                float(np.asarray(data[0].get_ydata()).min()), 0.0)
        finally:
            w.close()

    def test_aux_lines_excluded_from_snapshot_and_hover(self):
        """辅助线不进快照、悬停不选它们（否则样式回填错位、悬停乱跳）。"""
        w = create_window()
        try:
            self._open_1d(w)
            self._set_mode(w, "auto")
            ax = _axes(w, "1D", self.PATH)
            _, _, _, _, old_lines = gui_views._snapshot_canvas(ax)
            self.assertEqual(len(old_lines), 1, "快照只该收数据曲线")
            # 悬停选线只认数据曲线；取点标记自己也是辅助线（不参与选线）
            gui_views._hover_motion(w, self.KEY, _hover_event(ax, 1.0))
            marker = w.plot_docks[self.KEY].hover_marker
            self.assertTrue(gui_views._is_aux_line(marker))
            self.assertTrue(marker.get_visible())
        finally:
            w.close()

    def test_style_restore_stays_aligned_with_aux_lines(self):
        """重画时旧样式按序套回数据曲线（辅助线不占序号，不错位）。"""
        w = create_window()
        try:
            self._open_1d(w)
            self._set_mode(w, "auto")
            ax = _axes(w, "1D", self.PATH)
            data_line = _bg_lines(ax)[0][0]
            data_line.set_linestyle("--")   # 假装用户在 Customize 里改过
            data_line.set_linewidth(2.5)
            gui_views._draw_1d(w, w.plot_docks[self.KEY],
                               w.plot_docks[self.KEY].last_tth,
                               w.plot_docks[self.KEY].last_intensity)
            data_line = _bg_lines(ax)[0][0]
            self.assertEqual(data_line.get_linestyle(), "--")
            self.assertEqual(data_line.get_linewidth(), 2.5)
        finally:
            w.close()

    def test_compare_curves_are_background_subtracted(self):
        """对比面板的每条曲线也扣背景（口径一致：同一份数据在不同
        面板里长得一样）。"""
        w = create_window()
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_bg_compute):
            try:
                w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
                gui_app._plot_compare(w)
                keys = [k for k in w.plot_docks if k.startswith("对比|")]
                self.assertTrue(_wait_until(lambda: len(keys) == 1))
                key = keys[0]
                dock = w.plot_docks[key]
                self.assertTrue(_wait_until(
                    lambda: len(getattr(dock, "compare_data", {})) == 2))
                ax = gui_app._content(dock).axes_1d
                before = np.asarray(_bg_lines(ax)[0][0].get_ydata(),
                                    dtype=float).copy()
                self._set_mode(w, "auto")
                # ax.clear() 会换掉线对象——重画后必须重新取线，否则拿到
                # 的是已从轴上摘下来的旧对象（数据永远是旧的）
                after = np.asarray(_bg_lines(ax)[0][0].get_ydata(),
                                   dtype=float)
                self.assertFalse(np.allclose(before, after),
                                 "对比曲线应跟着扣背景")
                self.assertLess(float(after.max()), float(before.max()))
            finally:
                w.close()

    def test_waterfall_uses_common_baseline(self):
        """瀑布用扇区均值的**共同**基线（不逐扇区各扣各的，否则抹平
        扇区之间的真实强度差——那是瀑布图存在的意义）。

        判据：扣完之后相邻扇区首点的**间距**必须和原始数据里的间距一致
        （各扇区减同一条基线 → 扇区间距不变）。若逐扇区各扣各的，间距会
        被各自的基线吃掉、变得不一致。
        """
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_waterfall",
                                   side_effect=_fake_waterfall_compute):
                w.add_files(["data/fake_w.tif"])
                _open_view(w, "瀑布")
                dock = _dock(w, "瀑布", "data/fake_w.tif")
                self.assertTrue(_wait_until(
                    lambda: getattr(dock, "last_waterfall", None) is not None))
                self._set_mode(w, "auto")
                _, i2d, _ = dock.last_waterfall
                # 直接验设计决定：_bg_curve 只被调用一次，且喂进去的是
                # **扇区均值**（共同基线）。逐扇区各扣各的会调用 4 次、
                # 每次喂一条扇区曲线——扇区之间的真实强度差就被抹平了
                with mock.patch.object(gui_views, "_bg_curve",
                                       wraps=gui_views._bg_curve) as spy:
                    gui_views._draw_waterfall(w, dock, *dock.last_waterfall)
                self.assertEqual(spy.call_count, 1, "应只估一条共同基线")
                fed = spy.call_args[0][4]
                np.testing.assert_allclose(
                    np.asarray(fed, dtype=float),
                    np.nanmean(np.asarray(i2d, dtype=float), axis=1),
                    err_msg="喂给基线估计的应是扇区均值")
                wax = gui_app._content(dock).axes_waterfall
                self.assertEqual(len(_bg_lines(wax)[0]), 4, "四条扇区曲线")
        finally:
            w.close()

    def test_heat_matrix_subtracted_per_file(self):
        """热图逐行按各文件自己的参数扣背景。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            tth = np.linspace(0.5, 8.5, 200)
            dock.heat_files = [(gui_views._bg_path_of(dock), "fake_bg.tif")]
            dock.heat_results = [("fake_bg", tth,
                                  _fake_bg_compute("", {}, 0)[1])]
            blank = 0.5 * (100.0 + 900.0 * np.exp(-tth / 2.0))
            w.bg_blank = {"path": "b.tif", "tth": tth,
                          "intensity": blank, "geom": ""}
            self._set_mode(w, "blank")
            data = gui_views._heat_data(w, dock)
            self.assertIsNotNone(data)
            np.testing.assert_allclose(
                data[1][0], _fake_bg_compute("", {}, 0)[1] - blank)
        finally:
            w.close()

    def test_anchor_takes_raw_value_even_with_raw_overlay_off(self):
        """回归：关掉"显示原始曲线对比"后点锚点，锚点 y 仍须取自**原始**
        曲线。

        那时轴上没有 bg:raw 辅助线，若照屏幕上那条扣过的曲线取值，在已有
        关键点处会记下负值/0（探针实测中间锚点记成 −476.9）→ 该处背景等于
        没扣，而画面上看不出错；锚点还会持久化，重新勾上原始叠加也不自愈。
        """
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            w.params["背景显示原始"].setChecked(False)   # 关键：关掉原始叠加
            ax = _axes(w, "1D", self.PATH)
            for x in (1.0, 5.0, 8.0):
                self._click_anchor(w, ax, x)
            anchors = self._anchors_of(w, dock)
            self.assertEqual(len(anchors), 3)
            raw = np.asarray(dock.last_intensity, dtype=float)
            tth = dock.last_tth
            for x, y in anchors:
                self.assertAlmostEqual(y, float(np.interp(x, tth, raw)),
                                       places=6,
                                       msg=f"锚点 {x:.3f}° 的 y 应是原始曲线值")
            self.assertTrue(all(y > 0 for _, y in anchors),
                            "锚点不该被记成 0/负值")
        finally:
            w.close()

    def test_switching_focus_does_not_mix_panel_snapshots(self):
        """回归：反复切焦点后，两块面板的显示参数快照仍各是各的。

        背景扣除控件连着 _refresh_bg（实时预览），而 _set_focus 会回放面板
        快照进控件 → 不挂回放旗标的话，回放途中的 setValue 会触发
        _refresh_bg 把"回放了一半的控件值"写进本面板快照，把上一块面板的
        显示参数（实测是 热图色图 等注册在背景组之后的几项）串过来。
        """
        w = create_window()
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_bg_compute):
            try:
                w.add_files(["data/fg_a.tif", "data/fg_b.tif"])
                _open_view(w, "1D")
                keys = [k for k in w.plot_docks if k.startswith("1D|")]
                self.assertTrue(_wait_until(
                    lambda: all(getattr(w.plot_docks[k], "last_tth", None)
                                is not None for k in keys), 20000))
                ka = next(k for k in keys if "fg_a" in k)
                kb = next(k for k in keys if "fg_b" in k)
                da, db = w.plot_docks[ka], w.plot_docks[kb]
                gui_state._set_focus(w, ka, da.panel_display)
                hc = w.params["热图色图"]
                hc.setCurrentIndex(hc.findData("viridis"))
                da.params_snapshot["热图色图"] = "viridis"   # A 自己的设置
                self._set_mode(w, "auto")                    # 背景组也动一下
                db.params_snapshot["热图色图"] = "magma"     # B 自己的设置
                gui_state._set_focus(w, kb, db.panel_display)
                gui_state._set_focus(w, ka, da.panel_display)
                self.assertEqual(da.params_snapshot.get("热图色图"), "viridis")
                self.assertEqual(db.params_snapshot.get("热图色图"), "magma")
            finally:
                w.close()

    def test_dragging_on_another_panel_keeps_pick_armed(self):
        """回归：在别的 1D 面板上做一次**拖拽平移**，不该把"拾取锚点"弄丢。

        按下时若就切焦点，会回放那块面板的快照（模式多半是"关闭"）→
        _sync_bg_rows 顺手把拾取开关取消，用户只是在别的图上拖了一下就
        失去了拾取状态。切焦点挪到确认是点击之后，拖拽就不影响。
        """
        w = create_window()
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_bg_compute):
            try:
                w.add_files(["data/fg_a.tif", "data/fg_b.tif"])
                _open_view(w, "1D")
                keys = [k for k in w.plot_docks if k.startswith("1D|")]
                self.assertTrue(_wait_until(
                    lambda: all(getattr(w.plot_docks[k], "last_tth", None)
                                is not None for k in keys), 20000))
                ka = next(k for k in keys if "fg_a" in k)
                kb = next(k for k in keys if "fg_b" in k)
                gui_state._set_focus(w, ka, w.plot_docks[ka].panel_display)
                self._set_mode(w, "anchor")
                w.bg_pick_btn.setChecked(True)
                # 在 B 面板上按下并拖走（>5 px = 平移手势，不是点击）
                axb = _axes(w, "1D", "data/fg_b.tif")
                press, release = _bg_click(axb, 1.0, drag=(150, 150))
                gui_views._anchor_press(w, kb, press)
                gui_views._anchor_release(w, kb, release)
                self.assertTrue(w.bg_pick_btn.isChecked(),
                                "拖拽平移不该取消拾取状态")
            finally:
                w.close()

    def test_blank_partial_coverage_warns(self):
        """空扫只覆盖部分 2θ 区间时提示（未覆盖段不扣，静默会让人以为坏了）。"""
        w = create_window()
        try:
            self._open_1d(w)
            tth = np.linspace(0.5, 8.5, 200)
            part = np.linspace(2.0, 6.0, 80)
            w.bg_blank = {"path": "b.tif", "tth": part,
                          "intensity": np.full(80, 5.0), "geom_sig": None}
            self._set_mode(w, "blank")
            self.assertIn("空扫只覆盖", w.log_text.toPlainText())
            # 覆盖齐全时不提示
            w2 = create_window()
            try:
                with mock.patch.object(gui_views, "_compute_integration",
                                       side_effect=_fake_bg_compute):
                    w2.add_files([self.PATH])
                    _open_view(w2, "1D")
                    self.assertTrue(_wait_until(
                        lambda: getattr(w2.plot_docks.get(self.KEY), "last_tth",
                                        None) is not None))
                d2 = w2.plot_docks[self.KEY]
                gui_state._set_focus(w2, self.KEY, d2.panel_display)
                tth2 = d2.last_tth
                w2.bg_blank = {"path": "b.tif", "tth": tth2,
                               "intensity": np.full(len(tth2), 5.0),
                               "geom_sig": None}
                self._set_mode(w2, "blank")
                self.assertNotIn("空扫只覆盖", w2.log_text.toPlainText())
            finally:
                w2.close()
        finally:
            w.close()

    def test_blank_geometry_mismatch_warns_once(self):
        """空扫图与当前几何不一致时提示（线性前提被破坏），且只提示一次
        ——本函数每次重画都会跑，每次记日志会刷屏。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            tth = np.linspace(0.5, 8.5, 200)
            blank = 0.5 * (100.0 + 900.0 * np.exp(-tth / 2.0))
            geom = gui_app._collect_geometry(w)
            w.bg_blank = {"path": "b.tif", "tth": tth, "intensity": blank,
                          "geom_sig": gui_app._bg_geom_sig(geom)}
            self._set_mode(w, "blank")
            self.assertNotIn("空扫图与当前几何不一致", w.log_text.toPlainText())
            # 改一个几何量 → 再扣就该提示
            w.params["初始距离 (mm)"].setValue(
                w.params["初始距离 (mm)"].value() + 50.0)
            self._set_mode(w, "auto")
            self._set_mode(w, "blank")
            text = w.log_text.toPlainText()
            self.assertIn("空扫图与当前几何不一致", text)
            n_once = text.count("空扫图与当前几何不一致")
            self._set_mode(w, "auto")
            self._set_mode(w, "blank")
            self.assertEqual(w.log_text.toPlainText().count(
                "空扫图与当前几何不一致"), n_once, "同一状态下只该提示一次")
        finally:
            w.close()

    def test_anchors_survive_panel_pop_out(self):
        """锚点/空扫存在 window 上（不是 dock），面板弹出/收回不丢——
        panels.py 的 _PANEL_ATTRS 白名单只搬少量 dock 属性，新加的会丢。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            self._click_anchor(w, ax, 1.0)
            self.assertEqual(len(self._anchors_of(w, dock)), 1)
            gui_panels._toggle_pop_out(w, self.KEY)
            QApplication.processEvents()
            new_dock = _dock(w, "1D", self.PATH)
            self.assertEqual(len(self._anchors_of(w, new_dock)), 1,
                             "弹出后锚点还在")
            gui_panels._toggle_pop_out(w, self.KEY)
            QApplication.processEvents()
            self.assertEqual(len(self._anchors_of(
                w, _dock(w, "1D", self.PATH))), 1, "收回后锚点还在")
        finally:
            w.close()

    def test_mode_switch_keeps_dock_narrow(self):
        """每种模式只放出一行专用控件，且坞最小宽始终 ≤ 320。"""
        w = create_window()
        try:
            for mode in ("off", "blank", "auto", "anchor"):
                self._set_mode(w, mode)
                vis = [n for n, r in w.bg_rows.items() if not r.isHidden()]
                self.assertEqual(len(vis), 0 if mode == "off" else 1,
                                 f"{mode} 只该放出一行，实际 {vis}")
                self.assertLess(w.param_dock.minimumWidth(), 320)
        finally:
            w.close()

    def test_reset_defaults_turns_background_off(self):
        """[恢复默认] 把背景扣除复位（锚点/空扫属于用户挑的数据，不代删）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "auto")
            w.params["背景窗口 (°)"].setValue(4.0)
            w.findChild(QPushButton, "reset_image_btn").click()
            self.assertEqual(w.params["背景扣除模式"].currentData(), "off")
            self.assertEqual(w.params["背景窗口 (°)"].value(), 1.0)
            ax = _axes(w, "1D", self.PATH)
            self.assertEqual(_bg_lines(ax)[1], [])
            self.assertIsNotNone(getattr(dock, "last_tth", None))
        finally:
            w.close()

    def test_export_bg_option_subtracts(self):
        """[导出数据] 勾"导出扣除背景后的曲线"→ 取到的是扣完的值。"""
        w = create_window()
        try:
            self._open_1d(w)
            self._set_mode(w, "auto")
            plain = gui_app._checked_1d_results(w, want_bg=False)
            with_bg = gui_app._checked_1d_results(w, want_bg=True)
            self.assertEqual(len(plain), 1)
            raw = np.asarray(plain[0][2], dtype=float)
            sub = np.asarray(with_bg[0][2], dtype=float)
            np.testing.assert_allclose(raw, _fake_bg_compute("", {}, 0)[1])
            self.assertFalse(np.allclose(sub, raw),
                             "勾了扣背景，导出的不该还是原始值")
            self.assertLess(float(sub.min()), 0.0)   # 负值保留
        finally:
            w.close()

    def test_export_dialog_has_bg_checkbox(self):
        """导出弹窗里有"导出扣除背景后的曲线"复选项（默认不勾）。"""
        w = create_window()
        try:
            captured = {}

            def fake_exec(self):
                captured["bg"] = self.findChild(
                    QCheckBox, "export_bg_check").isChecked()
                captured["check"] = self.findChild(QCheckBox, "export_bg_check")
                return QDialog.Rejected

            with mock.patch.object(QDialog, "exec", new=fake_exec):
                self.assertIsNone(gui_app._build_export_dialog(w, 1))
            self.assertIsNotNone(captured.get("check"))
            self.assertFalse(captured["bg"], "默认不勾 = 导出原始曲线")
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
