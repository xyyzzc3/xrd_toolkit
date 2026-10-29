"""GUI「文件 → 视图 → 出图」交互模型的集成测试（unittest）。

用 mock 替换 _compute_integration（真积分慢且依赖 data/ 数据），
验证接线本身：
  - 选文件只登记不算（点选本身轻快，不触发任何计算）；
  - 工具栏五个入口（校准/1D/扣背景/对比│绘图）：点一个 = 参数坞翻到
    那一页 + 高亮跟着动；[校准] 另有一层"进出校准工作台"的含义
    （TestParamDockSplitLayout.test_pages_structure /
    test_entrance_switching_follows_buttons）；
  - 作图类型按钮住在「原图」页里（属性名不变）：点击 = 为当前文件
    开面板并计算该视图 → 出图（点一次算一次，纯动作不是开关）；
    页里另有 [出图（勾选文件）]，1D/扣背景/对比页各带
    自己的产出按钮（"重画"按钮 2026-09-30 已删，见那条用例）；
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
  - 参数坞的"未应用"灰字（TestPendingEdits，用户 2026-09-30 的"丙"）：
    改了要按一下才生效的控件 → 灰字亮，按了 → 灭；改动**按图记着**，
    切走再切回来还在；自动开关填的展示值不算改动；
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
    在飞任务结果迟到即作废（对比/热图 2026-10-16 栅栏后不再有在飞
    任务——只读已经算好的曲线）；
  - 背景扣除（TestBackgroundSubtraction）：三种模式 + 实时预览 + 辅助
    线隔离；锚点点选逐条调处理函数，另有一条从 canvas.callbacks 发真
    MouseEvent 的接线护栏——漏导入名字这类拆分伤只有真点画布才现形，
    直接调处理函数的测试全绿也照样漏它；
  - 校准工作台（TestCalibration / TestSaveCalibConfig）：[校准] 进
    模式 = 参数坞换页 + 中央校准图面板开出（勾选的第一个文件；没勾
    文件只提示）；自动校准后台跑（mock 引擎）→ 自动列 + 保存区提示；
    校准图点环判环吸附/拒点、撤销/清空、手动校准 → 手动列 + Δ 列；
    判环可疑（同一环号横跨两个半径）时按距离尺度重判（TestManualReindex）；
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
import builtins
import itertools
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
from matplotlib.backend_bases import MouseButton, MouseEvent
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
from xrd_toolkit import paths as xrd_paths
from xrd_toolkit.services import (export as data_export, process,
                                  recipes as recipe_store, stage_cache)
from xrd_toolkit.services.integrator import lab6_theoretical_2theta
from xrd_toolkit.gui import app as gui_app
_gui_app = gui_app
from xrd_toolkit.gui import file_dock as gui_file_dock
from xrd_toolkit.gui.files import actions as gui_file_actions
from xrd_toolkit.gui.files import importing as gui_file_importing
from xrd_toolkit.gui.state import ranges as gui_state_ranges
from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui import plot_views as gui_plot_views
from xrd_toolkit.gui import panel_state as gui_panel_state
from xrd_toolkit.gui import plot_compare as gui_plot_compare
from xrd_toolkit.gui import plot_panels as gui_plot_panels
from xrd_toolkit.gui.plot import panel_build as gui_plot_panel_build
from xrd_toolkit.gui.plot import drawing as gui_plot_drawing
from xrd_toolkit.gui.plot import workers as gui_plot_workers
from xrd_toolkit.gui.plot import runners as gui_plot_runners
from xrd_toolkit.gui.plot import spawn as gui_plot_spawn
from xrd_toolkit.gui.plot import view_flow as gui_plot_view_flow
from xrd_toolkit.gui import plot_export as gui_plot_export
from xrd_toolkit.gui import watchdog as gui_watchdog

# 卡死看门狗的现场文件默认写进仓库的 outputs/（那是给人的）；测试里换到临时
# 目录——否则跑一次全量就把 outputs/ 塞满 hang-*.txt，还会覆盖真实运行留下的
# 现场（2026-10-01 踩到：用户卡死三次，现场全被测试套件挤掉了）
gui_watchdog.OUT_DIR = Path(tempfile.mkdtemp(prefix="xrd_hang_suite_"))
from xrd_toolkit.gui import config_ops as gui_config_ops
from xrd_toolkit.gui import calib_panel as gui_calib_panel
from xrd_toolkit.gui import calib_panel_draw as gui_calib_panel_draw
from xrd_toolkit.gui import calib_model as gui_calib_model
from xrd_toolkit.gui import calib_table as gui_calib_table
from xrd_toolkit.gui import plot_export as gui_export
from xrd_toolkit.gui import customize as gui_customize
from xrd_toolkit.gui.app import create_window
# 拆分后 patch 目标 = 调用点所在的模块（gui_app 只是兼容再导出，
# 打它的名字截不住别的模块里的裸名查找）
from xrd_toolkit.gui import calib as gui_calib
from xrd_toolkit.gui import calib_metrics as gui_calib_metrics
from xrd_toolkit.gui import calib_scale as gui_calib_scale
from xrd_toolkit.gui import calib_workers as gui_calib_workers
from xrd_toolkit.gui import panel_state as gui_state
from xrd_toolkit.gui import panels as gui_panels
from xrd_toolkit.gui import plot_views as gui_views
from xrd_toolkit.gui.param_dock import helpers as gui_param_helpers
from xrd_toolkit.gui.param_dock import pages as gui_param_pages

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


def _prime_1d(w, paths, stub=None):
    """栅栏前置（2026-10-16 用户："原始数据不允许直接进行对比，必须先去
    1d 积分"）：先给这批文件点一遍 [1D] 出图，对比 / 热图才放行。

    为什么这样就能放行：假路径（data/fake_*.tif）产物落不了盘（写产物
    要先 stat 文件，静默失败），但积分出的曲线会落在各自 1D 面板的内存
    里——栅栏与取数认的就是"本会话的 1D 面板"这条（plot_views.
    _ready_curve）。假路径也不会**长出产物**，所以不会触发"新产物 =
    勾选清零"，勾选集原样保留给后面的 [对比]/[热图]。
    """
    with mock.patch.object(gui_plot_spawn, "_compute_integration",
                           side_effect=stub or _fake_compare_compute):
        add_checked(w, paths)
        _open_view(w, "1D")
        ready = _wait_until(
            lambda: all(f"1D|{p}" in w.plot_docks
                        and getattr(w.plot_docks[f"1D|{p}"], "last_tth", None)
                        is not None for p in paths), timeout_ms=10000)
    assert ready, f"1D 前置没算完：{paths}"
    return w


def recheck_all(w):
    """把全树的勾重新勾上（原始数据 + 各产物分组；「打开的图」不给勾，跳过）。

    为什么要这个辅助（用户 2026-10-01 第 8 条）：**出现新产物 = 上一轮的
    勾选清零**——勾选集是出图/批量处理的输入，不跨"产出动作"粘着。于是
    用例里"出图 → 再拿同一批去批量/对比"这类流程要显式重勾一遍；这些用例
    测的是批量/对比/上限本身，不是勾选的粘性。
    """
    for i in range(w.file_list.count()):
        node = w.file_list.item(i)
        if gui_file_dock._is_checkable(node):
            node.setCheckState(Qt.Checked)
    QApplication.processEvents()
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


def _draw_one_1d(w):
    """画一张 fake_b 的 1D 图（mock 积分），返回面板。"""
    with mock.patch.object(gui_plot_spawn, "_compute_integration",
                           side_effect=_fake_compute):
        add_checked(w, ["data/fake_b.tif"])
        _open_view(w, "1D")
        assert _wait_until(
            lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
    return _dock(w, "1D", "data/fake_b.tif")


def _kw_of(w):
    """窗口当前的积分参数（与 plot_views 取缓存键用的那一套同源）。"""
    geom = gui_state._collect_geometry(w)
    return dict(config=w.config_name,
                npt=int(w.params["输出点数"].value()),
                tth_min=geom.get("tth_min_deg"),
                tth_max=geom.get("tth_max_deg"))


_TMP_SEQ = itertools.count(1)


def _tmp_files(n=2, prefix=None):
    """临时目录里造 n 个真文件（产物要有真文件才算得出指纹）。

    prefix 默认**按调用序号唯一**（f1_、f2_…）：整个测试模块共用同一个缓存
    目录，而文件栏扫盘认产物时靠"全路径（新产物）/文件名（老产物）"——
    不同测试若用同名文件（都叫 s1.tif），扫盘会把甲的产物挂到乙名下。
    要按名字断言的地方显式传 prefix。
    """
    prefix = prefix or f"f{next(_TMP_SEQ)}_"
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


def _spiky_compute(path_str, geom, npt):
    """一条窄尖峰曲线（200 点 / 0.5–8.5°）：平滑与裁剪都看得见效果。"""
    tth = np.linspace(0.5, 8.5, 200)
    y = 10.0 + 500.0 * np.exp(-0.5 * ((tth - 4.0) / 0.1) ** 2)
    return tth, y


def _fake_compare_compute(path_str, geom, npt):
    """假积分（对比用）：同 tth 不同强度——fake_a 慢 0.2 s、最强峰 3，
    其余文件最强峰 30。归一化开关的效果一眼可分。"""
    if path_str.endswith("fake_a.tif"):
        time.sleep(0.2)
        return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])
    return np.array([0.5, 1.0, 8.5]), np.array([10.0, 20.0, 30.0])


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


def _scaled_compute(path_str, geom, npt):
    """假积分：两个文件的曲线**尺度不同**（模拟不同曝光/衰减）。

    批量处理的核心语义就靠它验：锚点跨文件只传 2θ，强度必须到每张
    自己的曲线上重取——直接套 A 的强度会把 B 的基线抬错几倍。
    """
    # 按**结尾**判断（batch_a 里也有个 b，别用 in）
    scale = 3.0 if path_str.endswith("_b.tif") else 1.0
    tth = np.array([0.5, 1.0, 2.0, 4.0, 8.5])
    return tth, np.array([1.0, 2.0, 3.0, 2.0, 1.0]) * scale


def _summary_csv(path) -> tuple:
    """读全部数据.csv：返回 (列名列表, 数据数组)。

    2026-10-03 起说明块在最**上面**（用户："把无关信息放到最上面"），
    跳掉 `#` 注释、第一行非注释就是表头——等价于 pandas 的
    `read_csv(path, comment="#")`；原来的 `np.loadtxt(skiprows=1)`
    已不适用（注释行数随列数变）。
    """
    body = [ln for ln in Path(path).read_text(encoding="utf-8-sig")
            .splitlines() if not ln.startswith("#")]
    return body[0].split(","), np.loadtxt(body[1:], delimiter=",")


def _only_batch_dir(outdir: Path) -> Path:
    """批量导出目录：outdir 下恰好一个文件夹（名字由默认/自定义规则定）。"""
    found = [p for p in Path(outdir).iterdir() if p.is_dir()]
    assert len(found) == 1, f"应当恰好一个批量目录，实际 {found}"
    return found[0]


def _only_file(outdir: Path, suffix: str = ".txt") -> Path:
    """单条导出目录里恰好一个曲线文件。"""
    found = [p for p in Path(outdir).iterdir()
             if p.is_file() and p.suffix == suffix]
    assert len(found) == 1, f"应当恰好一个 {suffix} 文件，实际 {found}"
    return found[0]


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


# 给 `from ... import *` 用：连下划线开头的辅助也一起导出。
# 测试模块靠它拿到导入别名（np / mock / Qt / gui_*）与所有辅助。
__all__ = [name for name in globals() if not name.startswith("__")]
