"""GUI 主程序：主框架（MDI 子窗口式绘图区）与事件循环（PySide6）。

入口：python -m xrd_toolkit.gui（__main__.py 转发到本文件的 main()）。
create_window() 与 main() 分离：测试里可以只建窗口、不进事件循环。

架构说明：
  - 外窗口（QMainWindow）管三块固定坞：文件（左）/ 参数（右）/
    日志（底）+ 顶部工具栏 + 状态行；工具栏尾部有 [文件][参数]
    [日志] 三个收起开关（也可用各坞标题栏的 ×），全收起来就只剩
    绘图区，看图视野最大；
  - 中央绘图区 = QMdiArea：每张图一个子窗口（QMdiSubWindow），
    各拖各的、互不牵连——旧的停靠分栏已按用户定稿撤掉（图之间
    不再"连在一起"）；
  - 框架阶段的占位动作统一写日志区 + 状态行提示"尚未实现"；
    实现功能时只替换动作背后的处理，框架结构不变；
  - 文件进列表两种方式：拖文件（tif/edf/cbf）到窗口任意位置，或
    点 [打开] 选文件；
  - 已接线（1D 闭环）：交互模型 = 文件列表以对号选择（点行 = 只
    加选不取消，取消只能用对号方块；选入/拖入默认全勾；重复文件
    弹窗覆盖/改名）→ 点击视图按钮对每个对号文件各开面板并算该
    视图（多选 = 批量，一次出多张图；按钮是纯动作不是开关，重复
    点击安全；面板按「视图 + 文件」成对创建，同一视图可同时开多
    张不同文件的图）→ 点图面板设"编辑对象"，参数坞回放该面板
    的参数快照（点哪张图就显示哪张图作图时的参数，含显示参数）→
    两个 [应用] 各管各的：数据 [应用] 只更新数据参数并重算焦点
    面板，图像 [应用] 只更新显示参数并用已有数据重画焦点那张图。
    显示参数（对数纵轴/纵轴范围/对比归一化等）每张图各记各的：
    新开面板从默认起步，重算保留自己的旧设置，别的图的设置不
    串台；自动纵轴/自动对比度把程序实际用的区间填进置灰输入框
    （只读展示）；计算走 gui/tasks.py 后台线程，界面不卡。
  - [对比]（工具栏，仅 1D）：把勾选的多个文件叠进同一张图（勾选
    数 >= 2），每文件一个后台任务、全部算完再一起画；面板键 =
    f"对比|{排序后的路径们}"，重复点击同一选择 = 复用同一面板并
    重算；颜色自动循环（C0/C1/...），图例 = 显示名；"对比归一化
    到最强峰"（默认开，1D 显示组）= 每条曲线除以自己的最强峰，
    曝光差很多的文件也看得清彼此峰形；1D 显示参数（对数/纵轴
    范围）对对比面板同样生效。
  - 图面板布局与比例（与用户讨论定稿）：每张图 = MDI 里的独立
    子窗口，手动缩放完全自由（拖成什么样就什么样，不再有普通
    拖/Shift 拖之分）；每拖一次 = 记住当前画布比例
    （_canvas_pref/_dragged），没拖过 = 默认画布 5:3（500×300）。
    开新图以默认大小左上角小错位级联（24px 一档、6 档循环回起点，
    下面几张的标题栏露出来）、完全不动旧图。macOS 原生样式的
    子窗口边框几乎不可见 → 不靠边框：内容四边各留 5px 抓取带、
    四角各留 16px 抓取区（右下角有可见把手 ▙），悬停换方向光标、
    按住拖 = 拉伸容器（_PanelGripFilter）。横排/竖排按钮 =
    纯摆位置：按类型分层（类型顺序 = 开图先后），横排每类一行、
    竖排每类一列，图保持各自大小绝不缩放；摆图前先把滚动归零
    （QMdiArea 滚动状态下 move 会混入滚动偏移、图越排越漂，
    探针实证）；行比视口宽/层总高比视口高 → QMdiArea 滚动条
    兜底（宁可滚动也不压扁），弹出去的窗口不参与。总缩放 =
    绘图区全体同比缩放（50%–200%，每格 10%）：Ctrl+滚轮（Excel
    习惯）或底部条 − 100% + 按钮，围绕视口中心缩放；只动绘图
    区里的图，弹出去的窗口不参与（各管各的），平铺也不碰它；
    新开/收回的图按当前总缩放落位（和周围的图大小一致）。每张
    1D/对比面板工具栏末尾有 [弹出]/[收回]：把面板搬进独立 OS
    窗口再收回来（状态跟着走）。点窗口任何地方（标题栏/边框/图/
    工具栏）都选中该面板（选中子窗口 = 选中参数，不必点图本体）。
    关闭面板（子窗口 × 或弹出窗口 ×）= 关闭即遗忘：从登记表移除、
    状态全丢，重开 = 全新默认面板；关软件时的保存询问只列当时
    还开着的面板。
  - 面板工具栏精简为 [Home][Zoom][Customize][Save]：拖 = 平移；
    滚轮（触摸板两指滚动）= 只滚动绘图区（看别的图，再也不会误
    缩图）；放大镜按钮 = 开关（点亮/熄灭状态可见 + 日志提示）：
    点亮 = 滚轮以光标为中心缩放（每格 10%——25% 连乘几下图就飞
    了）+ 左键拖框放大（mpl 自带），熄灭 = 滚轮滚动 + 左键平移。
    双击不回全图（Home 就是回首页）；Customize = matplotlib 轴
    属性对话框。缩放/平移/Home/改范围都会实时同步写回参数面板：
    视图 2θ 范围（只看图不参与计算，与数据组的积分 2θ 范围互不
    干扰）+ 纵轴窗口（自动纵轴随之关掉——用户手动定的窗口由
    用户接管）。
  - [保存] 是主动操作：弹窗勾选要保存的已出图面板 → 逐个选文件
    名存 PNG；另外关闭窗口时若有尚未保存的图会弹窗询问
    （保存后关闭 / 不保存直接关 / 取消留在程序里）。

窗口上的公共接口（供后续页面接线与测试使用）：
  window.log(text)        写日志区 + 状态行
  window.add_files(paths) 把文件加进左侧列表
  window.mdi              QMdiArea（绘图区，所有图子窗口的父场地）
  window.plot_docks       {面板键: QMdiSubWindow 或 _FloatedWindow}，
                          键 = f"{视图}|{路径}"，重复文件改名条目再补
                          |显示名 区分；标题 = f"{视图}_{显示名}"
                          （如 1D_lab6-00024.tif）；已关闭的面板不在
                          登记表里
  window.focus_panel      参数面板编辑对象 = 面板键（点窗口任意处/计算完成时设定）
  window.params           参数面板控件字典
  window.config_name      当前选中的配置条目 key（如 lmfp1_lab6）
  window.config           完整条目 dict（label / geometry / beam_center）
  1D 面板的画布/坐标轴在面板内容上：_content(dock).axes_1d
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("qtagg")   # 必须在导入 FigureCanvasQTAgg 之前选定 Qt 后端
# 图标题取自文件显示名（重复文件改名可输入中文）→ 字体回退链补上
# macOS 中文字体，缺字形时逐字体回退，标题不会渲染成方框。
# 注意要设 font.family 直接给列表：实测 qtagg 后端下 font.sans-serif
# 列表不触发回退（Agg 可以），中文仍会变方框
matplotlib.rcParams["font.family"] = [
    "DejaVu Sans", "PingFang SC", "Hiragino Sans GB", "Arial Unicode MS"]
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import QEvent, QObject, Qt, QSize, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QInputDialog, QListWidget, QListWidgetItem, QMainWindow,
    QMdiArea, QMdiSubWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QToolBar, QVBoxLayout,
    QWidget, QDockWidget, QApplication)

from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG  # 几何配置注册表（下拉框数据源）
from xrd_toolkit.gui.tasks import BackgroundTask  # 后台线程任务（积分等耗时计算）
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.services.integrator import integrate_1d

FILE_FILTER = "衍射图像 (*.tif *.edf *.cbf);;所有文件 (*)"
VIEW_NAMES = ("2D", "剖面", "1D", "瀑布")   # 四个图面板（作图按钮的顺序）
SUPPORTED_SUFFIXES = (".tif", ".edf", ".cbf")   # 拖放只认这三种

PLOT_OPEN_W, PLOT_OPEN_H = 500, 300   # 新面板默认画布尺寸（画布真 5:3，
                                      # 面板总高 = 画布 + 工具栏 + 标题栏）

# 面板状态属性的白名单：弹出/收回时整体搬家的"行李清单"。
# 不能用 vars() 整体拷：PySide6 包装对象 vars() 里混着信号实例
# （windowStateChanged/destroyed…），整体拷会把新容器的信号盖成
# 旧容器的绑定信号（探针验证过）。
_PANEL_ATTRS = (
    "panel_file", "panel_item", "panel_display", "figure_saved",
    "params_snapshot", "compare_files", "compare_gen", "compare_pending",
    "compare_data", "_dragged", "_canvas_pref", "hover_marker",
    "_pan_start", "_pan_limits", "last_tth", "last_intensity",
    "_last_canvas", "_settling", "panel_key",
)


def _copy_panel_attrs(src, dst) -> None:
    """按白名单把面板状态从旧容器搬到新容器（弹出/收回用）。"""
    for name in _PANEL_ATTRS:
        if hasattr(src, name):
            setattr(dst, name, getattr(src, name))


# ══ 日志 / 状态行 ═══════════════════════════════════════════
def _dropped_paths(event) -> list:
    """从拖放事件提取本地文件路径（只留支持的类型 tif/edf/cbf）。"""
    paths = []
    for url in event.mimeData().urls():
        if url.isLocalFile():
            p = url.toLocalFile()
            if p.lower().endswith(SUPPORTED_SUFFIXES):
                paths.append(p)
    return paths


class _MainWindow(QMainWindow):
    """顶层窗口：接受拖入文件（拖到窗口任意位置 = 加进文件列表）。

    至少拖进一个支持类型的文件才"接住"（光标变 +）；子控件默认
    不接拖放（日志区显式关掉了文本拖放），事件会冒泡到顶层窗口
    ——一个入口覆盖整个窗口面。drop_callback 由 create_window 接
    到 add_files 上。
    """

    def __init__(self):
        super().__init__()
        self.drop_callback = None
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if self.drop_callback and _dropped_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = _dropped_paths(event)
        if paths and self.drop_callback:
            self.drop_callback(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


def _log(window: QMainWindow, text: str) -> None:
    """统一反馈出口：写日志区（累积）+ 状态行（常驻"就绪"，消息
    替换它 5 秒后自动恢复——状态栏永远有内容，不会隐形）。"""
    window.log_text.appendPlainText(text)
    window.status_text.setText(text)
    if window._status_timer is None:
        window._status_timer = QTimer(window)
        window._status_timer.setSingleShot(True)
        window._status_timer.timeout.connect(
            lambda: window.status_text.setText("就绪"))
    window._status_timer.start(5000)   # 重新计时


class _PanelClickTracker(QObject):
    """应用级事件过滤器：点任何面板窗口内任何位置都选中该面板。

    与用户讨论定稿：选中子窗口 = 选中参数，不必非点图本体（标题
    栏、边框、图、工具栏都算）。为什么必须是应用级：探针实证
    QWidget 的父过滤器收不到子部件事件（子部件 accept 后事件不
    向上传播，matplotlib 画布恰恰会 accept 鼠标按下）——只有挂
    在 QApplication 上的过滤器能看到一切。收到鼠标按下后从落点
    逐级向上找面板容器（_PlotSubWindow / _FloatedWindow），且该
    容器必须登记在本窗口 plot_docks 里（测试会同时开多个窗口，
    各窗口的过滤器只认自己登记的面板，防止串窗）。只"监听"不
    "拦截"：返回 False，事件照常传递。
    """

    def __init__(self, window: QMainWindow):
        super().__init__(window)   # 挂在窗口上，随窗口销毁
        # 刻意不存 window 引用：QApplication 的过滤器表 → 过滤器 →
        # window 会形成引用环，关掉的窗口永不回收（探针实测全套件
        # 30 个窗口全存活）。窗口就是父对象，用时 self.parent() 取。

    def eventFilter(self, obj, event):
        if event.type() != QEvent.MouseButtonPress:
            return False
        window = self.parent()
        if not isinstance(window, QMainWindow):
            return False   # 窗口已销毁：过滤器随父摘除前的兜底
        w = obj if isinstance(obj, QWidget) else None
        while w is not None:
            if isinstance(w, (_PlotSubWindow, _FloatedWindow)):
                key = w.panel_key
                if window.plot_docks.get(key) is w:
                    _set_focus(window, key, w.windowTitle())
                break
            w = w.parentWidget()
        return False   # 不消费事件


class _ElideLabel(QLabel):
    """宽度受限的单行标签：文字超宽时打省略号，绝不撑宽父布局。

    用在参数坞的长名称行（编辑对象标题 / 几何配置说明）：窄坞下
    缩略显示，悬停有完整提示；把坞拖宽自动多显示几个字。text()
    仍返回完整文字（缩略只是显示层面，程序与测试读 text() 不受
    影响）。横向尺寸策略设 Ignored = "布局给多宽就显示多宽，
    不按文字宽度反过来要地方"——正是它治住"名字太长撑大参数区"。
    """

    def __init__(self, text: str = "", mode=Qt.ElideMiddle, parent=None):
        super().__init__(text, parent)
        self._full = text
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setToolTip(text)

    def setText(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._refresh()

    def text(self) -> str:
        return self._full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        if self.width() <= 0:
            super().setText(self._full)   # 布局还没定宽：先显示全量
            return
        super().setText(self.fontMetrics().elidedText(
            self._full, self._mode, self.width()))


def _snapshot_params(window: QMainWindow) -> dict:
    """拍下参数面板当前状态：配置条目 + 各控件值 + 自动对比度开关。

    每张面板在开图/重算时拍一张快照挂在 dock.params_snapshot 上 =
    "这张图是用什么参数画的"；点选面板时参数坞回放快照（既能看也
    能改，改完 [应用] 重算并刷新快照）。显示参数（对数纵轴/纵轴
    范围/对比归一化）也收在快照里：每张图各记各的，画图时只读
    自己面板的快照（见 _panel_param）。
    """
    snap = {"config": window.config_name}
    for name, w in window.params.items():
        if isinstance(w, QCheckBox):
            snap[name] = w.isChecked()
        else:
            snap[name] = w.value()
    return snap


# 图像参数组的全部条目（"只看图不参与计算"的那一组）。快照里这两类
# 参数按职责分开更新（见 _data_snapshot / _display_snapshot）：两个
# [应用] 各管各的，互不串改。
_DISPLAY_DEFAULTS = {
    "自动对比度": True,
    "对比度下限": 1.0,
    "对比度上限": 100000.0,
    "剖面角度 (°)": 0.0,
    "对数纵轴": False,
    "纵轴自动": True,
    "纵轴下限": 1.0,
    "纵轴上限": 100000.0,
    "对比归一化": True,
    "视图 2θ 下限 (°)": None,   # None = 跟随积分 2θ 范围；缩放/平移后写回显式值
    "视图 2θ 上限 (°)": None,
}
_DISPLAY_PARAMS = frozenset(_DISPLAY_DEFAULTS)   # 显示参数 = 以上全部


def _data_snapshot(window: QMainWindow, base: dict = None) -> dict:
    """数据参数快照：数据组取控件当前值，显示参数沿用 base（无 base
    用默认值）。

    base = 面板自己的旧快照（重算时传入）：显示参数每张图各记各的，
    重算不改它的长相；新面板（base=None）显示参数从默认起步——不
    受上一张焦点图的设置影响（开新图永远是"默认长相"）。
    """
    snap = _snapshot_params(window)
    for name in _DISPLAY_PARAMS:
        if base is not None and name in base:
            snap[name] = base[name]
        else:
            snap[name] = _DISPLAY_DEFAULTS[name]
    return snap


def _display_snapshot(window: QMainWindow, base: dict = None) -> dict:
    """显示参数快照：显示组取控件当前值，数据参数沿用 base。

    base = 面板自己的旧快照（图像 [应用] 时传入）：图像 [应用] 只改
    显示不重算，数据参数保持"这张图当时是用什么算的"，顺手改了
    数据控件也不会冒充成这张图的计算参数。
    """
    base = base or {}
    snap = {"config": window.config_name}
    for name, w in window.params.items():
        if name in _DISPLAY_PARAMS:
            snap[name] = w.isChecked() if isinstance(w, QCheckBox) else w.value()
        elif name in base:
            snap[name] = base[name]
        else:
            snap[name] = w.isChecked() if isinstance(w, QCheckBox) else w.value()
    return snap


def _panel_param(window: QMainWindow, dock, name: str,
                 default):
    """面板自己的参数值：优先快照（这张图作图时用的值）。

    没有快照（或快照缺这个键）时退回参数坞控件当前值——防御后路：
    以后新增画图调用点若忘了拍快照，也不至于崩溃。画图一律走这里
    读参数，而不是直接读控件：控件是"编辑对象"的编辑界面，画别的
    面板时控件可能正显示另一张图的设置。
    """
    snap = getattr(dock, "params_snapshot", None)
    if snap and name in snap:
        return snap[name]
    w = window.params.get(name)
    if w is None:
        return default
    if isinstance(w, QCheckBox):
        return w.isChecked()
    return w.value()


def _load_params_snapshot(window: QMainWindow, snap: dict) -> None:
    """把参数快照填回参数面板（只展示不计算）。

    顺序讲究：
      - 先切几何配置：触发 _apply_config，同步 window.config（PONI/
        倾斜角取该条目注册表值，[应用] 依赖它）；
      - 再填各输入框：用"当时真用的"值覆盖注册表几何值；
      - 最后处理自动对比度开关（blockSignals 防日志刷屏）：勾着 →
        静默刷新展示值（焦点是 2D/剖面 才读图，其余占位）；手动
        模式 → 回放快照里的值。

    显示参数（对数纵轴/纵轴范围/对比归一化）也一起回放：每张图的
    显示设置各记各的，点哪张图参数坞就显示哪张图作图时的设置。
    纵轴自动勾着时，最后按焦点图的实际数据重算并填回置灰输入框
    （同自动对比度的套路，见 _apply_auto_ylim）。
    """
    idx = window.config_combo.findData(snap.get("config"))
    if idx >= 0 and idx != window.config_combo.currentIndex():
        # 静默切换：拦下 currentIndexChanged，手动应用（不记日志）
        window.config_combo.blockSignals(True)
        window.config_combo.setCurrentIndex(idx)
        window.config_combo.blockSignals(False)
        _apply_config(window, idx, silent=True)
    auto = window.params.get("自动对比度")
    for name, value in snap.items():
        if name == "config" or name == "自动对比度":
            continue
        if value is None:
            continue   # 视图 2θ 范围没被用户动过 = 跟随积分范围（下方补填）
        w = window.params.get(name)
        if w is None:
            continue
        if isinstance(w, QCheckBox):
            w.setChecked(value)
        else:
            w.setValue(value)
    # 视图 2θ 范围跟随积分范围时：输入框显示"正在用的视图" =
    # 该面板快照里的积分范围（无快照值退回控件当前值，防御后路）
    for name, fallback in (("视图 2θ 下限 (°)", "2θ 下限 (°)"),
                           ("视图 2θ 上限 (°)", "2θ 上限 (°)")):
        box = window.params.get(name)
        if box is None or snap.get(name) is not None:
            continue
        other = window.params.get(fallback)
        if other is not None:
            box.setValue(snap.get(fallback, other.value()))
    if auto is not None:
        auto.blockSignals(True)
        auto.setChecked(snap.get("自动对比度", True))
        auto.blockSignals(False)
        window.params["对比度下限"].setEnabled(not auto.isChecked())
        window.params["对比度上限"].setEnabled(not auto.isChecked())
        if auto.isChecked():
            _apply_auto_contrast(window, silent=True)   # 焦点图的自动值
        else:
            window.params["对比度下限"].setValue(snap.get("对比度下限", 1.0))
            window.params["对比度上限"].setValue(
                snap.get("对比度上限", 100000.0))
    # 纵轴自动开关的联动状态同步：自动开 → 上下限输入框置灰（只读
    # 展示）。回放 setChecked 时 toggled 信号一般已联动，这里再兜底
    # 一次（同值 setChecked 不触发信号，联动状态可能停在旧面板的）；
    # 勾着自动 → 按焦点图数据重算填回（toggled 时填过一次，但随后
    # 的回放循环可能又覆盖了上下限框的值，收尾再算一次才是准的）
    auto_y = window.params.get("纵轴自动")
    if auto_y is not None:
        window.params["纵轴下限"].setEnabled(not auto_y.isChecked())
        window.params["纵轴上限"].setEnabled(not auto_y.isChecked())
        if auto_y.isChecked():
            _apply_auto_ylim(window, silent=True)


def _set_focus(window: QMainWindow, key: str, title: str) -> None:
    """把参数面板的编辑对象切到某面板（点面板 / 计算完成时调用）。

    切过去的同时回放该面板的参数快照——点哪张图，旁边就显示哪张
    图的参数。重复点同一面板直接返回：不冲掉用户正在改的值。
    """
    if window.focus_panel == key:
        return
    window.focus_panel = key
    window.focus_label.setText(f"编辑对象：{title}")
    dock = window.plot_docks.get(key)
    snap = getattr(dock, "params_snapshot", None)
    if snap is not None:
        _load_params_snapshot(window, snap)


class _PressRecorder(QObject):
    """记录鼠标按下时命中的文件项与对号状态（装在列表视口上）。

    用途：区分"点对号方块"（Qt 在弹起时自动切换对号，itemChanged
    先到）与"点行其他位置"（对号不动）——itemClicked 据此决定手势：
    点行 = 只勾不取消（加选），方块 = 勾上/取消。
    """

    def __init__(self, window: QMainWindow, lst: QListWidget):
        super().__init__(window)   # 挂在窗口上，随窗口销毁
        self._list = lst
        window._press_item = None
        window._press_state = None

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress:
            item = self._list.itemAt(event.position().toPoint())
            window = self.parent()   # 不存 window 引用（同 _PanelClickTracker：防引用环）
            window._press_item = item
            window._press_state = item.checkState() if item else None
        return False   # 不消费事件


# ══ 左侧：文件列 ═══════════════════════════════════════════
class NarrowList(QListWidget):
    """最小宽度可收窄的列表。

    QListWidget 的 minimumSizeHint 内部写死约 270px（按"能显示
    条目"设计），且 dock 布局只认这个 hint、不认 setMinimumWidth。
    覆盖 hint 后文件列才能收到按钮行决定的真实最窄宽度。
    """

    def minimumSizeHint(self):
        return QSize(100, 120)


def _build_file_dock(window: QMainWindow) -> QDockWidget:
    """文件坞：打开（多选）/ 保存 / 删除 + 以对号为选择的文件列表。

    选择 = 对号，两种手势：
      - 点行 = 加选：勾上这一行，其他对号不动；已勾的行再点没
        反应（不取消）；
      - 点对号方块 = 勾上/取消这一行（取消对号的唯一途径）。
    背景高亮跟随最后点的那行（且必须是对号行，没对号就不高亮），
    作图按钮对所有对号文件各开一张图，删除删所有对号文件。
    itemChanged 统一刷新标签和日志；itemClicked 靠 _PressRecorder
    记着的按下状态区分方块/行体手势。
    """
    dock = QDockWidget("文件", window)
    dock.setObjectName("file_dock")
    # 可移动/可浮动/可关闭：收起文件列给图让地方（工具栏 [文件] 开关
    # 或标题栏 × 都能收，visibilityChanged 双向同步按钮状态）
    dock.setFeatures(QDockWidget.DockWidgetMovable
                     | QDockWidget.DockWidgetFloatable
                     | QDockWidget.DockWidgetClosable)

    content = QWidget()
    lay = QVBoxLayout(content)
    # 按钮排 2 列网格：[打开] 占满第一行（长按钮），[保存][删除] 第二行。
    # 一行三个按钮的最小宽度 ≈ 3×80+间距，把文件列锁在 270；
    # 网格最宽的一行只有两个按钮 ≈ 184，文件列才能和参数列一样收窄
    btns = QGridLayout()
    btn_open = QPushButton("打开")
    btn_save = QPushButton("保存")
    btn_delete = QPushButton("删除")
    btn_save.setObjectName("save_btn")
    btn_delete.setObjectName("delete_btn")
    btns.addWidget(btn_open, 0, 0, 1, 2)   # 打开占满第一行
    btns.addWidget(btn_save, 1, 0)
    btns.addWidget(btn_delete, 1, 1)
    lay.addLayout(btns)

    window.file_list = NarrowList()   # 覆盖了 minimumSizeHint，可以收窄
    # 对号 = 选中（可多选）。用系统默认的选中样式：背景高亮 + 白字
    # （可读）。之前压掉背景导致"白字配白底"看不见字——背景高亮
    # 必须保留，且始终跟随对号集合（对号是唯一的选择表达）
    window.file_list.setSelectionMode(QAbstractItemView.SingleSelection)
    lay.addWidget(window.file_list)

    def on_item_changed(item):
        """对号状态变了 → 状态行标签 + 背景高亮同步；勾上时记日志。"""
        if item.checkState() == Qt.Checked:
            _log(window, f"已选中 {item.text()}（点击视图按钮开始计算）")
        _sync_current_to_checks(window)
        _refresh_file_label(window)

    def on_item_clicked(item):
        """手势区分：
          - 点对号方块：Qt 已自动切换（勾上/取消），无需再动；
          - 点行：只勾上这一行（加选），其他对号不动；已勾的行再
            点没反应。取消对号只能用对号方块。
        """
        square = (window._press_item is item
                  and item.checkState() != window._press_state)
        if square:
            return   # Qt 已切换，itemChanged 已同步
        if item.checkState() == Qt.Unchecked:
            item.setCheckState(Qt.Checked)   # → itemChanged 同步高亮/标签/日志

    window.file_list.itemChanged.connect(on_item_changed)
    window.file_list.itemClicked.connect(on_item_clicked)
    # 记录鼠标按下时命中的条目与对号状态（区分方块点击/行体点击）
    window.file_list.viewport().installEventFilter(
        _PressRecorder(window, window.file_list))

    def open_dialog():
        paths, _ = QFileDialog.getOpenFileNames(
            window, "选择数据文件", "data", FILE_FILTER)
        if paths:
            window.add_files(paths)

    def delete_selected():
        checked = [window.file_list.item(i)
                   for i in range(window.file_list.count())
                   if window.file_list.item(i).checkState() == Qt.Checked]
        if not checked:
            _log(window, "没有选中要删除的文件")
            return
        # 屏蔽信号：移除过程中 Qt 会把当前项挪到相邻行，别让
        # 中间状态触发登记/日志
        window.file_list.blockSignals(True)
        for it in checked:
            window.file_list.takeItem(window.file_list.row(it))
        window.file_list.blockSignals(False)
        _sync_current_to_checks(window)   # 高亮跟随剩余对号集合
        _refresh_file_label(window)   # 状态行跟随剩余对号集合
        # 条目没了，面板绑定的列表条目随之失效：解除引用（面板照常
        # 工作，靠 panel_file 记住自己的文件；重新加回时由 _plot_view
        # 把面板归位到新条目）。对比面板没有 panel_item，跳过。
        for dock in window.plot_docks.values():
            if getattr(dock, "panel_item", None) in checked:
                dock.panel_item = None
        _log(window, f"已删除 {len(checked)} 个文件")

    btn_open.clicked.connect(open_dialog)
    btn_save.clicked.connect(lambda: _save_figures(window))
    btn_delete.clicked.connect(delete_selected)

    dock.setWidget(content)
    window.addDockWidget(Qt.LeftDockWidgetArea, dock)
    return dock


def _unique_display_name(window: QMainWindow, name: str) -> str:
    """给重复文件起不重名的显示名："xxx.tif" → "xxx (1).tif" → "xxx (2).tif"…"""
    taken = {window.file_list.item(i).text()
             for i in range(window.file_list.count())}
    p = Path(name)
    n = 1
    while f"{p.stem} ({n}){p.suffix}" in taken:
        n += 1
    return f"{p.stem} ({n}){p.suffix}"


def _ask_duplicate(window: QMainWindow, name: str) -> str:
    """同一文件再次加入时的询问弹窗。返回 "overwrite" / "rename" / "cancel"。

    窗口没显示（测试环境）时不弹模态框，默认 "overwrite"（保留原
    条目）——与 _confirm_close 的可见性护栏同理，防测试挂死。
    """
    if not window.isVisible():
        return "overwrite"
    box = QMessageBox(window)
    box.setWindowTitle("文件已存在")
    box.setText(f"{name} 已经在文件列表里了。")
    box.setInformativeText(
        "覆盖 = 保留原条目；改名 = 弹输入框起个新名字（预填编号名，"
        "可自己改）；取消 = 这次不加。")
    btn_overwrite = box.addButton("覆盖", QMessageBox.AcceptRole)
    btn_rename = box.addButton("改名", QMessageBox.ActionRole)
    btn_cancel = box.addButton("取消", QMessageBox.RejectRole)
    box.setDefaultButton(btn_overwrite)
    box.exec()
    clicked = box.clickedButton()
    if clicked is btn_rename:
        return "rename"
    if clicked is btn_overwrite:
        return "overwrite"
    return "cancel"


def _ask_rename(window: QMainWindow, default_name: str):
    """改名输入框：预填默认编号名，用户可以改成自己想要的名字。

    返回用户输入（去首尾空格）；取消或输入为空 → None。窗口没显示
    （测试环境）不弹模态框，直接返回默认名（防挂死）。
    """
    if not window.isVisible():
        return default_name
    text, ok = QInputDialog.getText(
        window, "改名", "给这个重复文件起个新名字：", text=default_name)
    text = text.strip()
    if not ok or not text:
        return None
    return text


def add_files(window: QMainWindow, paths) -> None:
    """把文件加进左侧列表；一批新加的文件全部打对号（选中）。

    对号是唯一的选择表达：一起选入/拖入的文件默认全部勾上，点一次
    作图按钮就批量出图。重复文件（同一路径再次加入）弹窗询问：
      - 覆盖 = 保留原条目（新条目跳过，原条目勾上）；
      - 改名 = 弹输入框起新名字（预填 "xxx (1).tif"，可自己改；
        输入的名字已被占用会要求换一个）再开一条（同文件两条条目）；
      - 取消 = 这次不加。
    列表里永远不出现重名。
    """
    existing = {}   # 绝对路径 → 已有条目（重复检测按真实路径）
    for i in range(window.file_list.count()):
        item = window.file_list.item(i)
        existing[Path(item.data(Qt.UserRole)).resolve()] = item

    added = []   # 本批真正新加的条目
    window.file_list.blockSignals(True)   # 批量加：结束后统一同步/记日志
    for p in paths:
        p = Path(p)
        old = existing.get(p.resolve())
        if old is not None:   # 重复文件
            choice = _ask_duplicate(window, p.name)
            if choice == "overwrite":
                old.setCheckState(Qt.Checked)   # 保留原条目并勾上
                _log(window, f"{p.name} 已在列表中（覆盖：保留原条目）")
            elif choice == "rename":
                new_name = None
                default_name = _unique_display_name(window, p.name)
                while True:
                    answer = _ask_rename(window, default_name)
                    if answer is None:
                        break   # 取消 → 这次不加
                    if any(window.file_list.item(i).text() == answer
                           for i in range(window.file_list.count())):
                        _log(window, f"显示名 {answer} 已被占用，请换一个")
                        default_name = answer   # 重开输入框保留用户输入
                        continue
                    new_name = answer
                    break
                if new_name is None:
                    _log(window, f"已跳过重复文件 {p.name}")
                    continue
                item = QListWidgetItem(new_name)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setData(Qt.UserRole, str(p))
                window.file_list.addItem(item)
                item.setCheckState(Qt.Checked)
                added.append(item)
                existing[p.resolve()] = item   # 同批再出现同路径时走本条目
                _log(window, f"{p.name} 已在列表中（改名加入：{new_name}）")
            else:
                _log(window, f"已跳过重复文件 {p.name}")
            continue
        item = QListWidgetItem(p.name)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setData(Qt.UserRole, str(p))       # 全路径藏在 UserRole
        window.file_list.addItem(item)
        item.setCheckState(Qt.Checked)          # 新文件默认选中
        existing[p.resolve()] = item
        added.append(item)
    window.file_list.blockSignals(False)
    if added:
        window.file_list.setCurrentItem(added[-1])   # 高亮最后新条目
        _log(window, f"已添加 {len(added)} 个文件")
    _sync_current_to_checks(window)
    _refresh_file_label(window)


# ══ 文件 → 1D 闭环：选文件 → 后台积分 → 1D 面板出图 ══════════
def _collect_geometry(window: QMainWindow) -> dict:
    """从参数面板收集积分几何。

    PONI/倾斜角取当前配置条目的标定值（面板暂不暴露），像素/波长/
    距离取面板输入框——对应 integrate_pattern 里 --pixel/--wavelength/
    --dist 覆盖配置值的语义。
    """
    geom = window.config["geometry"]
    return dict(
        pixel_size_m=window.params["像素尺寸 (µm)"].value() * 1e-6,
        wavelength_m=window.params["波长 (Å)"].value() * 1e-10,
        dist_m=window.params["初始距离 (mm)"].value() * 1e-3,
        poni1_m=geom["poni1_m"],
        poni2_m=geom["poni2_m"],
        rot1_deg=geom["rot1_deg"],
        rot2_deg=geom["rot2_deg"],
    )


def _compute_integration(path_str: str, geom: dict, npt: int) -> tuple:
    """后台线程里运行的纯计算：读图 → 全角度积分。

    不碰任何界面控件；异常由 tasks.BackgroundTask 转成 error 信号
    送回主线程。
    """
    image = load_diffraction_image(path_str)
    tth, intensity = integrate_1d(image, npt=npt, **geom)
    return tth, intensity


def _sync_current_to_checks(window: QMainWindow) -> None:
    """高亮跟随对号：当前项必须是对号行；没对号就不高亮。

    防"看起来选中了其实没勾"的假象——比如点对号方块取消勾选时，
    Qt 会先把那行设为当前项，不纠正就会留下一行无对号的高亮。
    """
    current = window.file_list.currentItem()
    if current is not None and current.checkState() == Qt.Checked:
        return
    for i in range(window.file_list.count()):
        item = window.file_list.item(i)
        if item.checkState() == Qt.Checked:
            window.file_list.setCurrentItem(item)
            return
    window.file_list.setCurrentRow(-1)


def _refresh_file_label(window: QMainWindow) -> None:
    """状态行文件标签跟随对号集合：0 个 = 未打开 / 1 个 = 文件名 /
    N 个 = 已选 N 个文件。"""
    checked = [window.file_list.item(i)
               for i in range(window.file_list.count())
               if window.file_list.item(i).checkState() == Qt.Checked]
    if not checked:
        window.file_label.setText("未打开文件")
    elif len(checked) == 1:
        window.file_label.setText(checked[0].text())
    else:
        window.file_label.setText(f"已选 {len(checked)} 个文件")


def _on_file_selected(window: QMainWindow, current, previous) -> None:
    """文件列表选中变化 → 只刷新状态行标签，不计算。

    算哪个视图由工具栏的作图按钮决定（点击 = 对每个对号文件开
    面板并计算），文件选择本身保持轻快——这也是"反应迟钝"问题
    的根源：以前每次点选都触发一次完整积分。
    """
    _refresh_file_label(window)


def _run_view(window: QMainWindow, name: str, path: Path, key: str) -> None:
    """对 (视图, 文件) 面板跑对应的计算（后台线程）。

    1D = 全角度积分；其余视图尚未接线（面板保持占位）。
    """
    if name != "1D":
        _log(window, f"{name} 视图尚未接线（面板占位）")
        return
    # 开工前先刷新参数快照：数据参数 = 控件当前值（计算就用它），
    # 显示参数沿用面板自己的旧快照（重算不改长相）；新面板旧快照
    # 里是默认值，正好从默认起步
    dock = window.plot_docks.get(key)
    if dock is not None:
        dock.params_snapshot = _data_snapshot(window, dock.params_snapshot)
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    window.status_text.setText(f"正在积分 {path.name}…")
    _log(window, f"开始积分 {path.name}（后台线程）")
    _spawn(window, path, geom, npt, key)


def _apply_params(window: QMainWindow) -> None:
    """[应用] 按钮：用当前参数重算焦点面板。

    焦点面板 = 参数坞顶部"编辑对象"（点图面板或计算完成时设定）；
    没选焦点时提示用户先点图。
    """
    key = window.focus_panel
    if key is None:
        _log(window, "先点击要更新的图面板（如 1D），再点 [应用]")
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        _log(window, "编辑对象的面板已不存在")
        window.focus_panel = None
        window.focus_label.setText("编辑对象：未选中图面板")
        return
    view = key.split("|", 1)[0]
    _log(window, f"[应用] 重算编辑对象：{dock.windowTitle()}")
    if view == "对比":
        _run_compare(window, key)   # 一组文件全部重算
        return
    _run_view(window, view, dock.panel_file, key)


def _apply_image_params(window: QMainWindow) -> None:
    """[应用] 按钮（图像参数组）：把当前显示参数应用到编辑对象（焦点面板）。

    两个 [应用] 各管各的：这个按钮只更新焦点面板快照里的显示参数
    （_display_snapshot），数据参数沿用旧快照——顺手改了数据控件也
    不会冒充成这张图的计算参数。显示参数（对数纵轴/纵轴范围/对比
    归一化）与数据参数同款：每张图各记各的（存在各自面板的
    params_snapshot 里），点哪张图参数坞就显示哪张图的设置。改完
    点 [应用] 只重画焦点那张图（用已有数据，不重新积分），别的图
    保持自己的设置不动。没算完的焦点面板提示先完成积分。2D/剖面
    尚未接线，其占位面板只记快照。
    """
    key = window.focus_panel
    if key is None:
        _log(window, "先点击要更新的图面板（如 1D），再点 [应用]")
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        _log(window, "编辑对象的面板已不存在")
        window.focus_panel = None
        window.focus_label.setText("编辑对象：未选中图面板")
        return
    dock.params_snapshot = _display_snapshot(window, dock.params_snapshot)
    view = key.split("|", 1)[0]
    if view == "1D":
        if getattr(dock, "last_tth", None) is None:
            _log(window, f"[应用] 图像参数：{dock.windowTitle()} 还没有"
                         f"计算结果（积分完成后再试）")
            return
        _draw_1d(window, dock, dock.last_tth, dock.last_intensity)
    elif view == "对比":
        if not getattr(dock, "compare_data", None):
            _log(window, f"[应用] 图像参数：{dock.windowTitle()} 还没有"
                         f"计算结果（积分完成后再试）")
            return
        _redraw_compare(window, key)
    else:
        _log(window, f"[应用] 图像参数已更新编辑对象：{dock.windowTitle()}"
                     f"（{view} 视图尚未接线）")
        return
    _log(window, f"[应用] 图像参数已重画：{dock.windowTitle()}")


def _spawn(window: QMainWindow, path: Path, geom: dict, npt: int,
           key: str, on_done=None, on_error=None) -> None:
    """启动后台积分任务；引用挂在 window._tasks 防垃圾回收，结束移除。

    task 变量在闭包外定义、闭包内只引用：done/error 回调在任务结束
    时才被调用，那时 task 早已完成赋值。

    单文件面板走默认回调（_on_integration_done 画一张图）；对比
    面板传入 on_done/on_error——一个面板有多个任务，各自把结果
    画到同一张图上、出错时各自计数。
    """
    task = None

    def done(result):
        window._tasks.remove(task)
        (on_done or _on_integration_done)(window, key, task, result)
        # 收尾后再清登记：过期检查（回调里对比 _latest_task）要先看得到自己
        if window._latest_task.get(key) is task:
            del window._latest_task[key]   # 不残留已完成任务（防涨爆）

    def error(msg):
        window._tasks.remove(task)
        if on_error is not None:
            on_error(msg)
        else:
            _on_integration_error(window, path, msg)
        if window._latest_task.get(key) is task:
            del window._latest_task[key]

    task = BackgroundTask(_compute_integration, str(path), geom, npt,
                          on_done=done, on_error=error)
    window._latest_task[key] = task   # 每面板只认最新任务（防旧结果覆盖）
    window._tasks.append(task)
    task.start()


def _on_integration_done(window: QMainWindow, key: str, task, result) -> None:
    """面板的计算完成（主线程）：画进它自己的面板。

    每个面板绑定自己的文件，结果永远画回自己的面板；唯一的过期
    情况是同一面板连点两次开了两个任务——先开的晚到会被丢弃
    （每面板只认最新任务，旧结果不得覆盖新图）。
    """
    if window._latest_task.get(key) is not task:
        dock = window.plot_docks.get(key)
        if dock is not None:   # 面板还开着才记日志；关了静默丢弃
            _log(window, f"已忽略 {dock.panel_display} 的过期结果"
                         f"（同一面板已有更新的计算）")
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已被关闭（关闭即遗忘）：迟到结果静默丢弃
    tth, intensity = result
    # 结果留在面板上：图像参数 [应用] 只改显示时，用已有数据重画，
    # 不用重新积分
    dock.last_tth = tth
    dock.last_intensity = intensity
    _set_focus(window, key, dock.windowTitle())   # 最新出的图成为编辑对象
    _draw_1d(window, dock, tth, intensity)
    if len(tth):
        _log(window, f"积分完成：{dock.panel_display}（{len(tth)} 点，"
                     f"2θ {tth.min():.3f}~{tth.max():.3f}°）")
    else:
        _log(window, f"积分完成：{dock.panel_display}（0 点，无有效数据）")


def _on_integration_error(window: QMainWindow, path: Path, msg: str) -> None:
    """积分失败（主线程）：报错进日志区，不崩溃。"""
    _log(window, f"积分失败：{path.name} — {msg}")


def _draw_1d(window: QMainWindow, dock, tth, intensity) -> None:
    """在指定的 1D 面板画出积分曲线（只允许主线程调用）。

    x 轴范围：优先该面板快照里的"视图 2θ 范围"（缩放/平移实时
    写回的显示窗口，只看图不参与计算）；没被用户动过（None）就
    跟随数据组的积分 2θ 上下限。显示样式跟随该面板自己的 1D 显示
    参数（对数纵轴 / 纵轴范围）——每张图各记各的，画哪张就用哪张
    的设置（_panel_param），不看参数坞控件当前值：控件此刻可能正
    显示别的面板的设置。积分完成与图像参数 [应用] 都会走这里——
    后者只改显示、不重新积分。

    全程举着 _setting_limits 旗标：画图里 set_xlim/set_ylim/clear
    引发的范围变化是程序自己设的，不算用户改动，不触发同步写回
    （否则 ax.clear() 会先把范围重置成 (0,1)，同步会写回错值）。
    """
    ax = _content(dock).axes_1d
    window._setting_limits = True
    try:
        ax.clear()
        ax.plot(tth, intensity, "b-", lw=0.8)
        lo = _panel_param(window, dock, "视图 2θ 下限 (°)", None)
        hi = _panel_param(window, dock, "视图 2θ 上限 (°)", None)
        if lo is None or hi is None or not lo < hi:
            lo = _panel_param(window, dock, "2θ 下限 (°)", 1.0)
            hi = _panel_param(window, dock, "2θ 上限 (°)", 8.0)
        if lo < hi:
            ax.set_xlim(lo, hi)
        # 视图范围框显示"正在看的窗口"——只有画的正是焦点面板才填
        if window.plot_docks.get(window.focus_panel) is dock:
            window.params["视图 2θ 下限 (°)"].setValue(lo)
            window.params["视图 2θ 上限 (°)"].setValue(hi)
        # 对数纵轴：弱峰"抬起来"（XRD 行规，主峰与弱峰强度差几个数量级）
        log_y = _panel_param(window, dock, "对数纵轴", False)
        if log_y:
            ax.set_yscale("log")
        # 纵轴范围：自动 = 按曲线 1%/99.9% 分位；手动 = 手填上下限。
        # 对数轴画不出 ≤0 的范围，手动值也兜底抬高。自动模式把算出的
        # 区间填进置灰输入框（只读展示"程序正在用的区间"）——只有画的
        # 正是焦点面板才填：否则会覆盖用户正在看的别面板参数
        if _panel_param(window, dock, "纵轴自动", True):
            ylo, yhi = _auto_y_range(intensity, log_y)
            if window.plot_docks.get(window.focus_panel) is dock:
                window.params["纵轴下限"].setValue(ylo)
                window.params["纵轴上限"].setValue(yhi)
        else:
            ylo = _panel_param(window, dock, "纵轴下限", 1.0)
            yhi = _panel_param(window, dock, "纵轴上限", 100000.0)
            if log_y:
                ylo = max(ylo, 1e-6)
        if ylo < yhi:
            ax.set_ylim(ylo, yhi)
        ax.set_xlabel("2θ (deg)")
        ax.set_ylabel("Intensity (a.u.)")
        ax.set_title(f"{dock.panel_display}: full azimuthal integration")
        ax.grid(alpha=0.3)
        _content(dock).draw()
    finally:
        window._setting_limits = False
    _connect_axis_sync(window, dock.panel_key)   # ax.clear() 清掉了回调（见 helper 注释）
    dock.figure_saved = False   # 重画 = 新内容还没存盘


# ══ 悬停取点（鼠标放曲线上 = 出点 + 状态栏坐标）═════════════
def _hover_motion(window: QMainWindow, key: str, event) -> None:
    """鼠标在曲线上移动时：选最近的那条线、吸附最近的真实数据点，
    画一个白边圆点，状态栏右侧实时显示该点坐标。

    选线按像素距离：把每条线在鼠标 x 处的 y 换算成屏幕像素再比
    距离，纵轴对数、各线范围不同时也公平。圆点颜色 = 所选线的
    颜色（对比图里一眼对上图例），白边保证点在线上也看得清。
    点吸附最近真实数据点而不是鼠标原始位置——坐标报的是真的
    算出来的值。ax.clear()（重算/[应用]）会连标记一起删掉，懒
    重建：发现标记已不在当前坐标轴上就重画一个。
    """
    dock = window.plot_docks.get(key)
    ax = getattr(event, "inaxes", None)
    if dock is None or ax is None:
        _hover_leave(window, key)
        return
    if getattr(dock, "_pan_start", None) is not None:
        _hover_leave(window, key)   # 正在按住左键平移：悬停点退场，别乱跳
        return
    lines = [ln for ln in ax.lines if len(ln.get_xdata()) > 1]
    if not lines:
        _hover_leave(window, key)
        return
    # 选线：数据坐标换算成像素坐标后比距离（对数轴/范围差异下仍公平）
    line, best_d2 = None, None
    for ln in lines:
        xd = ln.get_xdata()
        if xd.size < 2 or not (xd[0] <= event.xdata <= xd[-1]):
            continue   # 鼠标不在该线的 x 范围里，直接跳过
        yline = float(np.interp(event.xdata, xd, ln.get_ydata()))
        px, py = ax.transData.transform((event.xdata, yline))
        d2 = (px - event.x) ** 2 + (py - event.y) ** 2
        if best_d2 is None or d2 < best_d2:
            line, best_d2 = ln, d2
    if line is None:
        _hover_leave(window, key)
        return
    # 吸附最近真实数据点
    xd = line.get_xdata()
    i = int(np.argmin(np.abs(xd - event.xdata)))
    x, y = float(xd[i]), float(line.get_ydata()[i])
    # 白边圆点（ax.clear() 会删掉它 → 不在当前轴上就重建）
    marker = getattr(dock, "hover_marker", None)
    if marker is None or marker.axes is not ax:
        marker = ax.plot([], [], "o", ms=7, mec="white", mew=1.0,
                         zorder=5)[0]
        dock.hover_marker = marker
    marker.set_color(line.get_color())
    marker.set_data([x], [y])
    marker.set_visible(True)
    ax.figure.canvas.draw_idle()
    # 坐标前缀 = 曲线名（对比图 = 文件名）；1D 没设图例名时
    # matplotlib 会默认给 _childN，不算数 → 回退面板标题
    name = line.get_label()
    if not name or name.startswith("_child"):
        name = dock.panel_display
    window.coord_label.setText(f"{name}　2θ = {x:.4g}°, 强度 = {y:.4g}")


def _hover_leave(window: QMainWindow, key: str, event=None) -> None:
    """鼠标离开曲线/坐标轴：藏起圆点、清空坐标标签（常驻标签留空）。"""
    dock = window.plot_docks.get(key)
    if dock is not None:
        marker = getattr(dock, "hover_marker", None)
        if marker is not None:
            marker.set_data([], [])
            marker.set_visible(False)
            if marker.axes is not None:   # ax.clear() 后标记已与轴断开
                marker.axes.figure.canvas.draw_idle()
    window.coord_label.setText("")


# ══ 面板工具栏与手势（拖 = 平移 / 滚轮 = 缩放）═════════════
class _SlimToolbar(NavigationToolbar2QT):
    """只留 [Home][Zoom][Customize][Save] 的精简工具栏（过滤父类工具清单）。

    放大/平移改成鼠标手势（拖 = 平移），放大镜按钮当开关：点亮 =
    滚轮（触摸板两指滚动）以光标为中心缩放（每格 10%）+ 左键拖框
    放大（mpl 自带框选）；熄灭 = 滚轮还给绘图区滚动、左键 = 平移。
    抓手/前进/后退/子图按钮退休；双击不回全图——回首页只有 Home
    一个入口；Customize = matplotlib 自带的轴属性对话框（改范围/
    刻度/标题，范围改动经 xlim_changed 自动同步写回参数）；Save =
    本面板另存为图片。父类 __init__ 按 toolitems 表逐个建按钮，
    覆盖成只含这四个的表即可；放大镜 QAction mpl 自带 checkable，
    点击自动亮灭翻转（mode 同步切 ZOOM/NONE），toggled 信号接
    日志提示。

    Save 重写 save_figure 走 _save_panel：存完置 figure_saved，
    关窗询问"未保存"时不会再问已经存过盘的面板（旧版工具栏 Save
    绕过记账，存过还问）。
    """

    toolitems = [t for t in NavigationToolbar2QT.toolitems
                 if t[0] in ("Home", "Zoom", "Customize", "Save")]

    def __init__(self, canvas, parent=None, window=None, key=None):
        super().__init__(canvas, parent)
        self._window = window
        self._panel_key = key
        # 放大镜开关状态写日志：QAction 点击自带亮灭翻转，mpl 的
        # zoom() 同步切模式（点亮 = ZOOM、熄灭 = NONE）
        self._actions["zoom"].toggled.connect(self._log_zoom_toggle)

    def _log_zoom_toggle(self, on: bool) -> None:
        if self._window is not None:
            _log(self._window,
                 f"放大镜已{'开启' if on else '关闭'}："
                 f"{'滚轮缩放 + 左键框选放大' if on else '滚轮滚动 + 左键平移'}")

    def save_figure(self, *args):
        if self._window is not None and self._panel_key is not None:
            _save_panel(self._window, self._panel_key)
        else:
            super().save_figure(*args)


def _save_panel(window: QMainWindow, key: str) -> None:
    """单面板保存（工具栏 [Save] 走这里）：选文件名存 PNG。

    成功即置 figure_saved = True——这张面板在关窗询问里不再算
    "未保存"；用户取消（没选文件名）不动记账。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return
    figure = getattr(_content(dock), "figure", None)
    if figure is None:
        _log(window, "该面板还没有可保存的图")
        return
    default = str(Path("outputs") / f"{dock.windowTitle()}.png")
    name, _ = QFileDialog.getSaveFileName(
        window, f"保存 {dock.windowTitle()}", default, "PNG 图片 (*.png)")
    if not name:
        return   # 用户取消：不动已保存记账
    if not name.lower().endswith(".png"):
        name += ".png"
    try:
        Path(name).parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(name)
    except OSError as err:
        _log(window, f"保存失败 {dock.windowTitle()} → {name}（{err}）")
        return
    dock.figure_saved = True
    _log(window, f"已保存 {dock.windowTitle()} → {name}")


def _magnifier_on(dock) -> bool:
    """该面板的放大镜（mpl 缩放模式）是否点亮。"""
    toolbar = getattr(_content(dock), "toolbar", None)
    return (toolbar is not None
            and getattr(toolbar.mode, "name", "") == "ZOOM")


def _pan_press(window: QMainWindow, key: str, event) -> None:
    """按住左键在图上按下：记起点像素与当时的显示范围，准备平移。"""
    dock = window.plot_docks.get(key)
    if dock is None or event.inaxes is None or event.button != 1:
        return
    if _magnifier_on(dock):
        return   # 放大镜点亮：左键归 mpl 框选缩放（拖框放大），平移让位
    dock._pan_start = (event.x, event.y)
    dock._pan_limits = (event.inaxes.get_xlim(), event.inaxes.get_ylim())


def _pan_motion(window: QMainWindow, key: str, event) -> None:
    """按住左键拖动 = 整图平移（图跟着鼠标走，像拖地图）。

    起点之后每次移动都从"按下时的显示范围"重算（绝对位移，不
    累计误差）。像素差换算：把起始范围的两个角换算成像素坐标，
    平移后再反算回数据坐标——对数轴也精确（数据坐标直接相减在
    对数轴上会变形）。范围变化走 xlim_changed/ylim_changed →
    自动同步写回参数快照。
    """
    dock = window.plot_docks.get(key)
    if dock is None or event.inaxes is None or event.button != 1:
        return
    start = getattr(dock, "_pan_start", None)
    limits = getattr(dock, "_pan_limits", None)
    if start is None or limits is None:
        return
    ax = event.inaxes
    (x0, x1), (y0, y1) = limits
    dx = event.x - start[0]
    dy = event.y - start[1]
    inv = ax.transData.inverted()
    p0 = ax.transData.transform((x0, y0))
    p1 = ax.transData.transform((x1, y1))
    nx0, ny0 = inv.transform((p0[0] - dx, p0[1] - dy))
    nx1, ny1 = inv.transform((p1[0] - dx, p1[1] - dy))
    ax.set_xlim(nx0, nx1)
    ax.set_ylim(ny0, ny1)
    ax.figure.canvas.draw_idle()


def _pan_release(window: QMainWindow, key: str, event) -> None:
    """松开鼠标：结束平移（清掉起点与起始范围）。"""
    dock = window.plot_docks.get(key)
    if dock is not None:
        dock._pan_start = None
        dock._pan_limits = None


def _wheel_zoom(window: QMainWindow, key: str, event) -> None:
    """滚轮 = 以光标为中心缩放——只在放大镜点亮时生效（与用户讨论定稿）。

    放大镜熄灭时滚轮事件穿透给 QMdiArea 兜底滚动（滚轮 = 滚动
    绘图区看别的图，再也不会误缩图）。点亮后：
    光标对着的那个数据点缩放前后钉在原地（像地图应用）：上下限
    按比例向光标收拢/张开。范围变化自动同步写回参数（缩放会动
    纵轴 → 纵轴自动随之关掉，纵轴窗口由用户接管）。
    Ctrl+滚轮 = 总缩放（Excel 习惯），优先级最高、放大镜点不点亮
    都生效：画布把 Qt 层滚轮事件吃进 mpl 事件（guiEvent），到不
    了视口上的总缩放过滤器——在图上方 Ctrl+滚轮在这里拦截，并把
    Qt 事件 accept 掉，否则未消费的事件传播到视口会再缩一次（双倍）。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return
    gui = getattr(event, "guiEvent", None)
    if gui is not None and gui.modifiers() & Qt.ControlModifier:
        delta = gui.angleDelta().y()
        if delta != 0:
            _apply_area_zoom(window, window._area_zoom
                             * (1.1 if delta > 0 else 1.0 / 1.1))
        gui.accept()   # 防事件再传播到视口过滤器（会缩两次）
        return
    ax = getattr(event, "inaxes", None)
    if ax is None or event.xdata is None or event.ydata is None:
        return
    if not _magnifier_on(dock):
        return   # 放大镜熄灭：滚轮只滚动绘图区，不缩图
    # 每格 10%（1.25 = 25% 太猛：触摸板两指一滑是连续好多小格事件，
    # 连乘几下图就飞了；与用户讨论定为 10%）
    factor = 1.0 / 1.1 if event.button == "up" else 1.1
    x, y = event.xdata, event.ydata
    xlo, xhi = ax.get_xlim()
    ylo, yhi = ax.get_ylim()
    ax.set_xlim(x - (x - xlo) * factor, x + (xhi - x) * factor)
    if ax.get_yscale() == "log" and ylo > 0 and y > 0:
        # 对数轴：加性缩放会把下限算到 0 以下 → matplotlib 整个忽略
        # 这次 set_ylim，上限还跟着涨、下限卡死（缩不动）。改乘性：
        # 光标两侧的比例各开 factor 次方，永远 > 0，光标点在
        # 对数空间里同样钉在原地
        ax.set_ylim(y * (ylo / y) ** factor, y * (yhi / y) ** factor)
    else:
        ax.set_ylim(y - (y - ylo) * factor, y + (yhi - y) * factor)
    ax.figure.canvas.draw_idle()


def _on_xlim_changed(window: QMainWindow, key: str, ax) -> None:
    """x 范围被改动（缩放/平移/Home/Customize 对话框）→ 写回视图
    2θ 范围（看图的窗口，与数据组的积分 2θ 范围互不干扰）。

    焦点面板同时刷新参数坞视图范围框，别面板只写快照——控件正
    显示焦点面板的值，不能串台。程序自己画图设的范围
    （_setting_limits）不算用户改动，跳过（否则 ax.clear() 把
    范围重置成 (0,1) 时会把错值写回快照）。
    """
    if getattr(window, "_setting_limits", False):
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：静默跳过
    snap = getattr(dock, "params_snapshot", None)
    if not snap:
        return
    xlo, xhi = ax.get_xlim()
    snap["视图 2θ 下限 (°)"] = float(xlo)
    snap["视图 2θ 上限 (°)"] = float(xhi)
    if window.plot_docks.get(window.focus_panel) is dock:
        window.params["视图 2θ 下限 (°)"].setValue(xlo)
        window.params["视图 2θ 上限 (°)"].setValue(xhi)


def _on_ylim_changed(window: QMainWindow, key: str, ax) -> None:
    """y 范围被改动 → 写回纵轴窗口 + 关掉纵轴自动。

    用户手动定过的纵轴窗口由用户接管（自动不再覆盖）；纯 x 方向
    的缩放/平移不碰这里——只有真的动了纵轴才关自动。焦点面板
    同时刷新参数坞控件；程序自己画图（_setting_limits）跳过。
    """
    if getattr(window, "_setting_limits", False):
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：静默跳过
    snap = getattr(dock, "params_snapshot", None)
    if not snap:
        return
    ylo, yhi = ax.get_ylim()
    snap["纵轴自动"] = False
    snap["纵轴下限"] = float(ylo)
    snap["纵轴上限"] = float(yhi)
    if window.plot_docks.get(window.focus_panel) is dock:
        # setChecked(False) 触发 sync_ylim → 上下限框解除置灰
        window.params["纵轴自动"].setChecked(False)
        window.params["纵轴下限"].setValue(ylo)
        window.params["纵轴上限"].setValue(yhi)


def _connect_axis_sync(window: QMainWindow, key: str, ax=None) -> None:
    """把 x/y 范围同步写回回调连到面板的坐标轴。

    matplotlib 3.11 起 ax.clear()（cla）会把 ax 的回调注册表整个
    清空 → 每次重画完都必须重连（清空后重连只有一套，不会叠罗汉）。
    闭包只抓 key 不抓容器对象：面板弹出/收回会换容器（子窗口 ↔
    弹出窗口），抓 key 回调永远现查到当前容器；面板关了则 None
    守卫静默跳过。构建面板时内容还没挂进容器 → 调用方直接把 ax
    传进来。
    """
    if ax is None:
        dock = window.plot_docks.get(key)
        if dock is None:
            return
        ax = _content(dock).axes_1d
    ax.callbacks.connect("xlim_changed",
                         lambda a, k=key: _on_xlim_changed(window, k, a))
    ax.callbacks.connect("ylim_changed",
                         lambda a, k=key: _on_ylim_changed(window, k, a))


# ══ 右侧：参数面板 ═════════════════════════════════════════
_CONTRAST_FALLBACK = (1.0, 100000.0)   # 没有焦点图时的自动对比度占位默认
_YLIM_FALLBACK = (1.0, 100000.0)   # 曲线无有效数值时的纵轴占位（任意但安全）


def _auto_y_range(intensity, log_scale: bool) -> tuple:
    """1D 曲线自动纵轴范围：取 1%/99.9% 分位，掐掉极端强/弱值。

    与自动对比度同一套路（见 _auto_contrast_values），只是对象从
    图像像素换成曲线强度。对数纵轴时只取正值参与分位（对数轴画
    不出 ≤0 的范围）；没有有效数值时回退占位默认。
    """
    arr = np.asarray(intensity, dtype=float)
    finite = arr[np.isfinite(arr)]
    if log_scale:
        finite = finite[finite > 0]
    if finite.size == 0:
        return _YLIM_FALLBACK
    return float(np.percentile(finite, 1.0)), float(np.percentile(finite, 99.9))


def _auto_contrast_values(image) -> tuple:
    """自动对比度：取全体像素的 1%/99.9% 分位，掐掉极端亮暗值。

    与 CLI view_diffraction 的默认 vmin/vmax 语义一致。返回 (lo, hi)；
    图像没有有效数值时报 ValueError。
    """
    arr = np.asarray(image, dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        raise ValueError("图像没有有效数值")
    return float(np.percentile(finite, 1.0)), float(np.percentile(finite, 99.9))


def _apply_auto_contrast(window: QMainWindow, silent: bool = False) -> None:
    """按编辑对象（焦点图）重算自动对比度，填进置灰的输入框。

    输入框在自动模式下只是"程序正在用的区间"的展示（只读）。
    对比度只对 2D/剖面视图有意义：焦点是 1D/对比面板时不读文件
    （读一张大 tif 要解码整幅图，主线程上会卡），直接占位默认。
    2D/剖面 焦点 → 按该面板的文件算（图像缓存按路径存 3 张，
    同一文件反复点不再重复解码）。2D 面板真正画图时用
    _auto_contrast_values 从内存里的图像直接算，与这里的展示一致。
    """
    lo, hi = _CONTRAST_FALLBACK
    path = None
    if window.focus_panel is not None:
        dock = window.plot_docks.get(window.focus_panel)
        if dock is not None:
            view = window.focus_panel.split("|", 1)[0]
            # 对比面板没有 panel_file；1D/对比焦点不读文件（见 docstring）
            if view in ("2D", "剖面"):
                path = getattr(dock, "panel_file", None)
    loaded = False
    if path is not None:
        cache = window._image_cache
        image = cache.get(str(path))
        if image is None:
            try:
                image = load_diffraction_image(str(path))
            except Exception as err:
                if not silent:
                    _log(window, f"自动对比度：读取 {path.name} 失败（{err}），"
                                 f"恢复占位默认值")
            else:
                cache[str(path)] = image   # 路径键，改显示名不影响缓存
                while len(cache) > 3:   # 上限 3 张：弹出最早的一张
                    cache.pop(next(iter(cache)))
        if image is not None:
            try:
                lo, hi = _auto_contrast_values(image)
                loaded = True
            except ValueError:
                pass   # 图像没有有效数值：占位默认
    window.params["对比度下限"].setValue(lo)
    window.params["对比度上限"].setValue(hi)
    if not silent:
        if loaded:
            _log(window, f"自动对比度：按 {path.name} 算得 {lo:.1f} ~ {hi:.1f}")
        elif window.focus_panel is None:
            _log(window, "自动对比度：没有编辑对象，恢复占位默认值")
        else:
            _log(window, "自动对比度：编辑对象不是 2D/剖面 视图，"
                         f"恢复占位默认值")


def _compare_shown_curves(window: QMainWindow, dock) -> list:
    """对比面板实际画上去的曲线：[(tth, shown, display, i), ...]。

    shown = 按该面板自己的"对比归一化"设置处理后的显示数据（归一化
    是显示层，原始结果原样保留在 compare_data）；i = 在 compare_files
    里的序号（决定颜色/图例顺序）。画图（_redraw_compare）与自动
    纵轴（_apply_auto_ylim）共用这一份数据——两边口径一致，置灰框
    显示的区间才跟图对得上。
    """
    normalize = _panel_param(window, dock, "对比归一化", True)
    curves = []
    for i, (path, display) in enumerate(dock.compare_files):
        if display not in dock.compare_data:
            continue   # 这条还没算成（本函数只在全部到齐后调用）
        tth, raw = dock.compare_data[display]
        shown = np.asarray(raw, dtype=float)
        if normalize:
            peak = float(np.nanmax(shown)) if shown.size else 0.0
            if peak > 0:
                shown = shown / peak
        curves.append((tth, shown, display, i))
    return curves


def _apply_auto_ylim(window: QMainWindow, silent: bool = False) -> None:
    """按编辑对象（焦点图）重算自动纵轴范围，填进置灰的输入框。

    与 _apply_auto_contrast 同款：自动模式下输入框只是"程序正在用
    的区间"的只读展示。1D 焦点 → 按该面板曲线数据算；对比焦点 →
    按叠图显示数据（含归一化）算；2D/剖面 没有纵轴概念、或还没算
    完 → 占位默认。画图时（_draw_1d/_redraw_compare）也会填一次，
    所以焦点图刚算完/刚 [应用] 后输入框一定是准的。
    """
    ylo, yhi = _YLIM_FALLBACK
    loaded = False
    if window.focus_panel is not None:
        dock = window.plot_docks.get(window.focus_panel)
        if dock is not None:
            view = window.focus_panel.split("|", 1)[0]
            log_y = _panel_param(window, dock, "对数纵轴", False)
            if view == "1D" and getattr(dock, "last_tth", None) is not None:
                ylo, yhi = _auto_y_range(dock.last_intensity, log_y)
                loaded = True
            elif view == "对比" and getattr(dock, "compare_data", None):
                shown = [s for _, s, _, _ in _compare_shown_curves(window, dock)]
                if shown:
                    ylo, yhi = _auto_y_range(np.concatenate(shown), log_y)
                    loaded = True
    window.params["纵轴下限"].setValue(ylo)
    window.params["纵轴上限"].setValue(yhi)
    if not silent and loaded:
        _log(window, f"自动纵轴：编辑对象算得 {ylo:.4g} ~ {yhi:.4g}")


def _build_param_dock(window: QMainWindow) -> QDockWidget:
    """参数坞：顶部固定"编辑对象"名，下面上下对半分两块（QGroupBox）
    ——数据参数（上）/ 图像参数（下），中间分隔条可拖。

    数据参数 = 参与计算的（几何配置 + 积分区间 + 点数），改它们
    会改变积分/校准的结果；图像参数 = 只看图不参与计算的，组内
    分两个区：2D/剖面视图（对比度 + 剖面线角度，接线后生效）+
    "1D 显示" 小节（对数纵轴 + 纵轴范围，随 [应用] 重画曲线）。
    两块各自独立滚动（内容放不下时自动出滚动条），[应用] 在上、
    [恢复默认] 在下竖排一列，固定在各区最下方，不随滚动走。
    排版约定（为窄排版）：单位放在输入框后缀里（标签不带括号单
    位）；成对的上下限并排一行（中间 ~ 连接，转盘限宽到数值能
    完整显示的底限）；小节用全宽灰色小标题（"1D 显示"不套子分组
    框，省嵌套边距）；每项悬停有人话提示。坞有尺寸下限（拖动边
    框时不会把控件裁掉）：左右 = 最宽一行完整显示的宽度，上下 =
    固定件不被遮没的高度。
    对照 CLI：数据参数来自 integrate/calibrate 脚本，图像参数来自
    view_diffraction 的 --vmin/--vmax/--angle。
    """
    dock = QDockWidget("参数", window)
    dock.setObjectName("param_dock")
    # 同文件坞：可移动/可浮动/可关闭，收起参数列给图让地方
    dock.setFeatures(QDockWidget.DockWidgetMovable
                     | QDockWidget.DockWidgetFloatable
                     | QDockWidget.DockWidgetClosable)

    content = QWidget()
    lay = QVBoxLayout(content)
    lay.setContentsMargins(0, 0, 0, 0)

    # 编辑对象：参数坞当前作用在哪个图面板上。点图面板（_FocusMarker）
    # 或某视图计算完成（_on_integration_done）时更新；[应用] 重算它。
    # 编辑对象标题 = 文件名直出，可能很长：_ElideLabel 单行缩略，
    # 中间打省略号保留首尾（重名条目的区分后缀在尾部），悬停看
    # 全名，不撑宽参数坞
    window.focus_label = _ElideLabel("编辑对象：未选中图面板",
                                     Qt.ElideMiddle)
    window.focus_label.setStyleSheet("color: gray;")
    lay.addWidget(window.focus_label)   # 固定最上方，不随下面滚动

    # 下半区上下对半分：两块各自独立滚动的 QScrollArea + 底部固定
    # 按钮行。分隔条可拖（初始 1:1）；两块不允许拖到完全收起
    splitter = QSplitter(Qt.Vertical)
    splitter.setChildrenCollapsible(False)
    lay.addWidget(splitter, 1)

    # ── 数据参数组（上半）──
    data_box = QGroupBox("数据参数")
    data_v = QVBoxLayout(data_box)
    data_v.setContentsMargins(4, 2, 4, 4)

    data_scroll = QScrollArea()
    data_scroll.setWidgetResizable(True)   # 条目随滚动区宽度自动重排
    data_scroll.setFrameShape(QFrame.NoFrame)   # 分组框已有边框，不再套一层
    data_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    data_fields = QWidget()
    form = QFormLayout(data_fields)
    form.setContentsMargins(2, 1, 2, 1)
    data_scroll.setWidget(data_fields)
    data_v.addWidget(data_scroll, 1)

    # 几何配置选择器：下拉框只显示短 key（如 lmfp1_lab6）——长备注
    # 挤进下拉框会撑宽参数坞；完整批次备注放在下方灰色说明行
    # （随选择更新）+ 悬停提示。key 藏在 itemData 里给程序用。
    # 选中即把该条目的标定几何填进下方三个输入框；完整条目（含
    # beam_center）挂在 window.config，后续 2D/剖面接线时直接取用。
    # 注意顺序：先填条目、设默认，再连接信号——建坞阶段日志区还没
    # 建好，信号此刻触发会去写一个还不存在的控件；默认值改由
    # create_window 收尾时显式调用 _apply_config 应用。
    window.config_combo = QComboBox()
    for name, entry in CONFIGS.items():
        window.config_combo.addItem(name, name)
        # 悬停提示：完整批次备注（下拉框里只放短 key）
        window.config_combo.setItemData(
            window.config_combo.count() - 1, entry["label"], Qt.ToolTipRole)

    # 说明行也换 _ElideLabel：自动换行模式下第二行会被 QFormLayout
    # 按一行高布局、压在下一行控件底下（"被遮挡"）。改单行缩略 +
    # 悬停全名：多长的备注都只占一行，不撑宽也不被遮
    window.config_label = _ElideLabel(
        CONFIGS[DEFAULT_CONFIG]["label"], Qt.ElideRight)
    window.config_label.setStyleSheet("color: gray;")

    field = QWidget()   # 下拉框 + 说明行装进一个字段（表格行内竖排）
    box = QVBoxLayout(field)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(2)
    box.addWidget(window.config_combo)
    box.addWidget(window.config_label)

    window.config_combo.setCurrentIndex(
        window.config_combo.findData(DEFAULT_CONFIG))
    window.config_combo.currentIndexChanged.connect(
        lambda i: _apply_config(window, i))
    form.addRow("几何配置", field)

    window.params = {}
    def add_caption(form, text):
        """全宽灰色小节标题（布局行横跨标签/字段两列）。"""
        cap = QLabel(text)
        cap.setStyleSheet("color: gray;")
        row = QHBoxLayout()
        row.addWidget(cap)
        form.addRow(row)
        return cap

    def add_float(form, name, lo, hi, value, decimals=2,
                  label=None, suffix="", tooltip=""):
        box = QDoubleSpinBox()
        box.setRange(lo, hi)
        box.setValue(value)
        box.setDecimals(decimals)
        if suffix:
            box.setSuffix(suffix)   # 单位跟在数字后（标签不再带括号单位）
        if tooltip:
            box.setToolTip(tooltip)
        window.params[name] = box
        form.addRow(label if label is not None else name, box)
        return box

    def add_range(form, lo_name, hi_name, lo, hi, lo_value, hi_value,
                  label, suffix="", decimals=1, tooltip="", max_width=84):
        """成对的上下限并排一行：左框 ~ 右框，共用一个行标签。

        window.params 的键保持原来的 lo_name/hi_name 不动（快照回放、
        测试都按这些键找控件），只改显示排版。max_width = 单框限宽：
        mac 原生转盘内边距很肥（sizeHint 117px 且缩不小），两框并排
        会把坞撑太宽；84 = "100000.0" 在编辑区完整显示的实测底限。
        """
        lo_box = QDoubleSpinBox()
        lo_box.setRange(lo, hi)
        lo_box.setValue(lo_value)
        lo_box.setDecimals(decimals)
        hi_box = QDoubleSpinBox()
        hi_box.setRange(lo, hi)
        hi_box.setValue(hi_value)
        hi_box.setDecimals(decimals)
        for box in (lo_box, hi_box):
            if suffix:
                box.setSuffix(suffix)
            if tooltip:
                box.setToolTip(tooltip)
            box.setMaximumWidth(max_width)
        window.params[lo_name] = lo_box
        window.params[hi_name] = hi_box
        field = QWidget()
        row = QHBoxLayout(field)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        row.addWidget(lo_box, 1)   # 两框平分行宽
        row.addWidget(QLabel("~"))
        row.addWidget(hi_box, 1)
        form.addRow(label, field)
        return lo_box, hi_box

    add_caption(form, "标定几何")
    add_float(form, "像素尺寸 (µm)", 0.0, 10000.0, 200.0, decimals=1,
              label="像素尺寸", suffix=" µm",
              tooltip="探测器单个像素的边长；随几何配置自动填入，也可手改")
    add_float(form, "波长 (Å)", 0.0, 10.0, 0.1223, decimals=4,
              label="波长", suffix=" Å",
              tooltip="X 射线波长；随几何配置自动填入，也可手改")
    add_float(form, "初始距离 (mm)", 0.0, 10000.0, 1600.0, decimals=1,
              label="初始距离", suffix=" mm",
              tooltip="样品到探测器的距离；随几何配置自动填入，也可手改")

    add_caption(form, "积分设置")
    add_range(form, "2θ 下限 (°)", "2θ 上限 (°)", 0.0, 90.0, 1.0, 8.0,
              label="2θ 范围", suffix=" °", max_width=88,
              tooltip="参与积分的衍射角区间")

    npt = QSpinBox()
    npt.setRange(100, 100000)
    npt.setValue(3000)
    npt.setToolTip("2θ 区间内取多少个采样点，越大曲线越细、计算越慢")
    window.params["输出点数"] = npt
    form.addRow("输出点数", npt)

    # [恢复默认] + [应用] 并排：[恢复默认] 只把参数复位（几何回到
    # 当前配置条目、区间/点数回到初值），不计算；[应用] 才重算焦点视图
    data_defaults = {
        "2θ 下限 (°)": 1.0,
        "2θ 上限 (°)": 8.0,
        "输出点数": 3000,
    }
    btn_reset_data = QPushButton("恢复默认")
    btn_reset_data.setObjectName("reset_data_btn")
    btn_apply = QPushButton("应用")
    btn_apply.setObjectName("apply_btn")
    btn_apply.clicked.connect(lambda: _apply_params(window))

    def reset_data():
        _apply_config(window, window.config_combo.currentIndex())
        for name, value in data_defaults.items():
            window.params[name].setValue(value)
        _log(window, "数据参数已恢复默认（未计算，点 [应用] 生效）")

    btn_reset_data.clicked.connect(reset_data)

    btn_col = QVBoxLayout()
    btn_col.setSpacing(4)
    btn_col.addWidget(btn_apply)      # [应用] 在上（主按钮），[恢复默认] 在下
    btn_col.addWidget(btn_reset_data)
    data_v.addLayout(btn_col)   # 按钮列固定在数据区最下方（滚动区之外）

    splitter.addWidget(data_box)

    # ── 图像参数组（下半）──
    img_box = QGroupBox("图像参数")
    img_v = QVBoxLayout(img_box)
    img_v.setContentsMargins(4, 2, 4, 4)

    img_scroll = QScrollArea()
    img_scroll.setWidgetResizable(True)   # 条目随滚动区宽度自动重排
    img_scroll.setFrameShape(QFrame.NoFrame)
    img_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    img_fields = QWidget()
    form2 = QFormLayout(img_fields)
    form2.setContentsMargins(2, 1, 2, 1)
    img_scroll.setWidget(img_fields)
    img_v.addWidget(img_scroll, 1)

    # 组内分区：上面的对比度/剖面角只对二维视图有意义（接线后
    # 生效）；下面的 "1D 显示" 子分组管曲线图自己的显示参数
    add_caption(form2, "2D/剖面视图（接线后生效）")

    # 自动对比度（默认开）：显示区间按编辑对象（焦点图）数据的
    # 1%/99.9% 分位自定，与 view_diffraction 的默认行为一致；取消勾
    # 选后手填两个输入框（对应 --vmin/--vmax 的"指定时覆盖自动值"
    # 语义）。自动模式下输入框置灰 = 只读展示程序正在用的区间；
    # 勾回自动 = 立刻按焦点图重算并填回（恢复默认对比度）。
    auto = QCheckBox("自动对比度")
    auto.setChecked(True)
    auto.setToolTip("显示区间按图像 1%~99.9% 分位自动确定")
    window.params["自动对比度"] = auto
    form2.addRow(auto)

    add_range(form2, "对比度下限", "对比度上限", 0.0, 1e9, 1.0, 100000.0,
              label="显示范围", decimals=1,
              tooltip="取消自动对比度后手填的显示区间（下限 ~ 上限）")

    def sync_contrast(checked, silent=False):
        window.params["对比度下限"].setEnabled(not checked)
        window.params["对比度上限"].setEnabled(not checked)
        if checked:
            _apply_auto_contrast(window, silent=silent)

    auto.toggled.connect(sync_contrast)
    sync_contrast(True, silent=True)   # 初始状态：自动开 → 输入框置灰（不刷日志）

    angle = add_float(form2, "剖面角度 (°)", -180.0, 180.0, 0.0, decimals=1,
                      label="剖面角度", suffix=" °",
                      tooltip="剖面线相对参考方向的角度")
    angle.setSingleStep(5.0)   # 步进 5°，对应 view_diffraction 的 --angle

    # 看图参数：不参与计算，只影响图怎么显示；点 [应用] 落到编辑
    # 对象（快照跟着更新）
    hint = QLabel("只看图不参与计算，点 [应用] 生效")
    hint.setStyleSheet("color: gray;")
    hint.setWordWrap(True)
    hint_row = QHBoxLayout()
    hint_row.addWidget(hint)
    form2.addRow(hint_row)   # 全宽一行（不再挤在标签列里竖排）

    # ── 1D 显示（小节）：曲线图自己的显示参数 ──
    # 不套子分组框（嵌套框自带一套标签列 + 边框，白白多占 ~30px
    # 宽）：灰色小节标题的层次感够用。对数纵轴：主峰与弱峰强度
    # 差几个数量级，对数刻度把弱峰"抬起来"（XRD 软件行规）。纵轴
    # 范围与对比度同套路：自动 = 按曲线 1%/99.9% 分位，取消勾选
    # 手填；自动模式输入框置灰 = 只读展示正在用的区间
    add_caption(form2, "1D 显示")

    # 视图 2θ 范围：只看图不参与计算的显示窗口。初始跟随数据组的
    # 积分 2θ 范围；在图里缩放/平移（滚轮/拖拽/Home/自定义对话框）
    # 会实时写回这里，[应用] 再用这里重画。与数据组的 2θ 范围完全
    # 分开——改这里不会影响积分的区间
    add_range(form2, "视图 2θ 下限 (°)", "视图 2θ 上限 (°)",
              0.0, 90.0, 1.0, 8.0,
              label="视图 2θ 范围", suffix=" °", max_width=88,
              tooltip="看图的窗口：缩放/平移实时写回；[应用] 用这里重画。"
                      "恢复默认 = 回到跟随积分范围")

    log_y = QCheckBox("对数纵轴")
    log_y.setToolTip("对数刻度：强弱峰差几个数量级时弱峰也看得清")
    window.params["对数纵轴"] = log_y
    form2.addRow(log_y)

    auto_y = QCheckBox("纵轴自动")
    auto_y.setChecked(True)
    auto_y.setToolTip("按曲线 1%~99.9% 分位自动确定纵轴区间")
    window.params["纵轴自动"] = auto_y
    form2.addRow(auto_y)

    add_range(form2, "纵轴下限", "纵轴上限", 0.0, 1e9, 1.0, 100000.0,
              label="纵轴范围", decimals=1,
              tooltip="取消自动后手填的纵轴区间（下限 ~ 上限）")

    # 对比归一化：叠图时每条曲线除以自己的最强峰——曝光时间/衰减
    # 不同的文件强度差很多，不归一会被强者压扁（默认开）
    cmp_norm = QCheckBox("归一化到最强峰")
    cmp_norm.setChecked(True)
    cmp_norm.setToolTip("每条曲线除以自己的最强峰：强度差很大的曲线叠图也能比")
    window.params["对比归一化"] = cmp_norm
    form2.addRow(cmp_norm)

    def sync_ylim(checked):
        window.params["纵轴下限"].setEnabled(not checked)
        window.params["纵轴上限"].setEnabled(not checked)
        if checked:
            _apply_auto_ylim(window)   # 勾回自动：立刻按焦点图算并填回（同对比度套路）

    auto_y.toggled.connect(sync_ylim)
    sync_ylim(True)   # 初始状态：自动开 → 输入框置灰

    img_defaults = {
        "对比度下限": 1.0,
        "对比度上限": 100000.0,
        "剖面角度 (°)": 0.0,
        "对数纵轴": False,
        "纵轴自动": True,
        "对比归一化": True,
    }
    btn_reset_img = QPushButton("恢复默认")
    btn_reset_img.setObjectName("reset_image_btn")

    def reset_image():
        if not auto.isChecked():
            auto.setChecked(True)   # 触发 sync_contrast → 按焦点图重算自动值
        else:
            _apply_auto_contrast(window)   # 已勾着 toggled 不响，手动重算
        window.params["剖面角度 (°)"].setValue(img_defaults["剖面角度 (°)"])
        window.params["对数纵轴"].setChecked(img_defaults["对数纵轴"])
        window.params["纵轴自动"].setChecked(img_defaults["纵轴自动"])
        window.params["对比归一化"].setChecked(img_defaults["对比归一化"])
        if window.params["纵轴自动"].isChecked():
            _apply_auto_ylim(window)   # 已勾着 toggled 不响，手动重算填回
        # 视图 2θ 范围回到"跟随积分范围"：从焦点面板快照里删掉
        # 显式视图值（None = 跟随），输入框显示回积分范围
        dock = window.plot_docks.get(window.focus_panel)
        if dock is not None and getattr(dock, "params_snapshot", None):
            for name in ("视图 2θ 下限 (°)", "视图 2θ 上限 (°)"):
                dock.params_snapshot.pop(name, None)
        for name, base in (("视图 2θ 下限 (°)", "2θ 下限 (°)"),
                           ("视图 2θ 上限 (°)", "2θ 上限 (°)")):
            window.params[name].setValue(window.params[base].value())
        _log(window, "图像参数已恢复默认")

    btn_reset_img.clicked.connect(reset_image)

    # [应用] + [恢复默认] 竖排一列（应用在上）：与数据参数组同款
    # 布局。[应用] 把当前图像参数应用到编辑对象（见 _apply_image_params）
    btn_apply_img = QPushButton("应用")
    btn_apply_img.setObjectName("apply_image_btn")
    btn_apply_img.clicked.connect(lambda: _apply_image_params(window))

    btn_col2 = QVBoxLayout()
    btn_col2.setSpacing(4)
    btn_col2.addWidget(btn_apply_img)      # [应用] 在上（主按钮），[恢复默认] 在下
    btn_col2.addWidget(btn_reset_img)
    img_v.addLayout(btn_col2)   # 按钮列固定在图像区最下方（滚动区之外）

    splitter.addWidget(img_box)
    splitter.setSizes([1, 1])          # 初始上下各一半
    splitter.setStretchFactor(0, 1)    # 随坞变高/变矮两块等比例伸缩
    splitter.setStretchFactor(1, 1)
    # 每半的最低高度：至少露出标题 + 按钮行 + 几行内容（内容靠滚动看）
    data_scroll.setMinimumHeight(110)
    img_scroll.setMinimumHeight(110)

    # 拖动坞边框的尺寸下限（上下左右都设）：左右 = 最宽一张表单的
    # 最小宽度 + "壳"（滚动条 + 分组/表单边距，另加少量余量），拖
    # 再窄也到这就停，不会裁掉控件；上下 = 固定件（编辑对象名 +
    # 两组标题/按钮行 + 各几行内容）不被遮没的高度，再矮由各半
    # 自己的滚动条接管。用表单 minimumSizeHint 而不是 content 的
    # sizeHint：转盘限宽后 sizeHint 低估了成对行的最小宽度（实测
    # 会横向裁掉近 20px）
    form_min = max(data_fields.minimumSizeHint().width(),
                   img_fields.minimumSizeHint().width())
    chrome = (data_scroll.verticalScrollBar().sizeHint().width()
              + data_v.contentsMargins().left() + data_v.contentsMargins().right()
              + form.contentsMargins().left() + form.contentsMargins().right())
    dock.setMinimumWidth(form_min + chrome + 2)
    content.setMinimumWidth(form_min + chrome + 2)   # 双保险：坞本身也算上
    dock.setMinimumHeight(content.layout().minimumSize().height())

    dock.setWidget(content)
    window.addDockWidget(Qt.RightDockWidgetArea, dock)
    return dock


def _apply_config(window: QMainWindow, index: int, silent: bool = False) -> None:
    """把下拉框选中的配置条目应用到参数面板。

    几何值来自 config.py 注册表（标定值，不是占位默认值）。只同步
    参数坞里已有的三个输入框（像素/波长/距离）；PONI、倾斜角等其余
    几何键随完整条目一起挂在 window.config 上，留给后续图面板接线。
    silent = 快照回放时的静默切换（用户没动手，不记日志）。
    """
    key = window.config_combo.itemData(index)
    cfg = CONFIGS[key]
    geom = cfg["geometry"]
    window.config_name = key
    window.config = cfg   # 完整条目（label / geometry / beam_center）
    window.config_label.setText(cfg["label"])   # 说明行随选择更新
    window.params["像素尺寸 (µm)"].setValue(geom["pixel_size_m"] * 1e6)
    window.params["波长 (Å)"].setValue(geom["wavelength_m"] * 1e10)
    window.params["初始距离 (mm)"].setValue(geom["dist_m"] * 1e3)
    if not silent:
        _log(window, f"已加载几何配置 {key}（{cfg['label']}）")


# ══ 底部：日志区（两层之一，状态行在 _build_status 里）════════
def _build_log_dock(window: QMainWindow) -> QDockWidget:
    """日志坞：累积输出（校准结果 / 积分统计都滚在这里），可收起。"""
    dock = QDockWidget("日志", window)
    dock.setObjectName("log_dock")
    window.log_text = QPlainTextEdit()
    window.log_text.setReadOnly(True)
    window.log_text.setMaximumBlockCount(5000)   # 只留最近 5000 行，防涨爆
    # 文本编辑器默认接受拖放：不关掉的话，文件拖到日志区会被它
    # 吞掉（塞成文字），到不了顶层窗口的"拖入文件"入口
    window.log_text.setAcceptDrops(False)
    dock.setWidget(window.log_text)
    window.addDockWidget(Qt.BottomDockWidgetArea, dock)
    return dock


def _build_status(window: QMainWindow) -> None:
    """状态行（两层之二）：左侧常驻文字（"就绪"/瞬时消息）+ 右侧
    两块分明：坐标标签（等宽字体 + 凹槽框，一眼就是"数据读数"）
    | 竖分隔线 | 当前文件。用常驻 QLabel 而不是 showMessage——
    后者超时清空后状态栏会变成看不见的细条。坐标标签常驻（鼠标
    没悬停在曲线上时是空文字，悬停时才显示，见 _hover_motion），
    这样出字时状态栏不会整体跳动。"""
    window.status_text = QLabel("就绪")
    window.statusBar().addWidget(window.status_text)
    # 坐标标签：凹槽框 + 等宽字体——坐标是机器读出来的数，用等宽
    # 字体数字不会左右跳；凹槽框把"坐标信息"和右侧文件信息切开
    window.coord_label = QLabel("")   # 鼠标悬停时实时显示曲线坐标
    window.coord_label.setFrameShape(QFrame.Shape.StyledPanel)
    window.coord_label.setStyleSheet(
        "font-family: Menlo, Consolas, monospace; padding: 1px 4px;")
    window.statusBar().addPermanentWidget(window.coord_label)
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    window.statusBar().addPermanentWidget(sep)
    window.file_label = QLabel("未打开文件")
    window.statusBar().addPermanentWidget(window.file_label)


# ══ 顶部：工具栏 ═══════════════════════════════════════════
def _build_toolbar(window: QMainWindow) -> None:
    """工具栏 = [校准] 模式开关 + 作图按钮（2D/剖面/1D/瀑布 + 对比）+
    面板开关（文件/参数/日志）。"""
    tb = QToolBar("主工具栏", window)
    tb.setMovable(False)
    window.addToolBar(tb)

    # 模式开关：[校准] 可勾选。按下 = 校准工作台（几何参数 +
    # 点图微调束心），弹起 = 默认的分析工作台。原来的 [图像] 按钮
    # 已删：outputs 摊成四个作图按钮后，它只剩空壳
    btn_calib = QPushButton("校准")
    btn_calib.setCheckable(True)   # 默认弹起 = 分析工作台
    tb.addWidget(btn_calib)
    window.calib_btn = btn_calib   # 登记按钮（测试与后续接线用）
    btn_calib.toggled.connect(lambda on: _on_mode(window, on))

    tb.addSeparator()

    # 作图按钮：[2D][剖面][1D][瀑布]——点一下 = 打开面板 + 计算
    # 该视图并出图。纯动作不是开关：点几下算几下，重复点击安全；
    # 面板的开/关只由 × 和拖动管理（勾选式的第二次点击会关面板，
    # 让人误以为"画不了"）
    window.view_buttons = {}   # 登记按钮（测试与后续接线用）
    for name in VIEW_NAMES:
        btn = QPushButton(name)
        tb.addWidget(btn)
        window.view_buttons[name] = btn
        btn.clicked.connect(
            lambda checked=False, n=name: _plot_view(window, n))

    # [对比]：把勾选文件的 1D 曲线叠到一张图（见 _plot_compare）。
    # 同为纯动作：重复点击 = 刷新那张对比面板
    btn_compare = QPushButton("对比")
    tb.addWidget(btn_compare)
    window.compare_btn = btn_compare   # 登记按钮（测试用）
    btn_compare.clicked.connect(lambda: _plot_compare(window))

    tb.addSeparator()

    # 面板开关：[文件][参数][日志] 三个勾选按钮，收起/展开对应坞。
    # 全收起来 = 中央只剩绘图区，看图视野最大。双向同步：按钮点
    # 击 → 坞显隐；坞被标题栏 × 关掉 → 按钮自动弹起（visibilityChanged
    # 信号），下次点按钮还能再展开。
    window.panel_toggles = {}   # 登记按钮（测试与后续接线用）
    for name, dock in (("文件", window.file_dock),
                       ("参数", window.param_dock),
                       ("日志", window.log_dock)):
        btn = QPushButton(name)
        btn.setCheckable(True)
        btn.setChecked(True)
        tb.addWidget(btn)
        window.panel_toggles[name] = btn
        dock.visibilityChanged.connect(btn.setChecked)
        btn.toggled.connect(dock.setVisible)


def _on_mode(window: QMainWindow, calibrating: bool) -> None:
    """模式开关：勾选 = 校准工作台，弹起 = 分析工作台（框架阶段只换占位提示）。"""
    if calibrating:
        window.mode_label.setText("校准模式 — 待实现")
        _log(window, "进入校准模式")
    else:
        window.mode_label.setText("分析模式 — 待实现")
        _log(window, "回到分析模式")


# ══ 中央：图面板区（QMdiArea 子窗口，互不牵连，可弹出）══════
class _PlotSubWindow(QMdiSubWindow):
    """绘图子窗口：× 关闭 = 面板从 plot_docks 移除（关闭即遗忘）。

    Qt 默认行为是"关闭 = 隐藏"（还留在 subWindowList 里），与
    "关闭即遗忘"的规则不符 → 重写 closeEvent 走统一关闭入口
    _close_panel（从登记表移除 + 焦点移交 + 销毁容器）。
    """

    def __init__(self, window: QMainWindow, key: str):
        super().__init__()
        self._window = window
        self.panel_key = key
        # 点窗口任何地方都选中该面板：由 _PanelClickTracker（应用级
        # 过滤器，见 create_window）统一处理——QWidget 的父过滤器
        # 收不到子部件事件，容器级过滤器盖不住内容区

    def closeEvent(self, event):
        _close_panel(self._window, self.panel_key)
        super().closeEvent(event)


class _FloatedWindow(QWidget):
    """弹出的独立窗口：面板内容整个搬进来，× 关闭同样"关闭即遗忘"。

    无父 = 顶层 OS 窗口，不随主窗口移动/关闭。收回主窗口时不走
    close()（那会触发 _close_panel 把面板登记抹掉），由调用方直接
    deleteLater() 销毁壳。
    """

    def __init__(self, window: QMainWindow, key: str):
        super().__init__()   # 无父 = 顶层独立窗口
        self._window = window
        self.panel_key = key
        self.content = None   # 面板内容（_content(dock) 从这里取）
        self.setAttribute(Qt.WA_DeleteOnClose)
        # 点窗口任何地方都选中该面板：同 _PlotSubWindow，
        # 由 _PanelClickTracker 统一处理

    def closeEvent(self, event):
        _close_panel(self._window, self.panel_key)
        super().closeEvent(event)


def _content(dock) -> QWidget:
    """面板容器 → 面板内容：子窗口用 widget()，弹出窗口用 content。"""
    if isinstance(dock, QMdiSubWindow):
        return dock.widget()
    return dock.content


def _close_panel(window: QMainWindow, key: str) -> None:
    """统一关闭面板：从登记表移除（关闭即遗忘）+ 焦点移交 + 销毁容器。

    幂等：面板已不在登记表里（如收回主窗口后旧壳被删除）直接返回。
    子窗口先从 MDI 摘下再 deleteLater（探针验证 closeEvent 里这组
    操作安全）；弹出窗口靠 WA_DeleteOnClose 自行销毁。焦点面板被
    关 → 编辑对象移给下一张还开着的图；一张不剩则清空。
    """
    dock = window.plot_docks.pop(key, None)
    if dock is None:
        return
    if isinstance(dock, QMdiSubWindow):
        if dock in window.mdi.subWindowList():
            window.mdi.removeSubWindow(dock)
        dock.deleteLater()
    _log(window, f"已关闭面板：{dock.windowTitle()}")
    if window.focus_panel == key:
        window.focus_panel = None   # 先清空，绕过 _set_focus 的同键早退
        for next_key, next_dock in window.plot_docks.items():
            _set_focus(window, next_key, next_dock.windowTitle())
            break
        if window.focus_panel is None:
            window.focus_label.setText("编辑对象：未选中图面板")


def _build_center(window: QMainWindow) -> None:
    """中央绘图区 = QMdiArea：每张图一个子窗口，各拖各的互不牵连。

    旧方案用停靠分栏（内层 QMainWindow + QDockWidget）：图与图
    共用分栏把手，拽一张必然牵动邻居——这就是"图总是连在一起"
    的根源，参数上无解，换成子窗口才治本。底部细条放模式提示
    （左）+ 横排/竖排按钮（右，替代旧内层状态栏）。
    """
    mdi = QMdiArea()
    mdi.setObjectName("plot_area")
    # 滚动条策略必须显式设（默认策略下溢出区域的滚动条不出现，
    # 探针验证）；平铺只摆位置不缩放，放不下就靠滚动条看
    mdi.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    mdi.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    mdi.setStyleSheet("QMdiArea { background-color: #c8c8c8; }")
    window.mdi = mdi
    # Ctrl+滚轮 = 总缩放（Excel 习惯）：过滤器装在视口上管灰底/
    # 标题栏上的 Ctrl+滚轮，普通滚轮穿透给 QMdiArea 自己滚动；
    # 光标在图上方的那条路在 _wheel_zoom 的 mpl 层拦截
    mdi.viewport().installEventFilter(_AreaZoomFilter(window))
    window.plot_docks = {}
    # 当前面板布局方向（横排/竖排按钮设定）："row" = 一行 /
    # "column" = 一列
    window._layout_orient = "row"
    # 面板代数：每次（重新）开面板 +1；后台任务回调核对代数——
    # 面板关过重开后，旧代迟到结果不会串进新图（对比面板的
    # compare_gen 归零漏洞由它补上）
    window._panel_epoch = {}
    # 总缩放（50%–200%，每格 10%）：Ctrl+滚轮 / 底部 − + 按钮驱动，
    # 绘图区所有子窗口围绕视口中心整体同比缩放（像 Excel 缩放
    # 工作表）；弹出去的独立窗口不参与
    window._area_zoom = 1.0

    window.mode_label = QLabel("分析模式 — 待实现")   # 默认：分析工作台
    window.mode_label.setAlignment(Qt.AlignCenter)

    arrange_box = QWidget()
    row = QHBoxLayout(arrange_box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(2)
    window.arrange_buttons = {}   # 登记按钮（测试与后续接线用）
    for name in ("横排", "竖排"):
        btn = QPushButton(name)
        btn.setFlat(True)      # 扁平样式，像 Excel 角上的小按钮
        row.addWidget(btn)
        window.arrange_buttons[name] = btn
        btn.clicked.connect(
            lambda checked=False, n=name: _arrange(window, n))

    # 总缩放控件（Excel 式 − 100% +）：放在横排/竖排右边
    zoom_box = QWidget()
    zrow = QHBoxLayout(zoom_box)
    zrow.setContentsMargins(0, 0, 0, 0)
    zrow.setSpacing(2)
    window.zoom_label = QLabel("100%")
    window.zoom_label.setMinimumWidth(40)
    window.zoom_label.setAlignment(Qt.AlignCenter)
    window.zoom_buttons = {}   # 登记按钮（测试用）
    for name in ("−", "+"):
        btn = QPushButton(name)
        btn.setFlat(True)
        window.zoom_buttons[name] = btn
        factor = 1.0 / 1.1 if name == "−" else 1.1
        btn.clicked.connect(
            lambda checked=False, f=factor:
            _apply_area_zoom(window, window._area_zoom * f))
    zrow.addWidget(window.zoom_buttons["−"])
    zrow.addWidget(window.zoom_label)
    zrow.addWidget(window.zoom_buttons["+"])

    strip = QWidget()
    srow = QHBoxLayout(strip)
    srow.setContentsMargins(4, 2, 4, 2)
    srow.setSpacing(4)
    srow.addWidget(window.mode_label)
    srow.addStretch(1)
    srow.addWidget(arrange_box)
    srow.addWidget(zoom_box)

    center = QWidget()
    lay = QVBoxLayout(center)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(mdi, 1)
    lay.addWidget(strip)
    window.setCentralWidget(center)


def _build_view_widget(window: QMainWindow, name: str, key: str,
                       title: str) -> QWidget:
    """按视图名创建面板内容：1D = matplotlib 画布，其余暂为占位标签。

    每张面板内容独立（自己的画布/坐标轴，挂在控件上供 _draw_1d
    使用）；以后 2D/剖面/瀑布接线时在此函数里加分支。每个面板都
    装 _FocusMarker：点它即成为参数面板的编辑对象。
    """
    if name == "1D":
        fig = Figure(figsize=(5, 3), tight_layout=True)
        canvas = FigureCanvasQTAgg(fig)
        canvas.axes_1d = fig.add_subplot(111)
        # 每张面板自己的精简工具栏 [Home][Customize][Save] + [弹出]，
        # 只作用于本面板的图。放大/平移改成鼠标手势（拖 = 平移、
        # 滚轮 = 以光标为中心缩放），放大镜/抓手/前进后退/子图按钮
        # 全砍掉；回首页不绑双击——Home 按钮就是回首页。工具栏放
        # 画布上方，面板标题栏不动
        toolbar = _SlimToolbar(canvas, canvas, window, key)
        # 容器 = 工具栏 + 画布竖排。把画布原有属性挂到容器上
        # （axes_1d / figure / draw），其余代码仍按 _content(dock)
        # 直取，不必改调用点
        widget = QWidget()
        box = QVBoxLayout(widget)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(toolbar)
        box.addWidget(canvas)
        widget.axes_1d = canvas.axes_1d
        widget.figure = fig
        widget.canvas = canvas
        widget.toolbar = toolbar
        widget.draw = canvas.draw   # _content(dock).draw() 仍直接落到画布
        widget.panel_key = key   # 弹出/收回按钮经它找面板
        # 弹出按钮：工具栏末尾（按钮跟着内容走，弹出后在新窗口里
        # 照样能点；addWidget 不动 toolitems 表，测试不受影响）
        popout = QPushButton("弹出")
        popout.setFocusPolicy(Qt.NoFocus)
        toolbar.addWidget(popout)
        widget.popout_btn = popout
        popout.clicked.connect(lambda: _toggle_pop_out(window, key))
        # 画布尺寸变化 = 用户拖了面板边框（或程序平铺/开局）→ 记
        # 比例记忆。过滤器装在画布上而不是容器上：弹出/收回换容器
        # 不用重挂（逻辑见 _on_canvas_resized）
        canvas.installEventFilter(_PanelResizeFilter(window, key, canvas))
        # 悬停取点：鼠标移动 → 曲线上出点 + 状态栏出坐标；
        # 移出坐标轴 → 清空（细节见 _hover_motion/_hover_leave）
        canvas.mpl_connect("motion_notify_event",
                           lambda ev, k=key: _hover_motion(window, k, ev))
        canvas.mpl_connect("axes_leave_event",
                           lambda ev, k=key: _hover_leave(window, k, ev))
        # 手势：按住左键拖 = 平移；滚轮（触摸板两指滚动）=
        # 以光标为中心缩放。拖动期间悬停点退场（别在拖图时乱跳）
        canvas.mpl_connect("button_press_event",
                           lambda ev, k=key: _pan_press(window, k, ev))
        canvas.mpl_connect("motion_notify_event",
                           lambda ev, k=key: _pan_motion(window, k, ev))
        canvas.mpl_connect("button_release_event",
                           lambda ev, k=key: _pan_release(window, k, ev))
        canvas.mpl_connect("scroll_event",
                           lambda ev, k=key: _wheel_zoom(window, k, ev))
        # 范围同步写回：缩放/平移/Home/Customize 对话框改动 x/y 范围
        # → 写回该面板快照 + 焦点时同步参数坞控件（x/y 分开处理：
        # 动 x 只写视图范围，动 y 才关纵轴自动，见两个处理函数）。
        # 每次重画 ax.clear() 都会清掉这些回调，画完由 _draw_1d/
        # _redraw_compare 重连（见 _connect_axis_sync）
        _connect_axis_sync(window, key, canvas.axes_1d)
    else:
        placeholder = QLabel(f"{title} — 尚未接线")
        placeholder.setAlignment(Qt.AlignCenter)
        widget = placeholder
    # 焦点切换改挂容器（子窗口/弹出窗口）上：点标题栏/边框也选中
    # （见 _FocusMarker 与两个容器的 __init__），这里不再挂内容上
    _install_resize_grip(window, key, widget)
    return widget


def _open_plot_panel(window: QMainWindow, name: str, key: str,
                     title: str) -> QMdiSubWindow:
    """新开一张图面板：QMdiSubWindow + 内容 + 几何状态 + 级联摆放。

    开局几何：画布默认 500×300（真 5:3，内容 sizeHint 自带），
    子窗口显式 resize(sizeHint())——QMdiSubWindow 不会自动适配内容
    （探针验证），不显式设会以极小尺寸裁剪内容。初始尺寸按当前
    总缩放比例开（和周围的图大小一致）。落点 = 左上角小错位级联
    （像扑克牌发牌：下面几张的标题栏露出来，一眼知道叠着几张）：
    24px 一档、6 档循环回起点，永远待在绘图区左上角区域——旧的
    一路向右下角排（10 档不循环）会让图堆越滚越远。级联只数子
    窗口（弹出的不算），开新图完全不动旧图——这正是"图不再连
    在一起"的核心。
    """
    sub = _PlotSubWindow(window, key)
    sub.setObjectName(f"plot_{name}")
    window.mdi.addSubWindow(sub)
    content = _build_view_widget(window, name, key, title)
    sub.setWidget(content)
    sub.setWindowTitle(title)
    # 比例记忆的初始状态：没拖过 = 默认画布 (500, 300)。_settling
    # 期间（开局/弹出/收回/平铺的程序性尺寸变化）画布 Resize
    # 事件不记成"用户拖过"
    sub._dragged = False
    sub._canvas_pref = (PLOT_OPEN_W, PLOT_OPEN_H)
    sub._last_canvas = (PLOT_OPEN_W, PLOT_OPEN_H)
    sub._settling = True
    # 面板代数 +1：后台任务回调核对代数，关过重开后旧代迟到结果
    # 不会串进新面板
    window._panel_epoch[key] = window._panel_epoch.get(key, 0) + 1
    window.plot_docks[key] = sub
    z = window._area_zoom
    hint = sub.sizeHint()
    sub.resize(max(60, round(hint.width() * z)),   # 必须显式设（见 docstring）
               max(40, round(hint.height() * z)))
    n = sum(1 for d in window.plot_docks.values()
            if isinstance(d, QMdiSubWindow))
    off = 16 + 24 * ((n - 1) % 6)   # 左上角小错位：6 档循环（见 docstring）
    sub.move(round(off * z), round(off * z))
    sub.show()
    _settle(window)
    # 开局引发的画布尺寸事件已全部消化：把最终实际画布尺寸记下，
    # 之后到达的迟到事件对不上预期值会被跳过（不误标"拖过"）
    canvas = getattr(content, "canvas", None)
    if canvas is not None:
        sub._last_canvas = (canvas.width(), canvas.height())
    else:
        # 占位面板没有画布尺寸约束：给个和 1D 面板相仿的开局大小
        ew, eh = _panel_extra(sub)
        sub.resize(PLOT_OPEN_W + ew, PLOT_OPEN_H + eh)
    sub._settling = False
    return sub


def _plot_view(window: QMainWindow, name: str) -> None:
    """工具栏作图按钮的动作：对每个对号文件开面板（或复用）并计算。

    支持批量：勾选几个文件（选入/拖入即全勾），点一下视图按钮 =
    一次开出多张图。面板按「视图 + 文件条目」成对创建：同一文件
    重复点 = 刷新那张图；面板标题 = 视图_显示名（如 1D_lab6-
    00024.tif），多张图一眼分清。重复文件改名加入的条目显示名不同
    （xxx (1).tif），面板键补显示名区分，各自成图、互不当过期。
    按钮是纯动作不是开关——点一下算一下，重复点击安全；面板的
    开/关只由 × 和拖动管理。
    """
    checked = [window.file_list.item(i)
               for i in range(window.file_list.count())
               if window.file_list.item(i).checkState() == Qt.Checked]
    if not checked:
        _log(window, "没有选中的文件")
        return
    for item in checked:
        path = Path(item.data(Qt.UserRole))
        display = item.text()
        key = f"{name}|{path}"
        dock = window.plot_docks.get(key)
        if dock is not None and getattr(dock, "panel_item", None) is not item:
            if getattr(dock, "panel_display", None) == display:
                dock.panel_item = item   # 条目删后重加：面板归位到新条目
            else:
                # 同路径的另一条目（重复文件改名加入）→ 键补显示名区分
                key = f"{name}|{path}|{display}"
                dock = window.plot_docks.get(key)
        if dock is None:
            title = f"{name}_{display}"
            # 新面板级联摆放，现有面板原地不动（开新图不再重排旧图）
            dock = _open_plot_panel(window, name, key, title)
            dock.panel_file = path   # 面板绑定自己的文件（删文件不影响已开的面板）
            dock.panel_item = item   # 面板绑定自己的列表条目（重名条目各自成图）
            dock.panel_display = display   # 显示名（标题/日志/默认存盘名用）
            dock.figure_saved = False   # 有没有存过盘（关窗询问用）
            dock.params_snapshot = _data_snapshot(window)   # 开图快照：数据用当前值，显示从默认起步
            _log(window, f"打开{name}面板：{display}")
        dock.setVisible(True)
        _run_view(window, name, path, key)


# ══ 对比面板（1D 多文件叠图）════════════════════════════════
def _compare_title(displays) -> str:
    """对比面板标题：两个文件 = A vs B；更多 = A 等 N 个文件。"""
    if len(displays) == 2:
        return f"对比_{displays[0]}_vs_{displays[1]}"
    return f"对比_{displays[0]} 等 {len(displays)} 个文件"


def _redraw_compare(window: QMainWindow, key: str) -> None:
    """用面板已有的曲线数据按该面板自己的 1D 显示参数重画整张对比图。

    与 _draw_1d 同套路：坐标范围随参数（2θ 上下限 / 对数纵轴 /
    纵轴范围），只是画多条曲线 + 图例。显示参数同样只读本面板快照
    （_panel_param），不读参数坞控件——各算完到齐收尾时焦点可能还
    在别的面板上，读控件会把别的图的设置画到这张图上。归一化到
    最强峰只动显示数据（原始结果原样保留在 compare_data）。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：静默丢弃
    ax = _content(dock).axes_1d
    window._setting_limits = True   # 同 _draw_1d：程序设范围不算用户改动
    try:
        ax.clear()
        curves = _compare_shown_curves(window, dock)
        # 画图顺序 = 文件列表顺序（不随各文件算完的先后变）→ 图例顺序、
        # 颜色序号稳定（i 由 _compare_shown_curves 携带）
        for tth, shown, display, i in curves:
            ax.plot(tth, shown, f"C{i}", lw=0.8, label=display)
        xlo = _panel_param(window, dock, "视图 2θ 下限 (°)", None)
        xhi = _panel_param(window, dock, "视图 2θ 上限 (°)", None)
        if xlo is None or xhi is None or not xlo < xhi:
            xlo = _panel_param(window, dock, "2θ 下限 (°)", 1.0)
            xhi = _panel_param(window, dock, "2θ 上限 (°)", 8.0)
        if xlo < xhi:
            ax.set_xlim(xlo, xhi)
        if window.plot_docks.get(window.focus_panel) is dock:
            window.params["视图 2θ 下限 (°)"].setValue(xlo)
            window.params["视图 2θ 上限 (°)"].setValue(xhi)
        log_y = _panel_param(window, dock, "对数纵轴", False)
        if log_y:
            ax.set_yscale("log")
        auto_y = _panel_param(window, dock, "纵轴自动", True)
        ylo = yhi = None
        if auto_y:
            if curves:
                ylo, yhi = _auto_y_range(
                    np.concatenate([s for _, s, _, _ in curves]), log_y)
                if ylo < yhi:
                    ax.set_ylim(ylo, yhi)
            else:
                pass   # 一条曲线都没算成：空图，纵轴交给 matplotlib 默认
        else:
            ylo = _panel_param(window, dock, "纵轴下限", 1.0)
            yhi = _panel_param(window, dock, "纵轴上限", 100000.0)
            if log_y:
                ylo = max(ylo, 1e-6)
            if ylo < yhi:
                ax.set_ylim(ylo, yhi)
        # 自动模式把实际用的区间填进置灰输入框（同 _draw_1d，只填焦点）
        if auto_y and ylo is not None and window.plot_docks.get(window.focus_panel) is dock:
            window.params["纵轴下限"].setValue(ylo)
            window.params["纵轴上限"].setValue(yhi)
        ax.set_xlabel("2θ (deg)")
        ax.set_ylabel("Intensity (a.u.)")
        ax.set_title(f"{dock.panel_display}: full azimuthal integration")
        if dock.compare_data:
            ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        _content(dock).draw()
    finally:
        window._setting_limits = False
    _connect_axis_sync(window, dock.panel_key)   # ax.clear() 清掉了回调（见 helper 注释）
    dock.figure_saved = False   # 重画 = 新内容还没存盘


def _finish_compare(window: QMainWindow, key: str) -> None:
    """对比面板全部曲线到齐（含失败）：整图重画 → 成为编辑对象 → 记日志。"""
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：静默丢弃
    _redraw_compare(window, key)
    _set_focus(window, key, dock.panel_display)
    if dock.compare_data:
        _log(window, f"对比完成：{len(dock.compare_data)} 条曲线")
    else:
        _log(window, "对比失败：所有文件的积分都失败了，面板留空")


def _run_compare(window: QMainWindow, key: str) -> None:
    """对对比面板的每个文件各起一个后台积分，结果画到同一张图。

    代次（gen）防过期：重复点 [对比] 或数据 [应用] 时旧代任务
    全部作废。快照开工前拍下（与单文件面板一致）。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：迟到点击/重算不落地
    # 数据参数 = 控件当前值（计算就用它），显示参数沿用面板自己的
    # 旧快照（重按 [对比] 刷新不改这张图的长相）
    dock.params_snapshot = _data_snapshot(window, dock.params_snapshot)
    dock.compare_gen += 1
    gen = dock.compare_gen
    epoch = window._panel_epoch.get(key, 0)   # 面板代数：关过重开旧代全作废
    dock.compare_pending = len(dock.compare_files)
    dock.compare_data = {}
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    window._setting_limits = True   # 程序自己清轴：不触发范围同步写回
    try:
        _content(dock).axes_1d.clear()
    finally:
        window._setting_limits = False

    def finish_one(path, display, result):
        """单文件结果到位：存原始数据、画一条曲线；全齐后收尾。"""
        # 现查面板（不捕获对象：面板可能已关、已换容器）：
        # 面板没了 / 代数变了（关过重开）/ 代次旧了 → 丢弃
        panel = window.plot_docks.get(key)
        if (panel is None
                or window._panel_epoch.get(key, 0) != epoch
                or panel.compare_gen != gen):
            return   # 旧结果静默丢弃（整图已由新代次重画）
        tth, intensity = result
        panel.compare_data[display] = (tth, intensity)
        panel.compare_pending -= 1
        if panel.compare_pending == 0:
            _finish_compare(window, key)

    def fail_one(path, display, msg):
        panel = window.plot_docks.get(key)
        if (panel is None
                or window._panel_epoch.get(key, 0) != epoch
                or panel.compare_gen != gen):
            return
        panel.compare_pending -= 1
        _log(window, f"对比：{display} 积分失败 — {msg}")
        if panel.compare_pending == 0:
            _finish_compare(window, key)

    for path, display in dock.compare_files:
        # 默认参数绑定防闭包晚绑定（循环变量到回调执行时已走到末尾）
        def spawn_one(path=path, display=display):
            def done(window_, key_, task, result):
                finish_one(path, display, result)

            def error(msg):
                fail_one(path, display, msg)

            window.status_text.setText(f"正在积分 {path.name}…")
            _spawn(window, path, geom, npt, key,
                   on_done=done, on_error=error)

        spawn_one()
    _log(window, f"开始对比 {len(dock.compare_files)} 个文件（后台线程）")


def _plot_compare(window: QMainWindow) -> None:
    """[对比] 按钮：把勾选文件的 1D 曲线叠到同一张面板上。

    只做 1D。勾选 ≥2 个文件，每个文件后台各算各的积分，结果陆续
    画进同一张图（一个文件一条曲线，自动分色 + 图例显示名），
    全部完成后该面板成为编辑对象。同一勾选集合重复点 = 复用同一
    张面板刷新；换集合 = 新开一张。面板键 = "对比|排序后的路径串"
    （与单文件面板 "1D|路径" 并存，互不干扰）。
    """
    checked = [window.file_list.item(i)
               for i in range(window.file_list.count())
               if window.file_list.item(i).checkState() == Qt.Checked]
    if len(checked) < 2:
        _log(window, "对比至少勾选两个文件（勾上的文件叠到一张图）")
        return
    files = [(Path(item.data(Qt.UserRole)), item.text()) for item in checked]
    key = "对比|" + ",".join(sorted(str(p) for p, _ in files))
    dock = window.plot_docks.get(key)
    if dock is None:
        title = _compare_title([d for _, d in files])
        # 新面板级联摆放，现有面板原地不动（同 _plot_view）
        dock = _open_plot_panel(window, "1D", key, title)
        dock.panel_display = title   # 标题/日志/默认存盘名用
        dock.figure_saved = False
        dock.compare_files = files   # 面板绑定这组文件（重算用）
        dock.compare_gen = 0
        dock.params_snapshot = _data_snapshot(window)   # 新面板：显示参数从默认起步
        _log(window, f"打开对比面板：{len(files)} 个文件叠一张图")
    else:
        # 复用面板：文件显示名可能变过（删除重加/改名）→ 绑定刷新，
        # 标题/图例跟着新名字走
        dock.compare_files = files
        title = _compare_title([d for _, d in files])
        if title != dock.panel_display:
            dock.setWindowTitle(title)
            dock.panel_display = title
    dock.setVisible(True)
    _run_compare(window, key)


class _PanelGripFilter(QObject):
    """四边抓取带 + 四角抓取区（右下角有可见把手 ▙）的事件过滤器。

    macOS 原生样式画的 MDI 子窗口边框几乎不可见（用户实测：没有
    拉伸光标、抓不到边）→ 不靠边框了，自己给图装"抓手"：内容四
    边各留 5px 抓取带、四角各留 16px 抓取区，悬停换方向光标，按住
    左键拖 = 直接改容器（子窗口/弹出窗口）几何。装到事件落点控件
    上（画布/占位内容/把手，见 _install_resize_grip 的原因说明）；
    不消费普通区域的鼠标事件——平移/悬停取点照旧。拖完画布尺寸
    变化照常走 _on_canvas_resized 记"拖过"。位置判定统一换算到
    内容坐标：把手/画布的事件都按同一套几何算。
    """

    CORNER, EDGE = 16, 5
    _CURSORS = {
        "left": Qt.SizeHorCursor, "right": Qt.SizeHorCursor,
        "top": Qt.SizeVerCursor, "bottom": Qt.SizeVerCursor,
        "topleft": Qt.SizeFDiagCursor, "bottomright": Qt.SizeFDiagCursor,
        "topright": Qt.SizeBDiagCursor, "bottomleft": Qt.SizeBDiagCursor,
    }

    def __init__(self, window: QMainWindow, key: str, content, grip):
        super().__init__(content)   # 父 = 内容：防 Python GC 静默失效
        self._window = window
        self._key = key
        self._content = content
        self._grip = grip
        self._zone = None      # 当前悬停区（控制光标）
        self._drag = None      # (起点全局坐标, 起点几何, 抓取区名)

    @classmethod
    def _zone_at(cls, w, h, x, y):
        """内容坐标 → 抓取区名；不在任何区 → None（角优先于边）。"""
        c, e = cls.CORNER, cls.EDGE
        if x < c and y < c:
            return "topleft"
        if x > w - c and y < c:
            return "topright"
        if x < c and y > h - c:
            return "bottomleft"
        if x > w - c and y > h - c:
            return "bottomright"
        if x < e:
            return "left"
        if x > w - e:
            return "right"
        if y < e:
            return "top"
        if y > h - e:
            return "bottom"
        return None

    def _apply_drag(self, gpos):
        """拖拽中：按起点几何 + 全局位移算新几何（增量法，子窗口的
        MDI 坐标与弹出窗口的屏幕坐标都适用）。"""
        sx, sy, gx, gy, gw, gh, z = self._drag
        dx, dy = gpos.x() - sx, gpos.y() - sy
        x, y, w, h = gx, gy, gw, gh
        if "left" in z:
            x, w = gx + dx, gw - dx
        elif "right" in z:
            w = gw + dx
        if "top" in z:
            y, h = gy + dy, gh - dy
        elif "bottom" in z:
            h = gh + dy
        # 下限：别缩到看不见（内容自身最小 ~65×57，留够余地）
        w, h = max(w, 120), max(h, 100)
        dock = self._window.plot_docks.get(self._key)
        if dock is not None:
            dock.setGeometry(x, y, w, h)

    def eventFilter(self, obj, event):
        et = event.type()
        if et == QEvent.Type.Resize and obj is self._content:
            # 把手钉在右下角（内容变尺寸时跟随）
            self._grip.move(self._content.width() - self._grip.width() - 2,
                            self._content.height() - self._grip.height() - 2)
            return False
        if et not in (QEvent.Type.MouseMove, QEvent.Type.Enter,
                      QEvent.Type.MouseButtonPress,
                      QEvent.Type.MouseButtonRelease):
            return False
        # 事件可能落在子部件（画布/把手）上：统一换算到内容坐标判区
        gpos = event.globalPosition().toPoint()
        pos = self._content.mapFromGlobal(gpos)
        zone = self._zone_at(self._content.width(), self._content.height(),
                             pos.x(), pos.y())
        if et in (QEvent.Type.MouseMove, QEvent.Type.Enter):
            if self._drag is not None:
                if et == QEvent.Type.MouseMove:
                    self._apply_drag(gpos)
                return True   # 拖拽中：吃下事件，不传给画布平移
            if zone != self._zone or et == QEvent.Type.Enter:
                self._zone = zone
                # 方向光标直接设在落点控件上：画布被 mpl 设过自己的
                # 光标，靠内容级继承会被它盖住；把手自带固定斜向光标
                if obj is not self._grip:
                    cur = self._CURSORS[zone] if zone else Qt.ArrowCursor
                    if isinstance(obj, QToolBar):
                        # QToolBar 会丢弃自己的光标（探针实证：setCursor
                        # 后立刻读回是对的，事件循环一转就没了）——
                        # 设到内容上让它继承（内容自己的光标只露在
                        # 工具栏条上，画布被自己的光标盖着不冲突）
                        self._content.setCursor(cur)
                    else:
                        obj.setCursor(cur)
            return False   # 悬停不拦截：画布取点照旧
        if et == QEvent.Type.MouseButtonPress:
            if (zone is not None
                    and event.button() == Qt.LeftButton):
                self._drag = (gpos.x(), gpos.y(), *_dock_geo(self._window,
                                                             self._key),
                              zone)
                return True   # 吃下：不让画布当平移起点
            return False
        # MouseButtonRelease
        if self._drag is not None:
            self._drag = None
            return True
        return False


def _dock_geo(window: QMainWindow, key: str):
    """当前容器几何 (x, y, w, h)；面板已关就原地踏步（拖拽兜底）。"""
    dock = window.plot_docks.get(key)
    if dock is None:
        return (0, 0, 0, 0)
    g = dock.geometry()
    return (g.x(), g.y(), g.width(), g.height())


def _install_resize_grip(window: QMainWindow, key: str, content) -> None:
    """给面板内容装四边/四角抓手 + 右下角可见把手。

    把手 = 半透明小三角标签（child of 内容）：光标变斜向箭头、
    按住拖 = 拉伸右下角；位置随内容尺寸变化由 _PanelGripFilter
    钉住。过滤器必须装到事件落点控件上：QWidget 的父过滤器收不
    到子部件事件（探针实证：子部件 accept 后不向上传播，而
    matplotlib 画布会 accept 鼠标按下）——所以 1D 面板装画布、
    占位面板装内容本体、把手自己再装一份；顶边抓取带落在工具栏
    条上（含坐标标签），也各挂一份（按钮是它的子部件，按到按钮
    仍各司其职，不会误拉伸）。mouseTracking 打开：悬停换光标需
    要鼠标移动事件（按住拖动期间的移动事件有隐式鼠标抓取，把手
    不开也照常拖）。光标机制：过滤器把方向光标直接设在落点控件
    上——画布被 mpl 设过自己的光标，靠内容级继承会被它盖住；
    QToolBar 反过来会丢弃自己的光标（探针实证），它的方向光标
    设到内容上让它继承；右下角被把手挡着，把手自带 SizeFDiag
    光标，按住把手拖 = 右下角拉伸（按压位置会换算回内容坐标判区）。
    """
    grip = QLabel("▙", content)
    grip.setCursor(Qt.SizeFDiagCursor)
    grip.setStyleSheet("color: #808080; background: transparent;")
    grip.setFixedSize(16, 16)
    grip.move(max(content.width() - 18, 0), max(content.height() - 18, 0))
    content.setMouseTracking(True)
    filt = _PanelGripFilter(window, key, content, grip)
    canvas = getattr(content, "canvas", None)
    # content 必挂：接自己的 Resize 事件重定位把手（1D 面板它的
    # 鼠标事件全被画布挡着，只有 Resize 会来，无害）；画布另挂
    # 一份接真实鼠标落点（见 docstring 的原因说明）
    targets = [content, grip]
    if canvas is not None:
        canvas.setMouseTracking(True)
        targets.append(canvas)
    toolbar = getattr(content, "toolbar", None)
    if toolbar is not None:
        # 内容上边 5px 抓取带落在工具栏条上：也挂一份（按钮是它的
        # 子部件，按到按钮仍各司其职，不会误拉伸）
        toolbar.setMouseTracking(True)
        targets.append(toolbar)
        loc = getattr(toolbar, "locLabel", None)
        if loc is not None:
            # 坐标标签占住工具栏右侧大半：不挂它顶边拖拽会在这里断
            loc.setMouseTracking(True)
            targets.append(loc)
    for target in targets:
        target.installEventFilter(filt)
    content._resize_grip = grip


class _PanelResizeFilter(QObject):
    """装在画布上的事件过滤器：画布尺寸一变就记"用户拖过面板"。

    挂在画布而不是容器上：弹出/收回换容器不用重挂。MDI 子窗口
    自由缩放——用户拖边 = 画布跟着变，把当前画布尺寸记成新偏好
    比例；程序自己的布局变化（开局/平铺/弹出收回）不算拖动。
    真正的逻辑在 _on_canvas_resized。以画布为父对象：installEventFilter
    不接管所有权，无父的过滤器对象会被 Python 垃圾回收、静默失效
    （探针验证过）。
    """

    def __init__(self, window: QMainWindow, key: str, canvas):
        super().__init__(canvas)
        self._window = window
        self._key = key

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            _on_canvas_resized(self._window, self._key)
        return False


def _on_canvas_resized(window: QMainWindow, key: str) -> None:
    """画布尺寸变化 → 记比例记忆（每拖一次 = 记住当前画布比例）。

    规则（与用户讨论定稿）：
      - 手动缩放完全自由：拖成什么样就什么样，不弹回任何比例
        （旧的普通拖/Shift 拖之分随停靠分栏一起退场）；
      - 每拖一次 = _canvas_pref 记成当前画布尺寸、_dragged = True
        ——之后开新图不动它、横排/竖排平铺按它等比摆放，这就是
        "拖过就永远按拖成的比例缩放"的记忆载体；
      - 比例记忆是缩放无关值：总缩放 80% 时拖成 400×240 的画布，
        记 500×300——Ctrl+滚轮回到 100% 时面板正好是拖成的比例，
        不会把总缩放误记成"用户拖过"（记忆 ÷ 当前总缩放）；
      - 程序自己的布局变化不算拖动：_layouting（平铺）与面板级
        _settling（开局/弹出/收回）举旗期间直接跳过；旗外还有
        _last_canvas 预期值兜底——与预期一致的迟到事件同样跳过，
        不误标"拖过"。子窗口不随主窗口缩放，无需再区分"拖图"与
        "拖窗口"（旧规则里那一大段启发式随分栏一起删掉）。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：静默丢弃
    canvas = getattr(_content(dock), "canvas", None)
    if canvas is None:
        return   # 占位面板没有画布，没有比例记忆
    now = (canvas.width(), canvas.height())
    if (getattr(window, "_layouting", False)
            or getattr(dock, "_settling", False)
            or now == getattr(dock, "_last_canvas", None)):
        return
    dock._last_canvas = now
    z = getattr(window, "_area_zoom", 1.0)
    dock._canvas_pref = (now[0] / z, now[1] / z)
    dock._dragged = True


def _panel_extra(dock) -> tuple:
    """面板"壳"尺寸 = 容器尺寸 − 画布尺寸（标题栏 + 工具栏 + 边框）。

    布局稳定后直接量；刚创建/未布局时量出来是垃圾值（探针实测
    子窗口刚 setWidget 后宽 0..60、高 10..300 都有）→ 改用
    sizeHint 差值兜底（创建时 sizeHint 差值与稳定后的实测一致，
    同旧 _dock_extra 的验证结论）。返回 (宽, 高)；占位面板没有
    画布 → (0, 0)。弹出窗口的壳是 OS 标题栏（不进 widget 几何），
    量出来 = (0, 0)——正常，弹出状态本就不需要壳。
    """
    content = _content(dock)
    canvas = getattr(content, "canvas", None)
    if canvas is None:
        return 0, 0
    live = (dock.width() - canvas.width(), dock.height() - canvas.height())
    if 0 <= live[0] <= 60 and 10 <= live[1] <= 300:   # 合理区间外的 = 垃圾
        return live
    fallback = (dock.sizeHint().width() - content.sizeHint().width(),
                dock.sizeHint().height() - content.sizeHint().height())
    if 0 <= fallback[0] <= 60 and 10 <= fallback[1] <= 300:
        return fallback
    return 0, 0


def _apply_area_zoom(window: QMainWindow, new: float) -> None:
    """总缩放：绘图区所有子窗口围绕视口中心整体同比缩放（50%–200%）。

    像 Excel 缩放工作表：所有图一起变大变小、相对位置不变，每张
    图自己的比例记忆（_canvas_pref）是缩放无关值（见
    _on_canvas_resized），总缩放不碰它。只动 MDI 里的子窗口——
    弹出去的独立窗口各管各的（与平铺同理）。先滚动归零再动手：
    QMdiArea 在滚动状态下会把滚动偏移混进子窗口 move 坐标
    （平铺踩过的同一个坑，见 _tile_panels）。面板 _settling 举
    旗：程序性尺寸变化不记成"用户拖过"。
    """
    new = min(2.0, max(0.5, new))
    if abs(new - window._area_zoom) < 1e-9:
        return
    mdi = window.mdi
    mdi.horizontalScrollBar().setValue(0)
    mdi.verticalScrollBar().setValue(0)
    QApplication.processEvents()
    subs = [d for d in window.plot_docks.values()
            if isinstance(d, QMdiSubWindow)]
    k = new / window._area_zoom
    cx = mdi.viewport().width() / 2   # 滚动已归零：锚点 = 视口中心
    cy = mdi.viewport().height() / 2
    for d in subs:
        d._settling = True
        d.move(round(cx + (d.x() - cx) * k), round(cy + (d.y() - cy) * k))
        d.resize(max(60, round(d.width() * k)),
                 max(40, round(d.height() * k)))
    window._area_zoom = new
    _settle(window)
    for d in subs:
        canvas = getattr(_content(d), "canvas", None)
        if canvas is not None:
            d._last_canvas = (canvas.width(), canvas.height())
        d._settling = False
    window.zoom_label.setText(f"{round(new * 100)}%")
    _log(window, f"总缩放 {round(new * 100)}%")


class _AreaZoomFilter(QObject):
    """装在 QMdiArea 视口上的事件过滤器：Ctrl+滚轮 = 总缩放。

    普通滚轮不管（穿透给 QMdiArea 自己滚动看图）；Ctrl+滚轮吃下
    、每格 10%（Excel 的 Ctrl+滚轮缩放工作表习惯）。管灰底/标题
    栏上的 Ctrl+滚轮；光标在图上方的那条路在 _wheel_zoom 的
    mpl 层拦截（画布把滚轮事件吃进 mpl 事件，到不了这里）。
    """

    def __init__(self, window: QMainWindow):
        super().__init__(window.mdi.viewport())   # 以视口为父：不被 GC
        self._window = window

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Type.Wheel
                and event.modifiers() & Qt.ControlModifier):
            delta = event.angleDelta().y()
            if delta == 0:
                return True   # Ctrl+横向滚轮：吃掉，别误触缩放
            factor = 1.1 if delta > 0 else 1.0 / 1.1
            _apply_area_zoom(self._window,
                             self._window._area_zoom * factor)
            return True
        return False


def _tile_panels(window: QMainWindow, subs, orient: str) -> None:
    """平铺 = 纯摆位置：按类型分层，图保持各自大小，绝不缩放。

    缩放数学题整个退役：旧实现为了"贴满视口"要算统一系数、量壳、
    防压扁——用户拍板"宁可滚动也不压扁"后这些全部不需要（与用户
    讨论定稿：默认图永远 500×300、拖过的图永远保持拖成比例，
    平铺根本不碰尺寸，"拖过 = 永远按拖成比例"从此不需要任何
    保护代码，_layouting 旗标也随之退役）。

    摆图前先把滚动归零：QMdiArea 在滚动状态下会把滚动偏移混进
    子窗口 move 坐标（探针实证：横滚 628 时再排列，桌面多出
    628px 灰区、图整体向右下漂移，每排一次漂一次）——归零后
    坐标精确落在 (4,4) 起步，排完视图自然从左上角开始展示。
    平铺不碰总缩放（各管各的）。

    分组：按面板键第一段（2D/剖面/1D/瀑布/对比各算一类），类型
    顺序 = 开图先后（plot_docks 的键序）。横排 = 每类一行（顶
    对齐，行内从左往右）；竖排 = 每类一列（左对齐，列内从上往
    下）。行比视口宽 / 层总高比视口高 → QMdiArea 滚动条兜底
    （两向 AsNeeded 策略本来就开着）。
    """
    if not subs:
        return
    window.mdi.horizontalScrollBar().setValue(0)
    window.mdi.verticalScrollBar().setValue(0)
    QApplication.processEvents()
    groups = {}
    for d in subs:
        groups.setdefault(d.panel_key.split("|", 1)[0], []).append(d)
    if orient == "column":
        # 竖排：每类一列，列内从上往下
        x = 4
        for row in groups.values():
            col_w = max(d.width() for d in row)
            y = 4
            for d in row:
                d.move(x, y)
                y += d.height() + 8
            x += col_w + 8
    else:
        # 横排：每类一行，行内从左往右
        y = 4
        for row in groups.values():
            x = 4
            row_h = max(d.height() for d in row)
            for d in row:
                d.move(x, y)
                x += d.width() + 8
            y += row_h + 8
    _log(window, f"已{'竖排' if orient == 'column' else '横排'}"
                 f" {len(subs)} 个面板（按类型分层，保持各自大小）")


def _settle(window: QMainWindow) -> None:
    """消化排队中的 Qt 布局事件（_layouting 举着时处理函数会跳过）。"""
    for _ in range(5):
        QApplication.processEvents()


def _arrange(window: QMainWindow, mode: str) -> None:
    """一键重排主窗口内的面板：横排 = 每类一行 / 竖排 = 每类一列。

    纯摆位置、绝不缩放（默认图保持 500×300、拖过的保持拖成比例，
    放不下靠滚动条看——"宁可滚动也不压扁"）；只排主窗口内的子窗
    口，弹出去的独立窗口不碰。每次点击都重新确立布局（之前怎么
    摆的都归位），方向记在 _layout_orient 上。
    """
    subs = [d for d in window.plot_docks.values()
            if isinstance(d, QMdiSubWindow)]
    if not subs:
        _log(window, "没有打开的面板")
        return
    window._layout_orient = "column" if mode == "竖排" else "row"
    _tile_panels(window, subs, window._layout_orient)


def _toggle_pop_out(window: QMainWindow, key: str) -> None:
    """[弹出]/[收回]：面板内容搬到独立 OS 窗口，或搬回 MDI 子窗口。

    弹出（探针验证顺序）：先建浮动窗口、把内容改挂过去（addWidget
    自带改挂），再删旧子窗口——顺序反了内容会被连带销毁。状态经
    白名单 _copy_panel_attrs 搬家（vars() 整体拷会砸坏 PySide6
    信号）。壳尺寸（标题栏 + 边框）在弹出时量好记下；收回时按
    "内容尺寸 + 壳"×（当前总缩放 / 弹出时总缩放）恢复——内容的
    画布在弹出时已带当时的缩放，直接乘当前缩放会双重缩（探针
    实证：80% 弹出 100% 收回会落位 406 而不是 508）。
    收回：浮动壳用 deleteLater 而不是 close()——close() 会触发
    _close_panel 把面板登记抹掉；子窗口回到主窗口左上角。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return
    content = _content(dock)
    btn = getattr(content, "popout_btn", None)
    if isinstance(dock, QMdiSubWindow):
        # ── 弹出：MDI 子窗口 → 顶层窗口 ──
        dock._settling = True
        cw, ch = content.width(), content.height()
        floated = _FloatedWindow(window, key)
        floated.setWindowTitle(dock.windowTitle())
        floated.content = content
        box = QVBoxLayout(floated)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(content)   # 先改挂内容，再删旧子窗口
        floated._shell = (dock.width() - cw, dock.height() - ch)
        floated._pop_zoom = window._area_zoom   # 收回时折算用（见 docstring）
        floated._object_name = dock.objectName()
        _copy_panel_attrs(dock, floated)
        window.plot_docks[key] = floated
        if dock in window.mdi.subWindowList():
            window.mdi.removeSubWindow(dock)
        dock.deleteLater()
        floated.resize(cw, ch)
        floated.show()
        _settle(window)
        canvas = getattr(content, "canvas", None)
        if canvas is not None:
            floated._last_canvas = (canvas.width(), canvas.height())
        floated._settling = False
        if btn is not None:
            btn.setText("收回")
        _log(window, f"已弹出面板：{floated.windowTitle()}")
    else:
        # ── 收回：顶层窗口 → MDI 子窗口 ──
        floated = dock
        floated._settling = True
        cw, ch = content.width(), content.height()
        ew, eh = getattr(floated, "_shell", (0, 0))
        sub = _PlotSubWindow(window, key)
        sub.setObjectName(getattr(floated, "_object_name", "plot_1D"))
        window.mdi.addSubWindow(sub)
        sub.setWidget(content)   # 先改挂内容，再删旧壳（同弹出）
        sub.setWindowTitle(floated.windowTitle())
        _copy_panel_attrs(floated, sub)
        window.plot_docks[key] = sub
        floated.deleteLater()   # 不用 close()：close 会触发 _close_panel 抹掉登记
        z = window._area_zoom
        k = z / getattr(floated, "_pop_zoom", 1.0)   # 内容带弹出时缩放，折算回当前
        sub.resize(max(round((cw + ew) * k), 60),   # 按当前总缩放落位：
                   max(round((ch + eh) * k), 40))   # 和周围的图大小一致
        sub.move(round(16 * z), round(16 * z))
        sub.show()
        _settle(window)
        canvas = getattr(content, "canvas", None)
        if canvas is not None:
            sub._last_canvas = (canvas.width(), canvas.height())
        sub._settling = False
        if btn is not None:
            btn.setText("弹出")
        _log(window, f"已收回面板：{sub.windowTitle()}")


# ══ 保存：勾选已输出的图 → 逐个选文件名存 PNG ═══════════════
def _save_figures(window: QMainWindow) -> bool:
    """[保存] 按钮与关窗询问共用：弹窗勾选要保存的图 → 逐个选文件名存 PNG。

    返回 False = 流程被取消（关窗时应留在程序里），True = 完成。
    目前只有 1D 面板有真图（figure）；占位面板不参与。
    """
    panels = [d for d in window.plot_docks.values()
              if getattr(_content(d), "figure", None) is not None]
    if not panels:
        _log(window, "没有已输出的图可保存")
        return True
    chosen = _choose_panels(window, panels)
    if chosen is None:
        _log(window, "已取消保存")
        return False
    if not chosen:
        _log(window, "没有勾选要保存的图")
        return False
    saved, skipped = 0, 0
    for dock in chosen:
        default = str(Path("outputs") / f"{dock.windowTitle()}.png")
        name, _ = QFileDialog.getSaveFileName(
            window, f"保存 {dock.windowTitle()}", default, "PNG 图片 (*.png)")
        if not name:
            skipped += 1   # 这张图用户没存：不算"保存完成"
            _log(window, f"已跳过保存 {dock.windowTitle()}")
            continue
        if not name.lower().endswith(".png"):
            name += ".png"
        try:
            Path(name).parent.mkdir(parents=True, exist_ok=True)
            _content(dock).figure.savefig(name)
        except OSError as err:
            _log(window, f"保存失败 {dock.windowTitle()} → {name}（{err}）")
            skipped += 1
            continue
        dock.figure_saved = True
        saved += 1
        _log(window, f"已保存 {dock.windowTitle()} → {name}")
    if saved:
        _log(window, f"保存完成：{saved} 张图")
    return skipped == 0


def _choose_panels(window: QMainWindow, panels) -> list:
    """弹窗勾选要保存的面板（默认全勾）；确定 = 勾选列表（可为空），
    取消 = None（与"确定但一张没勾"区分开）。"""
    dlg = QDialog(window)
    dlg.setWindowTitle("保存哪些图")
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel("勾选要保存的图："))
    lst = QListWidget()
    for d in panels:
        it = QListWidgetItem(d.windowTitle())
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Checked)   # 默认全勾
        lst.addItem(it)
    lay.addWidget(lst)
    row = QHBoxLayout()
    ok = QPushButton("确定")
    cancel = QPushButton("取消")
    row.addWidget(ok)
    row.addWidget(cancel)
    lay.addLayout(row)
    ok.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    if dlg.exec() != QDialog.Accepted:
        return None
    return [panels[i] for i in range(lst.count())
            if lst.item(i).checkState() == Qt.Checked]


def _confirm_close(window: QMainWindow, n_unsaved: int) -> str:
    """关窗时询问未保存的图怎么处理；返回 "save" / "discard" / "cancel"。

    窗口从未显示过（测试等非交互场景）不弹框，直接按"不保存"关，
    免得模态对话框把测试挂死；真实使用中窗口显示过，正常弹框。
    """
    if not window.isVisible():
        return "discard"
    ans = QMessageBox.question(
        window, "关闭 XRD Toolkit",
        f"有 {n_unsaved} 张图尚未保存，要保存后再关闭吗？",
        QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        QMessageBox.Save)
    return {QMessageBox.Save: "save", QMessageBox.Discard: "discard"}.get(
        ans, "cancel")


# ══ 主窗口组装 ════════════════════════════════════════════
def create_window() -> QMainWindow:
    """组装主框架：文件坞 + 参数坞 + 日志坞 + 工具栏 + 中央面板区。"""
    window = _MainWindow()
    window.setWindowTitle("XRD Toolkit")
    window.resize(1200, 800)
    # 关窗即销毁 C++ 对象：窗口自己的 dict 里挂满了捕获自己的闭包
    # （点选/作图/保存等回调），Python 引用计数环永远归不了零；而
    # shiboken 的 C++→Python 绑定映射在 C++ 对象存活期间持有包装
    # 器，gc 视之为可达也收不掉（探针实证：裸窗口能回收、带闭包环
    # 的窗口不能）。close 时先销毁 C++ 对象 → 绑定映射解除 → 闭包
    # 环变成无根环，gc 即可收走。取消/保存失败走 event.ignore()，
    # 窗口留在程序里不受影响。
    window.setAttribute(Qt.WA_DeleteOnClose)
    # 拖文件进窗口任意位置 = 加进文件列表（拖放事件冒泡到顶层窗口）
    window.drop_callback = lambda paths: add_files(window, paths)

    window._status_timer = None   # _log 里的状态栏恢复计时器（懒创建）
    window.log = lambda text: _log(window, text)
    window.add_files = lambda paths: add_files(window, paths)
    # 布局/同步旗标（各自用途见 _on_canvas_resized / _tile_panels /
    # _draw_1d / _on_limits_changed 的注释）
    window._layouting = False          # 程序自己在平铺/布局（不算用户拖动）
    window._setting_limits = False     # 程序自己在画图设范围（不算用户改动）

    # 后台任务簿：进行中的积分任务挂在这里防垃圾回收（结束回调里
    # 移除）；关窗口时逐一 discard（等待后台函数返回，防线程悬空）
    window._tasks = []
    # 每面板的最新任务：同面板连点两次时，先开的晚到会被丢弃
    window._latest_task = {}
    # 自动对比度的图像缓存（路径 → 图像数组，上限 3 张）：同一文件
    # 反复点焦点不重复解码大 tif
    window._image_cache = {}
    # 焦点面板：参数面板"编辑对象"指向的图面板（点图/计算完成时更新）
    window.focus_panel = None

    _build_center(window)
    # 点任何面板窗口内任何位置都选中该面板（应用级过滤器，原因见
    # _PanelClickTracker）。每个窗口装一个；窗口销毁时过滤器随父
    # 对象销毁，Qt 自动把它从应用事件分发里摘掉
    QApplication.instance().installEventFilter(_PanelClickTracker(window))
    window.file_dock = _build_file_dock(window)
    window.param_dock = _build_param_dock(window)
    window.log_dock = _build_log_dock(window)
    _build_status(window)
    _build_toolbar(window)

    # 点选文件 → 只登记当前文件（此时各坞已就绪，可安全接信号）
    window.file_list.currentItemChanged.connect(
        lambda cur, prev: _on_file_selected(window, cur, prev))

    # 关窗口：有未保存的图先询问（保存 / 不保存 / 取消），再等后台
    # 任务收尾——直接销毁运行中的线程 Qt 会 abort
    def on_close(event):
        unsaved = [d for d in window.plot_docks.values()
                   if getattr(_content(d), "figure", None) is not None
                   and not getattr(d, "figure_saved", False)]
        if unsaved:
            choice = _confirm_close(window, len(unsaved))
            if choice == "cancel":
                event.ignore()   # 取消 → 留在程序里
                return
            if choice == "save" and not _save_figures(window):
                event.ignore()   # 保存流程被取消 → 留在程序里
                return
        for task in window._tasks:
            task.discard()
        # 弹出的顶层窗口不随主窗口关，逐一关掉（close → 遗忘登记；
        # 遍历拷贝：close 会把面板从 plot_docks 里摘掉）
        for d in list(window.plot_docks.values()):
            if isinstance(d, _FloatedWindow):
                d.close()
        event.accept()

    window.closeEvent = on_close

    # 初始尺寸：文件列/参数列默认收到最窄（各自内容的最小宽度），
    # 需要时用户自己拖宽；日志高 140（都可拖动）
    for d in (window.file_dock, window.param_dock):
        window.resizeDocks([d], [d.minimumSizeHint().width()], Qt.Horizontal)
    window.resizeDocks([window.log_dock], [140], Qt.Vertical)

    # 启动即应用默认配置条目：参数坞初值来自注册表（如初始距离
    # 1595.80 mm），而不是写死的占位默认值
    _apply_config(window, window.config_combo.currentIndex())

    window.log("主框架已就绪")
    return window


def main() -> int:
    """程序入口：创建 QApplication → 建窗口 → 进入事件循环。

    事件循环（app.exec()）是 GUI 程序的"心跳"：程序停在这里，
    不断接收鼠标/键盘/重绘事件并分发出去，直到窗口被关闭。
    """
    app = QApplication(sys.argv)
    window = create_window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
