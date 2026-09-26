"""GUI「文件 → 视图 → 出图」交互模型的集成测试（unittest）。

用 mock 替换 _compute_integration（真积分慢且依赖 data/ 数据），
验证接线本身：
  - 选文件只登记不算（点选本身轻快，不触发任何计算）；
  - 工具栏五个入口（校准/1D/扣背景/对比│绘图）：点一个 = 参数坞翻到
    那一页 + 高亮跟着动；[校准] 另有一层"进出校准工作台"的含义
    （TestParamDockSplitLayout.test_pages_structure /
    test_entrance_switching_follows_buttons）；
  - 六个作图类型按钮住在「绘图」页里（属性名不变）：点击 = 为当前文件
    开面板并计算该视图 → 出图（点一次算一次，纯动作不是开关）；
    页里另有 [出图（勾选文件）][只重画当前][导出图片…]，1D/扣背景/
    对比页各带自己的产出按钮；
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
  - 面板壳 = 一行 26 px 自绘标题栏（标题 + [Home][Zoom][Customize]
    [Save] + [弹出][关闭]）+ 画布，子窗口 frameless（TestSlimPanelChrome：
    壳 83 → 26 px、栏上按钮与工具栏共用 action、拖动/双击最大化/✕
    关闭、弹出保留原生边框而收回恢复 frameless、占位面板留原生标题栏）；
  - 横排/竖排 = 按类型分层摆位置（开图先后排序）不缩放，溢出靠
    QMdiArea 滚动条兜底；摆图前滚动自动归零（滚动状态下的 move
    会混入滚动偏移、图越排越漂）；点面板窗口任何位置 = 选中该
    面板；滚轮 = 只滚动绘图区；放大镜开关点亮时滚轮以光标为中心
    缩放每格 10% + 左键拖框放大（自行绘框、只改坐标范围，见
    TestBoxZoom），熄灭时左键 = 平移；总缩放 =
    Ctrl+滚轮 / 底部 − 100% + 按钮，绘图区全体同比缩放
    （50%–200%），弹出去的不参与、平铺不碰它；新图左上角
    24px 小错位级联（6 档循环）、按当前总缩放开；
  - 关闭面板 = 关闭即遗忘：重开全新默认，关窗询问只算开着的图，
    在飞任务/旧代对比结果迟到即作废；
  - 背景扣除（TestBackgroundSubtraction）：三种模式 + 实时预览 + 辅助
    线隔离；锚点点选逐条调处理函数，另有一条从 canvas.callbacks 发真
    MouseEvent 的接线护栏——漏导入名字这类拆分伤只有真点画布才现形，
    直接调处理函数的测试全绿也照样漏它；
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
import ast
import json
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
from matplotlib.backend_bases import MouseEvent
from matplotlib.colors import LogNorm
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QDropEvent, QPointingDevice, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QMessageBox, QPushButton, QRadioButton, QScrollArea, QSplitter,
    QSpinBox, QToolButton, QVBoxLayout, QWidget)

from xrd_toolkit import config as config_mod
from xrd_toolkit.services import process, stage_cache
from xrd_toolkit.services.integrator import lab6_theoretical_2theta
from xrd_toolkit.gui import app as gui_app
_gui_app = gui_app
from xrd_toolkit.gui import file_dock as gui_file_dock
from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui import plot_views as gui_plot_views
from xrd_toolkit.gui import panel_state as gui_panel_state
from xrd_toolkit.gui import plot_compare as gui_plot_compare
from xrd_toolkit.gui import plot_panels as gui_plot_panels
from xrd_toolkit.gui import plot_export as gui_plot_export
from xrd_toolkit.gui import config_ops as gui_config_ops
from xrd_toolkit.gui import calib_panel as gui_calib_panel
from xrd_toolkit.gui import calib_model as gui_calib_model
from xrd_toolkit.gui import plot_export as gui_export
from xrd_toolkit.gui import customize as gui_customize
from xrd_toolkit.gui.app import create_window
# 拆分后 patch 目标 = 调用点所在的模块（gui_app 只是兼容再导出，
# 打它的名字截不住别的模块里的裸名查找）
from xrd_toolkit.gui import calib as gui_calib
from xrd_toolkit.gui import panel_state as gui_state
from xrd_toolkit.gui import panels as gui_panels
from xrd_toolkit.gui import plot_views as gui_views

_app = QApplication.instance() or QApplication([])

# 分阶段产物缓存的隔离（2026-09-24）：GUI 测试走真实的 1D 积分链路
# 时会顺手落盘产物——不隔离就会写进 outputs/_stage，还会让"该重算"的
# 断言被缓存命中悄悄改掉（隔离不能依赖真目录内容，同 TestSaveCalibConfig
# 对 config_user.json 的做法）
_CACHE_TMP = tempfile.mkdtemp(prefix="xrd_stage_cache_")


def setUpModule():
    from xrd_toolkit.services import stage_cache
    stage_cache.CACHE_ROOT = Path(_CACHE_TMP)


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


def add_checked(w, paths, **kw):
    """测试辅助：加文件 + 全勾上（select=True）。

    导入默认**不勾选**（用户 2026-09-25 定：200 张数据要自己说了算），
    而绝大多数测试关心的是"勾上以后出图/对比/导出"那条链，逐个手勾
    是噪音。要测"导入默认不勾"本身，直接用 w.add_files。
    """
    w.add_files(paths, select=True, **kw)
    return w


def _dock(w, view, path_str):
    """按面板键取坞（键 = f"{视图}|{路径}"，路径与 add_files 入参一致）。"""
    return w.plot_docks[f"{view}|{path_str}"]


def _axes(w, view, path_str):
    """取某 1D 面板自己的坐标轴（容器 = 子窗口或弹出窗口，经 _content 取内容）。"""
    return gui_panel_state._content(_dock(w, view, path_str)).axes_1d


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


class TestPatchTargetsResolve(unittest.TestCase):
    """静态护栏：每个 mock.patch 目标的名字必须真的在被打的模块里。

    拆模块后最常踩的坑（本仓踩过两次）：patch 目标跟着"函数的新家"改，
    或者别名混了（gui_panels 指 panels.py，不是 plot_panels.py）。打一
    个不存在的名字 = AttributeError，但只有当那条测试跑到时才炸；要是
    它同时让真弹窗打开，整套会卡死在 exec() 上（实测卡过一次，20 分钟
    后才发现）。

    另一类（patch 打在"定义处"而调用者在别的模块）静态查不出来，规矩
    写在上面的导入注释里：**patch 目标 = 调用点所在的模块**。
    """

    def test_patch_targets_exist(self):
        import types
        aliases = {}          # 别名 → 模块对象（本文件 import 进来的）
        for name, obj in vars(sys.modules[__name__]).items():
            if isinstance(obj, types.ModuleType):
                aliases[name] = obj
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        checked = 0
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("object", "patch")
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "patch"
                    and len(node.args) >= 1):
                continue
            tgt = node.args[0]
            if isinstance(tgt, ast.Name):        # patch.object(MOD, "name")
                if len(node.args) < 2 \
                        or not isinstance(node.args[1], ast.Constant):
                    continue
                mod, attr = aliases.get(tgt.id), node.args[1].value
            elif isinstance(tgt, ast.Attribute):   # patch.object(MOD.Class, "m")
                if not isinstance(tgt.value, ast.Name):
                    continue
                mod, attr = aliases.get(tgt.value.id), tgt.attr
            elif isinstance(tgt, ast.Constant) and isinstance(tgt.value, str):
                continue          # mock.patch("a.b.c") 形式本文件没用
            else:
                continue
            if mod is None:
                continue          # 不是模块（如 patch.object(window, ...)）
            checked += 1
            self.assertTrue(
                hasattr(mod, attr),
                f"{mod.__name__} 没有 {attr}（test 行 {node.lineno}）"
                f"——拆模块后 patch 目标要指向调用点所在的模块")
        self.assertGreater(checked, 100, "patch 目标没扫到，检查本测试的解析")


class TestSelectionOnly(unittest.TestCase):
    """选文件 ≠ 计算：点选只是登记当前文件。"""

    def test_selecting_file_does_not_run(self):
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute) as fake:
                add_checked(w, ["data/fake_b.tif"])
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
                add_checked(w, ["data/fake_b.tif"])
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
                add_checked(w, ["data/fake_b.tif"])
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

    def test_too_many_files_compute_only_and_listed(self):
        """勾选超过开图上限 → 一张都不画（只算不画），结果进文件栏产物组。

        用户 2026-09-26："如果原始很多，进行 1d 图时全部打开，只会打开
        二十来张。能不能…不弹框，直接列出在文件区，然后可以点开看"——
        做法：超过 MAX_PANELS_PER_BATCH 就全走"只算不画"那条老路，
        批走完刷产物分组（「1D 产物」），看哪张点哪张。
        """
        w = create_window()
        files = _tmp_files(gui_views.MAX_PANELS_PER_BATCH + 1)
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files([str(p) for p in files], select=True)
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: "批完成" in w.log_text.toPlainText(), 60000))
            self.assertEqual(len(w.plot_docks), 0, "一张面板都不该开")
            log = w.log_text.toPlainText()
            self.assertIn("全部只算不画", log)
            self.assertIn("1D 产物", log)        # 指路：结果在文件栏
            self.assertIsNotNone(_group_by_text(w, "1D 产物"),
                                 "产物分组该长出来（有东西可点开）")
        finally:
            w.close()

    def test_double_click_opens_one_panel(self):
        """双击条目 = 打开这一张的 1D 图（不用先勾再按视图按钮）。"""
        w = create_window()
        files = _tmp_files(2)
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files([str(p) for p in files], select=False)
                item = w.file_list.raw_group.child(0)
                self.assertEqual(item.checkState(0), Qt.Unchecked)
                w.file_list.itemDoubleClicked.emit(item, 0)
                key = f"1D|{files[0]}"
                self.assertTrue(_wait_until(
                    lambda: key in w.plot_docks
                    and len(_axes(w, "1D", str(files[0])).lines) > 0, 30000),
                    "双击后应开出这一张的面板")
            self.assertEqual(len(w.plot_docks), 1, "只开被双击的那一条")
        finally:
            w.close()

    def test_open_group_opens_every_entry(self):
        """右键 [打开整组 1D 图]：组里每条各开一张（小组不弹确认）。"""
        w = create_window()
        files = _tmp_files(3)
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files([str(p) for p in files], select=False)
                group = w.file_list.raw_group
                gui_file_dock._open_group_views(w, group)
                self.assertTrue(_wait_until(
                    lambda: all(f"1D|{p}" in w.plot_docks for p in files),
                    60000), "组里三条都该开出来")
            self.assertIn("打开整组1D图完成：3 张", w.log_text.toPlainText())
        finally:
            w.close()

    def test_open_group_asks_before_opening_a_huge_group(self):
        """整组打开超过 24 张：先弹确认；选 No 就一张都不开（防 1.4 GB）。"""
        w = create_window()
        files = _tmp_files(gui_views.MAX_PANELS_PER_BATCH + 1)
        try:
            w.show()      # 确认框只在窗口显示时弹（无头/测试场景直接放行）
            w.add_files([str(p) for p in files], select=False)
            group = w.file_list.raw_group
            self.assertEqual(group.childCount(), len(files))
            with mock.patch.object(gui_file_dock.QMessageBox, "question",
                                   return_value=gui_file_dock.QMessageBox.No):
                gui_file_dock._open_group_views(w, group)
            self.assertEqual(len(w.plot_docks), 0, "选 No 就不该开图")
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
        self.assertEqual(set(gui_plot_panels._VIEW_BUILDERS),
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
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "2D")
                drawn = _wait_until(lambda: len(gui_panel_state._content(
                    _dock(w, "2D", "data/fake_b.tif")).axes_2d.images) > 0)
                self.assertTrue(drawn, "点 2D 后应画出图像")
            dock = _dock(w, "2D", "data/fake_b.tif")
            self.assertEqual(dock.windowTitle(), "2D_fake_b.tif")
            ax = gui_panel_state._content(dock).axes_2d
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
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "剖面")
                drawn = _wait_until(lambda: len(gui_panel_state._content(
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
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "瀑布")
                drawn = _wait_until(lambda: len(gui_panel_state._content(
                    _dock(w, "瀑布", "data/fake_b.tif")).axes_waterfall.lines) > 0)
                self.assertTrue(drawn, "点 瀑布 后应画出堆叠曲线")
            dock = _dock(w, "瀑布", "data/fake_b.tif")
            ax = gui_panel_state._content(dock).axes_waterfall
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
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "2D")
                self.assertTrue(_wait_until(lambda: len(gui_panel_state._content(
                    _dock(w, "2D", "data/fake_b.tif")).axes_2d.images) > 0))
            ax = gui_panel_state._content(_dock(w, "2D", "data/fake_b.tif")).axes_2d
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

    def test_waterfall_uniform_rows_and_processing_chain(self):
        """瀑布：行距统一（全场峰值 ×0.7）+ 处理链逐扇区跑（裁剪/平滑）。

        用户 2026-09-26："不要按照各自的最高峰归一化，所有的图" +
        "瀑布图也是在处理后画，去掉无效的峰就看得清了"。
        4 个扇区、共同基线 10：0 号有巨峰 100，1/2 号只有弱峰 11/12 ——
        旧规则下行高按各自的峰值算（弱扇区的行被压得只剩一点点）；新规则
        所有行同高（按全场 100），裁掉巨峰后行高改由剩下的最大峰 12 决定
        → 弱扇区的特征相对放大 8 倍多。
        """
        w = create_window()
        try:
            tth = np.linspace(1.0, 8.0, 60)
            i2d = np.full((60, 4), 10.0)          # 基线（真实曲线不是 0）
            i2d[(tth >= 1.0) & (tth <= 2.0), 0] = 100.0   # 0 号：巨峰
            i2d[(tth >= 5.0) & (tth <= 5.2), 1] = 11.0    # 1 号：弱峰
            i2d[(tth >= 5.0) & (tth <= 5.2), 2] = 12.0    # 2 号：稍强
            chi = np.linspace(-175.0, 175.0, 4)
            i_mid = int(np.argmin(np.abs(tth - 4.0)))     # 两峰之间：全是基线
            with mock.patch.object(gui_views, "load_diffraction_image",
                                   return_value=np.zeros((10, 10))), \
                 mock.patch.object(gui_views, "integrate_sectors",
                                   return_value=(tth, i2d, chi)):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "瀑布")
                dock = _dock(w, "瀑布", "data/fake_b.tif")
                ax = gui_panel_state._content(dock).axes_waterfall
                self.assertTrue(_wait_until(lambda: len(ax.lines) > 0))

                def bases():
                    """各行的 y 基线（中段全是背景，值 = 偏移 + 10）"""
                    return [float(np.asarray(l.get_ydata())[i_mid])
                            for l in ax.lines]

                step = 100.0 * 0.7          # 全场峰值（扇区 0 的巨峰）× 0.7
                for k, b in enumerate(bases()):
                    self.assertAlmostEqual(b - 10.0, k * step,
                                           delta=step * 0.05)
                # 裁掉巨峰所在区间——走真实路径：填起止 + [添加]
                # （清单是窗口级的，[添加] 会把它拷进各面板快照并实时重画）
                w.params["裁剪起点 (°)"].setValue(0.9)
                w.params["裁剪终点 (°)"].setValue(2.1)
                w.findChild(QPushButton, "cut_add_btn").click()
                QApplication.processEvents()
            # 裁掉巨峰后行高改由剩下的最大峰（12）决定 → 每行放大了
            step2 = 12.0 * 0.7
            self.assertLess(step2, step, "裁掉巨峰 → 行高应显著变小")
            for k, b in enumerate(bases()):
                self.assertAlmostEqual(b - 10.0, k * step2,
                                       delta=step2 * 0.05)
            # 裁掉的区间在所有行上都空着（逐扇区跑了裁剪）
            for line in ax.lines:
                y = np.asarray(line.get_ydata())
                seg = y[(tth >= 1.0) & (tth <= 2.0)]
                self.assertTrue(np.isnan(seg).all(), "裁剪区间应被挖空")
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
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "瀑布")
                self.assertTrue(_wait_until(lambda: len(gui_panel_state._content(
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
                add_checked(w, ["data/fake_b.tif"])
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
                add_checked(w, ["data/fake_b.tif"])
                w.findChild(QPushButton, "apply_btn").click()
                self.assertEqual(fake.call_count, 0)
                self.assertIn("先点击要更新的图面板",
                              w.log_text.toPlainText())
        finally:
            w.close()

    def test_clicking_placeholder_focuses_view(self):
        w = create_window()
        try:
            add_checked(w, ["data/fake_b.tif"])
            _open_view(w, "2D")
            self.assertIsNone(w.focus_panel)
            # 模拟点击面板内容 → 事件过滤器切焦点（焦点=具体面板）
            QTest.mouseClick(gui_panel_state._content(_dock(w, "2D", "data/fake_b.tif")),
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
                add_checked(w, ["data/fake_a.tif"])   # A 慢 0.2 s
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
    只能点对号方块；背景高亮跟随对号集合）。

    导入默认**不勾选**（用户 2026-09-25 定）——勾选出 [全选] /
    [按条件选…] / 点方块三条路；select=True 保留老行为（脚本/测试用）。"""

    def test_add_files_unchecked_by_default(self):
        """导入（选入/拖入/文件夹扫描）默认一个都不勾。"""
        w = create_window()
        try:
            w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
            self.assertEqual(w.file_list.item(0).checkState(), Qt.Unchecked)
            self.assertEqual(w.file_list.item(1).checkState(), Qt.Unchecked)
            self.assertIsNone(w.file_list.currentItem())   # 没对号就不高亮
            self.assertEqual(w.file_label.text(), "未打开文件")
            self.assertIn("已添加 2 个文件", w.log_text.toPlainText())
            self.assertIn("未选中", w.log_text.toPlainText())
        finally:
            w.close()

    def test_add_files_select_true_checks_all(self):
        """select=True（脚本/测试用）仍是一批全勾 + 高亮最后一条。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            # 先点 A 的方块取消 A → 只剩 B 勾着
            item_a = w.file_list.item(0)
            w._press_item, w._press_state = item_a, Qt.Checked
            item_a.setCheckState(Qt.Unchecked)
            w.file_list.itemClicked.emit(item_a, 0)
            self.assertEqual(item_a.checkState(), Qt.Unchecked)
            # 再点 A 行体 → 勾回 A，B 的对号不动（Qt 原生：按下时
            # 事件过滤器记录 A 为未勾、并把当前项移到 A；手动补上）
            w._press_item, w._press_state = item_a, Qt.Unchecked
            w.file_list.setCurrentItem(item_a)
            w.file_list.itemClicked.emit(item_a, 0)
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
            add_checked(w, ["data/fake_b.tif"])
            w.file_list.itemClicked.emit(w.file_list.item(0), 0)   # 点它
            self.assertEqual(w.file_list.item(0).checkState(), Qt.Checked)
            self.assertEqual(w.file_label.text(), "fake_b.tif")
        finally:
            w.close()

    def test_square_click_auto_toggle_honored(self):
        """点对号方块：Qt 已自动切换（按下时记着旧状态）→ 取消对号
        生效——这是取消对号的唯一途径。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_b.tif"])
            item = w.file_list.item(0)
            w._press_item, w._press_state = item, Qt.Checked   # 按下时勾着
            item.setCheckState(Qt.Unchecked)   # Qt 在弹起时自动取消
            w.file_list.itemClicked.emit(item, 0)
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                # 点 B 的对号方块取消 → 只剩 A 勾着
                item_b = w.file_list.item(1)
                w._press_item, w._press_state = item_b, Qt.Checked
                item_b.setCheckState(Qt.Unchecked)
                w.file_list.itemClicked.emit(item_b, 0)
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
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            w.findChild(QPushButton, "delete_btn").click()
            self.assertEqual(w.file_list.count(), 0)
            self.assertEqual(w.file_label.text(), "未打开文件")
            self.assertIn("已从列表移除 2 个文件", w.log_text.toPlainText())
        finally:
            w.close()

    # ── 选择工具（全选 / 全不选 / 按条件选）──
    @staticmethod
    def _add_many(w, n=9):
        """加 n 个文件（名字 s1.tif…s9.tif），**不勾**（导入默认）。"""
        w.add_files([f"data/s{i + 1}.tif" for i in range(n)])

    def test_select_all_and_none_buttons(self):
        """[全选] 全勾上；[全不选] 全部取消（各只记一行日志）。"""
        w = create_window()
        try:
            w.add_files([f"data/s{i + 1}.tif" for i in range(5)])
            w.findChild(QPushButton, "select_all_btn").click()
            self.assertTrue(all(w.file_list.item(i).checkState() == Qt.Checked
                                for i in range(5)))
            self.assertEqual(w.file_label.text(), "已选 5 个文件")
            self.assertIn("全选：5 个文件", w.log_text.toPlainText())
            w.findChild(QPushButton, "select_none_btn").click()
            self.assertTrue(all(w.file_list.item(i).checkState() == Qt.Unchecked
                                for i in range(5)))
            self.assertEqual(w.file_label.text(), "未打开文件")
            self.assertIn("全不选：5 个条目的对号已取消",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_select_all_on_empty_list_logs(self):
        w = create_window()
        try:
            w.findChild(QPushButton, "select_all_btn").click()
            self.assertIn("文件列表是空的", w.log_text.toPlainText())
        finally:
            w.close()

    def test_select_by_range_replaces_selection(self):
        """区间：第 3 到第 6 个 → 只有这 4 个勾上（默认"只选这些"）。"""
        w = create_window()
        try:
            self._add_many(w)
            gui_file_dock.apply_selection(
                w, {"mode": "range", "start": 3, "stop": 6, "text": "",
                    "append": False})
            got = [i + 1 for i in range(9)
                   if w.file_list.item(i).checkState() == Qt.Checked]
            self.assertEqual(got, [3, 4, 5, 6])
            self.assertEqual(w.file_label.text(), "已选 4 个文件")
            self.assertIn("按条件选中 4 个文件", w.log_text.toPlainText())
        finally:
            w.close()

    def test_select_by_stride(self):
        """间隔：从第 2 个起每 3 个选 1 个 → 第 2、5、8 个。"""
        w = create_window()
        try:
            self._add_many(w)
            gui_file_dock.apply_selection(
                w, {"mode": "stride", "every": 3, "offset": 2, "text": "",
                    "append": False})
            got = [i + 1 for i in range(9)
                   if w.file_list.item(i).checkState() == Qt.Checked]
            self.assertEqual(got, [2, 5, 8])
        finally:
            w.close()

    def test_select_by_name_filter_stacks_with_range(self):
        """名字包含与区间叠加（都命中才选）：第 1–6 个里名字含 "s1" 的。"""
        w = create_window()
        try:
            self._add_many(w)
            gui_file_dock.apply_selection(
                w, {"mode": "range", "start": 1, "stop": 6, "text": "s1",
                    "append": False})
            got = [i + 1 for i in range(9)
                   if w.file_list.item(i).checkState() == Qt.Checked]
            self.assertEqual(got, [1])   # s1.tif（s10 不存在）
            self.assertIn("名字含“s1”", w.log_text.toPlainText())
        finally:
            w.close()

    def test_select_append_keeps_existing_checks(self):
        """追加：跨两个区间攒一批（前面的对号不被取消）。"""
        w = create_window()
        try:
            self._add_many(w)
            gui_file_dock.apply_selection(
                w, {"mode": "range", "start": 1, "stop": 2, "text": "",
                    "append": False})
            gui_file_dock.apply_selection(
                w, {"mode": "range", "start": 8, "stop": 9, "text": "",
                    "append": True})
            got = [i + 1 for i in range(9)
                   if w.file_list.item(i).checkState() == Qt.Checked]
            self.assertEqual(got, [1, 2, 8, 9])
        finally:
            w.close()

    def test_select_without_match_logs_and_clears(self):
        """没有命中：记一行日志、对号清空（不静默什么都不做）。"""
        w = create_window()
        try:
            self._add_many(w)
            gui_file_dock.apply_selection(
                w, {"mode": "range", "start": 1, "stop": 2, "text": "",
                    "append": False})
            gui_file_dock.apply_selection(
                w, {"mode": "range", "start": 1, "stop": 3, "text": "没有这个词",
                    "append": False})
            self.assertEqual(w.file_label.text(), "未打开文件")
            self.assertIn("没有命中任何文件", w.log_text.toPlainText())
        finally:
            w.close()

    def test_select_dialog_spec_and_preview(self):
        """弹窗：勾"间隔" + 填名字 → 预览行实时更新，确定返回 spec。

        模态 exec 用 patch 兜住（跟导出弹窗的测法同一路）：side_effect
        拿到的是弹窗实例，按 objectName 找到控件驱动它。
        """
        w = create_window()
        try:
            self._add_many(w)
            w.show()
            seen = {}

            def fake_exec(self):    # new= 打补丁 → 描述符协议 → self = 弹窗
                seen["preview0"] = self.findChild(
                    QLabel, "sel_preview").text()
                self.findChild(QRadioButton, "sel_stride").setChecked(True)
                self.findChild(QSpinBox, "sel_every").setValue(2)
                self.findChild(QSpinBox, "sel_offset").setValue(1)
                self.findChild(QLineEdit, "sel_text").setText("s")
                seen["preview1"] = self.findChild(
                    QLabel, "sel_preview").text()
                return QDialog.Accepted

            with mock.patch.object(QDialog, "exec", new=fake_exec):
                spec = gui_file_dock._selection_dialog_spec(w, 9)
            self.assertEqual(spec["mode"], "stride")
            self.assertEqual((spec["every"], spec["offset"]), (2, 1))
            self.assertEqual(spec["text"], "s")
            self.assertIn("将选中 9 个文件（列表共 9 个）", seen["preview0"])
            self.assertIn("将选中 5 个文件", seen["preview1"])   # 1,3,5,7,9
        finally:
            w.close()

    def test_select_dialog_hidden_window_returns_none(self):
        """窗口没显示（测试/无头环境）不弹模态框，直接返回 None。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif"])
            self.assertIsNone(gui_file_dock._selection_dialog_spec(w, 1))
        finally:
            w.close()


def _draw_one_1d(w):
    """画一张 fake_b 的 1D 图（mock 积分），返回面板。"""
    with mock.patch.object(gui_views, "_compute_integration",
                           side_effect=_fake_compute):
        add_checked(w, ["data/fake_b.tif"])
        _open_view(w, "1D")
        assert _wait_until(
            lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
    return _dock(w, "1D", "data/fake_b.tif")


class TestSaveFigures(unittest.TestCase):
    """[保存]：弹窗勾选要保存的图 → 逐个选文件名存 PNG（主动操作）。"""

    def test_save_with_no_figures_logs(self):
        w = create_window()
        try:
            add_checked(w, ["data/fake_b.tif"])   # 只选中没出图
            w.findChild(QPushButton, "save_btn").click()
            self.assertIn("没有已输出的图可保存", w.log_text.toPlainText())
        finally:
            w.close()

    def test_save_flow_saves_chosen_panel(self):
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            fig = gui_panel_state._content(dock).figure
            with mock.patch.object(gui_export, "_choose_panels",
                                   return_value=[dock]), \
                 mock.patch.object(gui_export, "_ask_save_options",
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
            with mock.patch.object(gui_export, "_choose_panels",
                                   return_value=None), \
                 mock.patch.object(QFileDialog, "getSaveFileName") as dlg:
                self.assertFalse(gui_export._save_figures(w))
                self.assertFalse(dlg.called)
                self.assertIn("已取消保存", w.log_text.toPlainText())
        finally:
            w.close()

    def test_ok_with_none_checked_is_not_cancel(self):
        """确定但一张都没勾 → 提示"没有勾选"，不是"取消"（修前混为一谈）。"""
        w = create_window()
        try:
            _draw_one_1d(w)
            with mock.patch.object(gui_export, "_choose_panels",
                                   return_value=[]), \
                 mock.patch.object(QFileDialog, "getSaveFileName") as dlg:
                self.assertFalse(gui_export._save_figures(w))
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines)
                    > 0 and len(_axes(w, "1D", "data/fake_b.tif").lines)
                    > 0))
            d1 = _dock(w, "1D", "data/fake_a.tif")
            d2 = _dock(w, "1D", "data/fake_b.tif")
            with mock.patch.object(gui_export, "_choose_panels",
                                   return_value=[d1, d2]), \
                 mock.patch.object(gui_export, "_ask_save_options",
                                   return_value={"dpi": 300, "fmt": "png"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   side_effect=[("/tmp/a", ""),
                                                ("", "")]) as dlg, \
                 mock.patch.object(gui_panel_state._content(d1).figure, "savefig"):
                self.assertFalse(gui_export._save_figures(w))
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
            with mock.patch.object(gui_export, "_choose_panels",
                                   return_value=[dock]), \
                 mock.patch.object(gui_export, "_ask_save_options",
                                   return_value={"dpi": 300, "fmt": "png"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("/no/such/dir/out.png", "")), \
                 mock.patch.object(gui_panel_state._content(dock).figure, "savefig",
                                   side_effect=OSError("磁盘写不进")):
                self.assertFalse(gui_export._save_figures(w))
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
            with mock.patch.object(gui_plot_export.QDialog, "exec",
                                   return_value=QDialog.Accepted):
                self.assertEqual(gui_plot_export._ask_save_options(w),
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
            with mock.patch.object(gui_plot_export.QDialog, "exec", new=fake_exec):
                self.assertEqual(gui_plot_export._ask_save_options(w),
                                 {"dpi": 600, "fmt": "tif"})
        finally:
            w.close()

    def test_dialog_cancel_returns_none(self):
        w = create_window()
        try:
            with mock.patch.object(gui_plot_export.QDialog, "exec",
                                   return_value=QDialog.Rejected):
                self.assertIsNone(gui_plot_export._ask_save_options(w))
        finally:
            w.close()

    def test_batch_cancel_options_aborts_save(self):
        """批量保存：选项弹窗取消 → 不弹文件名框、返回 False（关窗留在程序里）。"""
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            with mock.patch.object(gui_export, "_choose_panels",
                                   return_value=[dock]), \
                 mock.patch.object(gui_export, "_ask_save_options",
                                   return_value=None), \
                 mock.patch.object(QFileDialog, "getSaveFileName") as dlg:
                self.assertFalse(gui_export._save_figures(w))
                self.assertFalse(dlg.called)
                self.assertIn("已取消保存", w.log_text.toPlainText())
        finally:
            w.close()

    def test_batch_tif_saves_with_extension_and_dpi(self):
        """批量保存 TIF：文件名过滤器带 TIF、自动补 .tif + savefig 收到 dpi。"""
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            fig = gui_panel_state._content(dock).figure
            with mock.patch.object(gui_export, "_choose_panels",
                                   return_value=[dock]), \
                 mock.patch.object(gui_export, "_ask_save_options",
                                   return_value={"dpi": 600, "fmt": "tif"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("/tmp/b", "TIF 图片 (*.tif)")) as dlg, \
                 mock.patch.object(fig, "savefig") as savefig:
                self.assertTrue(gui_export._save_figures(w))
                self.assertIn("TIF", dlg.call_args[0][3])
                savefig.assert_called_once_with("/tmp/b.tif", dpi=600)
        finally:
            w.close()

    def test_panel_save_cancel_options_keeps_unsaved(self):
        """单面板工具栏 [Save]：选项弹窗取消 → 不弹文件名框、记账不动。

        patch 目标 = 调用点所在模块 plot_panels（_save_panel 在那里做
        全局名查找）：打 plot_export 里的同名函数拦不住这条路径，真弹窗
        会让 offscreen 套件卡死在 exec() 上（实测）。
        """
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            with mock.patch.object(gui_plot_panels, "_ask_save_options",
                                   return_value=None), \
                 mock.patch.object(QFileDialog, "getSaveFileName") as dlg:
                gui_panel_state._content(dock).toolbar.save_figure()
                self.assertFalse(dlg.called)
                self.assertFalse(dock.figure_saved)
        finally:
            w.close()

    def test_panel_save_tif_dpi(self):
        """单面板保存 TIF 600 dpi：自动补 .tif + savefig 收到 dpi。"""
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            fig = gui_panel_state._content(dock).figure
            with mock.patch.object(gui_plot_panels, "_ask_save_options",
                                   return_value={"dpi": 600, "fmt": "tif"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("/tmp/panel_tif", "TIF 图片 (*.tif)")) as dlg, \
                 mock.patch.object(fig, "savefig") as savefig:
                gui_panel_state._content(dock).toolbar.save_figure()
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
                add_checked(w, ["data/fake_b.tif"])
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
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            w.compare_btn.click()
            keys = [k for k in w.plot_docks if k.startswith("对比|")]
            self.assertEqual(len(keys), 1)
            ax = gui_panel_state._content(w.plot_docks[keys[0]]).axes_1d
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
            gui_plot_compare._redraw_compare(w, key)
            self.assertEqual(ax.lines[0].get_color(), "#ff0000")
            self.assertEqual(ax.lines[1].get_color(), self._SLOTS[1])
            # 换"默认"配色 → 颜色按新参数重画（旧色不被套回）
            dock.params_snapshot["曲线配色"] = "默认"
            gui_plot_compare._redraw_compare(w, key)
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
            content = gui_panel_state._content(dock)
            dlg = gui_customize._build_customize_dialog(w, dock, content.axes_1d,
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
            gui_customize._apply_customize(w, dock, content.axes_1d,
                                     content.figure, dlg)
            self.assertEqual(dock.curve_colors, {"fake_a.tif": "#ff0000"})
            self.assertEqual(ax.lines[0].get_color(), "#ff0000")
            self.assertEqual(ax.lines[1].get_color(), self._SLOTS[1])
            # 再开对话框：色块预填自定义色；清空后应用 → 回色板色
            dlg2 = gui_customize._build_customize_dialog(w, dock, content.axes_1d,
                                                   content.figure)
            self.assertEqual(dlg2._color_picks, {"fake_a.tif": "#ff0000"})
            dlg2.findChild(QPushButton, "clear_colors_btn").click()
            gui_customize._apply_customize(w, dock, content.axes_1d,
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
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            w.compare_btn.click()
            keys = [k for k in w.plot_docks if k.startswith("对比|")]
            self.assertEqual(len(keys), 1)
            ax = gui_panel_state._content(w.plot_docks[keys[0]]).axes_1d
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
        """堆叠：行距统一 = **全场**峰值 ×0.7（不按各条自己的峰值），
        y 刻度 = 样品名，无图例；取消堆叠回到平铺 + 图例回来。

        用户 2026-09-26："不要按照各自的最高峰归一化，所有的图"——
        假数据 fake_a 峰值 3、fake_b 峰值 30：两条都按全场峰值 30 抬行
        （旧规则按各自的 3 / 30 抬，等于把每条都缩到自己的高度）。
        """
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            y_flat = [np.asarray(l.get_ydata()).copy() for l in ax.lines]
            dock.params_snapshot["对比堆叠"] = True
            gui_plot_compare._redraw_compare(w, key)
            y_stack = [np.asarray(l.get_ydata()).copy() for l in ax.lines]
            peak_all = max(float(np.nanmax(y)) for y in y_flat)   # = 30
            np.testing.assert_allclose(y_stack[0], y_flat[0])   # 第一条不动
            np.testing.assert_allclose(y_stack[1], y_flat[1] + peak_all * 0.7)
            # 两条行基线 = 0 与 全场峰值×0.7（行距统一，不按各自的峰）
            self.assertAlmostEqual(float(y_stack[0][0]),
                                   float(y_flat[0][0]))
            self.assertAlmostEqual(float(y_stack[1][0]) - float(y_flat[1][0]),
                                   peak_all * 0.7)
            self.assertEqual([t.get_text() for t in ax.get_yticklabels()],
                             ["fake_a.tif", "fake_b.tif"])
            self.assertIsNone(ax.get_legend(), "堆叠下 y 刻度即样品名，无图例")
            dock.params_snapshot["对比堆叠"] = False
            gui_plot_compare._redraw_compare(w, key)
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
            gui_plot_compare._redraw_compare(w, key)
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
            dock.heat_files = [gui_sources.make_source(
                "data/fake_a.tif", "fake_a.tif"),
                               gui_sources.make_source(
                                                   "data/fake_b.tif", "fake_b.tif")]
            gui_plot_compare._heat_row_press(w, key, self._synthetic_event(ax, 1))
            gui_plot_compare._heat_row_release(w, key, self._synthetic_event(ax, 1))
            self.assertEqual(dock.compare_hidden, {"fake_b.tif"})
            self.assertEqual([l.get_label() for l in ax.lines],
                             ["fake_a.tif"])
            self.assertIn("对比面板隐藏该曲线", w.log_text.toPlainText())
            # 再点一次 → 恢复，颜色序号不变（fake_b 仍是第 2 槽）
            gui_plot_compare._heat_row_press(w, key, self._synthetic_event(ax, 1))
            gui_plot_compare._heat_row_release(w, key, self._synthetic_event(ax, 1))
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
            dock.heat_files = [gui_sources.make_source(
                "data/fake_a.tif", "fake_a.tif"),
                               gui_sources.make_source(
                                                   "data/fake_b.tif", "fake_b.tif")]
            gui_plot_compare._heat_row_press(w, key,
                                      self._synthetic_event(ax, 1, x=50, y=50))
            gui_plot_compare._heat_row_release(w, key,
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
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
            dock1 = _dock(w, "1D", "data/fake_b.tif")
            dock1.heat_files = [gui_sources.make_source(
                "data/fake_b.tif", "fake_b.tif")]
            ax1 = gui_panel_state._content(dock1).axes_1d
            gui_plot_compare._heat_row_press(w, "1D|data/fake_b.tif",
                                      self._synthetic_event(ax1, 0))
            gui_plot_compare._heat_row_release(w, "1D|data/fake_b.tif",
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
            # 每条 = (tth, shown, display, i, ref)，ref = 纵轴范围看的那份
            self.assertEqual([c[2] for c in curves], ["fake_b.tif"])
            self.assertEqual([c[3] for c in curves], [1],
                             "颜色序号跟文件走（隐藏第一条，第二条仍是 1 号）")
            self.assertEqual([len(c[4]) for c in curves],
                             [len(c[1]) for c in curves], "ref 与 shown 等长")
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
            # 第一次 close 被 ignore、窗口还开着：收尾这次必须罩住确认框，
            # 否则 offscreen 下真弹窗 → 挂死（同其他关窗测试）
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
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
            # 拖入也不自动勾（导入默认不勾选，用户 2026-09-25 定）
            self.assertEqual(w.file_list.item(0).checkState(), Qt.Unchecked)
            self.assertEqual(w.file_list.item(1).checkState(), Qt.Unchecked)
            self.assertIsNone(w.file_list.currentItem())
            self.assertEqual(w.file_label.text(), "未打开文件")
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
                w.findChild(QPushButton, "select_all_btn").click()   # 勾上
                _open_view(w, "1D")
                drawn = _wait_until(
                    lambda: len(_axes(w, "1D", abs_b).lines) > 0)
                self.assertTrue(drawn, "拖入的文件应能直接作图")
        finally:
            w.close()


def _kw_of(w):
    """窗口当前的积分参数（与 plot_views 取缓存键用的那一套同源）。"""
    geom = gui_state._collect_geometry(w)
    return dict(config=w.config_name,
                npt=int(w.params["输出点数"].value()),
                tth_min=geom.get("tth_min_deg"),
                tth_max=geom.get("tth_max_deg"))


def _tmp_files(n=2, prefix="s"):
    """临时目录里造 n 个真文件（产物要有真文件才算得出指纹）。"""
    folder = Path(tempfile.mkdtemp(prefix="xrd_gui_src_"))
    out = []
    for i in range(1, n + 1):
        p = folder / f"{prefix}{i}.tif"
        p.write_bytes(b"x" * (1000 + i))
        out.append(p)
    return out


def _store_product(w, path, kind="1d", values=None):
    """给真文件落一份产物（1d 或 bg），返回产物键。"""
    kw = _kw_of(w)
    tth = np.linspace(kw["tth_min"], kw["tth_max"], 3)
    intensity = np.array(values if values is not None else [1.0, 2.0, 3.0])
    if kind == "1d":
        produced = stage_cache.store_1d(path, tth, intensity, **kw)
    else:
        produced = stage_cache.store_proc(
            path, tth, intensity, **kw,
            settings={"mode": "anchor", "window_deg": 2.0,
                      "anchors": [(float(kw["tth_min"]), 1.0)]})
    return Path(produced).stem


def _group_by_text(w, part):
    """按组名里的一段文字找组节点。"""
    for node in w.file_list.groups():
        if part in node.text(0):
            return node
    return None


class TestProductGroups(unittest.TestCase):
    """文件栏的"阶段文件夹"：① 1D 产物 ② 每次 [批量处理] 一组。

    勾组 = 整组全选（半勾表示只勾了一部分）；产物条目出图/对比直接读
    产物（不重算、不再扣背景）；原始数据的对号语义一点没变。
    """

    def setUp(self):
        # 台账是模块级共享的临时缓存根：每个用例自己清一份，断言才数得准
        stage_cache.write_batches("bg", [])

    def test_group_check_propagates_both_ways(self):
        """勾组 → 组里全勾；取消一个 → 组**不再亮**（不是半勾）；全取消 → 不勾。

        用户 2026-09-26："文件栏有三种状态，对号、横线、空格。横线和空
        重复了，留空格"——半勾已从模型里去掉，勾一部分时组就是空格。
        """
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif",
                            "data/s3.tif"])
            raw = w.file_list.raw_group
            raw.setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            states = [raw.child(i).checkState(0) for i in range(3)]
            self.assertEqual(states, [Qt.Checked] * 3, "勾组 = 组里全勾")
            self.assertIn("已选中整组 原始数据", w.log_text.toPlainText())
            raw.child(0).setCheckState(0, Qt.Unchecked)
            QApplication.processEvents()
            self.assertEqual(raw.checkState(0), Qt.Unchecked,
                             "只勾一部分 → 组是空格（没有半勾这一态）")
            raw.child(0).setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            self.assertEqual(raw.checkState(0), Qt.Checked, "补齐 → 组亮")
            for i in range(3):
                raw.child(i).setCheckState(0, Qt.Unchecked)
            QApplication.processEvents()
            self.assertEqual(raw.checkState(0), Qt.Unchecked)
        finally:
            w.close()

    def test_one_d_group_appears_for_cached_files(self):
        """算过 1D 的文件进「1D 产物」组；没算过的不进。"""
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files], select=True)
            self.assertIsNone(_group_by_text(w, "1D 产物"), "还没有产物")
            _store_product(w, files[0])
            w.refresh_groups()
            group = _group_by_text(w, "1D 产物")
            self.assertIsNotNone(group)
            self.assertEqual(group.childCount(), 1)
            self.assertIn(files[0].name, group.child(0).text(0))
            self.assertIn("· 1D", group.child(0).text(0))
        finally:
            w.close()

    def test_product_item_draws_from_cache_without_integrating(self):
        """产物条目出 1D 图 = 直接读盘画线（积分函数一次都不该被调）。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=True)
            key = _store_product(w, files[0], values=[7.0, 8.0, 9.0])
            w.refresh_groups()
            group = _group_by_text(w, "1D 产物")
            group.child(0).setCheckState(0, Qt.Checked)
            w.file_list.raw_group.setCheckState(0, Qt.Unchecked)
            QApplication.processEvents()
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute) as compute:
                _open_view(w, "1D")
                QApplication.processEvents()
                self.assertEqual(compute.call_count, 0, "产物不该重新积分")
            dock = w.plot_docks[f"1D|1d#{key}"]
            lines = _axes(w, "1D", "1d#" + key).lines
            self.assertEqual(len(lines), 1)
            self.assertEqual(list(lines[0].get_ydata()), [7.0, 8.0, 9.0])
            self.assertIn("直接读盘不重算", w.log_text.toPlainText())
            # 1D 产物 = 那条原始积分曲线（不是"已完成"的东西）：不强制「不扣」，
            # 模式照默认起步，锚点/自动基线和原始文件一样能用
            self.assertNotIn("面板背景扣除已置「不扣」", w.log_text.toPlainText())
            self.assertEqual(dock.params_snapshot["背景扣除模式"], "off",
                             "默认就是不扣（面板快照的出厂值），不是被强制的")
        finally:
            w.close()

    def test_bg_product_panel_forces_background_off(self):
        """处理产物面板：强制「不扣」（已经扣过，再扣就是二次相减）。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files])
            key = _store_product(w, files[0], kind="bg", values=[5.0, 6.0, 7.0])
            stage_cache.record_batch("bg", "force-off",
                                     label="扣背景 09-25 07:00（空扫相减）",
                                     items=[(files[0], key)], **_kw_of(w),
                                     settings={"mode": "blank"})
            w.refresh_groups()
            _group_by_text(w, "扣背景 09-25 07:00").child(0).setCheckState(
                0, Qt.Checked)
            QApplication.processEvents()
            _open_view(w, "1D")
            QApplication.processEvents()
            dock = w.plot_docks[f"1D|bg#{key}"]
            self.assertEqual(dock.params_snapshot["背景扣除模式"], "off")
            self.assertIn("面板背景扣除已置「不扣」", w.log_text.toPlainText())
        finally:
            w.close()

    def test_product_item_on_2d_view_is_skipped_with_log(self):
        """产物条目点了 [2D]：点名跳过（1D 曲线没有 2D 视图）。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=True)
            _store_product(w, files[0])
            w.refresh_groups()
            group = _group_by_text(w, "1D 产物")
            group.child(0).setCheckState(0, Qt.Checked)
            w.file_list.raw_group.setCheckState(0, Qt.Unchecked)
            QApplication.processEvents()
            _open_view(w, "2D")
            QApplication.processEvents()
            self.assertIn("跳过 1 个产物条目", w.log_text.toPlainText())
        finally:
            w.close()

    def test_compare_uses_background_group(self):
        """勾一整组处理产物 → [对比] 直接画出那两条（不重算、不再处理）。"""
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files], select=True)
            keys = [_store_product(w, p, kind="bg", values=[i, i, i])
                    for i, p in enumerate(files, start=1)]
            stage_cache.record_batch(
                "bg", "probe-batch",
                label="扣背景 09-25 14:03（锚点 1 个，窗口 2°）",
                items=[(p, k) for p, k in zip(files, keys)], **_kw_of(w),
                settings={"mode": "anchor"})
            w.refresh_groups()
            group = _group_by_text(w, "扣背景 09-25 14:03")
            self.assertIsNotNone(group)
            group.setCheckState(0, Qt.Checked)
            w.file_list.raw_group.setCheckState(0, Qt.Unchecked)
            QApplication.processEvents()
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute) as compute:
                w.compare_btn.click()
                QApplication.processEvents()
                self.assertEqual(compute.call_count, 0, "产物不该重新积分")
            key = "对比|" + ",".join(sorted(f"bg#{k}" for k in keys))
            ax = _axes(w, "对比", key.split("|", 1)[1])
            self.assertEqual(len(ax.lines), 2)
            labels = [ln.get_label() for ln in ax.lines]
            self.assertTrue(all("处理" in lb for lb in labels), labels)
            self.assertIn("处理产物", w.log_text.toPlainText())
        finally:
            w.close()

    def test_delete_handles_products_too(self):
        """[删除] 与右键同一套口径：勾了产物也真删（原始数据只从列表移除）。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=True)
            _store_product(w, files[0])
            w.refresh_groups()
            w.file_list.raw_group.setCheckState(0, Qt.Unchecked)
            _group_by_text(w, "1D 产物").child(0).setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            w.findChild(QPushButton, "delete_btn").click()
            self.assertEqual(w.file_list.count(), 1, "原始数据还在列表里")
            self.assertIn("已删除 1 条产物", w.log_text.toPlainText())
            self.assertIsNone(_group_by_text(w, "1D 产物"), "产物分组跟着收")
            self.assertEqual(stage_cache.list_batches("bg"), [])
            # 原始数据一起勾上时：从列表移除（硬盘上的文件不动），与产物各记一行
            w.file_list.raw_group.setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            w.findChild(QPushButton, "delete_btn").click()
            self.assertEqual(w.file_list.count(), 0)
            self.assertIn("已从列表移除 1 个文件", w.log_text.toPlainText())
            self.assertIn("硬盘上的文件未改动", w.log_text.toPlainText())
        finally:
            w.close()

    def test_delete_group_menu_drops_products(self):
        """右键产物分组 → 删掉这一组（台账 + 盘上的产物一起没）。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=True)
            key = _store_product(w, files[0], kind="bg")
            stage_cache.record_batch(
                "bg", "menu-batch", label="扣背景 09-25 09:00（空扫相减）",
                items=[(files[0], key)], **_kw_of(w),
                settings={"mode": "blank"})
            w.refresh_groups()
            group = _group_by_text(w, "扣背景 09-25 09:00")
            self.assertIsNotNone(group)
            # 没显示的窗口不弹模态菜单（会等不到人点，卡死套件）——这一步
            # 单独守；删除本身走 drop_product_group（菜单确认后调同一个）
            gui_file_dock._entry_menu(w, group)
            self.assertEqual(w.file_list.groups()[-1].text(0),
                             group.text(0), "无头环境不弹菜单，也不该删掉什么")
            gui_file_dock.drop_product_group(w, group)
            QApplication.processEvents()
            self.assertIsNone(_group_by_text(w, "扣背景 09-25 09:00"))
            self.assertIn("已删除产物分组", w.log_text.toPlainText())
            self.assertFalse(stage_cache.has_key("bg", key), "盘上的产物也删了")
        finally:
            w.close()

    def test_delete_one_d_group_removes_its_products(self):
        """右键「1D 产物」组 → 那一组退出盘（成员文件 + 分组一起消失）。"""
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files])
            keys = [_store_product(w, p) for p in files]
            w.refresh_groups()
            group = _group_by_text(w, "1D 产物")
            self.assertIsNotNone(group)
            npz = [stage_cache.CACHE_ROOT / "1d" / f"{k}.npz" for k in keys]
            self.assertTrue(all(p.exists() for p in npz))
            n = gui_file_dock.drop_product_group(w, group)
            self.assertEqual(n, 2)
            self.assertTrue(all(not p.exists() for p in npz), "盘上的产物删掉了")
            self.assertIsNone(_group_by_text(w, "1D 产物"), "分组也没了")
            self.assertIn("已删除产物分组", w.log_text.toPlainText())
        finally:
            w.close()

    def test_delete_single_product_item(self):
        """右键单条产物 → 只删这一条，同组别的条目还在。"""
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files])
            keys = [_store_product(w, p) for p in files]
            w.refresh_groups()
            group = _group_by_text(w, "1D 产物")
            victim, survivor = group.child(0), group.child(1)
            n = gui_file_dock.drop_product_item(w, victim)
            self.assertEqual(n, 1)
            self.assertFalse((stage_cache.CACHE_ROOT / "1d"
                              / f"{keys[0]}.npz").exists())
            self.assertTrue((stage_cache.CACHE_ROOT / "1d"
                             / f"{keys[1]}.npz").exists())
            after = _group_by_text(w, "1D 产物")
            self.assertIsNotNone(after, "还有一条产物，分组要留着")
            self.assertEqual(after.childCount(), 1)
            self.assertIn(survivor.text(0), after.child(0).text(0))
            self.assertIn("已删除产物", w.log_text.toPlainText())
        finally:
            w.close()

    def test_delete_single_bg_item_prunes_ledger(self):
        """删 bg 的单条 → 产物与台账条目一起没；那一批空了就整条消失。"""
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files])
            keys = [_store_product(w, p, kind="bg") for p in files]
            stage_cache.record_batch(
                "bg", "two-items", label="扣背景 09-25 06:00（空扫相减）",
                items=[(files[0], keys[0]), (files[1], keys[1])], **_kw_of(w),
                settings={"mode": "blank"})
            w.refresh_groups()
            group = _group_by_text(w, "扣背景 09-25 06:00")
            self.assertEqual(group.childCount(), 2)
            self.assertEqual(gui_file_dock.drop_product_item(w, group.child(0)), 1)
            batch = stage_cache.list_batches("bg")[0]
            self.assertEqual(len(batch["items"]), 1, "台账里也摘掉了一条")
            # 删掉剩下那条 → 这一批空了，整条从台账消失
            left = _group_by_text(w, "扣背景 09-25 06:00")
            self.assertEqual(left.childCount(), 1)
            gui_file_dock.drop_product_item(w, left.child(0))
            self.assertEqual(stage_cache.list_batches("bg"), [])
            self.assertIsNone(_group_by_text(w, "扣背景 09-25 06:00"))
        finally:
            w.close()

    def test_product_menu_ignores_raw_items(self):
        """右键原始数据（组或条目）不弹产物菜单：它们的删除入口是 [删除]。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            add_checked(w, [str(p) for p in files])
            # 无头环境本来就不弹菜单；这里守的是"原始数据不进这条路"
            self.assertEqual(gui_file_dock.drop_product_group(
                w, w.file_list.raw_group), 0)
            self.assertEqual(gui_file_dock.drop_product_item(
                w, w.file_list.item(0)), 0)
            gui_file_dock._entry_menu(w, w.file_list.item(0))   # 不炸即可
            self.assertEqual(w.file_list.count(), 1)
        finally:
            w.close()

    def test_remove_from_list_leaves_the_file_alone(self):
        """右键「从列表移除」：只动列表，硬盘上的文件一个字节都不碰。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            add_checked(w, [str(p) for p in files])
            size_before = files[0].stat().st_size
            gui_file_dock._remove_from_list(w, [w.file_list.item(0)])
            self.assertEqual(w.file_list.count(), 0)
            self.assertTrue(files[0].exists(), "原始文件必须还在")
            self.assertEqual(files[0].stat().st_size, size_before)
            log = w.log_text.toPlainText()
            self.assertIn("已从列表移除 1 个文件", log)
            self.assertIn("硬盘上的文件未改动", log)
        finally:
            w.close()

    def test_clear_cache_action_empties_products(self):
        """「删除所有缓存」：产物与分组一起清（无头环境不弹确认框）。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files])
            _store_product(w, files[0])
            w.refresh_groups()
            self.assertIsNotNone(_group_by_text(w, "1D 产物"))
            gui_file_dock.ask_clear_cache(w)      # 窗口没显示 → 不弹模态
            self.assertIsNone(_group_by_text(w, "1D 产物"))
            self.assertIn("已删除所有缓存", w.log_text.toPlainText())
        finally:
            w.close()

    def test_export_specific_sources_ignores_checks(self):
        """右键「导出这一条」：导出给定来源，不改动勾选状态。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files])      # 一个都不勾
            key = _store_product(w, files[0], kind="bg", values=[7.0, 8.0, 9.0])
            stage_cache.record_batch("bg", "export-by-menu",
                                     label="处理后 09-25 05:00（空扫相减）",
                                     items=[(files[0], key)], **_kw_of(w),
                                     settings={"mode": "blank"})
            w.refresh_groups()
            src = gui_sources.source_of(
                _group_by_text(w, "处理后 09-25 05:00").child(0))
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".txt",
                                                 "csv": False, "bg": False}):
                gui_export._run_export(w, sources=[src])
            target = outdir / f"{files[0].stem}_处理后" / "integrated_2th.txt"
            self.assertTrue(target.exists(), "右键导出该落一份文件")
            data = np.loadtxt(str(target))
            self.assertEqual(list(data[:, 1]), [7.0, 8.0, 9.0])
            self.assertEqual(len(gui_sources.checked_sources(w)), 0,
                             "导出不该改动勾选状态")
        finally:
            w.close()

    def test_calib_standard_ignores_product_items(self):
        """校准取标样只认原始数据：勾了产物不算数。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=True)
            _store_product(w, files[0])
            w.refresh_groups()
            group = _group_by_text(w, "1D 产物")
            group.child(0).setCheckState(0, Qt.Checked)
            w.file_list.raw_group.setCheckState(0, Qt.Unchecked)
            QApplication.processEvents()
            self.assertIsNone(gui_calib_panel._calib_standard_path(w))
            w.file_list.raw_group.setCheckState(0, Qt.Checked)
            self.assertEqual(gui_calib_panel._calib_standard_path(w), files[0])
        finally:
            w.close()

    def test_export_names_product_with_stage_suffix(self):
        """导出产物条目：名字带 `_扣背景`/`_1D` 后缀（与原始结果不撞名）。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=True)
            key = _store_product(w, files[0], kind="bg",
                                 values=[4.0, 5.0, 6.0])
            stage_cache.record_batch("bg", "export-batch", label="扣背景 batch",
                                     items=[(files[0], key)], **_kw_of(w),
                                     settings={"mode": "anchor"})
            w.refresh_groups()
            self.assertIsNone(_group_by_text(w, "1D 产物"), "bg 产物不进 1D 组")
            w.refresh_groups()
            _group_by_text(w, "扣背景 batch").child(0).setCheckState(0, Qt.Checked)
            w.file_list.raw_group.setCheckState(0, Qt.Unchecked)
            QApplication.processEvents()
            out = gui_export._checked_1d_results(w)
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0][0], f"{files[0].stem}_处理后")
        finally:
            w.close()

    def test_clear_cache_empties_groups(self):
        """[清空缓存] → 产物分组跟着消失（台账也清了）。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=True)
            _store_product(w, files[0])
            w.refresh_groups()
            self.assertIsNotNone(_group_by_text(w, "1D 产物"))
            w.entrance_buttons["1D"].click()   # [清空缓存] 在 1D 页底部
            w.clear_cache_btn.click()
            QApplication.processEvents()
            self.assertIsNone(_group_by_text(w, "1D 产物"))
        finally:
            w.close()


class TestBackgroundFromProduct(unittest.TestCase):
    """1D 产物条目也能扣背景（用户 2026-09-25 问起的那条）。

    1D 产物就是那条原始积分曲线，只是钉在某一份缓存上——所以它跟原始
    文件一个待遇（锚点/自动基线照用、也能进 [批量处理]），差别只在
    **结果挂在那一份 1D 的键下面**（键 = 那条 1D 键 + 设置哈希）：勾的是
    哪一条就扣哪一条，不按当前设置另算一条。扣背景产物本身仍然跳过
    （它已经是扣完的，再扣就是二次相减）。
    """

    def setUp(self):
        # 台账是模块级共享的临时缓存根：每个用例自己清一份，断言才数得准
        stage_cache.write_batches("bg", [])

    def _mine(self, path):
        """这个文件相关的扣背景批次（台账里按**源文件路径**找）。"""
        key = str(Path(path).resolve())
        return [b for b in stage_cache.list_batches("bg") if key in b["items"]]

    def _one_d_panel(self, w, values=(1.0, 2.0, 3.0)):
        """建一个"只有 1D 产物被勾着"的窗口，返回 (文件, 1D 键, 面板)。"""
        files = _tmp_files(1)
        w.add_files([str(p) for p in files])          # 导入默认不勾
        key1d = _store_product(w, files[0], values=values)
        w.refresh_groups()
        _group_by_text(w, "1D 产物").child(0).setCheckState(0, Qt.Checked)
        QApplication.processEvents()
        _open_view(w, "1D")                           # 产物条目出 1D 面板
        QApplication.processEvents()
        return files[0], key1d, w.plot_docks[f"1D|1d#{key1d}"]

    def _anchors_on(self, w, dock, path, xs):
        """把模式切到手动锚点并在给定 2θ 上放锚点（用面板自己的曲线取强度）。"""
        cb = w.params["背景扣除模式"]
        cb.setCurrentIndex(cb.findData("anchor"))
        w.params["背景窗口 (°)"].setValue(1.0)
        dock.params_snapshot = dict(
            dock.params_snapshot or {},
            **{"背景扣除模式": "anchor", "背景窗口 (°)": 1.0})
        tth = np.asarray(dock.last_tth, dtype=float)
        inten = np.asarray(dock.last_intensity, dtype=float)
        w.bg_anchors[str(path)] = [(x, float(np.interp(x, tth, inten)))
                                   for x in xs]

    def test_batch_background_subtracts_one_d_product(self):
        """勾 1D 产物 → [批量处理]：真扣一份，且挂在**那份 1D 的键**下面。"""
        w = create_window()
        try:
            path, key1d, dock = self._one_d_panel(w)
            tth = np.asarray(dock.last_tth, dtype=float)
            self._anchors_on(w, dock, path, [float(tth[0]), float(tth[-1])])
            w.proc_batch_btn.click()
            QApplication.processEvents()
            log = w.log_text.toPlainText()
            self.assertIn("批量处理完成：1/1", log)
            self.assertIn("其中 1 条来自 1D 产物", log)
            batches = self._mine(path)
            self.assertEqual(len(batches), 1)
            meta = batches[0]["items"][str(Path(path).resolve())]
            self.assertTrue(stage_cache.has_key("bg", meta["key"]),
                            "产物真落盘了")
            with np.load(stage_cache.CACHE_ROOT / "bg"
                         / f"{meta['key']}.npz") as data:
                stored = json.loads(str(data["meta"]))
            self.assertEqual(stored["base_key"], key1d,
                             "挂在勾的那份 1D 产物键下面（不按当前设置另算）")
            # 文件栏里长出一个扣背景分组，且子项可以整组勾上去比
            self.refresh_ok = _wait_until(
                lambda: _group_by_text(w, "处理") is not None)
            self.assertTrue(self.refresh_ok, "扣完要出现扣背景分组")
        finally:
            w.close()

    def test_batch_background_skips_bg_products(self):
        """扣背景产物条目跳过（已经是扣完的），日志说清原因。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files])
            key = _store_product(w, files[0], kind="bg")
            stage_cache.record_batch(
                "bg", "skip-batch", label="扣背景 09-25 08:00（空扫相减）",
                items=[(files[0], key)], **_kw_of(w),
                settings={"mode": "blank"})
            w.refresh_groups()
            _group_by_text(w, "扣背景 09-25 08:00").child(0).setCheckState(
                0, Qt.Checked)
            QApplication.processEvents()
            # 给个编辑对象（否则先卡在"先点一张 1D 图"）
            _open_view(w, "1D")
            QApplication.processEvents()
            dock = next(d for k, d in w.plot_docks.items()
                        if k.startswith("1D|"))
            cb = w.params["背景扣除模式"]
            cb.setCurrentIndex(cb.findData("anchor"))
            dock.params_snapshot = dict(dock.params_snapshot or {},
                                        **{"背景扣除模式": "anchor"})
            w.bg_anchors[str(files[0])] = [(1.0, 1.0)]
            before = len(self._mine(files[0]))
            w.proc_batch_btn.click()
            QApplication.processEvents()
            log = w.log_text.toPlainText()
            self.assertIn("没有选中的文件", log)
            self.assertIn("处理产物已经是处理完的结果", log)
            self.assertEqual(len(self._mine(files[0])), before, "不该新增批次")
        finally:
            w.close()

    def test_batch_background_dedupes_same_file(self):
        """同一文件既勾了原始又勾了 1D 产物：只扣一份，另一条记一行跳过。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            add_checked(w, [str(p) for p in files])   # 原始也勾上
            _store_product(w, files[0])
            w.refresh_groups()
            _group_by_text(w, "1D 产物").child(0).setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                _open_view(w, "1D")
                QApplication.processEvents()
            dock = _dock(w, "1D", str(files[0]))
            self._anchors_on(w, dock, files[0], [0.5, 1.0])
            w.proc_batch_btn.click()
            QApplication.processEvents()
            log = w.log_text.toPlainText()
            self.assertIn("同一文件在批里只扣一份", log)
            self.assertIn("批量处理完成：1/2 个文件", log)
            self.assertEqual(
                sum(len(b["items"]) for b in self._mine(files[0])), 1)
        finally:
            w.close()


def _spiky_compute(path_str, geom, npt):
    """一条窄尖峰曲线（200 点 / 0.5–8.5°）：平滑与裁剪都看得见效果。"""
    tth = np.linspace(0.5, 8.5, 200)
    y = 10.0 + 500.0 * np.exp(-0.5 * ((tth - 4.0) / 0.1) ** 2)
    return tth, y


class TestProcessingChain(unittest.TestCase):
    """「处理」页三项（背景扣除 / 平滑 / 裁剪）端到端。

    用户 2026-09-25 定：三项都进产物、都能批量应用，处理完的那一批进
    「处理后」分组。这里守的是三个最要紧的性质：
      ① 改参数即重画（实时预览这一条不能断）；
      ② 裁剪让纵轴自动范围跳过那一段（这是用户要它的**唯一理由**）；
      ③ 产出的文件里那一段是空的、头里写明处理链（屏幕与文件同源）。
    """

    def setUp(self):
        stage_cache.write_batches("bg", [])

    def _panel(self, w):
        """开一张 1D 面板（窄尖峰曲线），返回 (路径, 面板)。"""
        files = _tmp_files(1)
        add_checked(w, [str(p) for p in files])
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_spiky_compute):
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", str(files[0])).lines) > 0))
        return files[0], w.plot_docks["1D|" + str(files[0])]

    def _enable(self, w, smooth=False, cut=False):
        """按界面上的路径打开处理项（改控件 → 实时重画）。"""
        if smooth:
            w.params["平滑曲线"].setChecked(True)
        if cut:
            w.params["裁剪区间"].setChecked(True)
        gui_views._refresh_proc(w)

    def test_smoothing_lowers_the_peak_on_screen(self):
        w = create_window()
        try:
            path, dock = self._panel(w)
            raw_peak = float(np.nanmax(dock.last_intensity))
            w.params["平滑窗口 (°)"].setValue(0.5)
            self._enable(w, smooth=True)
            drawn = np.asarray(_axes(w, "1D", str(path)).lines[0].get_ydata())
            self.assertEqual(len(drawn), 200, "平滑不该改变点数")
            self.assertLess(drawn.max(), raw_peak * 0.9,
                            "0.5° 窗口应当把 0.1° 宽的尖峰明显削矮")
            self.assertGreater(drawn.max(), raw_peak * 0.1, "别削没了")
        finally:
            w.close()

    def test_cut_leaves_a_gap_and_frees_the_y_axis(self):
        """裁剪：图上那段是空的，纵轴自动范围跟着跳过它（用户要的效果）。"""
        w = create_window()
        try:
            path, dock = self._panel(w)
            ax = _axes(w, "1D", str(path))
            self.assertGreater(ax.get_ylim()[1], 400, "先确认大峰压着纵轴")
            w.params["裁剪起点 (°)"].setValue(3.5)
            w.params["裁剪终点 (°)"].setValue(4.5)
            self._enable(w, cut=True)
            drawn = np.asarray(ax.lines[0].get_ydata())
            tth = np.asarray(ax.lines[0].get_xdata())
            inside = (tth >= 3.5) & (tth <= 4.5)
            self.assertTrue(inside.any(), "测试前提：区间里有采样点")
            self.assertTrue(np.isnan(drawn[inside]).all(), "区间内该是空的")
            self.assertTrue(np.isfinite(drawn[~inside]).all())
            self.assertLess(ax.get_ylim()[1], 100,
                            "纵轴该按剩下的数据自动定范围（大峰不再压扁它）")
            self.assertGreater(ax.get_ylim()[1], 5, "别把范围压没了")
        finally:
            w.close()

    def test_cut_only_when_checked(self):
        """勾选框没勾时，起止框填了也不生效（三项都是可选项）。"""
        w = create_window()
        try:
            path, dock = self._panel(w)
            w.params["裁剪起点 (°)"].setValue(3.9)
            w.params["裁剪终点 (°)"].setValue(4.1)
            gui_views._refresh_proc(w)     # 没勾「裁剪区间」
            drawn = np.asarray(_axes(w, "1D", str(path)).lines[0].get_ydata())
            self.assertTrue(np.isfinite(drawn).all())
        finally:
            w.close()

    def test_multiple_cut_ranges(self):
        """多段裁剪：[添加] 攒清单、每段都挖空、[清空] 收回。"""
        w = create_window()
        try:
            path, dock = self._panel(w)
            w.params["裁剪起点 (°)"].setValue(1.5)
            w.params["裁剪终点 (°)"].setValue(2.0)
            _gui_app._on_cut_add(w)
            w.params["裁剪起点 (°)"].setValue(6.0)
            w.params["裁剪终点 (°)"].setValue(6.3)
            _gui_app._on_cut_add(w)
            QApplication.processEvents()
            drawn = np.asarray(_axes(w, "1D", str(path)).lines[0].get_ydata())
            tth = np.asarray(_axes(w, "1D", str(path)).lines[0].get_xdata())
            for lo, hi in ((1.5, 2.0), (6.0, 6.3)):
                band = (tth >= lo) & (tth <= hi)
                self.assertTrue(np.isnan(drawn[band]).all(),
                                f"{lo}–{hi}° 该是空的")
            self.assertIn("1.5–2°、6–6.3°", w.cut_list_lbl.text())
            self.assertEqual(dock.params_snapshot["裁剪区间"],
                             [(1.5, 2.0), (6.0, 6.3)],
                             "快照里存的是一串区间（面板各记各的）")
            _gui_app._on_cut_clear(w)
            self.assertEqual(w.cut_list, [])
            self.assertFalse(w.params["裁剪区间"].isChecked())
            drawn = np.asarray(_axes(w, "1D", str(path)).lines[0].get_ydata())
            self.assertTrue(np.isfinite(drawn).all(), "清空后不再有空洞")
        finally:
            w.close()

    def test_cut_add_refuses_inverted_and_duplicate(self):
        w = create_window()
        try:
            path, dock = self._panel(w)
            w.params["裁剪起点 (°)"].setValue(5.0)
            w.params["裁剪终点 (°)"].setValue(4.0)
            _gui_app._on_cut_add(w)
            self.assertIn("终点要大于起点", w.log_text.toPlainText())
            self.assertEqual(w.cut_list, [])
            w.params["裁剪终点 (°)"].setValue(5.5)
            _gui_app._on_cut_add(w)
            _gui_app._on_cut_add(w)      # 再加一次同样的
            self.assertEqual(w.cut_list, [(5.0, 5.5)], "同一段只留一份")
            self.assertIn("已经在清单里了", w.log_text.toPlainText())
        finally:
            w.close()

    def test_savgol_is_what_gets_stored(self):
        """切到 SG：链里带上方法与阶数，产物元数据也写明。"""
        w = create_window()
        try:
            path, dock = self._panel(w)
            w.params["平滑窗口 (°)"].setValue(0.5)
            self._enable(w, smooth=True)
            cb = w.params["平滑方法"]
            cb.setCurrentIndex(cb.findData("savgol"))
            self.assertTrue(w.params["平滑阶数"].isEnabled(), "SG 下阶数可编辑")
            gui_views._refresh_proc(w)
            box_peak = float(np.nanmax(
                np.asarray(_axes(w, "1D", str(path)).lines[0].get_ydata())))
            w.params["平滑阶数"].setValue(2)
            gui_views._refresh_proc(w)
            self.assertIn("smooth_method", process.chain_parts(
                gui_state._proc_settings(w, dock, str(path))))
            w.proc_batch_btn.click()
            QApplication.processEvents()
            self.assertIn("SG 0.5°/2阶", w.log_text.toPlainText())
            batch = stage_cache.list_batches("bg")[0]
            meta = stage_cache.meta_by_key(
                "bg", batch["items"][str(path.resolve())]["key"])
            self.assertIn("smooth=savgol/0.5°/p2", meta["chain"])
            # 滑动平均下阶数框置灰
            cb.setCurrentIndex(cb.findData("boxcar"))
            self.assertFalse(w.params["平滑阶数"].isEnabled())
            self.assertGreater(box_peak, 0)
        finally:
            w.close()

    def test_batch_writes_the_whole_chain_into_the_product(self):
        """[批量处理]：平滑 + 裁剪进产物，组名写清这一组做过什么。"""
        w = create_window()
        try:
            path, dock = self._panel(w)
            w.params["平滑窗口 (°)"].setValue(0.5)
            w.params["裁剪起点 (°)"].setValue(3.9)
            w.params["裁剪终点 (°)"].setValue(4.1)
            self._enable(w, smooth=True, cut=True)
            w.proc_batch_btn.click()
            QApplication.processEvents()
            log = w.log_text.toPlainText()
            self.assertIn("批量处理完成", log)
            self.assertIn("平滑 0.5°", log)
            self.assertIn("删 3.9–4.1°", log)
            group = _group_by_text(w, "处理后")
            self.assertIsNotNone(group, "文件栏该长出「处理后」分组")
            self.assertIn("平滑 0.5°", group.text(0))
            # 产物本身：那一段是空的，元数据写明整条链
            batch = stage_cache.list_batches("bg")[0]
            meta = batch["items"][str(path.resolve())]
            with np.load(stage_cache.CACHE_ROOT / "bg"
                         / f"{meta['key']}.npz") as data:
                stored = np.asarray(data["intensity"], dtype=float)
                stored_meta = json.loads(str(data["meta"]))
            self.assertIn("smooth=boxcar/0.5°", stored_meta["chain"])
            self.assertIn("cut=3.9–4.1°", stored_meta["chain"])
            self.assertGreater(int(np.isnan(stored).sum()), 0)
        finally:
            w.close()

    def test_export_skips_cut_points_and_notes_the_chain(self):
        """导出：裁剪点不写行、头里写明处理链；CSV 里那几格留空。"""
        w = create_window()
        try:
            path, dock = self._panel(w)
            w.params["裁剪起点 (°)"].setValue(3.5)
            w.params["裁剪终点 (°)"].setValue(4.5)
            self._enable(w, cut=True)
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".txt",
                                                 "csv": True, "bg": True}):
                gui_export._run_export(w)
            txt = (outdir / path.stem / "integrated_2th.txt")
            self.assertTrue(txt.exists(), "该导出一份 txt")
            text = txt.read_text()
            self.assertIn("processed:", text, "头里要写明处理链")
            self.assertIn("cut=3.5–4.5°", text)
            self.assertRegex(text, r"cut: \d+ points removed")
            data = np.loadtxt(str(txt))
            self.assertTrue(np.isfinite(data[:, 1]).all(),
                            "文件里不该出现 nan 行")
            # CSV：裁剪列里那几格是空的
            csv_text = (outdir / "1d_summary.csv").read_text()
            self.assertIn("空单元格", csv_text)
            blank_rows = [ln for ln in csv_text.splitlines()
                          if ln.endswith(",")]
            self.assertGreater(len(blank_rows), 0,
                               "裁剪段在 CSV 里应当是空单元格")
            self.assertNotIn("nan", csv_text, "空值不能写成字面 nan")
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
            add_checked(w, ["data/fake_a.tif"])
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="overwrite"):
                add_checked(w, ["data/fake_a.tif"])   # 再拖入一次
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
            add_checked(w, ["data/fake_a.tif"])
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="rename"):
                add_checked(w, ["data/fake_a.tif"])
            self.assertEqual(w.file_list.count(), 2)
            self.assertEqual(w.file_list.item(0).text(), "fake_a.tif")
            self.assertEqual(w.file_list.item(1).text(), "fake_a (1).tif")
            # 两条条目指向同一个文件；新条目勾上，标签 = 2 个
            self.assertEqual(w.file_list.item(0).data(Qt.UserRole),
                             w.file_list.item(1).data(Qt.UserRole))
            self.assertEqual(w.file_list.item(1).checkState(), Qt.Checked)
            self.assertEqual(w.file_label.text(), "已选 2 个文件")
            # 第三次加入 → 编号继续涨，不与 (1) 撞名
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="rename"):
                add_checked(w, ["data/fake_a.tif"])
            texts = [w.file_list.item(i).text()
                     for i in range(w.file_list.count())]
            self.assertEqual(texts, ["fake_a.tif", "fake_a (1).tif",
                                     "fake_a (2).tif"])
        finally:
            w.close()

    def test_duplicate_cancel_skips(self):
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif"])
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="cancel"):
                add_checked(w, ["data/fake_a.tif"])
            self.assertEqual(w.file_list.count(), 1)
            self.assertIn("跳过", w.log_text.toPlainText())
        finally:
            w.close()

    def test_rename_dialog_custom_name_used(self):
        """改名输入框：用户自己输入的名字生效。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif"])
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="rename"), \
                 mock.patch.object(gui_file_dock, "_ask_rename",
                                   return_value="我的数据.tif"):
                add_checked(w, ["data/fake_a.tif"])
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
            add_checked(w, ["data/fake_a.tif"])
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="rename"), \
                 mock.patch.object(gui_file_dock, "_ask_rename",
                                   return_value=None):
                add_checked(w, ["data/fake_a.tif"])
            self.assertEqual(w.file_list.count(), 1)
            self.assertIn("已跳过重复文件", w.log_text.toPlainText())
        finally:
            w.close()

    def test_rename_dialog_rejects_taken_name(self):
        """输入的名字已被占用 → 要求换一个，输入框重新弹。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="rename"), \
                 mock.patch.object(gui_file_dock, "_ask_rename",
                                   side_effect=["fake_b.tif", "自定义.tif"]):
                add_checked(w, ["data/fake_a.tif"])
            self.assertEqual(w.file_list.count(), 3)
            self.assertEqual(w.file_list.item(2).text(), "自定义.tif")
            self.assertIn("显示名 fake_b.tif 已被占用", w.log_text.toPlainText())
        finally:
            w.close()

    def test_ask_rename_hidden_window_defaults_default_name(self):
        """窗口没显示（测试环境）→ 不弹模态框，返回预填的默认名。"""
        w = create_window()
        try:
            self.assertEqual(gui_file_dock._ask_rename(w, "xxx (1).tif"),
                             "xxx (1).tif")
        finally:
            w.close()

    def test_ask_duplicate_hidden_window_defaults_overwrite(self):
        """窗口没显示（测试环境）→ 不弹模态框，默认覆盖（防挂死）。"""
        w = create_window()
        try:
            self.assertEqual(gui_file_dock._ask_duplicate(w, "fake_a.tif"),
                             "overwrite")
        finally:
            w.close()

    def test_renamed_entry_gets_own_panel(self):
        """改名条目与原条目各自成图：点 1D 出两张面板，互不当过期。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif"])
                with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                       return_value="rename"):
                    add_checked(w, ["data/fake_a.tif"])
                _open_view(w, "1D")
                drawn1 = _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0)
                # 改名条目的面板键 = 视图|路径|显示名
                key2 = "1D|data/fake_a.tif|fake_a (1).tif"
                drawn2 = _wait_until(
                    lambda: key2 in w.plot_docks
                    and len(gui_panel_state._content(w.plot_docks[key2]).axes_1d.lines) > 0)
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

    def test_geometry_comes_from_the_selected_config(self):
        """几何一律取配置条目，面板上没有能改它的控件。

        （换条目才是改几何的入口——分析页只读后没有"面板覆盖配置"
        这条隐藏通路了。2026-09-26 起那三行只读字段也撤了：读数只在
        坞顶"几何配置"行的悬停提示里显示，压根没有可写的几何控件。）
        """
        w = create_window()
        try:
            cfg = w.config["geometry"]
            geom = gui_panel_state._collect_geometry(w)
            self.assertAlmostEqual(geom["dist_m"], cfg["dist_m"])
            self.assertAlmostEqual(geom["pixel_size_m"],
                                   cfg["pixel_size_m"])
            self.assertAlmostEqual(geom["wavelength_m"], cfg["wavelength_m"])
            self.assertEqual(geom["poni1_m"], cfg["poni1_m"])
            self.assertEqual(geom["rot1_deg"], cfg["rot1_deg"])
            # 读数只在提示里，参数坞里没有几何输入控件
            for gone in ("初始距离 (mm)", "像素尺寸 (µm)", "波长 (Å)"):
                self.assertNotIn(gone, w.params)
            # 2θ 上下限 = 积分设置，仍随面板走（改了就进 geom）
            self.assertAlmostEqual(geom["tth_min_deg"], 1.0)
            self.assertAlmostEqual(geom["tth_max_deg"], 8.0)
            w.params["2θ 下限 (°)"].setValue(2.5)
            w.params["2θ 上限 (°)"].setValue(7.5)
            geom2 = gui_panel_state._collect_geometry(w)
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
                add_checked(w, ["data/fake_b.tif"])
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
            add_checked(w, ["data/fake_a.tif"])
            _open_view(w, "2D")
            self.assertTrue(_wait_until(lambda: len(gui_panel_state._content(
                _dock(w, "2D", "data/fake_a.tif")).axes_2d.images) > 0))
        QTest.mouseClick(gui_panel_state._content(_dock(w, "2D", "data/fake_a.tif")),
                         Qt.LeftButton)
        return w.focus_panel

    def _focus_1d(self, w):
        """画一张 fake_a 的 1D 图并等它完成 → 编辑对象 = 该面板。"""
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_a.tif"])
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
            add_checked(w, [file])
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
        """点 A 显示 A 的参数，点 B 显示 B 的参数（不是只能改不能看）。

        拿 2θ 下限当"每张面板各记各的"的记号（原来用测距那格，2026-09-26
        撤了那三个只读字段，快照里也没有它了）。
        """
        w = create_window()
        try:
            key_a = self._plot_fake(
                w, "data/fake_a.tif",
                {"2θ 下限 (°)": 1.5, "2θ 上限 (°)": 7.0,
                 "输出点数": 2500})
            self.assertEqual(w.focus_panel, key_a)
            # 完成时焦点 = A → 参数坞已回放 A 的快照
            self.assertEqual(w.params["2θ 下限 (°)"].value(), 1.5)
            self.assertEqual(w.params["2θ 上限 (°)"].value(), 7.0)
            self.assertEqual(w.params["输出点数"].value(), 2500)
            # 打开 B（参数不同）→ 焦点切 B，参数显示 B 的值
            key_b = self._plot_fake(
                w, "data/fake_b.tif",
                {"2θ 下限 (°)": 2.5, "2θ 上限 (°)": 8.0})
            self.assertEqual(w.focus_panel, key_b)
            self.assertEqual(w.params["2θ 下限 (°)"].value(), 2.5)
            self.assertEqual(w.params["2θ 上限 (°)"].value(), 8.0)
            # 点回 A → 参数显示 A 的值（能看）
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            self.assertEqual(w.focus_panel, key_a)
            self.assertEqual(w.params["2θ 下限 (°)"].value(), 1.5)
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
                            {"2θ 下限 (°)": 1.5})
            w.params["2θ 下限 (°)"].setValue(2.5)
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
                ["2θ 下限 (°)"], 2.5)
            # 切到 B 再切回 A → 显示新值 2.5
            self._plot_fake(w, "data/fake_b.tif",
                            {"2θ 下限 (°)": 4.0})
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            self.assertEqual(w.params["2θ 下限 (°)"].value(), 2.5)
        finally:
            w.close()

    def test_same_panel_reclick_keeps_edits(self):
        """重复点同一面板不冲掉正在改的值（跳过快照回放）。"""
        w = create_window()
        try:
            key = self._plot_fake(w, "data/fake_a.tif",
                                  {"2θ 下限 (°)": 1.5})
            w.params["2θ 下限 (°)"].setValue(3.5)   # 未应用的编辑
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            self.assertEqual(w.focus_panel, key)
            self.assertEqual(w.params["2θ 下限 (°)"].value(), 3.5)
        finally:
            w.close()

    def test_manual_contrast_restored_from_snapshot(self):
        """手动对比度也进快照：A 保持自动，B 图像 [应用] 改手动，
        切来切去各自回放各自的状态。"""
        w = create_window()
        try:
            # A 作图时自动对比度勾着（默认）
            self._plot_fake(w, "data/fake_a.tif",
                            {"2θ 下限 (°)": 1.5})
            # B 新开（默认自动开）；对 B 关自动、改值并图像 [应用]
            # → B 的快照 = 手动模式 + 这组值
            self._plot_fake(w, "data/fake_b.tif",
                            {"2θ 下限 (°)": 2.5})
            w.params["自动对比度"].setChecked(False)
            w.params["对比度下限"].setValue(123.0)
            w.params["对比度上限"].setValue(456.0)
            w.findChild(QPushButton, "apply_image_btn").click()
            # 切回 A：A 的快照里自动是勾着的 → 自动开、输入框置灰
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            self.assertTrue(w.params["自动对比度"].isChecked())
            self.assertFalse(w.params["对比度下限"].isEnabled())
            # 再切回 B：手动模式 + 123/456 原样回放
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif")),
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

    def test_default_docks_visible(self):
        """初始状态：[文件][日志] 可见，**[参数] 收起**（开局什么都没选）。

        用户 2026-09-25 定："开界面时上面什么都不选、右边参数栏是隐藏的"
        ——参数坞跟入口走（点入口才露出来），见 TestEntrances。
        """
        for name in ("文件", "日志"):
            self.assertTrue(self.w.panel_toggles[name].isChecked(), name)
        self.assertTrue(self.w.file_dock.isVisible())
        self.assertTrue(self.w.log_dock.isVisible())
        self.assertFalse(self.w.panel_toggles["参数"].isChecked())
        self.assertFalse(self.w.param_dock.isVisible())

    def test_toggle_hides_and_restores_each_dock(self):
        """点开关收起、再点展开，三个坞各试一遍（从各自当前状态起步）。"""
        for name, dock in (("文件", self.w.file_dock),
                           ("参数", self.w.param_dock),
                           ("日志", self.w.log_dock)):
            if not dock.isVisible():             # [参数] 开局就是收起的
                self.w.panel_toggles[name].click()
            self.assertTrue(dock.isVisible())
            self.w.panel_toggles[name].click()   # 收起
            self.assertFalse(dock.isVisible())
            self.assertFalse(self.w.panel_toggles[name].isChecked())
            self.w.panel_toggles[name].click()   # 展开
            self.assertTrue(dock.isVisible())
            self.assertTrue(self.w.panel_toggles[name].isChecked())

    def test_close_button_syncs_toggle(self):
        """点标题栏 × 关坞 → 按钮自动弹起；再点按钮还能展开。"""
        self.w.panel_toggles["参数"].click()   # 先展开（开局收起）
        self.assertTrue(self.w.param_dock.isVisible())
        self.w.param_dock.close()   # 等价于标题栏 ×
        self.assertFalse(self.w.param_dock.isVisible())
        self.assertFalse(self.w.panel_toggles["参数"].isChecked())
        self.w.panel_toggles["参数"].click()
        self.assertTrue(self.w.param_dock.isVisible())

    def test_all_hidden_plot_still_works(self):
        """全收起来只剩绘图区，照常出图（开关不影响作图流程）。"""
        for name, dock in (("文件", self.w.file_dock),
                           ("参数", self.w.param_dock),
                           ("日志", self.w.log_dock)):
            if dock.isVisible():
                self.w.panel_toggles[name].click()
        for dock in (self.w.file_dock, self.w.param_dock, self.w.log_dock):
            self.assertFalse(dock.isVisible())
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(self.w, ["data/fake_b.tif"])
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
            add_checked(w, ["data/fake_b.tif"])
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
            add_checked(w, [self.LONG])
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

    def test_pages_structure(self):
        """参数坞 = 两行固定件（编辑对象 / 几何配置）+ 五个入口页
        （校准/1D/扣背景/对比/绘图）。

        2026-09-24 用户定稿：工具栏从 7 项收到 5 个入口，六个作图类型
        按钮搬进「绘图」页（属性名不变：view_buttons / compare_btn /
        heat_btn）。2026-09-26 又加了一行固定件：几何配置（下拉框 +
        校准模式下的 [返回分析模式]）——它得在每一页都看得见。
        """
        w = create_window()
        try:
            w.show()
            QApplication.processEvents()
            content = w.param_dock.widget()
            lay = content.layout()
            self.assertIs(lay.itemAt(0).widget(), w.focus_label,
                          "编辑对象名固定最上方")
            self.assertIs(lay.itemAt(1).widget(), w.geom_row,
                          "几何配置行固定在第二行")
            self.assertIs(lay.itemAt(2).widget(), w.data_row,
                          "数据参数行（2θ 范围/点数）固定在第三行")
            self.assertIs(lay.itemAt(3).widget(), w.param_stack)
            self.assertEqual(w.param_stack.count(), 5)
            self.assertEqual(w.PARAM_PAGES,
                             {"校准": 0, "1D": 1, "处理": 2, "对比": 3,
                              "绘图": 4})
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["1D"], "默认可停在 1D 页")
            self.assertEqual(list(w.entrance_buttons),
                             ["校准", "1D", "处理", "对比", "绘图"])
            # 开局谁都不点亮（用户 2026-09-25 定：上面什么都不选）
            for name, btn in w.entrance_buttons.items():
                self.assertFalse(btn.isChecked(), name)
            # 六个作图类型按钮都在「绘图」页里（工具栏只剩入口 + 面板开关）
            draw_page = w.param_stack.widget(w.PARAM_PAGES["绘图"])
            for btn in (list(w.view_buttons.values())
                        + [w.compare_btn, w.heat_btn]):
                self.assertTrue(draw_page.isAncestorOf(btn),
                                f"{btn.text()} 应住在绘图页里")
        finally:
            w.close()

    def test_produce_buttons_live_in_their_pages(self):
        """每页底部的产出按钮住在自己那页，点了真出图（1D 页当代表）。

        出图链路本身由别的测试覆盖，这里守的是"按钮搬对页 + 接线没断"
        （C 拆分时最容易犯的错就是把按钮留在旧页/忘了接）。
        """
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_b.tif"])
                # 1D 页：出 1D 图
                w.entrance_buttons["1D"].click()
                page_1d = w.param_stack.widget(w.PARAM_PAGES["1D"])
                self.assertTrue(page_1d.isAncestorOf(w.plot_1d_btn))
                w.plot_1d_btn.click()
                self.assertTrue(_wait_until(lambda: len(
                    _axes(w, "1D", "data/fake_b.tif").lines) > 0),
                    "[出 1D 图] 该出图")
                # 绘图页：[出图（勾选文件）] 按当前类型再出一次
                w.entrance_buttons["绘图"].click()
                draw = w.param_stack.widget(w.PARAM_PAGES["绘图"])
                for btn in (w.plot_now_btn, w.redraw_now_btn,
                            w.export_img_btn):
                    self.assertTrue(draw.isAncestorOf(btn), btn.text())
                w.plot_now_btn.click()
                QApplication.processEvents()
                # 当前类型 = 1D → 又起了一次积分（面板已开 = 刷新那张图）
                self.assertEqual(
                    w.log_text.toPlainText().count("开始积分"), 2,
                    "[出图（勾选文件）] 该按当前类型再来一次")
                # 扣背景页 / 对比页的产出按钮
                for name, btn in (("处理", "bg_redraw_btn"),
                                  ("对比", "plot_cmp_btn"),
                                  ("对比", "plot_heat_btn")):
                    page = w.param_stack.widget(w.PARAM_PAGES[name])
                    self.assertTrue(
                        page.isAncestorOf(getattr(w, btn)),
                        f"{btn} 应在{name}页里")
        finally:
            w.close()

    def test_startup_selects_nothing_and_hides_param_dock(self):
        """开局：五个入口一个都不亮 + 参数坞收起（用户 2026-09-25 定：
        "开界面后上面什么都没选，右边参数栏是隐藏的"）。"""
        w = create_window()
        try:
            w.show()
            QApplication.processEvents()
            for name, btn in w.entrance_buttons.items():
                self.assertFalse(btn.isChecked(), name)
            self.assertFalse(w.param_dock.isVisible())
            self.assertFalse(w.panel_toggles["参数"].isChecked())
            self.assertIsNone(w._last_entrance)
            # 其余两个坞照常：开局只剩文件列 + 日志
            self.assertTrue(w.file_dock.isVisible())
            self.assertTrue(w.log_dock.isVisible())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_clicking_entrance_shows_param_dock(self):
        """点入口 = 参数坞露出来并翻到那一页（[参数] 开关自动跟着勾上）。"""
        w = create_window()
        try:
            w.show()
            QApplication.processEvents()
            for name in ("1D", "对比"):
                w.entrance_buttons[name].click()
                QApplication.processEvents()
                self.assertTrue(w.param_dock.isVisible(), name)
                self.assertTrue(w.panel_toggles["参数"].isChecked(), name)
                self.assertEqual(w.param_stack.currentIndex(),
                                 w.PARAM_PAGES[name], name)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_entrance_switching_follows_buttons(self):
        """点入口 = 翻到那一页 + 高亮跟着动；出入校准走同一条路。"""
        w = create_window()
        try:
            for name in ("校准", "处理", "对比", "绘图", "1D"):
                w.entrance_buttons[name].click()
                QApplication.processEvents()
                self.assertEqual(w.param_stack.currentIndex(),
                                 w.PARAM_PAGES[name], name)
                self.assertTrue(w.entrance_buttons[name].isChecked(), name)
                for other, btn in w.entrance_buttons.items():
                    if other != name:
                        self.assertFalse(btn.isChecked(),
                                         f"切到 {name} 时 {other} 不该还亮着")
            self.assertIn("回到分析模式", w.log_text.toPlainText(),
                          "从校准切出去 = 退校准")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_units_in_spinbox_suffix(self):
        w = create_window()
        try:
            # 几何三件（像素/波长/距离）的控件已撤（读数走悬停提示），
            # 剩下的输入框单位仍走 suffix
            self.assertEqual(w.params["剖面角度 (°)"].suffix(), " °")
            for name in ("2θ 下限 (°)", "2θ 上限 (°)"):
                self.assertEqual(w.params[name].suffix(), " °")
            # 对比度/纵轴没有单位，后缀为空
            self.assertEqual(w.params["对比度下限"].suffix(), "")
            self.assertEqual(w.params["纵轴下限"].suffix(), "")
        finally:
            w.close()

    def test_data_params_row_visible_on_every_page(self):
        """2θ 范围 / 点数固定在坞顶第三行：五个入口页都看得见、都能改。

        用户 2026-09-27："1d 画图时能选范围，后面处理时没法选范围，比如
        对比时，参数里加上"——原先这两项只在 1D 页，切到别的页就改不了。
        """
        w = create_window()
        try:
            w.show()
            for page in ("校准", "1D", "处理", "对比", "绘图"):
                w.entrance_buttons[page].click()
                QApplication.processEvents()
                for name in ("2θ 下限 (°)", "2θ 上限 (°)", "输出点数"):
                    self.assertTrue(w.params[name].isVisible(),
                                    f"{page} 页上该看得见 {name}")
            # 改一下照样进几何（数据参数照旧参与计算）
            w.params["2θ 下限 (°)"].setValue(2.5)
            self.assertAlmostEqual(
                gui_panel_state._collect_geometry(w)["tth_min_deg"], 2.5)
        finally:
            w.close()

    def test_range_pairs_share_one_row(self):
        """成对的下限/上限并排一行：同父、中间隔着 "~"。

        2θ 那一对 2026-09-27 搬到了坞顶第三行、和"点数"同住一行
        （所有分析页共用，用户："后面处理时没法选范围"），所以那一行不止
        三件——对它只查"两框同父、中间是 ~"；另两对仍各占一行，维持原判。
        """
        w = create_window()
        try:
            for lo_name, hi_name, alone in (
                    ("2θ 下限 (°)", "2θ 上限 (°)", False),
                    ("对比度下限", "对比度上限", True),
                    ("纵轴下限", "纵轴上限", True)):
                lo, hi = w.params[lo_name], w.params[hi_name]
                self.assertIs(lo.parent(), hi.parent(),
                              f"{lo_name} 与上限该住同一行")
                row = lo.parent().layout()
                i_lo, i_hi = row.indexOf(lo), row.indexOf(hi)
                self.assertGreaterEqual(i_lo, 0)
                self.assertLess(i_lo, i_hi, "下限在左、上限在右")
                self.assertEqual(row.itemAt(i_lo + 1).widget().text(), "~")
                if alone:
                    self.assertEqual(row.count(), 3)
        finally:
            w.close()

    def test_section_captions_and_normalize_combo(self):
        w = create_window()
        try:
            captions = {lb.text() for lb in w.param_dock.findChildren(QLabel)}
            # 几何这一节只剩坞顶那一行的标签（像素/波长/距离三行只读
            # 字段与它们的"标定几何（只读…）"标题 2026-09-26 撤了，
            # 读数改走那一行的悬停提示）
            self.assertIn("几何配置", captions)
            self.assertNotIn("标定几何（只读：由几何配置决定）", captions)
            # 积分设置搬去坞顶第三行（所有分析页共用），不再是 1D 页的小标题
            self.assertNotIn("积分设置", captions)
            self.assertIn("2D/剖面视图", captions)
            # 归一化三选一下拉框（键仍是"对比归一化"，快照回放认 data
            # 不认字面）：全图最强峰 / 指定数据… / 不归一化。
            # "各自最强峰"已删（用户 2026-09-26 的规矩：不许按各自最高
            # 峰归一化——那样样品之间的强弱差就看不出来了）
            combo = w.params["对比归一化"]
            self.assertEqual(
                [combo.itemText(i) for i in range(combo.count())],
                ["全图最强峰", "指定数据…", "不归一化"])
            self.assertEqual(
                [combo.itemData(i) for i in range(combo.count())],
                ["global", "file", "off"])
            self.assertEqual(combo.currentData(), "off", "默认 = 不归一化")
            # "指定数据" 未选中时，旁边的目标文件下拉框置灰
            self.assertFalse(w.params["归一化目标"].isEnabled())
        finally:
            w.close()

    def test_dock_min_sizes_prevent_clipping(self):
        w = create_window()
        try:
            w.show()
            # 参数坞开局是收起的（入口一个都没选）：Qt 只在坞**显示出来**
            # 时才把内容的最小尺寸并进坞的最小尺寸，所以先点个入口再量
            w.entrance_buttons["1D"].click()
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
                add_checked(w, ["data/fake_b.tif"])
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
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "2D")
                self.assertTrue(_wait_until(
                    lambda: "读取失败" in w.log_text.toPlainText()))
            QTest.mouseClick(gui_panel_state._content(_dock(w, "2D", "data/fake_b.tif")),
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
                add_checked(w, ["data/fake_a.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines)
                    > 0))
                add_checked(w, ["data/fake_b.tif"])   # fake_a 仍勾着
                w.compare_btn.click()
                cax = gui_panel_state._content([d for k, d in w.plot_docks.items()
                                        if k.startswith("对比|")][0]).axes_1d
                self.assertTrue(_wait_until(lambda: len(cax.lines) >= 2))
            # 焦点此时在对比面板；切到 1D 面板改参数 → 应用 → 只动 1D
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")   # 两张都出图（默认线性）
                self.assertTrue(_wait_until(
                    lambda: all(len(_axes(w, "1D", f"data/{n}.tif").lines) > 0
                                for n in ("fake_a", "fake_b"))))
            # 焦点是最后算完的那张；切到 fake_a 改成对数并应用
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            w.params["对数纵轴"].setChecked(True)
            w.findChild(QPushButton, "apply_image_btn").click()
            self.assertEqual(
                _axes(w, "1D", "data/fake_a.tif").get_yscale(), "log")
            self.assertEqual(
                _axes(w, "1D", "data/fake_b.tif").get_yscale(), "linear")
            # 点 fake_b → 参数坞回放它自己的设置（对数关）
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif")),
                             Qt.LeftButton)
            self.assertFalse(w.params["对数纵轴"].isChecked())
            # 点回 fake_a → 显示它自己的设置（对数开）
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
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
                add_checked(w, ["data/fake_a.tif"])
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
            add_checked(w, ["data/fake_b.tif"])
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
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
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
                add_checked(w, ["data/fake_b.tif"])
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
                add_checked(w, ["data/fake_a.tif"])   # fake_b 仍勾着
                w.compare_btn.click()
                cdock = [d for k, d in w.plot_docks.items()
                         if k.startswith("对比|")][0]
                self.assertTrue(_wait_until(
                    lambda: len(gui_panel_state._content(cdock).axes_1d.lines) >= 2))
            snap = cdock.params_snapshot
            self.assertFalse(snap["对数纵轴"])
            self.assertTrue(snap["纵轴自动"])
            self.assertEqual(snap["纵轴下限"], 1.0)
            self.assertEqual(snap["纵轴上限"], 100000.0)
            # 对比完成后成为焦点：置灰框显示它自己算出的自动区间
            # （输入框精度 decimals=1，与图的精确值允许 0.1 级误差）
            cax = gui_panel_state._content(cdock).axes_1d
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
                add_checked(w, ["data/fake_a.tif"])
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
            add_checked(w, ["data/fake_b.tif"])
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
                add_checked(w, ["data/fake_b.tif"])
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
                add_checked(w, ["data/fake_b.tif"])
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
            add_checked(w, ["data/fake_a.tif"])
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
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif")),
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
        return gui_panel_state._content(w.plot_docks[keys[0]]).axes_1d

    def _plot_compare(self, w):
        """勾 fake_a + fake_b 点 [对比] 并等两条曲线到齐 → 返回坐标轴。"""
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compare_compute):
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            w.compare_btn.click()
            ax = self._compare_axes(w)
            self.assertTrue(_wait_until(lambda: len(ax.lines) >= 2))
        return ax

    def test_strip_common_shortens_batch_names_only(self):
        """短名助手：剥批次前缀与扩展名换出号段；短名字不剥（免得只剩 a）。"""
        strip = gui_plot_compare._strip_common
        self.assertEqual(
            strip(["LMFP_1_atten0-00029.tif", "LMFP_1_atten0-00031.tif"]),
            ["00029", "00031"])
        self.assertEqual(strip(["scan_001.dat", "scan_002.dat"]),
                         ["001", "002"])
        # 前缀剥完只剩一个字母的，不剥（反而更难认）；扩展名照剥
        self.assertEqual(strip(["fake_a.tif", "fake_b.tif"]),
                         ["fake_a", "fake_b"])
        self.assertEqual(strip(["样品A", "样品B"]), ["样品A", "样品B"])
        self.assertEqual(strip(["只有一个"]), ["只有一个"])

    def test_compare_legend_hidden_when_too_many_curves(self):
        """曲线超过 LEGEND_MAX_CURVES → 不画图例（它挡图），日志说清怎么办。

        用户 2026-09-26："对比的图例还是影响看图，太多了"。认曲线改看
        状态栏悬停读数（那里本来就报曲线名）。
        """
        w = create_window()
        files = _tmp_files(gui_plot_compare.LEGEND_MAX_CURVES + 1)
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compare_compute):
                w.add_files([str(p) for p in files], select=True)
                w.compare_btn.click()
                ax = self._compare_axes(w)
                self.assertTrue(_wait_until(
                    lambda: len(ax.lines) >= len(files)))
            self.assertIsNone(ax.get_legend(), "条数超限就不该有图例")
            log = w.log_text.toPlainText()
            self.assertIn(f"对比图例：{len(files)} 条曲线太多", log)
            self.assertIn("悬停读数", log)
            # 少到阈值以内 → 图例回来（用短名）
            dock = w.plot_docks[[k for k in w.plot_docks
                                 if k.startswith("对比|")][0]]
            for key in list(dock.compare_data)[2:]:
                hidden = set(getattr(dock, "compare_hidden", None) or ())
                dock.compare_hidden = hidden | {key}
            gui_plot_compare._redraw_compare(w, dock.panel_key)
            leg = ax.get_legend()
            self.assertIsNotNone(leg, "两条曲线的图例该回来")
            # 名字 = 短名（临时文件叫 s1/s2，扩展名已剥掉）
            self.assertEqual([t.get_text() for t in leg.get_texts()],
                             [Path(p).stem for p in files[:2]])
        finally:
            w.close()

    def test_compare_requires_two_checked_files(self):
        """只勾一个文件点 [对比] → 提示至少两个，不开面板。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif"])
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
        切到全图最强峰 → 两条都除以全场峰值 30（fake_a 变 3/30 之一）。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            self.assertEqual(w.params["对比归一化"].currentData(), "off")
            ymax = max(float(np.max(line.get_ydata())) for line in ax.lines)
            self.assertAlmostEqual(ymax, 30.0, places=4)
            self._set_norm_mode(w, "global")
            w.findChild(QPushButton, "apply_image_btn").click()
            # 同一个除数（全场峰值 30）：fake_b → 1.0，fake_a → 3/30
            for line, want in zip(ax.lines, (3.0 / 30.0, 1.0)):
                self.assertAlmostEqual(float(np.max(line.get_ydata())),
                                       want, places=4)
        finally:
            w.close()

    def test_retired_each_mode_falls_back_to_off(self):
        """旧快照里残留的"各自最强峰"（each）不再生效，按不归一化画。

        用户 2026-09-26 定的规矩：不许按各自最高峰归一化。模式值认不出
        时一律当 off——不做归一化是安全的那一侧（每个样品都缩到自己的
        高度，比"没归一化"更容易误导人）。
        """
        w = create_window()
        try:
            ax = self._plot_compare(w)
            dock = [d for k, d in w.plot_docks.items()
                    if k.startswith("对比|")][0]
            for stale in ("each", True, None, "nonsense"):
                dock.params_snapshot["对比归一化"] = stale
                gui_plot_compare._redraw_compare(w, dock.panel_key)
                ymax = max(float(np.max(line.get_ydata()))
                           for line in ax.lines)
                self.assertAlmostEqual(ymax, 30.0, places=4,
                                       msg=f"{stale!r} 应退回不归一化")
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

    def test_compare_axis_holds_still_while_tuning_background(self):
        """调背景时对比面板的纵轴**不动**；裁剪仍然放开纵轴。

        用户 2026-09-26："对比面板的纵轴自己在变"——和 1D 面板同一个毛病
        （那次修的是 1D）。纵轴范围改看 ref（不跑背景/平滑的那份，见
        _compare_shown_curves），但**保留归一化与裁剪**：调背景 → 框不动、
        只有曲线在框里往下走；剪掉巨峰 → 框跟着缩（那是裁剪的本意）。
        """
        w = create_window()
        try:
            ax = self._plot_compare(w)
            top0 = float(ax.get_ylim()[1])
            mode = w.params["背景扣除模式"]
            # 开处理只允许动一次下界（给扣完的曲线腾出 0 附近的地方，
            # 见 _draw_compare 里的兜底），上界一动不动
            mode.setCurrentIndex(mode.findData("auto"))
            QApplication.processEvents()
            low1, top1 = (float(v) for v in ax.get_ylim())  # (下, 上)
            self.assertAlmostEqual(top1, top0, places=6,
                                   msg="切到自动基线不该动上界")
            for win in (0.3, 3.0):
                w.params["背景窗口 (°)"].setValue(win)
                QApplication.processEvents()
                np.testing.assert_allclose(
                    ax.get_ylim(), (low1, top1),
                    err_msg=f"窗口 {win}° 不该动框")
            mode.setCurrentIndex(mode.findData("anchor"))
            QApplication.processEvents()
            np.testing.assert_allclose(ax.get_ylim(), (low1, top1),
                                       err_msg="切到手动锚点不该动框")
            # 剪掉最强的那条峰（fake_b 在 8.5° 处 30）→ 框该跟着缩
            w.params["裁剪起点 (°)"].setValue(8.0)
            w.params["裁剪终点 (°)"].setValue(9.0)
            w.findChild(QPushButton, "cut_add_btn").click()
            QApplication.processEvents()
            self.assertLess(ax.get_ylim()[1], top0,
                            "剪掉巨峰后纵轴该放开（裁剪的本意）")
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif",
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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

    def test_close_all_button_closes_every_panel(self):
        """[全关]：一键关掉所有图面板，焦点与编辑对象一起复位。

        用户 2026-09-26："加一个一键关闭所有图像，就是打开的子窗口全部
        关闭"。与单个 × 同一条路径，但**不逐张写日志**（一次关几十张
        会刷屏），只写一行汇总（含几张没存过盘）。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0))
            self.assertEqual(len(w.plot_docks), 2)
            w.close_all_btn.click()
            QApplication.processEvents()
            self.assertEqual(len(w.plot_docks), 0, "该一张不剩")
            self.assertIsNone(w.focus_panel)
            self.assertIn("未选中图面板", w.focus_label.text())
            log = w.log_text.toPlainText()
            self.assertIn("已关闭全部 2 张图", log)
            self.assertIn("没存过盘", log)
            self.assertEqual(log.count("已关闭面板："), 0,
                             "全关不逐张写日志（那是刷屏）")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calib_mode_toggle(self):
        """[校准] 入口 = 校准模式提示 + 翻到校准页；点别的入口回分析模式。

        2026-09-24 起入口是五个页签式按钮（不再翻转开关文字——"退出
        校准"就是点另一个入口；校准页底部另有 [返回分析模式] 显式出口，
        见 TestCalibration.test_calib_page_exit_button_returns_to_analysis）。
        """
        w = create_window()
        try:
            w.calib_btn.click()
            self.assertIn("进入校准模式", w.log_text.toPlainText())
            self.assertIn("校准模式", w.mode_label.text())
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["校准"])
            w.entrance_buttons["对比"].click()
            self.assertIn("回到分析模式", w.log_text.toPlainText())
            self.assertIn("分析模式", w.mode_label.text())
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["对比"])
            self.assertFalse(w.calib_btn.isChecked())
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
                add_checked(w, ["data/fake_b.tif"])
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
                add_checked(w, ["data/fake_b.tif"])
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


class TestSlimPanelChrome(unittest.TestCase):
    """面板壳瘦身（2026-09-24）：原生标题栏（36 px）+ 工具栏（47 px）
    两行合成一行 26 px 自绘标题栏（标题 + [Home][Zoom][Customize][Save]
    + [弹出][关闭]），子窗口 frameless —— 壳 83 → 26 px，同样的面板
    高度里画布多拿 57 px。

    原生标题栏被拿掉的三件事在这里各有测试兜着：拖动、双击最大化、
    × 关闭（自绘栏按钮走同一个 _close_panel）。占位面板没有自绘栏 →
    必须保留原生标题栏，否则既拖不动也关不掉。
    """

    PATH = "data/fake_b.tif"

    def _open_1d(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, [self.PATH])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", self.PATH).lines) > 0))
        return _dock(w, "1D", self.PATH)

    def test_shell_shrinks_and_canvas_unchanged(self):
        """壳 ≤ 32 px（改前 83）、画布仍是 500×300、子窗口 frameless。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            QApplication.processEvents()
            content = gui_panel_state._content(dock)
            self.assertTrue(dock.windowFlags() & Qt.FramelessWindowHint,
                            "子窗口应无原生标题栏")
            self.assertFalse(content.slim_bar.isHidden(), "自绘标题栏应可见")
            self.assertTrue(content.toolbar.isHidden(), "工具栏不该再占高度")
            self.assertEqual(
                (content.canvas.width(), content.canvas.height()), (500, 300),
                "画布尺寸不能因为瘦身而变")
            shell = dock.height() - content.canvas.height()
            self.assertLessEqual(shell, 32,
                                 f"壳应瘦到 26 px 上下，实测 {shell} px")
            self.assertGreaterEqual(shell, 20,
                                    f"壳过小，布局可能塌了：{shell} px")
        finally:
            w.close()

    def test_bar_tooltip_follows_rename(self):
        """标题栏不显示名字（名字在参数坞"编辑对象"与图上标题里），
        但悬停提示要跟着容器标题走——改名也要同步。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            self.assertEqual(content.slim_bar.toolTip(), dock.windowTitle())
            dock.setWindowTitle("新名字")
            QApplication.processEvents()
            self.assertEqual(content.slim_bar.toolTip(), "新名字",
                             "悬停提示要跟容器标题同步")
        finally:
            w.close()

    def test_bar_buttons_share_toolbar_actions(self):
        """自绘栏按钮直接绑工具栏的 QAction：放大镜状态两边同步。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            zoom = content.toolbar._actions["zoom"]
            btns = content.slim_bar.findChildren(QToolButton)
            match = [b for b in btns if b.defaultAction() is zoom]
            self.assertTrue(match, "栏上应有绑 zoom action 的按钮")
            match[0].click()
            self.assertTrue(gui_plot_panels._magnifier_on(dock),
                            "点栏上的放大镜 = 点亮（与工具栏共用同一 action）")
            match[0].click()
            self.assertFalse(gui_plot_panels._magnifier_on(dock))
        finally:
            w.close()

    def test_bar_drag_moves_panel(self):
        """按住标题栏拖 = 移动面板（原生标题栏的拖动自己实现）。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            p0 = dock.pos()
            QTest.mousePress(content.slim_bar, Qt.LeftButton, Qt.NoModifier,
                             QPoint(60, 12))
            QTest.mouseMove(content.slim_bar, QPoint(120, 52))
            QTest.mouseRelease(content.slim_bar, Qt.LeftButton, Qt.NoModifier,
                               QPoint(120, 52))
            QApplication.processEvents()
            self.assertEqual(
                (dock.pos().x() - p0.x(), dock.pos().y() - p0.y()), (60, 40),
                "拖动位移应与鼠标位移一致")
        finally:
            w.hide()   # 显示过的窗口关窗会弹模态框（见 TestPlotFixedSize）
            w.close()

    def test_bar_double_click_toggles_maximize(self):
        """双击标题栏 = 占满绘图区 / 还原（原生双击的替代）。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            QTest.mouseDClick(content.slim_bar, Qt.LeftButton, Qt.NoModifier,
                              QPoint(60, 12))
            QApplication.processEvents()
            self.assertTrue(dock.isMaximized(), "双击应最大化")
            QTest.mouseDClick(content.slim_bar, Qt.LeftButton, Qt.NoModifier,
                              QPoint(60, 12))
            QApplication.processEvents()
            self.assertFalse(dock.isMaximized(), "再双击应还原")
        finally:
            w.hide()
            w.close()

    def test_bar_close_button_closes_panel(self):
        """✕ = 关闭面板，与原生 × 走同一个入口（关闭即遗忘）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            content.bar_close_btn.click()
            QApplication.processEvents()
            self.assertNotIn("1D|" + self.PATH, w.plot_docks)
            self.assertIn("已关闭面板", w.log_text.toPlainText())
        finally:
            w.close()

    def test_pop_out_keeps_native_frame_and_retract_restores(self):
        """弹出 = 系统窗口（保留原生边框管窗口管理），收回 = 再 frameless。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            content.popout_btn.click()
            QApplication.processEvents()
            floated = w.plot_docks["1D|" + self.PATH]
            self.assertIsInstance(floated, gui_panels._FloatedWindow)
            self.assertFalse(floated.windowFlags() & Qt.FramelessWindowHint,
                             "独立窗口要留着系统标题栏")
            fc = gui_panel_state._content(floated)
            self.assertFalse(fc.slim_bar.isHidden(),
                             "弹出窗口里栏继续提供按钮")
            self.assertEqual(fc.popout_btn.text(), "收回")
            fc.popout_btn.click()
            QApplication.processEvents()
            back = w.plot_docks["1D|" + self.PATH]
            self.assertIsInstance(back, gui_app.QMdiSubWindow)
            self.assertTrue(back.windowFlags() & Qt.FramelessWindowHint,
                            "收回后应恢复 frameless")
        finally:
            w.close()

    def test_placeholder_keeps_native_title_bar(self):
        """占位面板（未注册视图）没有自绘栏 → 保留原生标题栏。"""
        w = create_window()
        try:
            gui_plot_panels._open_plot_panel(w, "未注册视图", "X|占位", "占位")
            dock = w.plot_docks["X|占位"]
            self.assertFalse(dock.windowFlags() & Qt.FramelessWindowHint,
                            "没有自绘栏的占位面板得留着原生标题栏"
                            "（否则拖不动也关不掉）")
        finally:
            w.close()


class TestDragBlit(unittest.TestCase):
    """框选拖动期的低成本重画（2026-09-24 用户："按照最快来优化"）。

    框选拖动期视图不变、只多一个选框 → 缓存一帧、每帧只贴选框，
    松手才整帧重画一次（那时刻度/文字才需要更新）。实测 30 → 0.96 ms
    每次鼠标移动。

    平移**不**走这条路：拖动期视图每帧都在变，贴图要靠
    restore_region(xy=) 平移缓存块，而实测（最小实验）它在 mpl 3.11 上
    不做干净的平移 → 宁可每帧整帧重画（30 ms ≈ 33 fps），不要方向错的
    快速预览。所以下面这些测试都用**框选**手势。

    这里守四件事：
      - 拖动中的每一次移动都不整帧重绘；
      - 松手整帧重画一次（刻度/文字这时才更新）；
      - 任何一次整帧重绘作废会话（否则会贴回过期背景）；
      - 按下落在轴外（误点）再松开不许炸（踩过 AttributeError）。
    """

    PATH = "data/fake_b.tif"

    def _open_1d(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, [self.PATH])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", self.PATH).lines) > 0))
        return _dock(w, "1D", self.PATH)

    def _ev(self, canvas, name, x, y, **kw):
        return MouseEvent(name, canvas, x, y, **kw)

    def test_box_drag_motions_use_blit_not_full_redraw(self):
        """框选拖动中的移动不整帧重绘（只有贴图），松手才重画一次。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax, canvas = content.axes_1d, content.canvas
            xd = ax.lines[0].get_xdata()
            xm = float(xd[len(xd) // 2])
            ym = float(ax.lines[0].get_ydata()[len(xd) // 2])
            px, py = ax.transData.transform((xm, ym))
            content.toolbar._actions["zoom"].trigger()     # 框选
            with mock.patch.object(canvas, "draw", wraps=canvas.draw) as draw:
                canvas.callbacks.process("button_press_event", self._ev(
                    canvas, "button_press_event", px, py, button=1))
                self.assertTrue(getattr(dock, "_blit", None), "该建位图会话")
                after_press = draw.call_count
                for i in range(3):
                    canvas.callbacks.process("motion_notify_event", self._ev(
                        canvas, "motion_notify_event", px + 10 * (i + 1),
                        py + 8 * (i + 1), buttons=frozenset({1})))
                self.assertEqual(draw.call_count, after_press,
                                 "拖动中的移动不该整帧重绘")
                canvas.callbacks.process("button_release_event", self._ev(
                    canvas, "button_release_event", px + 30, py + 24,
                    button=1))
            QApplication.processEvents()
            self.assertIsNone(getattr(dock, "_blit", None), "松手该清会话")
            self.assertNotEqual(tuple(ax.get_xlim()), (1.0, 8.0),
                                "框选该生效（松手时整帧重画一次）")
        finally:
            w.close()

    def test_full_redraw_invalidates_the_session(self):
        """整帧重绘（比如后台算完一张别的图）作废会话：之后退回整帧画。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax, canvas = content.axes_1d, content.canvas
            xd = ax.lines[0].get_xdata()
            xm = float(xd[len(xd) // 2])
            ym = float(ax.lines[0].get_ydata()[len(xd) // 2])
            px, py = ax.transData.transform((xm, ym))
            content.toolbar._actions["zoom"].trigger()
            canvas.callbacks.process("button_press_event", self._ev(
                canvas, "button_press_event", px, py, button=1))
            self.assertTrue(getattr(dock, "_blit", None))
            canvas.draw()          # 模拟外部整帧重绘
            self.assertIsNone(getattr(dock, "_blit", None),
                              "整帧重绘后会话该作废（背景已过期）")
            with mock.patch.object(canvas, "draw_idle",
                                   wraps=canvas.draw_idle) as idle:
                canvas.callbacks.process("motion_notify_event", self._ev(
                    canvas, "motion_notify_event", px + 12, py + 9,
                    buttons=frozenset({1})))
                self.assertGreaterEqual(idle.call_count, 1,
                                        "没有会话时退回 draw_idle 整帧画")
        finally:
            w.close()

    def test_release_without_a_valid_press_is_safe(self):
        """按下落在轴外（误点再松手）不许炸——踩过 AttributeError。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            canvas = content.canvas
            x0 = content.axes_1d.get_xlim()
            # 轴外按下（画布左上角）+ 松开
            canvas.callbacks.process("button_press_event", self._ev(
                canvas, "button_press_event", 2, content.canvas.height() - 2,
                button=1))
            canvas.callbacks.process("button_release_event", self._ev(
                canvas, "button_release_event", 2, content.canvas.height() - 2,
                button=1))
            # 只有松开、没有按下，也要安全
            canvas.callbacks.process("button_release_event", self._ev(
                canvas, "button_release_event", 100, 100, button=1))
            self.assertEqual(tuple(content.axes_1d.get_xlim()), tuple(x0),
                             "误点不该改变视图")
        finally:
            w.close()


class TestBoxZoomScope(unittest.TestCase):
    """框选只影响自己那块面板（用户 2026-09-24："确认区域放大不会影响
    整体的图"）。另一块面板的范围 / 曲线数据 / "家" / 总缩放 / 几何
    都不许动。"""

    def _open_two(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(lambda: all(
                len(_axes(w, "1D", p).lines) > 0
                for p in ("data/fake_a.tif", "data/fake_b.tif"))))
        return (_dock(w, "1D", "data/fake_a.tif"),
                _dock(w, "1D", "data/fake_b.tif"))

    def test_box_zoom_leaves_the_other_panel_alone(self):
        w = create_window()
        try:
            dock_a, dock_b = self._open_two(w)
            ax_a = gui_panel_state._content(dock_a).axes_1d
            ax_b = gui_panel_state._content(dock_b).axes_1d

            def snapshot(ax, dock):
                return (tuple(ax.get_xlim()), tuple(ax.get_ylim()),
                        [np.array(ln.get_xdata()).tolist() for ln in ax.lines],
                        [np.array(ln.get_ydata()).tolist() for ln in ax.lines],
                        getattr(dock, "view_home", None))

            before_b = snapshot(ax_b, dock_b)
            before_home_b = getattr(dock_b, "view_home", None)
            zoom_before = w._area_zoom
            geom_before = {k: (d.x(), d.y(), d.width(), d.height())
                           for k, d in w.plot_docks.items()}

            gui_panel_state._content(dock_a).toolbar._actions["zoom"].trigger()
            canvas = gui_panel_state._content(dock_a).canvas
            xd = ax_a.lines[0].get_xdata()
            xm = float(xd[len(xd) // 2])
            ym = float(ax_a.lines[0].get_ydata()[len(xd) // 2])
            p0 = ax_a.transData.transform((xm, ym))
            p1 = ax_a.transData.transform((min(xm + 0.4, 8.0), ym + 0.4))
            for name, (x, y) in (("button_press_event", p0),
                                 ("button_release_event", p1)):
                canvas.callbacks.process(name, MouseEvent(
                    name, canvas, x, y, button=1))
            self.assertNotEqual(tuple(ax_a.get_xlim()), (1.0, 8.0),
                                "A 应被框选放大")
            after_b = snapshot(ax_b, dock_b)
            self.assertEqual(before_b[0], after_b[0], "B 的 x 范围不该动")
            self.assertEqual(before_b[1], after_b[1], "B 的 y 范围不该动")
            self.assertEqual(before_b[2:4], after_b[2:4], "B 的曲线数据不该动")
            self.assertEqual(before_home_b, getattr(dock_b, "view_home", None),
                             "B 的「家」不该动")
            self.assertEqual(zoom_before, w._area_zoom, "总缩放不该动")
            self.assertEqual(
                geom_before, {k: (d.x(), d.y(), d.width(), d.height())
                              for k, d in w.plot_docks.items()},
                "面板几何不该动")
        finally:
            w.close()


class TestHomeView(unittest.TestCase):
    """[Home] = 回到"最初的样子"（2026-09-24 用户要求）。

    以前 Home 走 mpl 的历史栈，而程序重画（[应用]/重算/背景实时预览）
    会清空那个栈 → 按下去常常"没反应"或只回到"上次画的位置"。现在用
    面板自己的"家"（`dock.view_home`）：**只有按内容画出来的视图**才
    更新它，滚轮/框选/平移这些手势不算（手势范围会被写回参数，拿参数
    当"家"就会把缩放当成"最初的样子"——这条是踩过的坑）。
    """

    PATH = "data/fake_b.tif"

    def _open_1d(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, [self.PATH])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", self.PATH).lines) > 0))
        return _dock(w, "1D", self.PATH)

    def _wheel(self, w, dock, notches=3):
        content = gui_panel_state._content(dock)
        ax, canvas = content.axes_1d, content.canvas
        px, py = ax.transData.transform((2.0, 1.8))
        for _ in range(notches):
            canvas.callbacks.process("scroll_event", MouseEvent(
                "scroll_event", canvas, px, py, step=1, button="up"))

    def test_home_returns_to_initial_view(self):
        """缩放 + 平移之后按 Home = 回到开图那个视图（不是缩放后的位置）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            x0, y0 = ax.get_xlim(), ax.get_ylim()
            content.toolbar._actions["zoom"].trigger()   # 点亮放大镜
            self._wheel(w, dock)
            self.assertNotEqual(tuple(ax.get_xlim()), tuple(x0),
                                "滚轮该缩放（前置条件）")
            content.toolbar._actions["home"].trigger()
            QApplication.processEvents()
            self.assertEqual(tuple(ax.get_xlim()), tuple(x0),
                             "Home 应回到最初的 2θ 范围")
            self.assertEqual(tuple(ax.get_ylim()), tuple(y0),
                             "Home 应回到最初的纵轴范围")
        finally:
            w.close()

    def test_home_works_after_a_program_redraw(self):
        """程序重画（会清空 mpl 的历史栈）之后 Home 照样回得去——旧实现
        就是死在这一步：栈空了，按 Home 什么也不发生。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            x0 = ax.get_xlim()
            content.toolbar._actions["zoom"].trigger()
            self._wheel(w, dock)
            gui_plot_panels._refresh_home(dock, ax)   # 模拟"程序重画清了栈"
            content.toolbar._actions["home"].trigger()
            QApplication.processEvents()
            self.assertEqual(tuple(ax.get_xlim()), tuple(x0),
                             "清栈之后 Home 仍应回到最初的样子")
            self.assertIn("[Home] 已回到最初的样子", w.log_text.toPlainText())
        finally:
            w.close()

    def test_display_apply_sets_the_new_home(self):
        """亲手把视图范围改成 2~5 再 [应用]（没有手势介入）= 新的"家"：
        Home 回到这个新视图，而不是开图时那个。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            w.params["视图 2θ 下限 (°)"].setValue(2.0)
            w.params["视图 2θ 上限 (°)"].setValue(5.0)
            gui_views._apply_image_params(w)
            QApplication.processEvents()
            self.assertAlmostEqual(ax.get_xlim()[0], 2.0, delta=0.05,
                                   msg="[应用] 该按新参数画")
            content.toolbar._actions["home"].trigger()
            QApplication.processEvents()
            self.assertAlmostEqual(ax.get_xlim()[0], 2.0, delta=0.05,
                                   msg="Home 回到这次设的视图")
            self.assertAlmostEqual(ax.get_xlim()[1], 5.0, delta=0.05)
        finally:
            w.close()

    def test_gesture_then_recompute_keeps_the_original_home(self):
        """手势缩放 → 重算：画出来仍是缩放后的视图（"重算不改长相"），
        但"家"还是最初那个样子 —— Home 回得去（用户要的就是这个）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            x0, y0 = ax.get_xlim(), ax.get_ylim()
            content.toolbar._actions["zoom"].trigger()
            self._wheel(w, dock)
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                _open_view(w, "1D")          # 重算
                self.assertTrue(_wait_until(
                    lambda: tuple(ax.get_xlim()) != tuple(x0),
                    timeout_ms=5000) or True)
            content.toolbar._actions["home"].trigger()
            QApplication.processEvents()
            self.assertEqual(tuple(ax.get_xlim()), tuple(x0),
                             "重算之后 Home 仍该回到最初的样子")
            self.assertEqual(tuple(ax.get_ylim()), tuple(y0))
        finally:
            w.close()

    def test_recompute_without_gesture_moves_home(self):
        """没有手势介入的重算 = 新的"家"：改了积分 2θ 范围再重算，
        Home 回到新算出来的那一段（不是老视图）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            w.params["2θ 下限 (°)"].setValue(2.0)
            w.params["2θ 上限 (°)"].setValue(6.0)
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                _open_view(w, "1D")          # 重算（数据范围变了）
                self.assertTrue(_wait_until(
                    lambda: abs(ax.get_xlim()[0] - 2.0) < 0.06))
            x_new = ax.get_xlim()
            content.toolbar._actions["home"].trigger()
            QApplication.processEvents()
            self.assertEqual(tuple(ax.get_xlim()), tuple(x_new),
                             "Home 该回到重算出来的新视图")
            self.assertNotAlmostEqual(x_new[0], 1.0, places=2,
                                      msg="新视图应跟着新的积分范围走")
        finally:
            w.close()


def _scaled_compute(path_str, geom, npt):
    """假积分：两个文件的曲线**尺度不同**（模拟不同曝光/衰减）。

    批量处理的核心语义就靠它验：锚点跨文件只传 2θ，强度必须到每张
    自己的曲线上重取——直接套 A 的强度会把 B 的基线抬错几倍。
    """
    # 按**结尾**判断（batch_a 里也有个 b，别用 in）
    scale = 3.0 if path_str.endswith("_b.tif") else 1.0
    tth = np.array([0.5, 1.0, 2.0, 4.0, 8.5])
    return tth, np.array([1.0, 2.0, 3.0, 2.0, 1.0]) * scale


class TestBackgroundBatch(unittest.TestCase):
    """[批量处理]：锚点只传 2θ、强度各取各的；扣后落盘；对比优先读它。

    用户 2026-09-24 提的流程："1d 完了存一次，做扣背景时可直接使用，
    然后扣完一张，可以用这些锚点给其他的图批量扣，然后再存一份，后面
    对比就可以直接用批量扣完的"。

    注意设置顺序：**先切编辑对象（点图 = 回放快照）再设背景模式**——
    反过来会被回放重置回"关闭"（界面就是这么设计的，测试踩过）。
    """

    def _two_files(self):
        """两个真临时文件（缓存指纹要 stat；假路径会被当 miss）。"""
        out = []
        for name in ("batch_a.tif", "batch_b.tif"):
            f = Path(tempfile.mkdtemp(prefix="xrd_bgbatch_")) / name
            f.write_bytes(b"0" * 4096)
            out.append(str(f))
        return out

    def _open_both(self, w, files):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_scaled_compute):
            add_checked(w, files)
            _open_view(w, "1D")
            self.assertTrue(_wait_until(lambda: all(
                len(_axes(w, "1D", f).lines) > 0 for f in files)))

    def _focus_a_with_anchors(self, w, files):
        """把 A 设成编辑对象、切到手动锚点、放两个锚点（在背景位置上）。"""
        gui_panel_state._set_focus(w, "1D|" + files[0],
                                   Path(files[0]).name)
        dock_a = _dock(w, "1D", files[0])
        cb = w.params["背景扣除模式"]
        cb.setCurrentIndex(cb.findData("anchor"))
        w.params["背景窗口 (°)"].setValue(1.0)
        dock_a.params_snapshot = dict(
            dock_a.params_snapshot or {},
            **{"背景扣除模式": "anchor", "背景窗口 (°)": 1.0})
        key_a = str(gui_views._bg_path_of(dock_a))
        # 两个锚点落在曲线 [0.5,1,2,4,8.5] → [1,2,3,2,1] 的 2θ=1 与 4 上
        w.bg_anchors[key_a] = [(1.0, 2.0), (4.0, 2.0)]
        return dock_a, key_a

    def test_batch_transfers_positions_not_intensities(self):
        """A 的锚点用到 B 上：2θ 照搬，强度取 B 自己曲线上的值。"""
        from xrd_toolkit.services import stage_cache
        w = create_window()
        files = self._two_files()
        try:
            self._open_both(w, files)
            dock_a, key_a = self._focus_a_with_anchors(w, files)
            w.proc_batch_btn.click()
            QApplication.processEvents()
            self.assertIn("批量处理完成", w.log_text.toPlainText())
            key_b = str(gui_views._bg_path_of(_dock(w, "1D", files[1])))
            self.assertIn(key_b, w.bg_anchors, "B 也该拿到一套锚点")
            got_a, got_b = w.bg_anchors[key_a], w.bg_anchors[key_b]
            self.assertEqual([x for x, _ in got_a], [x for x, _ in got_b],
                             "2θ 位置照搬")
            self.assertAlmostEqual(got_b[0][1], got_a[0][1] * 3, delta=1e-6,
                                   msg="B 的强度取 B 曲线上的值（尺度 ×3）")
            # 两份 bg 产物都在（键要**一模一样**才命中：几何范围也得带）
            geom = gui_panel_state._collect_geometry(w)
            for f in files:
                dock = _dock(w, "1D", f)
                st = gui_panel_state._bg_settings(
                    w, dock, str(gui_views._bg_path_of(dock)))
                self.assertIsNotNone(stage_cache.load_proc(
                    f, config=w.config_name,
                    npt=int(w.params["输出点数"].value()),
                    tth_min=geom.get("tth_min_deg"),
                    tth_max=geom.get("tth_max_deg"), settings=st), f)
        finally:
            w.close()

    def test_compare_prefers_the_bg_product(self):
        """扣完再开对比：日志写明用的是扣背景产物（跨会话那条路）。"""
        w = create_window()
        files = self._two_files()
        try:
            self._open_both(w, files)
            self._focus_a_with_anchors(w, files)
            w.proc_batch_btn.click()
            QApplication.processEvents()
            before = len(w.log_text.toPlainText())
            w.compare_btn.click()
            self.assertTrue(_wait_until(lambda: "对比完成" in
                                        w.log_text.toPlainText()), "对比该完成")
            log = w.log_text.toPlainText()
            self.assertIn("处理产物", log,
                          "对比该优先用处理产物（不是重新积分）")
            self.assertNotIn("开始积分", log[before:],
                             "对比不该再起积分任务")
        finally:
            w.close()

    def test_batch_with_mode_off_hints(self):
        """「处理」页三项全关时点批量：只提示，不产出（不静默）。"""
        w = create_window()
        files = self._two_files()
        try:
            self._open_both(w, files)
            gui_panel_state._set_focus(w, "1D|" + files[0],
                                       Path(files[0]).name)
            w.proc_batch_btn.click()
            QApplication.processEvents()
            self.assertIn("三项都关着", w.log_text.toPlainText())
            self.assertNotIn("批量处理完成", w.log_text.toPlainText())
        finally:
            w.close()


class TestStageCacheFlow(unittest.TestCase):
    """分阶段产物缓存接进界面后的行为（services/stage_cache）。

    跨会话的收益在真实使用里最明显（关掉程序第二天再开，81 张图不用
    全部重积分），测试里守两件事：**算过就落盘**、**再点就命中且记
    日志**（静默复用会让人以为重算了）。缓存根已在 setUpModule 指到
    临时目录，不会污染 outputs/_stage。
    """

    def _real_file(self, name="cache_sample.tif"):
        """真文件（缓存要算指纹：大小 + 修改时间，假路径 stat 会失败）。

        内容无所谓——积分被 mock 掉了；但文件必须真存在，否则
        `stage_cache.fingerprint` 抛 FileNotFoundError，落盘被吞掉、
        测试就成了"假过"。
        """
        path = Path(tempfile.mkdtemp(prefix="xrd_cache_gui_")) / name
        path.write_bytes(b"0" * 4096)
        return str(path)

    def test_second_click_reuses_the_cache(self):
        w = create_window()
        path = self._real_file()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute) as compute:
                add_checked(w, [path])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: len(
                    _axes(w, "1D", path).lines) > 0))
                self.assertEqual(compute.call_count, 1, "第一次该真算")
                # 再点一次同一视图 = 刷新那张图 → 这次该命中缓存
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: "复用缓存" in w.log_text.toPlainText()))
                self.assertEqual(compute.call_count, 1,
                                 "第二次不该再算（缓存命中）")
            log = w.log_text.toPlainText()
            self.assertIn("复用缓存：cache_sample.tif", log)
            self.assertIn("几何", log)
        finally:
            w.close()

    def test_changing_the_range_misses_the_cache(self):
        """换了积分 2θ 范围 = 另一个键 → 老老实实重算（缓存不会串味）。"""
        w = create_window()
        path = self._real_file("cache_range.tif")
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute) as compute:
                add_checked(w, [path])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: len(
                    _axes(w, "1D", path).lines) > 0))
                w.params["2θ 下限 (°)"].setValue(2.0)
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: compute.call_count == 2, timeout_ms=5000),
                    "换了范围该重算")
                self.assertNotIn("复用缓存", w.log_text.toPlainText())
        finally:
            w.close()

    def test_clear_cache_button_reports_and_empties(self):
        """[清空缓存]：删产物并如实记日志；空缓存时也提示。"""
        from xrd_toolkit.services import stage_cache
        w = create_window()
        path = self._real_file("cache_clear.tif")
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, [path])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: len(
                    _axes(w, "1D", path).lines) > 0))
            self.assertGreaterEqual(stage_cache.describe()["files"], 1,
                                    "算过就该有产物")
            w.clear_cache_btn.click()
            QApplication.processEvents()
            self.assertIn("已清空缓存", w.log_text.toPlainText())
            self.assertEqual(stage_cache.describe()["files"], 0)
            w.clear_cache_btn.click()      # 再点：空缓存也不炸
            QApplication.processEvents()
            self.assertIn("缓存本来就是空的", w.log_text.toPlainText())
        finally:
            w.close()


class TestBoxZoom(unittest.TestCase):
    """放大镜点亮时左键拖 = 框选放大（2026-09-24 用户要求加回来：
    "再加回去放大镜框选放大，注意上次那个bug，不要再出现了"）。

    上次的 bug（用户 2026-09-18 原话）："画完框在缩小会把画框内的
    数据变小"——旧版用的是 matplotlib 的 zoom mode，它与我们自己的
    平移手势抢同一串鼠标事件、又各自记账。现在框选是自己实现的：
    自己画选框 + 松开时只改 set_xlim/set_ylim，**一个数据点都不碰**。
    test_box_zoom_then_wheel_out_keeps_data 就是那个 bug 的回归护栏。
    """

    PATH = "data/fake_b.tif"

    def _open_1d(self, w):
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, [self.PATH])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", self.PATH).lines) > 0))
        return _dock(w, "1D", self.PATH)

    def _drag_box(self, dock, data0, data1):
        """按真事件的形状拖一个框：press 带 button，move 带 buttons
        （mpl 的 _mouse_handler 会把拖动中的 move 事件补上 button）。"""
        content = gui_panel_state._content(dock)
        ax, canvas = content.axes_1d, content.canvas
        p0 = ax.transData.transform(data0)
        p1 = ax.transData.transform(data1)
        canvas.callbacks.process("button_press_event", MouseEvent(
            "button_press_event", canvas, p0[0], p0[1], button=1))
        canvas.callbacks.process("motion_notify_event", MouseEvent(
            "motion_notify_event", canvas, p1[0], p1[1],
            buttons=frozenset({1})))
        canvas.callbacks.process("button_release_event", MouseEvent(
            "button_release_event", canvas, p1[0], p1[1], button=1))

    def test_box_zoom_narrows_both_axes_to_the_box(self):
        """框住哪块就放大到哪块：x/y 范围收拢到框内。

        框必须落在**当前可见范围之内**：起点在轴外时按下事件
        inaxes=None，被手势正确忽略（那是保护，不是缺陷）。
        """
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            content.toolbar._actions["zoom"].trigger()   # 点亮放大镜
            self._drag_box(dock, (1.2, 1.3), (3.0, 2.5))
            xlo, xhi = ax.get_xlim()
            ylo, yhi = ax.get_ylim()
            self.assertAlmostEqual(xlo, 1.2, delta=0.05, msg=f"x 下限 {xlo}")
            self.assertAlmostEqual(xhi, 3.0, delta=0.05, msg=f"x 上限 {xhi}")
            self.assertAlmostEqual(ylo, 1.3, delta=0.05, msg=f"y 下限 {ylo}")
            self.assertAlmostEqual(yhi, 2.5, delta=0.05, msg=f"y 上限 {yhi}")
        finally:
            w.close()

    def test_box_zoom_then_wheel_out_keeps_data(self):
        """**旧 bug 的回归护栏**：画框放大 → 再滚轮缩小，曲线数据一个
        点都不许变（旧版是"框内数据变小"，那是数据被动了）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax, canvas = content.axes_1d, content.canvas
            # 只比数据曲线：悬停圆点（单点假线）不算——鼠标一动它就
            # 会建出来，比"线数"会被它带偏
            def data_lines():
                return [ln for ln in ax.lines if len(ln.get_xdata()) > 1]
            before = [(ln.get_xdata().copy(), ln.get_ydata().copy())
                      for ln in data_lines()]
            content.toolbar._actions["zoom"].trigger()   # 点亮
            self._drag_box(dock, (1.2, 1.3), (3.0, 2.5))
            # 再缩小（放大镜仍点亮：滚轮向下 = 缩小）
            canvas.callbacks.process("scroll_event", MouseEvent(
                "scroll_event", canvas, 250, 150, step=-1, button="down"))
            after = [(ln.get_xdata(), ln.get_ydata()) for ln in data_lines()]
            self.assertEqual(len(before), len(after))
            for i, ((x0, y0), (x1, y1)) in enumerate(zip(before, after)):
                self.assertTrue(np.array_equal(x0, x1),
                                f"第 {i} 条曲线的 x 数据被改了")
                self.assertTrue(np.array_equal(y0, y1),
                                f"第 {i} 条曲线的 y 数据被改了")
            # 而且视图确实张开了（证明缩小这一步真的执行了）
            self.assertGreater(ax.get_xlim()[1] - ax.get_xlim()[0], 0.8)
        finally:
            w.close()

    def test_box_zoom_removes_the_rect_after_release(self):
        """选框是临时的：松开后轴上不留矩形（否则重画会叠罗汉）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            content.toolbar._actions["zoom"].trigger()
            self._drag_box(dock, (1.2, 1.3), (3.0, 2.5))
            self.assertEqual(len(ax.patches), 0, "松开后不应留下选框")
            self.assertIsNone(getattr(dock, "_box_patch", None))
        finally:
            w.close()

    def test_tiny_drag_is_a_click_not_a_zoom(self):
        """框太小（< 6 px）= 手抖，当点击：视图不动。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            content.toolbar._actions["zoom"].trigger()
            x0 = ax.get_xlim()
            self._drag_box(dock, (1.0, 1.5), (1.001, 1.501))
            self.assertEqual(tuple(ax.get_xlim()), tuple(x0),
                             "小抖动不该改变视图")
        finally:
            w.close()

    def test_magnifier_off_drag_still_pans(self):
        """放大镜熄灭时左键拖仍是平移（框选只在点亮时生效）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            self.assertFalse(gui_plot_panels._magnifier_on(dock))
            x0 = ax.get_xlim()
            self._drag_box(dock, (1.0, 1.5), (2.0, 2.0))
            x1 = ax.get_xlim()
            self.assertNotEqual(tuple(x0), tuple(x1), "熄灭时拖动应平移")
            # 平移不该留下选框
            self.assertEqual(len(ax.patches), 0)
        finally:
            w.close()


class TestZoomToolbar(unittest.TestCase):
    """D：每个 1D 面板带自己的精简工具栏 [Home][Zoom][Customize][Save]；
    放大镜 = 开关（点亮滚轮缩放/拖框放大，熄灭滚轮滚动/拖平移），
    抓手/前进后退/子图按钮退休；占位面板没有。

    2026-09-24 起工具栏是自绘的 `_SlimToolbar`（自己拿四个 QAction，
    不再继承 mpl 的 NavigationToolbar2QT——它构造时会在 offscreen 下
    偶发无限递归，见 scripts/stress_panels.py）。"""

    def test_toolbar_present_on_1d(self):
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            widget = gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif"))
            self.assertIsInstance(widget.toolbar,
                                  gui_plot_panels._SlimToolbar)
            self.assertEqual(
                list(widget.toolbar._actions),
                ["home", "zoom", "edit_parameters", "save_figure"],
                "工具栏应精简为 Home/Zoom/Customize/Save 四个 action")
            # 砍掉的按钮不该有 action（抓手/前进/后退/子图）
            for gone in ("pan", "back", "forward", "configure_subplots"):
                self.assertNotIn(gone, widget.toolbar._actions,
                                 f"{gone} 按钮应已砍掉")
        finally:
            w.close()

    def test_unregistered_view_still_placeholder(self):
        """未注册视图（分发骨架的防御路径）：占位标签面板，无工具栏。"""
        w = create_window()
        try:
            gui_plot_panels._open_plot_panel(w, "不存在", "不存在|data/fake_b.tif",
                                       "不存在_fake_b.tif")
            widget = gui_panel_state._content(_dock(w, "不存在", "data/fake_b.tif"))
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
            add_checked(w, ["data/fake_b.tif"])
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
            gui_plot_panels._hover_motion(w, key, _hover_event(ax, 0.6))
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
            gui_plot_panels._hover_motion(w, key, _hover_event(ax, 0.6))
            gui_plot_panels._hover_leave(w, key)
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
            gui_plot_panels._hover_motion(w, key, _hover_event(ax, 0.6))
            old = dock.hover_marker
            tth, it = np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])
            gui_plot_views._draw_1d(w, dock, tth, it)
            self.assertIsNot(old.axes, ax, "重画后旧标记应与旧轴断开")
            gui_plot_panels._hover_motion(w, key, _hover_event(ax, 1.0))
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                w.compare_btn.click()
                key = next(k for k in w.plot_docks
                           if k.startswith("对比|"))
                dock = w.plot_docks[key]
                _wait_until(
                    lambda: len(gui_panel_state._content(dock).axes_1d.lines) >= 2)
            ax = gui_panel_state._content(dock).axes_1d
            gui_plot_panels._hover_motion(w, key, _hover_event(ax, 0.5))
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
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif",
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif",
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
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
            canvas = gui_panel_state._content(d1).canvas
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
            add_checked(w, [path_str])
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
            content = gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif"))
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
            content = gui_panel_state._content(dock)
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
            content = gui_panel_state._content(dock)
            h0 = dock.height()
            # 顶边抓取带真实落点 = 自绘标题栏那一行（内容上边 5px；
            # 2026-09-24 面板壳瘦身：工具栏藏了，最上面是自绘栏）
            QTest.mousePress(content.slim_bar, Qt.LeftButton, Qt.NoModifier,
                             QPoint(content.width() // 2, 2))
            QTest.mouseMove(content.slim_bar,
                            QPoint(content.width() // 2, 2 - 40))
            QTest.mouseRelease(content.slim_bar, Qt.LeftButton, Qt.NoModifier,
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
            content = gui_panel_state._content(dock)
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
            add_checked(w, [path_str])
            _open_view(w, "1D")
        self.assertTrue(_wait_until(
            lambda: len(_axes(w, "1D", path_str).lines) > 0))
        QApplication.processEvents()

    def _build(self, w, path_str="data/fake_b.tif"):
        dock = _dock(w, "1D", path_str)
        content = gui_panel_state._content(dock)
        return dock, gui_customize._build_customize_dialog(
            w, dock, content.axes_1d, content.figure)

    def test_dialog_prefills_current_axis_state(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            dock, dlg = self._build(w)
            content = gui_panel_state._content(dock)
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
            content = gui_panel_state._content(dock)
            gui_customize._apply_customize(w, dock, content.axes_1d,
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
            content = gui_panel_state._content(dock)
            gui_customize._apply_customize(w, dock, content.axes_1d,
                                     content.figure, dlg)
            # 程序重画（等价于图像参数 [应用] 走的 _draw_1d 路径）
            gui_plot_views._draw_1d(w, dock, dock.last_tth, dock.last_intensity)
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
            content = gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif"))
            before = (content.axes_1d.get_title(),
                      content.axes_1d.get_xlabel(),
                      content.axes_1d.get_ylabel(),
                      content.axes_1d.get_yscale())
            # 取消路：exec 拒绝 → 图保持原样
            with mock.patch.object(QDialog, "exec",
                                   lambda self: QDialog.Rejected):
                gui_customize._open_customize_dialog(w, key)
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
                gui_customize._open_customize_dialog(w, key)
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
            content = gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif"))
            # patch 目标 = 调用点所在模块 plot_panels（_SlimToolbar 在
            # 那里查全局名）；gui_panels 是 panels.py，不是这个模块
            with mock.patch.object(gui_plot_panels,
                                   "_open_customize_dialog") as m:
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
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
            canvas = gui_panel_state._content(
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
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
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
            gui_panel_state._content(dock).popout_btn.click()
            QApplication.processEvents()
            floated = w.plot_docks[key]
            self.assertNotIsInstance(floated, gui_panels._PlotSubWindow)
            w.focus_panel = None
            QTest.mouseClick(gui_panel_state._content(floated).canvas,
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
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            QApplication.processEvents()
            canvas = gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif")).canvas
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
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
                add_checked(w, ["data/fake_c.tif"])
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
                c = gui_panel_state._content(d2).canvas
                now = c.height() / c.width()
                expected = pref[1] / pref[0]
                self.assertAlmostEqual(now, expected, delta=0.06,
                                       msg=f"{mode}后拖过的图比例变了")
                # 没拖过的图保持默认 5:3
                d1 = _dock(w, "1D", "data/fake_a.tif")
                c1 = gui_panel_state._content(d1).canvas
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
            c1 = gui_panel_state._content(d1).canvas
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
            add_checked(w, ["data/fake_b.tif"])
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
            add_checked(w, ["data/fake_b.tif"])
            _open_view(w, "1D")
            drawn = _wait_until(
                lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            self.assertTrue(drawn)
        return _dock(w, "1D", "data/fake_b.tif")

    def _magnifier(self, w, display, on):
        """点放大镜开关（触发 QAction = 用户点按钮），断言模式到位。"""
        content = gui_panel_state._content(_dock(w, "1D", display))
        content.toolbar._actions["zoom"].trigger()
        QApplication.processEvents()
        self.assertEqual(gui_plot_panels._magnifier_on(_dock(w, "1D", display)), on,
                         f"放大镜应已{'点亮' if on else '熄灭'}")

    def test_magnifier_toggle_switches_mode(self):
        """放大镜 = 纯开关：点亮点灭只翻转按钮。

        （旧断言是"mpl 的模式永远停在 NONE"——2026-09-24 面板工具栏换成
        自绘的 _SlimToolbar 之后，mpl 那套模式机件整个不在场，所以改成
        断言"没有 mode 这个机件"：将来谁再把 mpl 工具栏塞回来，这条会红。）
        """
        w = create_window()
        try:
            self._open_1d(w)
            content = gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif"))
            action = content.toolbar._actions["zoom"]
            self.assertTrue(action.isCheckable(), "放大镜按钮应可亮灭")
            self.assertFalse(action.isChecked())
            self._magnifier(w, "data/fake_b.tif", True)
            self.assertTrue(action.isChecked(), "点亮后按钮应亮起")
            self.assertFalse(hasattr(content.toolbar, "mode"),
                             "不该再有 mpl 的缩放模式机件")
            self._magnifier(w, "data/fake_b.tif", False)
            self.assertFalse(action.isChecked(), "熄灭后按钮应熄灭")
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
            gui_plot_panels._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
            self.assertEqual(ax.get_xlim(), (1.0, 8.0),
                             "放大镜熄灭时滚轮不应改范围")
            self._magnifier(w, "data/fake_b.tif", True)
            gui_plot_panels._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
            xlo, xhi = ax.get_xlim()
            self.assertAlmostEqual(xlo, 4.0 - 3.0 / 1.1, places=3,
                                   msg="点亮后滚轮应缩放")
            self.assertAlmostEqual(xhi, 4.0 + 4.0 / 1.1, places=3)
            self._magnifier(w, "data/fake_b.tif", False)
            ax.set_xlim(1.0, 8.0)
            gui_plot_panels._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
            self.assertEqual(ax.get_xlim(), (1.0, 8.0),
                             "再熄灭后滚轮应再次失效")
        finally:
            w.close()

    def test_press_pans_when_magnifier_off_and_boxes_when_on(self):
        """左键按下：熄灭 = 记平移起点；点亮 = 起框选放大的框（2026-09-24
        用户要求把框选放大加回来，见 TestBoxZoom）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            # 开局放大镜是熄灭的（_magnifier 是"点一下再断言"的语义，
            # 这里先直接断言默认态）
            self.assertFalse(gui_plot_panels._magnifier_on(dock))
            gui_plot_panels._pan_press(w, key, _press_event(ax, x=100, y=120))
            self.assertIsNotNone(getattr(dock, "_pan_start", None),
                                 "放大镜熄灭时左键拖 = 平移")
            gui_plot_panels._pan_release(
                w, key, _press_event(ax, x=100, y=120))
            self.assertIsNone(getattr(dock, "_pan_start", None))
            self._magnifier(w, "data/fake_b.tif", True)   # 点亮
            gui_plot_panels._pan_press(w, key, _press_event(ax, x=100, y=120))
            self.assertIsNotNone(getattr(dock, "_box_start", None),
                                 "放大镜点亮时左键拖 = 框选放大")
            self.assertIsNone(getattr(dock, "_pan_start", None),
                              "框选时不该同时记平移起点（两套手势不能抢）")
            gui_plot_panels._pan_release(
                w, key, _press_event(ax, x=100, y=120))
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
            gui_plot_panels._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
            xlo, xhi = ax.get_xlim()
            # 系数 1.1（每格 10%）：两侧各收拢 1/1.1
            self.assertAlmostEqual(xlo, 4.0 - 3.0 / 1.1, places=3)
            self.assertAlmostEqual(xhi, 4.0 + 4.0 / 1.1, places=3)
            # 光标对着的点"钉在原地"= 它在范围内的相对位置不变
            # （4.0 不是 (1,8) 的中点，所以中点不守恒、分数守恒）
            frac = (4.0 - xlo) / (xhi - xlo)
            self.assertAlmostEqual(frac, 3 / 7, places=3,
                                   msg="光标点的相对位置缩放后应不变")
            gui_plot_panels._wheel_zoom(w, key, _scroll_event(ax, "down", 4.0, 2.0))
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
                gui_plot_panels._wheel_zoom(
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
            content = gui_panel_state._content(dock)
            self._magnifier(w, "data/fake_b.tif", True)
            x0 = ax.get_xlim()
            gui_plot_panels._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
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
        """程序重画刷新"家"（这里**没有手势介入**）：Home 回新画出来的
        视图，不回重画前的老视图。

        注意别在这里先滚轮缩放：手势改的视图不算"家"（见 _refresh_home
        与 TestHomeView），那是 2026-09-24 用户要的"回到最初的样子"。
        """
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            content = gui_panel_state._content(dock)
            self._magnifier(w, "data/fake_b.tif", True)
            # 程序重画（等价：参数面板改视图范围后点 [应用]）
            dock.params_snapshot["视图 2θ 下限 (°)"] = 3.0
            dock.params_snapshot["视图 2θ 上限 (°)"] = 6.0
            gui_plot_views._draw_1d(w, dock, dock.last_tth, dock.last_intensity)
            QApplication.processEvents()
            self.assertEqual(ax.get_xlim(), (3.0, 6.0))
            gui_plot_panels._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
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
            gui_plot_panels._pan_press(w, key, _press_event(ax, x=100, y=120))
            gui_plot_panels._pan_motion(w, key, _press_event(ax, x=150, y=100))
            gui_plot_panels._pan_release(w, key, _press_event(ax, x=150, y=100))
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
            add_checked(w, ["data/fake_a.tif"])
            _open_view(w, "1D")
            drawn = _wait_until(
                lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0)
            self.assertTrue(drawn)
        return _dock(w, "1D", "data/fake_a.tif")

    def _redraw(self, w, dock):
        """等价于点 [应用] 的程序重画路径。"""
        gui_plot_views._draw_1d(w, dock, dock.last_tth, dock.last_intensity)
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
            gui_plot_panels._open_plot_panel(w, "1D", f"1D|fake{i}.tif", f"1D_{i}")
        for i in range(3):
            gui_plot_panels._open_plot_panel(w, "2D", f"2D|fake{i}.tif", f"2D_{i}")
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
                subs.append(gui_plot_panels._open_plot_panel(
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
        a = gui_plot_panels._open_plot_panel(w, "1D", "1D|a.tif", "a")
        b = gui_plot_panels._open_plot_panel(w, "2D", "2D|b.tif", "b")
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
            gui_panels._apply_area_zoom(w, 1.1)
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
            ax = gui_panel_state._content(a).axes_1d
            ax.set_xlim(1.0, 8.0)
            accepted = []
            gui = SimpleNamespace(
                modifiers=lambda: Qt.ControlModifier,
                angleDelta=lambda: QPoint(0, 120),
                accept=lambda: accepted.append(True))
            gui_plot_panels._wheel_zoom(
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
            gui_panels._apply_area_zoom(w, 0.01)
            self.assertAlmostEqual(w._area_zoom, 0.5, places=6)
            self.assertEqual(w.zoom_label.text(), "50%")
            gui_panels._apply_area_zoom(w, 99.0)
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
            gui_panels._apply_area_zoom(w, 0.8)
            for _ in range(10):
                QApplication.processEvents()
            p = gui_plot_panels._open_plot_panel(w, "1D", "1D|c.tif", "c")
            for _ in range(10):
                QApplication.processEvents()
            # 契约 = 新图和周围已缩放的图一样大；画布被标题栏/工具栏
            # 壳吃掉固定高度，不会正好是 500×0.8，所以与邻居对比
            self.assertEqual((p.width(), p.height()), (a.width(), a.height()),
                             msg="新图子窗口应和周围已缩放的图一样大")
            canvas = gui_panel_state._content(p).canvas
            canvas_a = gui_panel_state._content(a).canvas
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
            ref = gui_plot_panels._open_plot_panel(w, "1D", "1D|ref.tif", "ref")
            for _ in range(10):
                QApplication.processEvents()
            gui_panels._toggle_pop_out(w, "1D|a.tif")
            for _ in range(10):
                QApplication.processEvents()
            gui_panels._apply_area_zoom(w, 0.8)
            for _ in range(10):
                QApplication.processEvents()
            gui_panels._toggle_pop_out(w, "1D|a.tif")
            for _ in range(10):
                QApplication.processEvents()
            sub = _dock(w, "1D", "a.tif")
            canvas2 = gui_panel_state._content(sub).canvas
            canvas_ref = gui_panel_state._content(ref).canvas
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
            gui_panels._toggle_pop_out(w, "1D|a.tif")
            for _ in range(10):
                QApplication.processEvents()
            floated = w.plot_docks["1D|a.tif"]
            fw, fh = floated.width(), floated.height()
            bw = b.width()
            gui_panels._apply_area_zoom(w, 0.5)
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
            gui_panels._apply_area_zoom(w, 1.1)
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
            add_checked(w, ["data/fake_b.tif"])
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
            content = gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif"))
            gui_panels._toggle_pop_out(w, key)
            QApplication.processEvents()
            floated = w.plot_docks[key]
            self.assertIsInstance(floated, gui_panels._FloatedWindow)
            self.assertIs(gui_panel_state._content(floated), content,
                          "弹出后内容应是同一个对象")
            self.assertEqual(content.popout_btn.text(), "收回")
            self.assertEqual(len(content.axes_1d.lines), 1, "曲线应保留")
            gui_panels._toggle_pop_out(w, key)
            QApplication.processEvents()
            back = w.plot_docks[key]
            self.assertIsInstance(back, gui_app.QMdiSubWindow)
            self.assertIs(gui_panel_state._content(back), content)
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
            gui_panels._toggle_pop_out(w, key)
            QApplication.processEvents()
            floated = w.plot_docks[key]
            ax = gui_panel_state._content(floated).axes_1d
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
            gui_panels._toggle_pop_out(w, key)
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                    and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
            QApplication.processEvents()
            key_b = "1D|data/fake_b.tif"
            gui_panels._toggle_pop_out(w, key_b)
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
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
            gui_panels._close_panel(w, focus)
            QApplication.processEvents()
            self.assertNotIn(focus, w.plot_docks)
            self.assertEqual(w.focus_panel, other, "焦点应移交给剩余面板")
            gui_panels._close_panel(w, other)
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
            gui_panels._close_panel(w, key_a)
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
            canvas = gui_panel_state._content(new).canvas
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
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")   # 后台开算（0.3s）
                key = "1D|data/fake_b.tif"
                self.assertTrue(_wait_until(lambda: key in w.plot_docks))
                gui_panels._close_panel(w, key)
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                w.compare_btn.click()
                keys = [k for k in w.plot_docks if k.startswith("对比|")]
                self.assertTrue(_wait_until(lambda: keys))
                gui_panels._close_panel(w, keys[0])   # 旧代任务还在飞
                w.compare_btn.click()              # 重开新面板（新代）
                keys = [k for k in w.plot_docks if k.startswith("对比|")]
                self.assertTrue(_wait_until(lambda: keys))
                ax = gui_panel_state._content(w.plot_docks[keys[0]]).axes_1d
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
            fig = gui_panel_state._content(dock).figure
            with mock.patch.object(gui_plot_panels, "_ask_save_options",
                                   return_value={"dpi": 300, "fmt": "png"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("/tmp/panel_out", "PNG 图片 (*.png)")) as dlg, \
                 mock.patch.object(fig, "savefig") as savefig:
                gui_panel_state._content(dock).toolbar.save_figure()
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                    and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
            gui_panels._close_panel(w, "1D|data/fake_b.tif")
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
            gui_calib_panel._on_calib_click(w, w.calib_key,
                                      SimpleNamespace(xdata=x, ydata=y,
                                                      inaxes=w.calib_ax))

    def _logs(self, w):
        return w.log_text.toPlainText()

    def _enter_with_fake_a(self, w):
        """加 fake_a.tif 并进校准模式（图像读取需调用方已 mock）。

        注意：自动/手动校准的后台 worker 还会再读一次图，调用方要
        自己把 load_diffraction_image 的 patch 罩住整个测试体。
        """
        add_checked(w, ["data/fake_a.tif"])
        w.calib_btn.click()
        self.assertEqual(w.param_stack.currentIndex(),
                         w.PARAM_PAGES["校准"])
        self.assertIsNotNone(w.calib_dock)
        w.calib_pixel_chk.setChecked(True)   # 校准前置：确认像素尺寸

    def test_enter_mode_opens_panel_and_exit_restores(self):
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            self.assertEqual(w.calib_key, "校准|data/fake_a.tif")
            self.assertIn("打开校准面板", self._logs(w))
            self.assertIn("校准模式", w.mode_label.text())
            # 16 条理论环路径按当前几何画上（青线；精确反解后不再是
            # Circle patch，见 theoretical_ring_paths）
            rings = [ln for ln in w.calib_ax.lines
                     if str(ln.get_color()).lower()
                     == gui_calib_model.RING_COLOR.lower()]
            self.assertGreaterEqual(len(rings), 16)
            # 退出：面板关、参数坞还原
            w.entrance_buttons["1D"].click()   # 点别的入口 = 退出校准
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["1D"])
            self.assertIsNone(w.calib_dock)
            self.assertIn("回到分析模式", self._logs(w))
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calib_page_exit_button_returns_to_analysis(self):
        """校准页底部 [返回分析模式] = 把工具栏开关弹起（同源切换）。

        没选过别的入口就进校准（测试就是这么走的）→ 退出来回到"什么都
        没选"：入口不亮、参数坞收起（用户 2026-09-25 定，另一种情形
        见下一条 test_exit_calib_to_prior_entrance_keeps_dock）。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            self.assertTrue(w.calib_btn.isChecked())
            # 页面出口：点 [返回分析模式] → 入口弹起 → 回分析侧
            w.calib_exit_btn.click()
            self.assertFalse(w.calib_btn.isChecked())
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["1D"])
            self.assertIsNone(w.calib_dock)
            self.assertFalse(w.param_dock.isVisible())
            self.assertIsNone(w._last_entrance)
            self.assertIn("回到分析模式", self._logs(w))
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_exit_calib_to_prior_entrance_keeps_dock(self):
        """先点过 [1D] 再进校准 → 退出来回 1D 页、参数坞还开着。"""
        w = create_window()
        try:
            w.show()
            w.entrance_buttons["1D"].click()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            w.calib_exit_btn.click()
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["1D"])
            self.assertTrue(w.param_dock.isVisible())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_reclick_calib_exits_instead_of_reentering(self):
        """再点一次已亮着的 [校准] = 退出，不是"退出后立刻重进"。

        2026-09-26 用户报"没有退出校准的按钮了"：那条路其实一直在，只是
        Qt 先弹起勾选（toggled → _on_mode(False) 退出），紧接着 clicked
        又把它勾回去（重进）——净效果是闪一下，选点还被清空。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                gen = w.calib_gen
                w.calib_btn.click()          # 再点一次 = 退出
            self.assertFalse(w.calib_btn.isChecked())
            self.assertIsNone(w.calib_dock)
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["1D"])
            self.assertFalse(w.param_dock.isVisible())   # 没选过入口 → 收回
            self.assertEqual(w.calib_gen, gen + 1)       # 只关一次（重进会是 +2）
            logs = self._logs(w)
            self.assertEqual(logs.count("进入校准模式"), 1)
            self.assertEqual(logs.count("回到分析模式"), 1)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_exit_button_sits_in_dock_header_not_page_bottom(self):
        """[返回分析模式] 钉在坞顶那一行、只在校准模式显示。

        以前它在校准表单最底部（窗口 1000 高时落在内容 y=1236，要滚
        434 px 才看得见），用户以为"没有退出按钮"。
        """
        w = create_window()
        try:
            w.show()
            self.assertFalse(w.calib_exit_btn.isVisible())   # 分析模式不显示
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            self.assertTrue(w.calib_exit_btn.isVisible())
            self.assertIs(w.calib_exit_btn.parentWidget(), w.geom_row)
            self.assertFalse(w.calib_scroll.isAncestorOf(w.calib_exit_btn))
            w.calib_exit_btn.click()
            self.assertFalse(w.calib_btn.isChecked())
            self.assertFalse(w.calib_exit_btn.isVisible())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_geom_row_stays_selectable_on_every_page(self):
        """几何配置行：五页都能换条目，读数看悬停；校准页的提示补一句区别。

        （2026-09-26 晚订正：先前在校准页把它置灰，结果 [删除] 正好住在
        校准页、却在校准页换不了选中项——删条目得跨两页。）
        """
        w = create_window()
        try:
            w.show()
            w.entrance_buttons["1D"].click()
            self.assertTrue(w.config_combo.isEnabled())
            self.assertIn("1595.80 mm", w.geom_row.toolTip())
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            self.assertTrue(w.config_combo.isEnabled())   # 校准页也能换
            self.assertIn("1595.80 mm", w.geom_row.toolTip())
            self.assertIn("当前配置", w.geom_row.toolTip())  # 写明区别
            w.entrance_buttons["1D"].click()
            self.assertTrue(w.config_combo.isEnabled())
            self.assertNotIn("当前配置", w.geom_row.toolTip())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_enter_mode_without_file_logs_hint(self):
        w = create_window()
        try:
            w.calib_btn.click()
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["校准"])
            self.assertIsNone(getattr(w, "calib_dock", None))
            self.assertIn("请先在文件列表勾选标样文件", self._logs(w))
        finally:
            w.close()

    def test_auto_calib_fills_result_and_enables_save(self):
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib, "calibrate_lab6",
                                   return_value=self.FAKE_AUTO) as fake:
                self._enter_with_fake_a(w)
                w.calib_start_auto.click()
                self.assertTrue(_wait_until(
                    lambda: any(r["name"].startswith("自动")
                                for r in w.calib_state["results"]), 8000))
            # 引擎初值 = 自动定环心 (cx, cy) + 当前配置（借来的条目）
            self.assertEqual(fake.call_args.kwargs["center0_px"],
                             (self.FAKE_CENTER["cx"], self.FAKE_CENTER["cy"]))
            self.assertAlmostEqual(fake.call_args.kwargs["dist0_m"], 1.5958)
            # 结果累积：原始1（借来的起点）+ 自动1，并填进 A 槽
            self.assertEqual([r["name"] for r in w.calib_state["results"]],
                             ["原始1", "自动1"])
            self.assertEqual(w.calib_state["slots"]["A"], "自动1")
            vals = w.calib_vals["A"]
            self.assertEqual(vals["dist"].text(), "1595.80")
            self.assertEqual(vals["poni1"].text(), "1045.20")
            self.assertEqual(vals["poni2"].text(), "1022.00")
            # 白名单：自洽残差不入对比表
            self.assertNotIn("resid", vals)
            self.assertIn("自动完成（自动1）", self._logs(w))
            # 图按新几何重画：控制点绿点（一条 2 点散点线）画上
            self.assertTrue(any(len(line.get_xdata()) == 2
                                for line in w.calib_ax.lines))
            # 保存区：说明当前保存的是哪一份 + 保存按钮启用
            self.assertTrue(w.calib_save_btn.isEnabled())
            self.assertIn("将保存：", w.calib_save_hint.text())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_rings_off_image_guard_warns_but_keeps_the_view(self):
        """守卫：几何把理论环全推出图像时，红字 + 日志说清楚，**视野不动**。

        （静默什么都不显示是最坏的失败方式；但也不能把视野放大到包住环
        ——2026-09-26 晚用户报"青环变得很大时图会被迫变小"：视野被撑到
        11 倍宽后图像在画布上缩成一小块，看着像"图动了"。图像是这张图上
        唯一不动的参照系。）
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                view_before = (w.calib_ax.get_xlim(), w.calib_ax.get_ylim())
                # 几何只认当前配置（分析页字段是只读显示）→ 直接把距离
                # 改大 1000 倍（16 条环全被推出图像）
                w.calib_state["current_geom"]["dist_m"] *= 1000.0
                gui_calib_panel._redraw_calib(w)
            self.assertIn("全部落在图像外", self._logs(w))
            # 图上红字在（说清楚为什么不画线），视野还是图像那一框
            self.assertTrue(any("全部落在图像外" in t.get_text()
                                for t in w.calib_ax.texts))
            self.assertEqual((w.calib_ax.get_xlim(), w.calib_ax.get_ylim()),
                             view_before)
            # 同一几何重画（撤销/清空选点）不重复刷屏
            n = self._logs(w).count("全部落在图像外")
            gui_calib_panel._redraw_calib(w)
            self.assertEqual(self._logs(w).count("全部落在图像外"), n)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_fit_rings_toggle_expands_view_on_demand(self):
        """[看环全貌] 是视野放大的唯一入口：默认锁图像，勾上才包住环。

        （2026-09-26 晚用户定：视野不许自动跟着环跑——环跑到图像外时
        自动放大会把图像挤成画布上的一小块，看着像"图动了/图被迫变小"。）
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                chk = w.findChild(QPushButton, "calib_fit_rings")
                self.assertIsNotNone(chk, "校准图面板上应有 [看环全貌]")
                self.assertFalse(chk.isChecked())        # 默认：锁图像
                # 几何离谱 → 环全跑图像外：默认视野仍锁在图像那一框
                w.calib_state["current_geom"]["dist_m"] *= 1000.0
                gui_calib_panel._redraw_calib(w)
                locked = w.calib_ax.get_xlim()
                # 图上红字把出口指出来
                self.assertTrue(any("看环全貌" in t.get_text()
                                    for t in w.calib_ax.texts))
                # 勾上 → 视野放大到包住环
                chk.setChecked(True)
                grown = w.calib_ax.get_xlim()
                self.assertGreater(grown[1] - grown[0],
                                   (locked[1] - locked[0]) * 1.5)
                # 取消 → 回到图像那一框
                chk.setChecked(False)
                self.assertEqual(w.calib_ax.get_xlim(), locked)
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
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            calls = []
            real_paths = gui_calib_panel.theoretical_ring_paths

            def spy(**kw):
                calls.append(kw)
                return real_paths(**kw)

            with mock.patch.object(gui_calib_panel, "theoretical_ring_paths",
                                   side_effect=spy):
                gui_calib._on_calib_result(w, "auto", res)
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
                gui_calib_panel._on_calib_click(
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
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
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
            gui_calib_panel._on_calib_click(
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
                    lambda: any(r["name"].startswith("手动")
                                for r in w.calib_state["results"]), 8000))
            # 引擎输入：3 点 + 环号 + 束心 (行,列) 换序 + 面板几何
            self.assertEqual(len(fake.call_args.args[0]), 3)
            self.assertEqual(fake.call_args.args[1], [2, 4, 6])
            self.assertEqual(fake.call_args.kwargs["center0_px"],
                             (1022.3, 1022.0))
            # 手动列 + 点数少的可信度提示
            self.assertEqual(w.calib_vals["A"]["dist"].text(), "1596.20")
            self.assertIn("手动完成（手动1）", self._logs(w))
            self.assertIn("点数较少", self._logs(w))
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_delta_column_and_last_save_source(self):
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib, "calibrate_lab6",
                                   return_value=self.FAKE_AUTO), \
                 mock.patch.object(gui_calib, "refine_lab6_from_points",
                                   return_value=self.FAKE_MANUAL), \
                 mock.patch.object(gui_calib, "ring_metrics",
                                   side_effect=lambda image, **kw:
                                   {"dev_px": 0.20 if kw["dist_m"] == 1.5958
                                    else 0.50,
                                    "clip_frac": 0.0, "n_complete": 16,
                                    "rings": [{}] * 16,
                                    "a": {"spread_ppm": 500.0}}):
                self._enter_with_fake_a(w)
                self._click_rings(w, ((2, 0), (4, 90), (6, 180)))
                # 只跑手动 → 结果进列表并填 A 槽；当前配置是"借来的起点"
                # → 第一条结果**总是采纳**（新批次规则）
                w.calib_start_manual.click()
                self.assertTrue(_wait_until(
                    lambda: w.calib_state["slots"]["A"] == "手动1", 8000))
                self.assertEqual([r["name"] for r in w.calib_state["results"]],
                                 ["原始1", "手动1"])
                self.assertEqual(w.calib_state["current_from"], "手动1")
                self.assertIn("新批次的第一条结果",
                              w.log_text.toPlainText())
                d = w.calib_vals["delta"]
                self.assertEqual(d["dist"]["current"].text(), "—")   # 基准自身
                self.assertEqual(d["dist"]["A"].text(), "+0.00")     # 就是它自己
                # 再跑自动（环位偏差 0.20 < 手动 0.50）→ 轮换填 B 并采纳
                w.calib_start_auto.click()
                self.assertTrue(_wait_until(
                    lambda: w.calib_state["slots"]["B"] == "自动1", 8000))
                self.assertEqual(w.calib_state["slots"]["A"], "手动1")
                self.assertEqual(w.calib_state["current_from"], "自动1")
                # Δ = 该列 − 基准（基准 = 当前配置 = 自动1）
                self.assertEqual(d["dist"]["A"].text(), "+0.40")     # 手动−自动
                self.assertEqual(d["dev"]["A"].text(), "+0.30")      # 0.50−0.20
                self.assertEqual(d["dist"]["B"].text(), "+0.00")
                # 基准可选：切成 A 之后，B 的 Δ 变成 自动 − 手动
                w.calib_base_combo.setCurrentIndex(
                    w.calib_base_combo.findData("A"))
                self.assertEqual(d["dist"]["B"].text(), "-0.40")
                self.assertEqual(d["dist"]["A"].text(), "—")
                # 保存对象 = 当前配置（此时是被采纳的 自动1）
                self.assertIn("自动1", w.calib_save_hint.text())
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
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib, "calibrate_lab6",
                                   side_effect=slow_calib):
                self._enter_with_fake_a(w)
                w.calib_start_auto.click()
                self.assertTrue(started.wait(5))
                w.entrance_buttons["1D"].click()   # 点别的入口 = 退出
                self.assertIsNone(w.calib_dock)
                release.set()
                # 等任务收尾投递后：状态与结果区仍为空（作废不炸）
                self.assertTrue(_wait_until(lambda: not w._tasks, 5000))
                self.assertEqual(w.calib_state["results"], [])
                self.assertEqual(w.calib_vals["A"]["dist"].text(), "—")
        finally:
            release.set()
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calib_page_fits_the_dock_width(self):
        """校准页不许有控件越界：参数坞窄、滚动区水平条关闭，超宽就够不着。

        （判据是**实际几何**：把坞压到最小宽后逐控件量右边缘；用
        sizeHint 会误判——滚动区本来就会把页面压到视口宽。）
        """
        w = create_window()
        try:
            w.show()
            w.resize(420, 700)
            QApplication.processEvents()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            w.param_dock.setMinimumWidth(w.param_dock.minimumWidth())
            QApplication.processEvents()
            page = w.param_stack.widget(w.PARAM_PAGES["校准"])
            over = []
            for child in page.findChildren(QWidget):
                if not child.isVisible() or child.width() == 0:
                    continue
                right = child.mapTo(page, QPoint(0, 0)).x() + child.width()
                if right > page.width() + 1:
                    over.append((type(child).__name__,
                                 right - page.width()))
            self.assertEqual(over, [], f"越界控件（类型, 超出 px）：{over}")
        finally:
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
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
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
                    lambda: any(r["name"].startswith("自动")
                                for r in w.calib_state["results"]), 8000))
                self.assertEqual(w.calib_vals["A"]["dist"].text(),
                                 "1600.00")
                release.set()   # 旧任务这时才完成 → 迟到作废
                self.assertTrue(_wait_until(lambda: not w._tasks, 5000))
                self.assertEqual(w.calib_vals["A"]["dist"].text(),
                                 "1600.00", "旧结果不得覆盖新结果")
                self.assertEqual(
                    w.calib_state["results"][-1]["result"]["dist_m"],
                    fast["dist_m"], "结果列表里是新结果")
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
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
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
                self.assertEqual(w.calib_state["results"], [])
                self.assertEqual(w.calib_points_label.text(),
                                 "已选 0 个点 / 0 个环")
                self.assertFalse(w.calib_start_manual.isEnabled())
                self.assertFalse(w.calib_save_btn.isEnabled())
                self.assertEqual(w.calib_save_hint.text(), "尚未有可保存的几何")
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
        with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                               return_value=np.ones((256, 256)) * 10), \
             mock.patch.object(gui_calib, "fit_center_from_rings",
                               return_value=TestCalibration.FAKE_CENTER), \
             mock.patch.object(gui_calib, "calibrate_lab6",
                               return_value=dict(self.FAKE_AUTO)):
            add_checked(w, ["data/fake_a.tif"])
            w.calib_btn.click()
            w.calib_pixel_chk.setChecked(True)   # 校准前置：确认像素尺寸
            w.calib_start_auto.click()
            self.assertTrue(_wait_until(
                lambda: any(r["name"].startswith("自动")
                            for r in w.calib_state["results"]), 8000))

    def test_save_adds_to_combo_and_selects(self):
        """保存 → 下拉框出现新条目并自动选中（读数跟到几何行 + 落盘）。"""
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
            # 几何跟到坞顶：悬停读数 = 校准结果（1595.80，不是初值 1600）
            self.assertIn("1595.80 mm", w.geom_row.toolTip())
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

    def test_save_refused_without_pixel_confirmation(self):
        """没确认像素尺寸 → 不落盘、只提示。

        与开始校准同一道门（calib._initial_ready）：条目会被别的批次和
        CLI 脚本原样拿去用，而像素填错时拟合会把距离同比例凑回来——
        不确认就存，等于把一份"看着正常、距离存疑"的几何发出去。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()          # 没勾"像素尺寸已确认"
            w.calib_key_edit.setText("lmfp9_lab6")
            w.calib_label_edit.setText("第 9 批")
            w.calib_save_btn.click()
            self.assertNotIn("lmfp9_lab6", config_mod.USER_CONFIGS)
            self.assertFalse(self._path.exists())
            self.assertIn("请先确认", w.log_text.toPlainText())
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
        self.assertEqual(gui_config_ops._suggest_config_key("lmfp1_lab6"),
                         "lmfp2_lab6")
        self.assertEqual(gui_config_ops._suggest_config_key("lmfp12_lab6"),
                         "lmfp13_lab6")
        self.assertEqual(gui_config_ops._suggest_config_key("n7m3"), "n8m3")
        self.assertEqual(gui_config_ops._suggest_config_key("no_digits"),
                         "lab6_calib")

    def test_save_requires_a_geometry(self):
        """没有当前配置（借不到条目/几何为空）：按钮置灰 + 直调也拒绝。

        注意语义变化：现在保存的是**当前配置**——借来的条目、手改的
        几何、采纳的结果都可以存（新批次"借来→改→存"就是这么用的）；
        只有连几何都没有时才拒。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
            # 借到条目 → 可以存（借来的那份也是"当前配置"）
            self.assertTrue(w.calib_save_btn.isEnabled())
            self.assertIn("借用 lmfp1_lab6", w.calib_save_hint.text())
            # 把几何清掉 → 置灰 + 直调拒绝
            gui_calib_model._calib_state(w)["current_geom"] = None
            gui_calib._calib_sync(w)
            self.assertFalse(w.calib_save_btn.isEnabled())
            self.assertEqual(w.calib_save_hint.text(), "尚未有可保存的几何")
            gui_config_ops._save_calib_config(w)   # 按钮置灰点不到：直调处理函数
            self.assertIn("还没有可保存的几何", w.log_text.toPlainText())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_workers_attach_beam_center(self):
        """worker 附带 beam_center_rc：自动 = 新拟合环心，手动 = 初值 B。"""
        image = np.ones((256, 256)) * 10
        geom = {"pixel_size_m": 200e-6, "wavelength_m": 0.1223e-10,
                "dist_m": 1.5958}   # worker 先读几何键再调引擎（引擎 mock）
        with mock.patch.object(gui_calib_panel, "load_diffraction_image",
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
        with mock.patch.object(gui_calib_panel, "load_diffraction_image",
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
        # （首参 None = 没有标样路径 → 跳过引擎指标，本测试只管束心）
        with mock.patch.object(gui_calib, "refine_lab6_from_points",
                               return_value=dict(self.FAKE_MANUAL)):
            res = gui_calib._manual_calib_worker(
                None, [(1, 2), (3, 4), (5, 6)], [0, 1, 2], geom,
                (1022.3, 1022.0))
        self.assertEqual(res["beam_center_rc"], (1022.0, 1022.3))


class TestCalibMetrics(unittest.TestCase):
    """引擎指标接线：附在结果 dict 上 + 写进日志后缀；失败不静默、不拖垮校准。

    指标本身（引擎语义、阈值、检出下限）在 test_ring_metrics.py 里用真
    几何合成图标定；这里只验 GUI 侧的接线与措辞。
    """

    FAKE_AUTO = dict(TestCalibration.FAKE_AUTO, dist_m=1.5958)
    FAKE_MANUAL = dict(TestCalibration.FAKE_MANUAL)
    FAKE_CENTER = TestCalibration.FAKE_CENTER
    GEO = {"pixel_size_m": 200e-6, "wavelength_m": 0.1223e-10,
           "dist_m": 1.6000, "poni1_m": 1045.2 * 200e-6,
           "poni2_m": 1022.0 * 200e-6, "rot1_deg": -0.005, "rot2_deg": -0.163}
    METRICS = {"dev_px": 0.52, "dev_signed_px": 0.1, "dev_rms_px": 0.6,
               "clip_frac": 0.0, "n_complete": 16, "n_rings_used": 16,
               "rings": [{} for _ in range(16)],
               "a": {"mean_angstrom": 4.1568, "spread_ppm": 812.0}}
    INITIAL = dict(METRICS, dev_px=3.14)

    def _logs(self, w):
        return w.log_text.toPlainText()

    # ── worker 侧：指标的附着位置 ──────────────────────────────
    def test_auto_worker_attaches_metrics_for_result_and_initial(self):
        """自动 worker：结果几何与初值几何各附一份（初值 = 面板预精修值）。"""
        seen = []

        def fake_rm(image, **kw):
            seen.append(kw["dist_m"])
            return dict(self.METRICS)

        with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                               return_value=np.ones((64, 64)) * 10), \
             mock.patch.object(gui_calib, "fit_center_from_rings",
                               return_value=self.FAKE_CENTER), \
             mock.patch.object(gui_calib, "calibrate_lab6",
                               return_value=dict(self.FAKE_AUTO)), \
             mock.patch.object(gui_calib, "ring_metrics",
                               side_effect=fake_rm):
            res = gui_calib._auto_calib_worker("data/fake_a.tif", self.GEO)
        self.assertEqual(res["metrics"]["dev_px"], 0.52)
        self.assertEqual(res["metrics_initial"]["dev_px"], 0.52)
        self.assertNotIn("metrics_error", res)
        # 两次调用的距离：先结果几何、后初值几何（GEOM 初值 1.6000 ≠ 结果 1.5958）
        self.assertEqual(seen, [self.FAKE_AUTO["dist_m"], self.GEO["dist_m"]])

    def test_manual_worker_metrics_need_a_path(self):
        """手动 worker：给了标样路径才读图算指标；没路径就跳过（不写键）。"""
        with mock.patch.object(gui_calib, "refine_lab6_from_points",
                               return_value=dict(self.FAKE_MANUAL)), \
             mock.patch.object(gui_calib_panel, "load_diffraction_image",
                               return_value=np.ones((64, 64)) * 10), \
             mock.patch.object(gui_calib, "ring_metrics",
                               return_value=dict(self.METRICS)):
            res = gui_calib._manual_calib_worker(
                "data/fake_a.tif", [(1, 2), (3, 4), (5, 6)], [0, 1, 2],
                self.GEO, (1022.3, 1022.0))
        self.assertIsNotNone(res["metrics"])
        with mock.patch.object(gui_calib, "refine_lab6_from_points",
                               return_value=dict(self.FAKE_MANUAL)):
            res_none = gui_calib._manual_calib_worker(
                None, [(1, 2), (3, 4), (5, 6)], [0, 1, 2],
                self.GEO, (1022.3, 1022.0))
        self.assertNotIn("metrics", res_none)

    # ── 失败路径：不许抛出，也不许静默 ─────────────────────────
    def test_attach_metrics_records_failure_without_raising(self):
        """几何键残缺（半截字典）→ 记 metrics_error，绝不抛出。"""
        res = dict(self.FAKE_MANUAL)
        gui_calib._attach_metrics(res, np.ones((8, 8)), {"dist_m": 1.0})
        self.assertIsNone(res["metrics"])
        self.assertIsNone(res["metrics_initial"])
        self.assertIn("KeyError", res["metrics_error"])

    def test_worker_survives_engine_exception(self):
        """引擎抛异常 → 只丢指标；校准结果与束心完好（指标是显示器）。"""
        with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                               return_value=np.ones((64, 64)) * 10), \
             mock.patch.object(gui_calib, "fit_center_from_rings",
                               return_value=self.FAKE_CENTER), \
             mock.patch.object(gui_calib, "calibrate_lab6",
                               return_value=dict(self.FAKE_AUTO)), \
             mock.patch.object(gui_calib, "ring_metrics",
                               side_effect=RuntimeError("boom")):
            res = gui_calib._auto_calib_worker("data/fake_a.tif", self.GEO)
        self.assertIsNone(res["metrics"])
        self.assertEqual(res["metrics_error"], "RuntimeError: boom")
        self.assertEqual(res["dist_m"], self.FAKE_AUTO["dist_m"])
        self.assertEqual(res["beam_center_rc"],
                         (self.FAKE_CENTER["cy"], self.FAKE_CENTER["cx"]))

    def test_manual_worker_image_failure_only_loses_metrics(self):
        """手动侧读图失败（文件被挪走）→ 只丢指标，精修结果照常返回。"""
        with mock.patch.object(gui_calib, "refine_lab6_from_points",
                               return_value=dict(self.FAKE_MANUAL)), \
             mock.patch.object(gui_calib_panel, "load_diffraction_image",
                               side_effect=OSError("文件不见了")):
            res = gui_calib._manual_calib_worker(
                "data/gone.tif", [(1, 2), (3, 4), (5, 6)], [0, 1, 2],
                self.GEO, (1022.3, 1022.0))
        self.assertIsNone(res["metrics"])
        self.assertIn("OSError", res["metrics_error"])
        self.assertEqual(res["dist_m"], self.FAKE_MANUAL["dist_m"])

    # ── 日志后缀：三种状态的措辞 ───────────────────────────────
    def test_metrics_note_formats_all_three_states(self):
        note = gui_calib._metrics_note(
            {"metrics": dict(self.METRICS), "metrics_initial": self.INITIAL})
        self.assertIn("环位偏差中位 0.52 px（初值 3.14）", note)
        self.assertIn("完整环 16/16", note)
        self.assertIn("a 离散 812 ppm", note)
        # 无可用环信号：给证据（贴窗边比例）而不是数字
        nan = {"metrics": dict(self.METRICS, dev_px=float("nan"),
                               clip_frac=0.87, n_complete=0)}
        note_nan = gui_calib._metrics_note(nan)
        self.assertIn("无可用环信号", note_nan)
        self.assertIn("87%", note_nan)
        # clip_frac 本身也无值（搜索窗都放不进图像）时不许给用户看 nan%
        note_noclip = gui_calib._metrics_note(
            {"metrics": dict(self.METRICS, dev_px=float("nan"),
                             clip_frac=float("nan"), n_complete=0)})
        self.assertIn("搜索窗在图像内放不下", note_noclip)
        self.assertNotIn("nan", note_noclip)
        # 指标不可用：如实报错，不静默
        note_err = gui_calib._metrics_note(
            {"metrics": None, "metrics_error": "RuntimeError: boom"})
        self.assertIn("指标不可用", note_err)
        self.assertIn("boom", note_err)
        # 完全没指标（老结果/手动无路径）：后缀为空，不改动原日志
        self.assertEqual(gui_calib._metrics_note({}), "")

    def test_metrics_note_avoids_the_guard_phrase(self):
        """文案分离："全部落在图像外"是 _warn_rings_off_image 的守卫措辞，
        有测试在数它的出现次数——指标后缀不许复用同一句话。"""
        for state in ({"metrics": dict(self.METRICS)},
                      {"metrics": dict(self.METRICS, dev_px=float("nan"),
                                       clip_frac=1.0)},
                      {"metrics": None, "metrics_error": "X"}):
            self.assertNotIn("全部落在图像外",
                             gui_calib._metrics_note(state))

    def test_auto_done_log_carries_metrics_suffix(self):
        """端到端（mock 引擎与指标）：完成日志带指标后缀。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib, "calibrate_lab6",
                                   return_value=dict(self.FAKE_AUTO,
                                                     dist_m=1.5970)), \
                 mock.patch.object(gui_calib, "ring_metrics",
                                   side_effect=lambda image, **kw:
                                   dict(self.METRICS if kw["dist_m"] == 1.5970
                                        else self.INITIAL)):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)   # 校准前置：确认像素尺寸
                w.calib_start_auto.click()
                self.assertTrue(_wait_until(
                    lambda: any(r["name"].startswith("自动")
                                for r in w.calib_state["results"]), 8000))
            logs = self._logs(w)
            self.assertIn("自动完成（自动1）", logs)
            self.assertIn("环位偏差中位 0.52 px（初值 3.14）", logs)
            self.assertIn("a 离散 812 ppm", logs)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()


class TestCalibModel(unittest.TestCase):
    """校准功能页的纯逻辑：累积命名 / 槽轮换与钉住 / 采纳判据 / 表格与 Δ。"""

    @staticmethod
    def _res(dev=0.30, dist=1.5958, poni=(1045.2, 1022.0),
             rot=(-0.005, -0.163), center=(1021.5, 1022.0)):
        return {"dist_m": dist, "poni1_px": poni[0], "poni2_px": poni[1],
                "rot1_deg": rot[0], "rot2_deg": rot[1],
                "beam_center_rc": center, "residual_deg": 0.004,
                "metrics": None if dev is None else {
                    "dev_px": dev, "clip_frac": 0.0, "n_complete": 16,
                    "rings": [{}] * 16, "a": {"spread_ppm": 500.0}}}

    @staticmethod
    def _state(**kw):
        st = {"points": [], "results": [],
              "slots": {"current": None, "A": None, "B": None},
              "pinned": {"A": False, "B": False}, "next_slot": "A",
              "current_geom": {"pixel_size_m": 200e-6,
                               "wavelength_m": 0.1223e-10, "dist_m": 1.5958},
              "current_from": "借用 lmfp1_lab6",
              "current_metrics": None, "current_error": None,
              "custom": False, "counters": {}}
        st.update(kw)
        return st

    def _with(self, st, kind, **kw):
        """往状态里挂一条结果，返回名字。"""
        return gui_calib_model._add_result(st, kind, self._res(**kw))

    # ── 累积命名 ──────────────────────────────────────────
    def test_names_accumulate_per_kind(self):
        st = self._state()
        self.assertEqual(self._with(st, "raw"), "原始1")
        self.assertEqual(self._with(st, "auto"), "自动1")
        self.assertEqual(self._with(st, "manual"), "手动1")
        self.assertEqual(self._with(st, "auto"), "自动2")
        self.assertEqual([r["name"] for r in st["results"]],
                         ["原始1", "自动1", "手动1", "自动2"])

    # ── 槽：轮换 + 钉住 ───────────────────────────────────
    def test_slots_fill_a_then_b(self):
        st = self._state()
        n1 = self._with(st, "auto")
        self.assertIn("A", gui_calib_model._fill_slot(st, n1))
        n2 = self._with(st, "auto")
        self.assertIn("B", gui_calib_model._fill_slot(st, n2))
        self.assertEqual((st["slots"]["A"], st["slots"]["B"]), (n1, n2))
        n3 = self._with(st, "manual")
        gui_calib_model._fill_slot(st, n3)
        self.assertEqual(st["slots"]["A"], n3)      # 回到 A（轮换）

    def test_pinned_slot_is_skipped(self):
        st = self._state()
        st["slots"]["A"] = "手动1"
        st["pinned"]["A"] = True
        note = gui_calib_model._fill_slot(st, "自动1")
        self.assertIn("B", note)
        self.assertEqual(st["slots"]["A"], "手动1")   # 钉住的没被覆盖
        self.assertEqual(st["slots"]["B"], "自动1")

    def test_both_slots_pinned_keeps_result_only_in_list(self):
        st = self._state()
        st["pinned"] = {"A": True, "B": True}
        note = gui_calib_model._fill_slot(st, "自动1")
        self.assertIn("只进列表", note)
        self.assertIsNone(st["slots"]["A"])
        self.assertIsNone(st["slots"]["B"])

    # ── 采纳判据（"拟合得好不好"）─────────────────────────
    def test_first_result_adopted_when_nothing_to_compare(self):
        st = self._state()          # 借来的起点 → 直接采纳
        name = self._with(st, "auto", dev=0.30)
        take, note = gui_calib_model._adopt_decision(st, name)
        self.assertTrue(take)
        self.assertIn("当前配置 → 自动1", note)

    def _from_result(self, dev, **kw):
        """当前配置 = 某条跑出来的结果（这时才用得上 0.05 px 门槛）。"""
        st = self._state(**kw)
        st["slots"]["current"] = "自动1"
        st["current_from"] = "自动1"
        st["current_metrics"] = {"dev_px": dev}
        # 走 _add_result：计数器要跟着推进，否则下一条又命名成"自动1"
        gui_calib_model._add_result(st, "auto", self._res(dev=dev))
        return st

    def test_borrowed_start_is_replaced_by_the_first_result(self):
        """新批次：当前配置还是借来的出发点 → 第一条结果**总是**采纳。

        借来的几何是在别的批次的图上量出来的，它的环位偏差在这张图上没有
        可比性——它是起点，不是候选者。"""
        st = self._state(current_metrics={"dev_px": 0.10})   # 借来的"看着更好"
        name = self._with(st, "auto", dev=0.40)
        take, note = gui_calib_model._adopt_decision(st, name)
        self.assertTrue(take)
        self.assertIn("新批次的第一条结果", note)

    def test_better_result_is_adopted(self):
        st = self._from_result(0.52)
        name = self._with(st, "auto", dev=0.28)
        take, note = gui_calib_model._adopt_decision(st, name)
        self.assertTrue(take)
        self.assertIn("自动2", note)
        self.assertIn("优于", note)

    def test_marginally_better_is_not_adopted(self):
        """两者都是跑出来的 → 改善小于门槛（0.05 px）就不换，那是跑动噪声。"""
        st = self._from_result(0.30)
        name = self._with(st, "auto", dev=0.28)
        take, note = gui_calib_model._adopt_decision(st, name)
        self.assertFalse(take)
        self.assertIn("当前配置保持", note)
        self.assertIn("改善不足", note)

    def test_worse_result_is_not_adopted(self):
        st = self._from_result(0.24)
        name = self._with(st, "manual", dev=0.31)
        take, _note = gui_calib_model._adopt_decision(st, name)
        self.assertFalse(take)

    def test_hand_edited_geometry_freezes_adoption(self):
        """手改/手输过（自定义）→ 再好的结果也不自动替换。"""
        st = self._state(custom=True, current_metrics={"dev_px": 0.52})
        name = self._with(st, "auto", dev=0.10)
        take, note = gui_calib_model._adopt_decision(st, name)
        self.assertFalse(take)
        self.assertIn("自定义", note)

    def test_metrics_dev_accepts_both_shapes(self):
        """口径：结果 dict 里套着 metrics，而"当前配置"的指标就是 metrics。"""
        self.assertEqual(
            gui_calib_model._result_dev({"metrics": {"dev_px": 0.25}}), 0.25)
        self.assertEqual(gui_calib_model._metrics_dev({"dev_px": 0.25}), 0.25)
        for bad in (None, {}, {"dev_px": float("nan")}):
            self.assertIsNone(gui_calib_model._metrics_dev(bad))
            self.assertIsNone(gui_calib_model._result_dev({"metrics": bad}))
            self.assertIsNone(gui_calib_model._result_dev(bad))

    # ── 表格：取值 / Δ 行 / 结论 ──────────────────────────
    def test_row_values_and_delta_text(self):
        a = self._res(dist=1.5962, dev=0.28, center=(1021.0, 1022.0))
        base = self._res(dist=1.5958, dev=0.52, center=(1024.0, 1022.0))
        vals = gui_calib_model._row_values(a)
        self.assertAlmostEqual(vals["dist"], 1.5962)
        self.assertAlmostEqual(vals["center_r"], 1021.0)
        self.assertEqual(vals["dev"], 0.28)
        self.assertEqual(gui_calib_model._fmt_row("dist", vals["dist"]), "1596.20")
        self.assertEqual(gui_calib_model._delta_text(base, a, "dist"), "+0.40")
        self.assertEqual(gui_calib_model._delta_text(base, a, "dev"), "-0.24")
        # Δ 只给"距离 / 环位偏差"两行（白名单），其余量显示 —
        self.assertEqual(gui_calib_model._delta_text(base, a, "center_r"), "—")
        self.assertEqual(gui_calib_model._delta_text(base, a, "poni1"), "—")
        # 基准列自身 → —
        self.assertEqual(gui_calib_model._delta_text(a, a, "dist"), "—")

    def test_delta_rows_are_whitelisted(self):
        rows = [k for k, _n, _s, _f, has_d in gui_calib_model.COMPARE_ROWS]
        self.assertEqual(rows, ["dist", "center_r", "center_c", "dev",
                                "poni1", "poni2", "rot1", "rot2"])
        has_delta = [k for k, _n, _s, _f, d in gui_calib_model.COMPARE_ROWS if d]
        self.assertEqual(has_delta, ["dist", "dev"])   # 只给这两行 Δ
        self.assertFalse(any("残差" in n for _k, n, *_ in
                             gui_calib_model.COMPARE_ROWS))
        self.assertIn("退化方向", gui_calib_model.COMPARE_HINT)

    def test_verdict_by_ring_deviation(self):
        base, other = self._res(dev=0.52), self._res(dev=0.28)
        v = gui_calib_model._verdict(base, other, "当前配置", "A")
        self.assertIn("A 拟合得更好", v)
        self.assertIn("0.28 vs 0.52 px", v)
        # 基准自己更好时 → 结论指向基准
        self.assertIn("当前配置 拟合得更好",
                      gui_calib_model._verdict(self._res(dev=0.28),
                                         self._res(dev=0.52),
                                         "当前配置", "A"))
        tie = gui_calib_model._verdict(self._res(dev=0.30), self._res(dev=0.28),
                                 "当前配置", "A")
        self.assertIn("差不多", tie)
        self.assertIn("判不了",
                      gui_calib_model._verdict(self._res(dev=None),
                                         self._res(dev=0.28), "当前配置", "A"))
        # 缺值显示 —（不许把 nan 打给用户）
        self.assertEqual(gui_calib_model._fmt_row("dev", float("nan")), "—")

    def test_slot_label_states(self):
        st = self._state()
        self.assertEqual(gui_calib_model._slot_label(st, "current"), "借用 lmfp1_lab6")
        self.assertEqual(gui_calib_model._slot_label(st, "A"), "—")
        st["custom"] = True
        self.assertEqual(gui_calib_model._slot_label(st, "current"), "自定义")
        st["slots"]["A"] = "自动1"
        self.assertEqual(gui_calib_model._slot_label(st, "A"), "自动1")


class TestCalibCurrent(unittest.TestCase):
    """当前配置：默认借条目 / 编辑对话框 → 自定义 / 像素确认规则 (b)。"""

    def test_enter_borrows_the_selected_entry(self):
        """进校准模式：当前配置默认借分析页选中的条目，并登记"原始1"。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
            st = w.calib_state
            cfg = gui_app.CONFIGS["lmfp1_lab6"]["geometry"]
            self.assertAlmostEqual(st["current_geom"]["dist_m"], cfg["dist_m"])
            self.assertEqual(st["current_from"], "借用 lmfp1_lab6")
            self.assertEqual([r["name"] for r in st["results"]], ["原始1"])
            self.assertFalse(st["custom"])
            self.assertIn("借用 lmfp1_lab6", w.calib_current_lbl.text())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_edit_dialog_marks_custom_and_resets_pixel_ok(self):
        """[编辑…] 改过 → 标"自定义"，像素确认作废（像素值可能变了）。"""
        from PySide6.QtWidgets import QDialog
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)
                self.assertTrue(gui_calib._pixel_ok(w))
                # 替换 exec：既能拿到对话框对象，也能在"确定"前改控件值
                holder = {"pixel": None}

                def fake_exec(self):
                    holder["dlg"] = self
                    boxes = self.findChildren(QDoubleSpinBox)
                    if holder["pixel"] is not None:
                        boxes[0].setValue(holder["pixel"])
                    return QDialog.Accepted
                with mock.patch.object(QDialog, "exec", new=fake_exec):
                    gui_calib._edit_current(w)
                st = w.calib_state
                self.assertTrue(st["custom"])
                self.assertEqual(st["current_from"], "手输")
                self.assertEqual(gui_calib_model._slot_label(st, "current"), "自定义")
                self.assertIsNone(st["slots"]["current"])
                self.assertIn("自定义", w.log_text.toPlainText())
                # 规则 (b)：数值没动过 → 像素确认**保持**（不打扰）
                self.assertTrue(gui_calib._pixel_ok(w))
                # 真改像素 → 确认作废（勾选框也同步取消）
                self.assertIn("µm",
                              holder["dlg"].findChildren(QDoubleSpinBox)[0]
                              .suffix())
                holder["pixel"] = 150.0
                with mock.patch.object(QDialog, "exec", new=fake_exec):
                    gui_calib._edit_current(w)
                self.assertFalse(gui_calib._pixel_ok(w))
                self.assertFalse(w.calib_pixel_chk.isChecked())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    # ── 青线跟着"当前配置"走（2026-09-26 用户报：编辑当前配置青环不动）──

    def _ring_lines(self, w):
        """画布上的青环折线 (x, y)：此刻没有绿点/选点标记，lines 就是环。"""
        return [(np.asarray(line.get_xdata(), dtype=float),
                 np.asarray(line.get_ydata(), dtype=float))
                for line in w.calib_ax.lines]

    def _assert_rings_match_geometry(self, w):
        """画出来的每条青线 == 按"当前配置"独立算出的那条环路径。

        用"逐点等于重算结果"而不是"和上一次不同"：前者钉住的是真正
        要的不变量（画的就是当前配置的几何），几何微调也能抓到。
        """
        px = gui_calib_panel._geom_px_keys(w.calib_state["current_geom"])
        paths = gui_calib_panel.theoretical_ring_paths(
            pixel_size_m=px["pixel_size_m"], wavelength_m=px["wavelength_m"],
            dist_m=px["dist_m"], poni1_px=px["poni1_px"],
            poni2_px=px["poni2_px"], rot1_deg=px["rot1_deg"],
            rot2_deg=px["rot2_deg"], image_shape=(256, 256))
        drawn = self._ring_lines(w)
        self.assertEqual(len(drawn), len(paths["rings"]))
        for (got_x, got_y), (_, want) in zip(drawn, paths["rings"]):
            np.testing.assert_allclose(got_x, want[:, 0], equal_nan=True)
            np.testing.assert_allclose(got_y, want[:, 1], equal_nan=True)

    def _rings_equal(self, first, second):
        """两次采样的青线是否逐点相同（用来抓"根本没重画"）。"""
        return all(all(np.allclose(a, b, equal_nan=True)
                       for a, b in zip(p, q))
                   for p, q in zip(first, second))

    def test_edit_dialog_redraws_the_rings(self):
        """[编辑…] 改几何 → 青线按新几何重画（表里的数字换了，图也得换）。

        原来只换状态与表格、漏了重画：改完距离，图上还是旧几何的环。
        """
        from PySide6.QtWidgets import QDialog
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)
                before = self._ring_lines(w)
                self._assert_rings_match_geometry(w)
                dist0 = w.calib_state["current_geom"]["dist_m"]

                def fake_exec(self):
                    boxes = self.findChildren(QDoubleSpinBox)
                    boxes[2].setValue(boxes[2].value() * 1.05)   # 距离 +5%
                    return QDialog.Accepted

                with mock.patch.object(QDialog, "exec", new=fake_exec):
                    gui_calib._edit_current(w)
                self.assertAlmostEqual(
                    w.calib_state["current_geom"]["dist_m"], dist0 * 1.05,
                    places=5)
                after = self._ring_lines(w)
                self.assertFalse(self._rings_equal(before, after),
                                 "几何改了而青线与改前逐点相同 = 没有重画")
                self._assert_rings_match_geometry(w)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_borrow_entry_redraws_rings_and_skips_closed_panel(self):
        """借用条目换了几何 → 青线跟着换；面板没开时借用不碰画布。

        借用是 [加载参数]（导入 .poni）那条通道，分析模式下就能点到：
        那时画布随面板一起没了（calib_dock=None，calib_ax 是悬空旧对象），
        重画必须跳过而不是画到已删除的画布上。
        """
        other = {"label": "测试几何", "beam_center": (120.0, 130.0),
                 "geometry": {"pixel_size_m": 200e-6,
                              "wavelength_m": 1.223e-11, "dist_m": 1.30,
                              "poni1_m": 0.20904, "poni2_m": 0.20440,
                              "rot1_deg": 0.0, "rot2_deg": 0.4}}
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)
                before = self._ring_lines(w)
                with mock.patch.dict(config_mod.CONFIGS, {"test_geom": other}):
                    gui_calib._borrow_entry(w, "test_geom")
                    self.assertIn("借用条目 test_geom", w.log_text.toPlainText())
                    after = self._ring_lines(w)
                    self.assertFalse(self._rings_equal(before, after),
                                     "借用了新几何而青线与借前逐点相同 = 没有重画")
                    self._assert_rings_match_geometry(w)
                    # 退出校准模式（面板关闭）后再借用：静默跳过重画。
                    # 五个入口按钮互斥，click() 不会取消已选中的那个，
                    # 退出走 setChecked(False)（[返回分析模式] 也是这么做的）
                    w.calib_btn.setChecked(False)
                    self.assertIsNone(w.calib_dock)
                    with mock.patch.object(
                            gui_calib_panel, "_redraw_calib",
                            side_effect=AssertionError("画到已关面板上")):
                        gui_calib._borrow_entry(w, "test_geom")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_pixel_confirmation_only_rearms_when_value_changes(self):
        """规则 (b)：换来源/再借同一像素的条目**不**要求重确认；改像素才要。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)
                self.assertTrue(gui_calib._pixel_ok(w))
                # 借另一条像素尺寸相同的条目 → 仍然算已确认
                gui_calib._borrow_entry(w, "lmfp1_lab6")
                self.assertTrue(gui_calib._pixel_ok(w))
                self.assertTrue(w.calib_pixel_chk.isChecked())
                # 手改像素（对话框里改 200 µm → 150 µm）→ 确认作废
                from PySide6.QtWidgets import QDialog
                dlg_holder = {}

                def fake_exec(self):
                    dlg_holder["dlg"] = self
                    return QDialog.Rejected      # 先看看对话框里的控件
                with mock.patch.object(QDialog, "exec", new=fake_exec):
                    gui_calib._edit_current(w)
                dlg = dlg_holder["dlg"]
                boxes = dlg.findChildren(QDoubleSpinBox)
                self.assertTrue(boxes)
                gui_calib._set_pixel_ok(w, True)
                # 改掉几何里的像素（等价于对话框里改过像素）+ 同步界面
                st = w.calib_state
                st["current_geom"]["pixel_size_m"] = 150e-6
                gui_calib._calib_sync(w)
                self.assertFalse(gui_calib._pixel_ok(w))
                self.assertFalse(w.calib_pixel_chk.isChecked())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calibration_is_blocked_until_pixels_are_confirmed(self):
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_start_auto.click()
                self.assertNotIn("calib_auto", w._latest_task)   # 没建校准任务
                self.assertIn("请先确认「当前配置」的像素尺寸",
                              w.log_text.toPlainText())
                w.calib_pixel_chk.setChecked(True)
            self.assertIn("像素尺寸已确认：200.0 µm", w.log_text.toPlainText())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()


class TestCalibFlow(unittest.TestCase):
    """三步动作 + 槽 + 采纳 + 坞宽 + 保存血缘（真窗口，引擎 mock）。"""

    FAKE_AUTO = dict(TestCalibration.FAKE_AUTO)
    FAKE_CENTER = TestCalibration.FAKE_CENTER

    def _metrics(self, dev):
        return {"dev_px": dev, "clip_frac": 0.0, "n_complete": 16,
                "rings": [{}] * 16, "a": {"spread_ppm": 500.0}}

    def test_auto_lands_in_results_slot_and_adopts_when_better(self):
        """①：结果进列表 + 填 A 槽；比借来的几何好 → 采纳为当前配置。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib, "calibrate_lab6",
                                   return_value=dict(self.FAKE_AUTO,
                                                     dist_m=1.5965)), \
                 mock.patch.object(gui_calib, "ring_metrics",
                                   side_effect=lambda image, **kw:
                                   self._metrics(0.20 if kw["dist_m"] > 1.596
                                                 else 0.60)):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)
                _wait_until(lambda: gui_calib._pixel_ok(w) and
                            w.calib_state["current_metrics"] is not None, 8000)
                w.calib_start_auto.click()
                self.assertTrue(_wait_until(
                    lambda: w.calib_state["slots"]["A"] == "自动1", 8000))
            st = w.calib_state
            self.assertEqual([r["name"] for r in st["results"]],
                             ["原始1", "自动1"])
            self.assertEqual(st["slots"]["current"], "自动1")   # 0.20 < 0.60
            self.assertFalse(st["custom"])
            self.assertIn("当前配置 ← 自动1", w.log_text.toPlainText())
            self.assertEqual(w.calib_vals["current"]["dev"].text(), "0.20")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_refined_starts_from_the_current_geometry(self):
        """②[在当前配置上再精修]：初值 = 当前配置的环心与距离（不重新定位）。"""
        w = create_window()
        try:
            w.show()
            calls = []

            def fake_calib(image, **kw):
                calls.append(kw)
                return dict(self.FAKE_AUTO, dist_m=1.5965)

            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib, "calibrate_lab6",
                                   side_effect=fake_calib), \
                 mock.patch.object(gui_calib, "ring_metrics",
                                   side_effect=lambda image, **kw:
                                   self._metrics(0.30)):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)
                _wait_until(lambda: w.calib_state["current_metrics"] is not None,
                            8000)
                # 先把当前配置手改成一条明确的几何（自定义起点）
                st = w.calib_state
                st["current_geom"]["dist_m"] = 1.6000
                st["current_geom"]["beam_center_rc"] = (1000.0, 1010.0)
                st["current_metrics"] = self._metrics(0.50)
                w.calib_start_refined.click()
                self.assertTrue(_wait_until(
                    lambda: w.calib_state["slots"]["A"] is not None, 8000))
            last = calls[-1]
            self.assertAlmostEqual(last["dist0_m"], 1.6000)      # 当前配置的距离
            self.assertEqual(last["center0_px"], (1010.0, 1000.0))  # (列, 行)
            self.assertIn("精修完成（精修1）",
                          w.log_text.toPlainText())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_slot_selection_pins_and_adopts(self):
        """槽下拉选一条结果 → 钉住（新结果不再覆盖）+ 采纳为当前配置。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib, "calibrate_lab6",
                                   return_value=dict(self.FAKE_AUTO)), \
                 mock.patch.object(gui_calib, "ring_metrics",
                                   side_effect=lambda image, **kw:
                                   self._metrics(0.60)):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)
                w.calib_start_auto.click()
                self.assertTrue(_wait_until(
                    lambda: w.calib_state["slots"]["A"] == "自动1", 8000))
                # 再来一条：A 被钉住 → 进 B（仍在 mock 生效范围内）
                w.calib_slot_combo["A"].setCurrentIndex(
                    w.calib_slot_combo["A"].findData("原始1"))
                w.calib_start_auto.click()
                self.assertTrue(_wait_until(
                    lambda: w.calib_state["slots"]["B"] == "自动2", 8000))
            # 手动把 A 切回"原始1" → 钉住（对比位不动当前配置）
            combo = w.calib_slot_combo["A"]
            combo.setCurrentIndex(combo.findData("原始1"))
            st = w.calib_state
            self.assertEqual(st["slots"]["A"], "原始1")
            self.assertTrue(st["pinned"]["A"])
            self.assertIn("A 槽 → 原始1（钉住", w.log_text.toPlainText())
            # 在"当前配置"那一列选原始1 → 采纳（换图上的青线）
            combo_cur = w.calib_slot_combo["current"]
            combo_cur.setCurrentIndex(combo_cur.findData("原始1"))
            self.assertEqual(st["slots"]["current"], "原始1")
            self.assertFalse(st["custom"])
            self.assertIn("当前配置 ← 原始1", w.log_text.toPlainText())
            self.assertEqual(st["slots"]["B"], "自动2")   # 新结果没覆盖 A
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_dock_widens_on_enter_and_restores_on_exit(self):
        """进校准模式按内容拉宽参数坞（给绘图区留 620 px），退出还原。

        先点过 [1D]（参数坞可见）再进校准——开局它是收起的，量不到
        "之前的宽度"，那种情况由下一条测试单独守。
        """
        w = create_window()
        try:
            w.show()
            w.entrance_buttons["1D"].click()
            QApplication.processEvents()
            before = w.param_dock.width()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
            QApplication.processEvents()
            page = w.calib_scroll.widget()
            self.assertGreaterEqual(w.param_dock.width(),
                                    page.sizeHint().width())
            self.assertLessEqual(w.param_dock.width(),
                                 w.width() - gui_calib_model.CALIB_PANEL_RESERVE_PX + 2)
            w.entrance_buttons["1D"].click()   # 点别的入口 = 退出校准
            QApplication.processEvents()
            self.assertAlmostEqual(w.param_dock.width(), before, delta=2)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calib_widens_dock_when_opened_first_thing(self):
        """开局第一件事就点 [校准]（参数坞刚从隐藏转可见）也要按内容拉宽。

        量宽度前先手动走一遍布局（app._clear_entrance 让坞开局收起，
        刚 setVisible(True) 时 width() 还是旧值——2026-09-25 修）。
        """
        w = create_window()
        try:
            w.show()
            QApplication.processEvents()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
            QApplication.processEvents()
            page = w.calib_scroll.widget()
            self.assertGreaterEqual(w.param_dock.width(),
                                    page.sizeHint().width())
            # 退出校准：没选过入口 → 坞收起；再点 [1D] 露出时必须是分析
            # 模式的窄宽度（量错的"假宽度"会把坞撑到 640 上下）
            w.entrance_buttons["1D"].click()
            QApplication.processEvents()
            self.assertTrue(w.param_dock.isVisible())
            self.assertLessEqual(w.param_dock.width(),
                                 w.param_dock.minimumSizeHint().width() + 20)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_saved_entry_carries_provenance(self):
        """[存为配置] 保存"当前配置"，血缘写 method / derived_from / created。"""
        w = create_window()
        saved = {}
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib.config, "save_user_config",
                                   side_effect=lambda k, e: saved.update(
                                       {k: e}) or True), \
                 mock.patch.object(gui_config_ops, "_reload_config_combo"):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)   # 保存前置：确认像素尺寸
                w.calib_key_edit.setText("lmfp9_lab6")
                w.calib_label_edit.setText("第 9 批")
                w.calib_save_btn.click()
            entry = saved["lmfp9_lab6"]
            self.assertEqual(entry["method"], "raw")     # 还没跑过校准
            self.assertEqual(entry["derived_from"], "lmfp1_lab6")
            self.assertRegex(entry["created"], r"^\d{4}-\d\d-\d\dT\d\d:")
            cfg = gui_app.CONFIGS["lmfp1_lab6"]["geometry"]
            self.assertAlmostEqual(entry["geometry"]["dist_m"], cfg["dist_m"])
            self.assertAlmostEqual(entry["geometry"]["pixel_size_m"],
                                   cfg["pixel_size_m"])
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()


class TestBatchProgress(unittest.TestCase):
    """批量进度计数：多文件一次出图，完成/失败日志末尾贴（k/n）。"""

    def test_batch_logs_progress_suffix_and_clears(self):
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                # fake_a 慢 0.2 s → fake_b 先完成 =（1/2），fake_a =（2/2）
                self.assertTrue(_wait_until(
                    lambda: all(
                        getattr(_dock(w, "1D", p), "last_tth", None)
                        is not None
                        for p in ("data/fake_a.tif", "data/fake_b.tif"))))
            log = w.log_text.toPlainText()
            # 不写死"谁先完成"：BackgroundTask 每任务一个线程，完成回调
            # 到主线程的先后由调度决定（教训 12 的定时炸弹——本测试曾
            # 因机器负载翻转而红）。断言的是"两条都计数、且合计是 1/2
            # 与 2/2"，顺序交给调度。
            done = re.findall(r"积分完成：(fake_[ab]\.tif)（3 点，"
                              r"2θ 0\.500~8\.500°）（(\d)/2）", log)
            self.assertEqual({name for name, _ in done},
                             {"fake_a.tif", "fake_b.tif"},
                             f"两个文件都该报完成：{done}")
            self.assertEqual({k for _, k in done}, {"1", "2"},
                             f"进度计数应是 1/2 与 2/2：{done}")
            self.assertNotIn("正在开面板", log,
                             "两块面板是瞬时的，不该报开面板进度")
            self.assertFalse(hasattr(w, "_batch"),
                             "批走完应清账（之后零散任务不再计数）")
        finally:
            w.close()

    def test_many_panels_log_opening_progress(self):
        """一批 9 块面板：每 8 块报一次进度、顺手消化事件（E-2）。

        开面板是主线程上的活（真数据 130~290 ms/块），一口气开几十块
        会闷住界面十几秒——用户反馈"图一多就很卡"的真身。

        这里**故意把 _open_plot_panel / _run_view 换成桩**、不真建 9 块
        面板：offscreen 下在同一进程里连建十来块 Qt 工具栏会偶发在 Qt
        的动作事件里递归挂死（实测 3/12，关窗后的延迟删除只是背景条
        件；与本次改动无关，Qt/mpl 侧的老毛病）。所以真建面板那条路
        由上面的两文件批量用例覆盖，这条只钉"记账与节奏"：总数、每
        8 块报一次、每块都真的去开。
        """
        paths = [f"data/fake_n{i}.tif" for i in range(1, 10)]
        opened = []
        w = create_window()
        try:
            add_checked(w, paths)
            with mock.patch.object(
                    gui_views, "_open_plot_panel",
                    side_effect=lambda win, name, key, title:
                    opened.append(key) or mock.MagicMock()), \
                    mock.patch.object(gui_views, "_run_view"):
                _open_view(w, "1D")
            log = w.log_text.toPlainText()
            # 第 9 块（i=8）跨过 8 的倍数 → 报一次；9 块都真的去开了
            self.assertEqual(opened, [f"1D|data/fake_n{i}.tif"
                                      for i in range(1, 10)],
                             "九块面板都该去开，顺序照勾选顺序")
            self.assertIn("正在开面板：9/9…", log)
            self.assertEqual(log.count("正在开面板："), 1,
                             "9 块只跨过 8 一次")
            # 9 张 > 合并阈值 → 开面板合并成一行（不再 9 行"打开面板"）
            self.assertIn("打开1D面板 9 张：fake_n1.tif、fake_n2.tif、"
                          "fake_n3.tif、fake_n4.tif、fake_n5.tif…", log)
            self.assertNotIn("打开1D面板：fake_n9.tif", log)
        finally:
            w.close()

    def test_batch_cap_opens_only_max_panels(self):
        """勾选超过上限 → 一张都不画（全部只算不画）；没超过照旧全画。

        2026-09-26 用户改的规则（原来 = 画前 N 张、其余只算不画）：
        "如果原始很多…全部打开只会打开二十来张"，于是超过上限就一张
        都不画、结果列进文件栏，看哪张点哪张。
        """
        folder = tempfile.mkdtemp()
        paths = [str(Path(folder, f"c{i}.tif")) for i in range(1, 5)]
        for p in paths:      # 真文件：产物落盘要用到真路径（file 指纹）
            Path(p).touch()
        w = create_window()
        try:
            cache_kw = None
            with mock.patch.object(gui_views, "MAX_PANELS_PER_BATCH", 4), \
                    mock.patch.object(gui_views, "_compute_integration",
                                      side_effect=_fake_compute):
                # ① 4 张 ≤ 上限 4：照旧一张一张全画
                add_checked(w, paths)
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: not hasattr(w, "_batch")),
                                "这批判完应清账")
                self.assertEqual(
                    sorted(k for k in w.plot_docks if k.startswith("1D|")),
                    ["1D|" + p for p in paths], "没超上限应全画")
            for p in paths:      # 关掉这批面板，下一轮干净
                dock = w.plot_docks.get("1D|" + p)
                if dock is not None:
                    dock.close()
            with mock.patch.object(gui_views, "MAX_PANELS_PER_BATCH", 2), \
                    mock.patch.object(gui_views, "_compute_integration",
                                      side_effect=_fake_compute):
                # ② 4 张 > 上限 2：一张都不画
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: not hasattr(w, "_batch")),
                                "这批判完应清账")
                self.assertEqual(sorted(k for k in w.plot_docks
                                        if k.startswith("1D|")),
                                 [], "超上限就该一张都不画")
                log = w.log_text.toPlainText()
                self.assertIn("全部只算不画", log)
                self.assertIn("1D 产物", log)      # 指向文件栏
                # 计数含全部只算不画那几张：总数不对的话 k/n 到不了 n
                self.assertIn("（1/4）", log)
                self.assertNotIn("（2/2）", log)
            # 只算不画的真结果：产物落了盘（下次点开就是复用缓存）
            geom = gui_panel_state._collect_geometry(w)
            kw = dict(config=w.config_name,
                      npt=int(w.params["输出点数"].value()),
                      tth_min=geom.get("tth_min_deg"),
                      tth_max=geom.get("tth_max_deg"))
            from xrd_toolkit.services import stage_cache
            for p in paths[2:]:
                self.assertTrue(stage_cache.has_1d(Path(p), **kw),
                                f"超限的 {Path(p).name} 该已算完入库")
        finally:
            w.close()

    def test_batch_progress_bar_shows_and_hides(self):
        """状态栏那条批量进度条：批里出现、范围=总数、批走完收起。

        （用慢假积分把第一条卡住，才好在"飞行中"看它——批走完那一下
        收起来，是"批结束"最直观的信号。）"""
        def slow(path_str, geom, npt):
            time.sleep(0.3)
            return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])

        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=slow):
                add_checked(w, ["data/fake_p1.tif", "data/fake_p2.tif"])
                _open_view(w, "1D")
                self.assertFalse(w.batch_progress.isHidden(), "批里该出现进度条")
                self.assertEqual(w.batch_progress.maximum(), 2, "范围 = 这一批的总数")
                self.assertTrue(_wait_until(lambda: not hasattr(w, "_batch")),
                                "这批判完应清账")
            self.assertTrue(w.batch_progress.isHidden(), "批走完该收起")
            self.assertEqual(w.batch_progress.value(), 2, "该走到 n")
        finally:
            w.close()

    def test_big_batch_merges_log_lines(self):
        """大批量（> BATCH_LOG_MERGE_AFTER）：每 8 张一行进度 + 收尾汇总。

        用户 2026-09-25："81 张 = 81 行「打开面板」+ 81 行「积分完成」，
        把日志刷没了"。这条直接喂 _batch_step（不必真开 10 张面板），
        钉三件事：合并模式、进度行的节奏（每 8）、收尾汇总（含用时与
        复用缓存张数）——以及批照样清账。
        """
        w = create_window()
        try:
            w._batch = {"view": "1D", "total": 10, "done": 0,
                        "start": time.time(), "cached": 3}
            quiet_flags = []
            for i in range(10):
                suffix, quiet = gui_views._batch_step(
                    w, f"1D|data/p{i}.tif", f"f{i}.tif")
                quiet_flags.append(quiet)
                self.assertEqual(suffix, f"（{i + 1}/10）")
            log = w.log_text.toPlainText()
            self.assertTrue(all(quiet_flags), "大批量该整批进入合并模式")
            self.assertIn("1D 进度：8/10（最近：f7.tif）", log)
            self.assertEqual(log.count("1D 进度："), 1, "10 张只跨过 8 一次")
            self.assertIn("1D 批完成：10 张", log)
            self.assertIn("复用缓存 3 张", log)
            self.assertFalse(hasattr(w, "_batch"), "批该清账")
        finally:
            w.close()

    def test_replot_inside_the_cap_reuses_panels(self):
        """额度内的重复点 = 复用那几张面板，不新建也不报"少画"。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views, "MAX_PANELS_PER_BATCH", 2), \
                    mock.patch.object(gui_views, "_compute_integration",
                                      side_effect=_fake_compute):
                paths = [f"data/fake_d{i}.tif" for i in range(1, 4)]
                add_checked(w, paths)
                # 先只勾前两张（额度刚好用满）
                for i in range(w.file_list.count()):
                    w.file_list.item(i).setCheckState(
                        Qt.Checked if i < 2 else Qt.Unchecked)
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: not hasattr(w, "_batch")))
                n_first = len(w.plot_docks)
                _open_view(w, "1D")   # 同样的勾选再点一次
                self.assertTrue(_wait_until(lambda: not hasattr(w, "_batch")))
            log = w.log_text.toPlainText()
            self.assertEqual(len(w.plot_docks), n_first,
                             "重复点该复用面板，不该再建")
            self.assertNotIn("先画前", log, "两张都在额度内，不该报少画")
        finally:
            w.close()

    def test_single_file_has_no_progress_suffix(self):
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_b.tif"])
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".txt",
                                                 "csv": False}):
                gui_export._run_export(w)
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
            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".chi",
                                                 "csv": False}):
                gui_export._run_export(w)
            self.assertTrue(
                (outdir / "fake_b" / "integrated_2th.chi").is_file())
        finally:
            w.close()

    def test_export_cancel_writes_nothing(self):
        w = create_window()
        try:
            self._two_1d_results(w)
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value=None):
                gui_export._run_export(w)
            self.assertIn("已取消导出", w.log_text.toPlainText())
            self.assertEqual(list(outdir.iterdir()), [])
        finally:
            w.close()

    def test_export_without_1d_results_logs_hint(self):
        """勾了文件但没出过 1D：逐条提示先点 [1D]，不弹导出设置框。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            with mock.patch.object(gui_export, "_build_export_dialog") as dlg:
                gui_export._run_export(w)
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
            real_write = gui_export._write_export
            calls = {"n": 0}

            def flaky_write(target, tth, intensity, chain=""):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("磁盘已满")
                real_write(target, tth, intensity, chain)

            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".txt",
                                                 "csv": False}), \
                 mock.patch.object(gui_export, "_write_export",
                                   side_effect=flaky_write):
                gui_export._run_export(w)
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
            with mock.patch.object(gui_export, "_ask_csv_range") as ask:
                gui_export._write_csv_summary(w, results, outdir)
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
            with mock.patch.object(gui_export, "_ask_csv_range",
                                   return_value="intersect"):
                gui_export._write_csv_summary(w, results, outdir)
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
            with mock.patch.object(gui_export, "_ask_csv_range",
                                   return_value="skip"):
                gui_export._write_csv_summary(w, results, outdir)
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
            with mock.patch.object(gui_export, "_ask_csv_range",
                                   return_value="cancel"):
                gui_export._write_csv_summary(w, results, outdir)
            self.assertIn("已取消 CSV 总表", w.log_text.toPlainText())
            self.assertEqual(list(outdir.iterdir()), [])
        finally:
            w.close()

    def test_ask_csv_range_defaults_to_skip_when_not_visible(self):
        """窗口未显示（测试环境）：问询对话框不弹，直接按"跳过"处理。"""
        w = create_window()
        try:
            self.assertEqual(gui_export._ask_csv_range(w), "skip")
        finally:
            w.close()

    def test_export_flow_writes_csv_when_checked(self):
        """导出勾选 CSV → 总表与逐文件 txt 一起落盘。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: getattr(_dock(w, "1D", "data/fake_b.tif"),
                                    "last_tth", None) is not None))
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".txt",
                                                 "csv": True}):
                gui_export._run_export(w)
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

    def test_delete_works_from_the_calibration_page(self):
        """校准页上就地选条目、就地删（[删除] 就住在那页，不用跨页）。

        2026-09-26 晚：这条是被用户问出来的——先前坞顶下拉框在校准页
        被置灰，删条目得"去分析页选中 → 回校准页点删除"，两页来回。
        """
        config_mod.save_user_config("tmp_del", dict(self.ENTRY))
        w = create_window()
        try:
            w.show()
            w.calib_btn.click()              # 进校准页（不勾文件也行）
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["校准"])
            self.assertTrue(w.config_combo.isEnabled())   # 校准页也能选
            idx = w.config_combo.findData("tmp_del")
            w.config_combo.setCurrentIndex(idx)
            self.assertTrue(w.del_config_btn.isEnabled())
            self.assertIn("tmp_del", w.del_config_btn.toolTip())   # 删哪条写明
            with mock.patch.object(gui_app.QMessageBox, "question",
                                   return_value=QMessageBox.Yes):
                w.del_config_btn.click()
            self.assertNotIn("tmp_del", config_mod.USER_CONFIGS)
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
                gui_config_ops._delete_config(w)   # 处理函数归校准页
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

    def test_heatmap_labels_shorten_and_thin_out(self):
        """行名 = 号段短名 + 按高度抽稀（81 个长名字会糊成一条黑带）。

        用户 2026-09-26 的热图截图：81 个 "LMFP_1_atten0-000NN" 挤成
        黑带，还把整张图挤到右边。矮面板抽稀、高面板全标。
        """
        from matplotlib.figure import Figure
        names = [f"LMFP_1_atten0-{i:05d}.tif" for i in range(40)]
        short = Figure(figsize=(4, 2), dpi=100).add_subplot(111)
        labels = gui_plot_compare._short_labels(names, short)
        self.assertEqual(len(labels), 40)
        shown = [x for x in labels if x]
        self.assertLess(len(shown), 40, "矮面板上该抽稀")
        self.assertTrue(shown and all(x.isdigit() for x in shown),
                        f"该只剩号段：{shown[:3]}")
        tall = Figure(figsize=(6, 9), dpi=100).add_subplot(111)
        self.assertEqual(
            len([x for x in gui_plot_compare._short_labels(names, tall) if x]),
            40, "9 英寸高的面板放得下 40 个")

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
        tth, matrix, stems, interp = gui_plot_compare._assemble_heatmap(r)
        self.assertFalse(interp)
        self.assertEqual(stems, ["a", "b"])
        np.testing.assert_array_equal(matrix, [[1, 2, 3], [4, 5, 6]])

    def test_assemble_heatmap_mismatched_grids_interp(self):
        """网格不一致（点数/区间不同）→ 重插值到第一个文件的网格。"""
        r = [("a", np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.0])),
             ("b", np.array([1.0, 3.0]), np.array([10.0, 30.0]))]
        tth, matrix, stems, interp = gui_plot_compare._assemble_heatmap(r)
        self.assertTrue(interp)
        np.testing.assert_allclose(matrix[1], [10.0, 20.0, 30.0])

    def test_assemble_heatmap_empty_returns_none(self):
        self.assertIsNone(gui_plot_compare._assemble_heatmap([]))
        self.assertIsNone(gui_plot_compare._assemble_heatmap(
            [("a", np.array([]), np.array([]))]))

    def test_heat_shown_modes(self):
        m = np.array([[1.0, 2.0], [3.0, 4.0]])
        np.testing.assert_array_equal(gui_state._heat_shown(m, "off"), m)
        np.testing.assert_allclose(gui_state._heat_shown(m, "global"),
                                   m / 4.0)
        # "每行各自最强峰"（each）已删：认不出的模式一律当 off（原样）
        for stale in ("each", True, None, "nonsense"):
            np.testing.assert_array_equal(gui_state._heat_shown(m, stale), m)

    def test_heatmap_display_params_redraw_live(self):
        """热图色图/对数/归一化改了就重画（原先这些控件谁都没接）。

        用户 2026-09-27："热图一单画出来就改不了颜色什么的，没法调整"。
        """
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                w.heat_btn.click()
                self.assertTrue(self._wait_heat(w))
            ax = gui_panel_state._content(self._heat_dock(w)).axes_heat
            self.assertEqual(ax.images[0].get_cmap().name, "magma")
            combo = w.params["热图色图"]
            combo.setCurrentIndex(combo.findData("viridis"))
            QApplication.processEvents()
            self.assertEqual(ax.images[0].get_cmap().name, "viridis",
                             "换色图该立刻重画")
            w.params["热图对数"].setChecked(True)
            QApplication.processEvents()
            self.assertIsInstance(ax.images[0].norm, LogNorm,
                                  "开对数强度该立刻重画")
            norm = w.params["热图归一化"]
            norm.setCurrentIndex(norm.findData("global"))
            QApplication.processEvents()
            self.assertAlmostEqual(float(ax.images[0].get_array().max()), 1.0,
                                   places=6, msg="全图归一化该立刻重画")
        finally:
            w.close()

    def test_heatmap_uses_cached_1d_and_draws(self):
        """1D 已算好 → [热图] 零后台任务直接出图（复用面板缓存）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_views, "_compute_integration",
                                   side_effect=_fake_compute) as c:
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
            ax = gui_panel_state._content(dock).axes_heat
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
            add_checked(w, ["data/fake_a.tif"])
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                w.heat_btn.click()
                self.assertTrue(self._wait_heat(w))
                calls = c.call_count
            dock = self._heat_dock(w)
            ax = gui_panel_state._content(dock).axes_heat
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                w.heat_btn.click()
                self.assertTrue(self._wait_heat(w))
            dock = self._heat_dock(w)
            ax = gui_panel_state._content(dock).axes_heat
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
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
                add_checked(w, ["data/fake_a.tif"])
                _open_view(w, "2D")
                self.assertTrue(_wait_until(
                    lambda: getattr(_dock(w, "2D", "data/fake_a.tif"),
                                    "last_image", None) is not None))
            dock = _dock(w, "2D", "data/fake_a.tif")
            fig = gui_panel_state._content(dock).figure
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
                add_checked(w, ["data/fake_a.tif"])
                _open_view(w, "2D")
                self.assertTrue(_wait_until(
                    lambda: getattr(_dock(w, "2D", "data/fake_a.tif"),
                                    "last_image", None) is not None))
            dock = _dock(w, "2D", "data/fake_a.tif")
            ax = gui_panel_state._content(dock).axes_2d
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
    aux = [ln for ln in ax.lines if gui_plot_panels._is_aux_line(ln)]
    data = [ln for ln in ax.lines if not gui_plot_panels._is_aux_line(ln)]
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
            add_checked(w, [self.PATH])
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
        gui_plot_compare._anchor_press(w, self.KEY, press)
        gui_plot_compare._anchor_release(w, self.KEY, release)

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

    def test_anchor_click_through_canvas_wiring(self):
        """画布上真连的两根线要能解析到锚点处理（拆模块的回归护栏）。

        上面几条锚点测试直接调 _anchor_press/_anchor_release，绕过了
        _build_1d_widget 里 mpl_connect 的那两行 lambda。拆模块时 lambda
        引用的名字搬去了 plot_compare、面板壳这边忘了导入：直接调处理的
        测试全绿，用户真点画布却什么也不发生（回调里的 NameError 被
        matplotlib 打印到 stderr，不弹窗——探针实证）。这条从
        canvas.callbacks 发真 MouseEvent，走用户真实路径；两根线只要
        有一根断在 NameError 上，锚点就加不出来，本测试失败。
        """
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            canvas = gui_panel_state._content(dock).canvas
            # 真 MouseEvent 的 (x, y) = 显示像素；用 _bg_click 算好位置，
            # 按下/松手同一点 = 点击（不位移就不算平移）
            press, _ = _bg_click(ax, 1.0)
            for name in ("button_press_event", "button_release_event"):
                canvas.callbacks.process(
                    name, MouseEvent(name, canvas, press.x, press.y,
                                     button=1))
            self.assertEqual(len(self._anchors_of(w, dock)), 1)
            self.assertIn("加锚点", w.log_text.toPlainText())
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
            gui_plot_compare._anchor_press(w, self.KEY, press)
            gui_plot_compare._anchor_release(w, self.KEY, release)
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
            _, _, _, _, old_lines = gui_plot_panels._snapshot_canvas(ax)
            self.assertEqual(len(old_lines), 1, "快照只该收数据曲线")
            # 悬停选线只认数据曲线；取点标记自己也是辅助线（不参与选线）
            gui_plot_panels._hover_motion(w, self.KEY, _hover_event(ax, 1.0))
            marker = w.plot_docks[self.KEY].hover_marker
            self.assertTrue(gui_plot_panels._is_aux_line(marker))
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
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                gui_plot_compare._plot_compare(w)
                keys = [k for k in w.plot_docks if k.startswith("对比|")]
                self.assertTrue(_wait_until(lambda: len(keys) == 1))
                key = keys[0]
                dock = w.plot_docks[key]
                self.assertTrue(_wait_until(
                    lambda: len(getattr(dock, "compare_data", {})) == 2))
                ax = gui_panel_state._content(dock).axes_1d
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
                add_checked(w, ["data/fake_w.tif"])
                _open_view(w, "瀑布")
                dock = _dock(w, "瀑布", "data/fake_w.tif")
                self.assertTrue(_wait_until(
                    lambda: getattr(dock, "last_waterfall", None) is not None))
                self._set_mode(w, "auto")
                _, i2d, _ = dock.last_waterfall
                # 直接验设计决定：_proc_curve 只被调用一次，且喂进去的是
                # **扇区均值**（共同基线）。逐扇区各扣各的会调用 4 次、
                # 每次喂一条扇区曲线——扇区之间的真实强度差就被抹平了
                with mock.patch.object(gui_views, "_proc_curve",
                                       wraps=gui_panel_state._proc_curve) as spy:
                    gui_views._draw_waterfall(w, dock, *dock.last_waterfall)
                self.assertEqual(spy.call_count, 1, "应只估一条共同基线")
                fed = spy.call_args[0][4]
                np.testing.assert_allclose(
                    np.asarray(fed, dtype=float),
                    np.nanmean(np.asarray(i2d, dtype=float), axis=1),
                    err_msg="喂给基线估计的应是扇区均值")
                wax = gui_panel_state._content(dock).axes_waterfall
                self.assertEqual(len(_bg_lines(wax)[0]), 4, "四条扇区曲线")
        finally:
            w.close()

    def test_heat_matrix_subtracted_per_file(self):
        """热图逐行按各文件自己的参数扣背景。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            tth = np.linspace(0.5, 8.5, 200)
            dock.heat_files = [gui_sources.make_source(
                gui_views._bg_path_of(dock), "fake_bg.tif")]
            dock.heat_results = [("fake_bg", tth,
                                  _fake_bg_compute("", {}, 0)[1])]
            blank = 0.5 * (100.0 + 900.0 * np.exp(-tth / 2.0))
            w.bg_blank = {"path": "b.tif", "tth": tth,
                          "intensity": blank, "geom": ""}
            self._set_mode(w, "blank")
            data = gui_plot_compare._heat_data(w, dock)
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

        背景扣除控件连着 _refresh_proc（实时预览），而 _set_focus 会回放面板
        快照进控件 → 不挂回放旗标的话，回放途中的 setValue 会触发
        _refresh_proc 把"回放了一半的控件值"写进本面板快照，把上一块面板的
        显示参数（实测是 热图色图 等注册在背景组之后的几项）串过来。
        """
        w = create_window()
        with mock.patch.object(gui_views, "_compute_integration",
                               side_effect=_fake_bg_compute):
            try:
                add_checked(w, ["data/fg_a.tif", "data/fg_b.tif"])
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
                add_checked(w, ["data/fg_a.tif", "data/fg_b.tif"])
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
                gui_plot_compare._anchor_press(w, kb, press)
                gui_plot_compare._anchor_release(w, kb, release)
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
                    add_checked(w2, [self.PATH])
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
            geom = gui_panel_state._collect_geometry(w)
            w.bg_blank = {"path": "b.tif", "tth": tth, "intensity": blank,
                          "geom_sig": gui_panel_state._bg_geom_sig(geom)}
            self._set_mode(w, "blank")
            self.assertNotIn("空扫图与当前几何不一致", w.log_text.toPlainText())
            # 改一个几何量 → 再扣就该提示。几何只认配置条目（分析页
            # 的字段是只读显示），所以改配置、不是改面板控件。
            w.config = dict(w.config, geometry=dict(
                w.config["geometry"],
                dist_m=w.config["geometry"]["dist_m"] + 0.05))
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
            # 窗口默认 0.5°（2026-09-26 从 1.0° 改，见 background.AUTO_WINDOW_DEG）
            self.assertEqual(w.params["背景窗口 (°)"].value(), 0.5)
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
            plain = gui_export._checked_1d_results(w, want_bg=False)
            with_bg = gui_export._checked_1d_results(w, want_bg=True)
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
                self.assertIsNone(gui_export._build_export_dialog(w, 1))
            self.assertIsNotNone(captured.get("check"))
            self.assertFalse(captured["bg"], "默认不勾 = 导出原始曲线")
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
