"""GUI 主程序：窗口组装（文件坞 / 参数坞 / 日志坞 / 工具栏 / 保存关窗流程）。

入口：python -m xrd_toolkit.gui（__main__.py 转发到 main()）。
create_window() 与 main() 分离：测试里可以只建窗口、不进事件循环。

模块图（2026-09-18 从 3622 行单文件拆分，调用方向永远从上往下，
循环导入无路可走）：
    app.py（本模块：窗口组装与保存/关窗流程）
      → plot_views.py   视图注册表 + 出图调度 + 绘图（2D/剖面/1D/
                         瀑布全接线）+ 悬停取点 + 手势（扩展点：
                         _VIEW_BUILDERS/_VIEW_RUNNERS 两张注册表）
      → file_dock.py    左侧文件坞：文件栏（树：原始数据 + 各阶段产物
                         分组）+ 导入/拖放 + 选择工具
      → sources.py      条目的"数据来源"：条目是原始文件还是哪个阶段的
                         产物、该去哪儿取数（跨组读勾选集合的地方只认它）
      → calib.py        校准工作台：参数坞第 2 页表单 + 中央校准图
                         面板 + 自动/手动校准后台任务
      → panels.py        面板容器生命周期：MDI 子窗口/弹出窗口、
                         关闭即遗忘、抓手、平铺、总缩放
      → customize.py     Customize 自绘轴属性对话框
      → panel_state.py   共享层：_log/_content/参数快照/焦点回放/
                         几何收集/自动显示区间
      → tasks.py         后台任务运行器（耗时计算挪出界面线程）

窗口上的公共接口（供面板与测试使用）：
  window.log(text)        写日志区 + 状态行
  window.add_files(paths) 把文件加进左侧文件栏（默认不勾选，见 file_dock）
  window.drop_folder(path) 扫描文件夹加入文件栏（与 [打开文件夹] 同逻辑）
  window.refresh_groups() 重建文件栏里的产物分组（批量处理/算完 1D/
                          清空缓存之后调；实现见 file_dock）
  window.mdi              QMdiArea（绘图区，所有图子窗口的父场地）
  window.plot_docks       {面板键: QMdiSubWindow 或 _FloatedWindow}，
                          键 = f"{视图}|{路径}"，重复文件改名条目再补
                          |显示名 区分；已关闭的面板不在登记表里
  window.focus_panel      参数面板编辑对象 = 面板键（点窗口任意处/
                          计算完成时设定）
  window.params           参数面板控件字典
  window.config_name      当前选中的配置条目 key（如 lmfp1_lab6）
  window.config           完整条目 dict（label / geometry / beam_center）
  各视图面板的画布/坐标轴在面板内容上：_content(dock).axes_1d
  （2D/剖面/瀑布 = axes_2d / axes_profile / axes_waterfall）

兼容再导出：拆分前全部函数都住在 app 模块里，测试等外部代码继续
经 gui_app 访问它们（下方 import 即再导出）。mock.patch 的目标请
指到实现所在的新模块（tests 里已改为 gui_views / gui_customize）。
"""
import re
import sys
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("qtagg")   # 必须在导入 FigureCanvasQTAgg 之前选定 Qt 后端
# 图标题取自文件显示名（重复文件改名可输入中文）→ 字体回退链补上
# macOS 中文字体，缺字形时逐字体回退，标题不会渲染成方框。
# 注意要设 font.family 直接给列表：实测 qtagg 后端下 font.sans-serif
# 列表不触发回退（Agg 可以），中文仍会变方框
matplotlib.rcParams["font.family"] = [
    "DejaVu Sans", "PingFang SC", "Hiragino Sans GB", "Arial Unicode MS"]
from PySide6.QtCore import QEvent, QObject, Qt, QSize
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QInputDialog, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QMdiSubWindow,  # 兼容再导出：测试 isinstance 用
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QStackedWidget, QToolBar, QVBoxLayout, QWidget,
    QDockWidget, QApplication)

from xrd_toolkit import config
from xrd_toolkit.cli import SUPPORTED_EXTS   # 文件夹导入的格式白名单（与 CLI 菜单一致）
from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG
# ── 兼容再导出（见模块 docstring）：测试继续经本模块访问 ──
from xrd_toolkit.gui.plot_export import (
    _ask_save_options, _build_export_dialog, _checked_1d_results,
    _run_export, _save_figures, _write_csv_summary, _write_export)
from xrd_toolkit.gui.file_dock import (
    FILE_FILTER, FileTree, _ask_duplicate, _ask_rename, _build_file_dock,
    _dropped_items, _on_file_selected, _refresh_file_label, _scan_folder,
    _sync_current_to_checks, _unique_display_name, add_files,
    refresh_product_groups)
# 校准相关按职责分了三块（2026-09-23 拆分）：页面与流程在 calib，
# 中央校准图面板在 calib_panel，配置条目进出在 config_ops。这里全部
# 再导出（见模块 docstring 的"兼容再导出"），外部照旧经 gui_app 取用。
from xrd_toolkit.gui.calib import (
    _build_calib_form, _enter_calib, _exit_calib, _start_auto_calib,
    _start_manual_calib)
from xrd_toolkit.gui.calib_panel import (
    _CalibSubWindow, _clear_calib_points, _close_calib_panel,
    _draw_calib_image, _on_calib_click, _open_calib_panel,
    _undo_calib_point)
from xrd_toolkit.gui.config_ops import _sync_del_config_btn
from xrd_toolkit.gui.customize import (
    _apply_customize, _build_customize_dialog, _open_customize_dialog)
from xrd_toolkit.gui.panels import (
    _apply_area_zoom, _build_center, _close_panel, _FloatedWindow,
    _PlotSubWindow, _toggle_pop_out)
from xrd_toolkit.gui.panel_state import (
    _apply_auto_contrast, _apply_auto_heatlim, _apply_auto_ylim,
    _apply_config, _bg_geom_sig, _collect_geometry, _content, _log,
    _reload_config_combo, _set_focus)
from xrd_toolkit.gui.panel_state import _proc_curve
from xrd_toolkit.gui.plot_compare import (
    _plot_compare, _plot_heatmap, _refresh_heat)
from xrd_toolkit.gui.plot_export import _ask_save_options
from xrd_toolkit.gui.plot_panels import (
    _hover_leave, _hover_motion, _magnifier_on, _open_plot_panel,
    _pan_motion, _pan_press, _pan_release, _sync_bar_active, _wheel_zoom)
from xrd_toolkit.gui.plot_views import (
    _apply_image_params, _apply_params, _proc_batch_apply, _compute_integration,
    _draw_1d, _open_source_group, _open_source_view, _plot_view, _refresh_proc,
    _spawn_task)


VIEW_NAMES = ("2D", "剖面", "1D", "瀑布")   # 四个图面板（作图按钮的顺序）

class _MainWindow(QMainWindow):
    """顶层窗口：接受拖入文件或文件夹（拖到窗口任意位置）。

    文件 → 加进文件列表；文件夹 → 扫描其中的数据文件。至少拖进
    一个支持类型的文件或一个目录才"接住"（光标变 +）；子控件默认
    不接拖放（日志区显式关掉了文本拖放），事件会冒泡到顶层窗口
    ——一个入口覆盖整个窗口面。drop_callback / drop_folder 由
    create_window 接到 add_files / _scan_folder 上。
    """

    def __init__(self):
        super().__init__()
        self.drop_callback = None
        self.drop_folder = None
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if (self.drop_callback or self.drop_folder) and _dropped_items(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        files, dirs = [], []
        for p in _dropped_items(event):
            (dirs if p.is_dir() else files).append(p)
        if not files and not dirs:
            event.ignore()
            return
        if files and self.drop_callback:
            self.drop_callback([str(p) for p in files])
        for d in dirs:
            if self.drop_folder:
                self.drop_folder(str(d))
        event.acceptProposedAction()


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


# ══ 右侧：参数面板 ═════════════════════════════════════════

def _bg_edit_dock(window: QMainWindow):
    """"背景扣除"里锚点操作作用的面板：优先当前编辑对象（若是 1D 面板），
    否则第一个开着的 1D 面板；都没有就 None。

    锚点在 1D 图上点选（那里能看到单条曲线的真实形状），但作用范围是
    **文件**——对比/热图/瀑布里同一条曲线也跟着扣。
    """
    key = window.focus_panel
    if key is not None and key.split("|", 1)[0] == "1D":
        dock = window.plot_docks.get(key)
        if dock is not None:
            return dock
    for k, d in window.plot_docks.items():
        if k.split("|", 1)[0] == "1D":
            return d
    return None


def _bg_anchor_file(window: QMainWindow):
    """锚点当前作用在哪个文件上（路径，供显示/日志）。"""
    dock = _bg_edit_dock(window)
    return getattr(dock, "panel_file", None) if dock is not None else None


def _bg_anchor_count(window: QMainWindow) -> int:
    """当前作用文件的锚点数（给参数坞那个计数标签用）。"""
    path = _bg_anchor_file(window)
    if path is None:
        return 0
    return len((getattr(window, "bg_anchors", None) or {}).get(str(path), []))


def _update_bg_count(window: QMainWindow) -> None:
    """刷新锚点计数标签。"""
    lbl = getattr(window, "bg_count_lbl", None)
    if lbl is None:
        return
    n = _bg_anchor_count(window)
    # 没有可作用的 1D 面板时留空（此时数字对用户没有意义）
    lbl.setText(f"{n} 点" if _bg_anchor_file(window) is not None else "")


def _sync_bg_rows(window: QMainWindow) -> None:
    """按背景扣除模式收起/放出专用行，并同步按钮可用状态。

    三个模式各有一行专用控件（空扫：选图+归一化；自动：窗口宽度；
    锚点：拾取+清空+计数），只放出当前模式那一行——参数坞窄，全部
    摊开既挤又让人不知道该填哪个。隐藏的行不占布局高度，也不计入
    坞的最小宽度（所以本函数只允许在宽度量完之后调用）。
    """
    rows = getattr(window, "bg_rows", None)
    if not rows:
        return
    mode = window.params["背景扣除模式"].currentData()
    for name, row in rows.items():
        row.setVisible(name == mode)
    # "显示原始曲线对比"只在真的在扣的时候才有意义
    window.params["背景显示原始"].setEnabled(mode != "off")
    window.params["负值截断为 0"].setEnabled(mode != "off")
    if hasattr(window, "bg_pick_btn"):
        window.bg_pick_btn.setEnabled(mode == "anchor")
        window.bg_clear_btn.setEnabled(mode == "anchor")
        if mode != "anchor" and window.bg_pick_btn.isChecked():
            window.bg_pick_btn.setChecked(False)   # 离开锚点模式即停止拾取
    _update_bg_count(window)


def _on_bg_mode(window: QMainWindow) -> None:
    """背景扣除模式切换：调好专用行的显隐、立刻重画、记一条日志。

    模式是显示参数（不重新积分），所以走 _refresh_proc 实时重画——
    与其余显示参数"等 [应用]"不同，理由见 _refresh_proc 的说明。
    """
    _sync_bg_rows(window)
    mode = window.params["背景扣除模式"].currentData()
    labels = {"off": "关闭", "blank": "空扫相减", "auto": "自动基线",
              "anchor": "手动锚点"}
    if mode == "blank" and getattr(window, "bg_blank", None) is None:
        _log(window, "背景扣除：空扫相减——还没选空扫图，先点 [选择空扫图]")
    elif mode == "anchor" and _bg_anchor_count(window) == 0:
        _log(window, "背景扣除：手动锚点——点 [拾取锚点] 后在 1D 图上"
                     "左键点选只有背景的位置")
    else:
        _log(window, f"背景扣除：{labels.get(mode, mode)}")
    _refresh_proc(window)


def _on_pick_anchor(window: QMainWindow, on: bool) -> None:
    """[拾取锚点] 开关：打开后在 1D 面板上点选锚点。

    点选逻辑在 plot_views 的 _anchor_press / _anchor_release（按 5 px
    位移阈值区分"点击"与"拖拽平移"）。
    """
    if on:
        dock = _bg_edit_dock(window)
        name = Path(dock.panel_file).name if dock is not None else "（无 1D 面板）"
        _log(window, f"开始拾取锚点：在 1D 图上左键点选纯背景位置"
                     f"（对象 {name}，再点已有关键点可删除）")
    else:
        _log(window, "已停止拾取锚点")


def _on_clear_anchors(window: QMainWindow) -> None:
    """[清空锚点]：清掉当前作用文件的全部锚点并立刻重画。"""
    path = _bg_anchor_file(window)
    if path is None:
        _log(window, "清空锚点：先打开一个 1D 面板")
        return
    # 不能写 `getattr(...) or {}`：空 dict 是 falsy，`or` 会换成临时新
    # 字典，pop 掉的是临时的（同 _anchor_release 的坑）
    anchors = getattr(window, "bg_anchors", None)
    if anchors is None:
        anchors = window.bg_anchors = {}
    n = len(anchors.get(str(path), []))
    if not n:
        _log(window, f"清空锚点：{Path(path).name} 本来就没有锚点")
        return
    anchors.pop(str(path), None)
    _update_bg_count(window)
    _refresh_proc(window)
    _log(window, f"已清空 {Path(path).name} 的 {n} 个锚点")


def _on_choose_blank(window: QMainWindow) -> None:
    """[选择空扫图]：挑一张空扫图 → 后台按当前几何积分 → 存进窗级缓存。

    空扫只需积分一次（三种模式共用曲线层扣除，见 services/background.py
    关于积分线性的说明），之后切文件/调参数都不再重算它。
    几何取参数坞当前选中的配置——空扫必须与样品同几何，否则"先扣图再
    积分 ≡ 先积分再扣"的线性前提不成立。
    """
    path_str, _ = QFileDialog.getOpenFileName(
        window, "选择空扫图（没有样品的空白测量）", "", FILE_FILTER)
    if not path_str:
        return
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    key = f"空扫|{path_str}"   # 独立键：不占 1D 面板缓存

    def done(window_, key_, task, result):
        tth, intensity = result
        window.bg_blank = {"path": path_str, "tth": tth,
                           "intensity": intensity,
                           "geom_sig": _bg_geom_sig(geom)}
        # 换了空扫图，两条提示重新计起
        window._bg_geom_warned = False
        window._bg_cover_warned = False
        _refresh_proc(window)
        _log(window, f"空扫积分完成：{Path(path_str).name}"
                     f"（{len(tth)} 点，2θ {tth[0]:.3f}~{tth[-1]:.3f}°）")

    def error(msg):
        _log(window, f"空扫积分失败：{Path(path_str).name} — {msg}")

    _spawn_task(window, key, _compute_integration, (path_str, geom, npt),
                done, error)
    _log(window, f"开始积分空扫图：{Path(path_str).name}")


def _build_param_dock(window: QMainWindow) -> QDockWidget:
    """参数坞：顶部两行固定件（"编辑对象"名 / "几何配置"条目）+ 五个
    入口页（校准 / 1D / 处理 / 对比 / 绘图），翻页由工具栏那五个入口
    按钮负责。

    固定件不随页面滚动：
      - 编辑对象：视图 [应用] 作用在它身上；
      - 几何配置：分析/积分/出图一律用这条条目（_collect_geometry 取
        window.config）。一行只放下拉框，像素/波长/距离的读数走悬停
        提示（_sync_geom_row 填）；校准模式下同一行还多一个
        [返回分析模式] —— 本模式的显式出口，钉在这里永远可见。
    各页自己套滚动区，页底按钮（产出按钮 / [恢复默认][应用]）固定
    不随滚动走。
    排版约定（为窄排版）：单位放输入框后缀里（标签不带括号单位）；
    成对上下限并排一行（中间 ~ 连接，转盘限宽到数值能完整显示的
    底限）；小节用全宽灰色小标题；每项悬停有人话提示。坞有尺寸下限
    （拖动边框时不会把控件裁掉）：左右 = 最宽一行完整显示的宽度与
    坞顶两行的宽度取大，上下 = 固定件不被遮没的高度。
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

    # 参数坞 = 两行固定件（编辑对象 / 几何配置）+ **五个入口页**（校准 /
    # 1D / 处理 / 对比 / 绘图）。工具栏那五个入口按钮翻页（位置 A =
    # 窗口顶部，见 _build_toolbar / _switch_entrance）——用户 2026-09-24
    # 定稿："最上方只留这四个功能，再加一个绘图；参数页选到谁就放谁的"。
    # 页 0 = 校准（校准表单，calib.py 建）；其余四页放本阶段的参数，
    # 底部各带一个"产出"按钮（1D：[出 1D 图]；处理：[重画]；对比：
    # [出对比][出热图]；绘图：[出图][只重画当前][导出图片]）。
    # 控件与键名全部沿用拆分前（window.params 白名单、快照回放、测试
    # 都按这些键找控件），变的只是"住在哪一页"。
    window.param_stack = QStackedWidget()
    window.PARAM_PAGES = {"校准": 0, "1D": 1, "处理": 2, "对比": 3,
                          "绘图": 4}

    # 编辑对象：五个入口共用的一行，固定在坞顶（不随页面滚动）。点图
    # 面板（_FocusMarker）或某视图计算完成（_on_integration_done）时
    # 更新；[应用] 作用在它身上。名字可能很长：_ElideLabel 单行缩略，
    # 中间打省略号保留首尾（重名条目的区分后缀在尾部），悬停看全名，
    # 不撑宽参数坞
    window.focus_label = _ElideLabel("编辑对象：未选中图面板",
                                     Qt.ElideMiddle)
    window.focus_label.setStyleSheet("color: gray;")
    lay.addWidget(window.focus_label)     # 固定最上方，不随页面滚动

    # 几何配置：五个入口共用的第二行，同样固定在坞顶（不随页面滚动）。
    # 分析/积分/出图一律用这条条目（_collect_geometry 取 window.config），
    # 所以它得在做分析的每一页都看得见——2026-09-26 之前它住在校准页
    # 顶部，还被挤进一个 88 px 高的内嵌滚动区（视口 88 / 内容 130），
    # 波长和距离都看不全（用户："位置看不清"）。
    # 一行三件：标签 + 下拉框（只放短 key）+ [返回分析模式（校准模式才显示）]；
    # 像素/波长/距离这些只读值改走悬停提示（文案在 _sync_geom_row 里
    # 拼），不再占三行灰色字段。提示同时挂在**整行容器**上：这样悬停
    # "几何配置"这个标签也能看到读数（下拉框自己的提示只有悬停它才出）。
    # 控件登记表：坞顶这几行（几何配置 / 数据参数）里的控件也要登记进来，
    # 所以先建表再建行（下面页面那段不再重复建）
    window.params = {}
    window.geom_row = QWidget()
    geom_lay = QHBoxLayout(window.geom_row)
    geom_lay.setContentsMargins(0, 0, 0, 0)
    geom_lay.setSpacing(4)
    geom_lay.addWidget(QLabel("几何配置"))
    # 下拉框只显示短 key（如 lmfp1_lab6），完整批次备注挂在**条目**的
    # 悬停提示上（下拉列表里逐条看）；key 藏在 itemData 里给程序用。
    # 完整条目（含 beam_center）挂在 window.config，2D/剖面视图直接取用。
    # 注意顺序：先填条目、设默认，再连接信号——建坞阶段日志区还没建好，
    # 信号此刻触发会去写一个还不存在的控件；默认值改由 create_window
    # 收尾时显式调用 _apply_config 应用。
    window.config_combo = QComboBox()
    for name, entry in CONFIGS.items():
        window.config_combo.addItem(name, name)
        window.config_combo.setItemData(
            window.config_combo.count() - 1, entry["label"], Qt.ToolTipRole)
    window.config_combo.setCurrentIndex(
        window.config_combo.findData(DEFAULT_CONFIG))
    window.config_combo.currentIndexChanged.connect(
        lambda i: (_apply_config(window, i),
                   _sync_del_config_btn(window)))
    geom_lay.addWidget(window.config_combo, 1)
    # 校准页的显式出口：固定在坞顶、永远可见。以前它在校准表单的最
    # 底部（窗口 1000 高时按钮落在内容 y=1236，要往下滚 434 px），
    # 用户 2026-09-26 报"没有退出校准的按钮了"。按下的效果与工具栏
    # [校准] 弹起同源（那条 toggled → _on_mode(False)）。
    btn_exit = QPushButton("返回分析模式")
    btn_exit.setObjectName("exit_calib_btn")
    btn_exit.setToolTip("退出校准工作台，回到分析模式"
                        "（选点与结果清零——关闭即遗忘）")
    btn_exit.clicked.connect(lambda: window.calib_btn.setChecked(False))
    btn_exit.setVisible(False)      # 只在校准模式显示（_enter/_exit_calib）
    geom_lay.addWidget(btn_exit)
    window.calib_exit_btn = btn_exit
    lay.addWidget(window.geom_row)        # 固定第二行，不随页面滚动

    # 数据参数（2θ 积分范围 + 输出点数）：**所有分析页共用**，固定在坞顶
    # 第三行。用户 2026-09-27："1d 画图时能选范围，后面处理时没法选范围，
    # 比如对比时，参数里加上"——原先这两项只在 1D 页，切到对比/处理页就
    # 改不了（它们是"参与计算的数据参数"，与几何配置同一类，所以并排住）
    data_row = QWidget()
    dlay = QHBoxLayout(data_row)
    dlay.setContentsMargins(0, 0, 0, 0)
    dlay.setSpacing(2)
    dlay.addWidget(QLabel("2θ"))
    for key, value in (("2θ 下限 (°)", 1.0), ("2θ 上限 (°)", 8.0)):
        box = QDoubleSpinBox()
        box.setRange(0.0, 90.0)
        box.setValue(value)
        box.setDecimals(1)
        box.setSuffix(" °")
        box.setMaximumWidth(84)      # 同 add_range：mac 转盘内边距很肥
        box.setToolTip("参与积分的衍射角区间（所有分析页共用；改了要重出图）")
        window.params[key] = box
    dlay.addWidget(window.params["2θ 下限 (°)"], 1)
    dlay.addWidget(QLabel("~"))
    dlay.addWidget(window.params["2θ 上限 (°)"], 1)
    npt = QSpinBox()
    npt.setRange(100, 100000)
    npt.setValue(3000)
    npt.setMaximumWidth(72)
    npt.setToolTip("2θ 区间内的采样点数（所有分析页共用）")
    window.params["输出点数"] = npt
    dlay.addWidget(QLabel("点"))
    dlay.addWidget(npt, 1)
    lay.addWidget(data_row)               # 固定第三行，不随页面滚动
    window.data_row = data_row

    lay.addWidget(window.param_stack)     # 下面才是五个入口页

    def make_page():
        """一页 = 滚动表单（装参数）+ 底部按钮行。

        返回 (页面, 表单, 底部行的布局)：参数一律加到表单上，按钮加到
        返回的布局里（固定最下方，不随滚动走）。"""
        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)   # 条目随滚动区宽度自动重排
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        fields = QWidget()
        form = QFormLayout(fields)
        form.setContentsMargins(2, 1, 2, 1)
        scroll.setWidget(fields)
        pv.addWidget(scroll, 1)
        return page, form, pv

    page_1d, form_1d, btns_1d = make_page()
    page_bg, form_bg, btns_bg = make_page()
    page_cmp, form_cmp, btns_cmp = make_page()
    page_draw, form_draw, btns_draw = make_page()

    # 校准页 = 校准表单（calib.py；见其 docstring）。原先压在这一页顶部的
    # "几何配置 + 标定几何（只读）"整块撤掉了：几何配置挪进坞顶公共行、
    # 只读值改悬停（用户 2026-09-26 定）——顺带消掉了那块被压成 88 px 的
    # 内嵌滚动区（"波长/距离看不见"的根因）
    window.param_stack.addWidget(_build_calib_form(window))   # 0 校准
    window.param_stack.addWidget(page_1d)      # 1 1D
    window.param_stack.addWidget(page_bg)      # 2 处理
    window.param_stack.addWidget(page_cmp)     # 3 对比
    window.param_stack.addWidget(page_draw)    # 4 绘图
    # 「绘图」页最上面：六个类型选择（点一个 = 选中并立即出图）
    page_draw.layout().insertWidget(0, _build_plot_type_row(window))
    window._plot_type = "1D"   # [出图] 用哪个类型（点类型按钮时更新）

    # 几何配置行跟着页走：校准页上它只是显示（置灰）——校准用的是
    # 「当前配置」那份几何，这里的条目只决定分析侧用哪条，在校准页
    # 能改会让人以为改了就换了校准的几何。悬停文案与置灰状态都由
    # _sync_geom_row 一处写（翻页、_apply_config 换条目两条路都调它）。
    window.param_stack.currentChanged.connect(
        lambda _i: (_sync_geom_row(window), _sync_data_row(window)))
    window._geom_row_sync = lambda: _sync_geom_row(window)
    _sync_geom_row(window)
    _sync_data_row(window)

    def add_caption(form, text):
        """全宽灰色小节标题（布局行横跨标签/字段两列）。"""
        cap = QLabel(text)
        cap.setStyleSheet("color: gray;")
        row = QHBoxLayout()
        row.addWidget(cap)
        form.addRow(row)
        return cap

    def add_float(form, name, lo, hi, value, decimals=2,
                  label=None, suffix="", tooltip="", readonly=False):
        box = QDoubleSpinBox()
        box.setRange(lo, hi)
        box.setValue(value)
        box.setDecimals(decimals)
        if suffix:
            box.setSuffix(suffix)   # 单位跟在数字后（标签不再带括号单位）
        if tooltip:
            box.setToolTip(tooltip)
        if readonly:
            # 只读摘要：几何值由所选配置条目决定（_apply_config 填），
            # 用户改不了——要改去校准页。setReadOnly 只挡用户输入，
            # 程序 setValue 照常工作，快照回放不受影响。
            box.setReadOnly(True)
            box.setButtonSymbols(QDoubleSpinBox.NoButtons)
            box.setStyleSheet("color: gray;")
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

    # 标定几何那三行只读字段（像素尺寸/波长/分析用距离）撤了：它们的
    # 值改在坞顶"几何配置"那一行的悬停提示里看（用户 2026-09-26 定：
    # "只留能选的那一行，下面的灰色的变成鼠标长放显示"）。控件撤掉后
    # window.params 里不再有这三个键，快照也就不会再记它们——几何随
    # 快照回放一直是走 "config" 那条（见 _snapshot_params/_restore）。

    # 积分设置（2θ 范围 + 输出点数）搬去坞顶第三行：所有分析页共用，
    # 见 _build_param_dock 里 data_row 的说明（用户 2026-09-27）

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

    btn_col = QHBoxLayout()
    btn_col.setSpacing(4)
    # 通栏宽一分为二（与用户讨论定稿）：[恢复默认] 在左、[应用] 在右
    btn_col.addWidget(btn_reset_data, 1)
    btn_col.addWidget(btn_apply, 1)
    btns_1d.addLayout(btn_col)   # 按钮固定在本页最下方（滚动区之外）
    # 本页产出：对勾选文件出 1D 图（常用循环不用切到「绘图」页）
    btn_plot_1d = QPushButton("出 1D 图")
    btn_plot_1d.setObjectName("plot_1d_btn")
    window.plot_1d_btn = btn_plot_1d   # 登记按钮（测试用）
    btn_plot_1d.clicked.connect(lambda: _plot_view(window, "1D"))
    btns_1d.addWidget(btn_plot_1d)


    # 组内分区：上面的对比度/剖面角只对二维视图有意义；下面的
    # "1D 显示" 子分组管曲线图自己的显示参数
    add_caption(form_draw, "2D/剖面视图")

    # 自动对比度（默认开）：显示区间按编辑对象（焦点图）数据的
    # 1%/99.9% 分位自定，与 view_diffraction 的默认行为一致；取消勾
    # 选后手填两个输入框（对应 --vmin/--vmax 的"指定时覆盖自动值"
    # 语义）。自动模式下输入框置灰 = 只读展示程序正在用的区间；
    # 勾回自动 = 立刻按焦点图重算并填回（恢复默认对比度）。
    auto = QCheckBox("自动对比度")
    auto.setChecked(True)
    auto.setToolTip("显示区间按图像 1%~99.9% 分位自动确定")
    window.params["自动对比度"] = auto
    form_draw.addRow(auto)

    add_range(form_draw, "对比度下限", "对比度上限", 0.0, 1e9, 1.0, 100000.0,
              label="显示范围", decimals=1,
              tooltip="取消自动对比度后手填的显示区间（下限 ~ 上限）")

    def sync_contrast(checked, silent=False):
        window.params["对比度下限"].setEnabled(not checked)
        window.params["对比度上限"].setEnabled(not checked)
        if checked:
            _apply_auto_contrast(window, silent=silent)

    auto.toggled.connect(sync_contrast)
    sync_contrast(True, silent=True)   # 初始状态：自动开 → 输入框置灰（不刷日志）

    angle = add_float(form_draw, "剖面角度 (°)", -180.0, 180.0, 0.0, decimals=1,
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
    form_draw.addRow(hint_row)   # 全宽一行（不再挤在标签列里竖排）

    # ── 1D 显示（小节）：曲线图自己的显示参数 ──
    # 不套子分组框（嵌套框自带一套标签列 + 边框，白白多占 ~30px
    # 宽）：灰色小节标题的层次感够用。对数纵轴：主峰与弱峰强度
    # 差几个数量级，对数刻度把弱峰"抬起来"（XRD 软件行规）。纵轴
    # 范围与对比度同套路：自动 = 按曲线 1%/99.9% 分位，取消勾选
    # 手填；自动模式输入框置灰 = 只读展示正在用的区间
    add_caption(form_draw, "1D 显示")

    # 视图 2θ 范围：只看图不参与计算的显示窗口。初始跟随数据组的
    # 积分 2θ 范围；在图里缩放/平移（滚轮/拖拽/Home/自定义对话框）
    # 会实时写回这里，[应用] 再用这里重画。与数据组的 2θ 范围完全
    # 分开——改这里不会影响积分的区间
    add_range(form_1d, "视图 2θ 下限 (°)", "视图 2θ 上限 (°)",
              0.0, 90.0, 1.0, 8.0,
              label="视图 2θ 范围", suffix=" °", max_width=88,
              tooltip="看图的窗口：缩放/平移实时写回；[应用] 用这里重画。"
                      "恢复默认 = 回到跟随积分范围")

    log_y = QCheckBox("对数纵轴")
    log_y.setToolTip("对数刻度：强弱峰差几个数量级时弱峰也看得清")
    window.params["对数纵轴"] = log_y
    form_draw.addRow(log_y)

    auto_y = QCheckBox("纵轴自动")
    auto_y.setChecked(True)
    auto_y.setToolTip("按曲线 1%~99.9% 分位自动确定纵轴区间")
    window.params["纵轴自动"] = auto_y
    form_draw.addRow(auto_y)

    add_range(form_draw, "纵轴下限", "纵轴上限", 0.0, 1e9, 1.0, 100000.0,
              label="纵轴范围", decimals=1,
              tooltip="取消自动后手填的纵轴区间（下限 ~ 上限）")

    # 对比归一化（下拉框三选一）——叠图时强度差很大的文件不归一会被
    # 强者压扁。三种模式（**都是全场统一的比例**）：
    #   global 全图最强峰：所有曲线除以全部曲线里最高的峰
    #   file   指定数据：所有曲线除以旁边下拉框选的文件的最强峰
    #   off    不归一化（默认：原样画原始强度）
    # "各自最强峰"（每条除以自己的峰）已删：它把每条曲线都缩到同一
    # 高度，样品之间的强弱差就看不出来了（用户 2026-09-26 定的规矩——
    # "不要按照各自的最高峰归一化，所有的图"）。
    # 归一化只动显示层，原始结果原样保留在 compare_data。
    cmp_norm = QComboBox()
    for text, data in (("全图最强峰", "global"),
                       ("指定数据…", "file"), ("不归一化", "off")):
        cmp_norm.addItem(text, data)
    cmp_norm.setCurrentIndex(cmp_norm.findData("off"))   # 默认 = 不归一化
    cmp_norm.setToolTip("叠图归一化（都用全场统一的比例）："
                        "全图最强峰 / 指定数据的最强峰 / 不归一化")
    window.params["对比归一化"] = cmp_norm
    norm_target = QComboBox()
    norm_target.setToolTip("以哪个文件的最强峰归一化（列表 = 对比面板的文件）")
    window.params["归一化目标"] = norm_target
    norm_row = QWidget()
    norm_lay = QHBoxLayout(norm_row)
    norm_lay.setContentsMargins(0, 0, 0, 0)
    norm_lay.setSpacing(2)
    norm_lay.addWidget(cmp_norm, 1)     # 同一行：模式在左、目标文件在右
    norm_lay.addWidget(norm_target, 1)
    form_cmp.addRow(norm_row)

    def sync_norm_target(*_):
        norm_target.setEnabled(cmp_norm.currentData() == "file")

    cmp_norm.currentIndexChanged.connect(sync_norm_target)
    sync_norm_target()   # 初始 = 不归一化 → 目标下拉框置灰

    # 曲线配色（显示参数）：多曲线的分类色。"高对比" = 固定顺序
    # 8 槽色盲友好（颜色跟着文件走，第一个文件永远是蓝）；
    # "默认" = matplotlib 自带循环。逐条自定义色走 Customize。
    curve_palette = QComboBox()
    for text, data in (("高对比（推荐）", "高对比"),
                       ("默认（matplotlib）", "默认")):
        curve_palette.addItem(text, data)
    curve_palette.setToolTip("多曲线配色：高对比 = 色盲友好固定色序"
                             "（颜色跟着文件走）/ matplotlib 默认循环")
    window.params["曲线配色"] = curve_palette
    form_cmp.addRow(curve_palette)

    cmp_stack = QCheckBox("堆叠显示")
    cmp_stack.setToolTip("瀑布式错开叠放：每条曲线按自身峰高抬到自己的"
                         "行上，y 刻度 = 样品名（堆叠下纵轴范围/对数不适用）")
    window.params["对比堆叠"] = cmp_stack
    form_cmp.addRow(cmp_stack)

    # ── 背景扣除（小节）：1D/对比/瀑布/热图四条曲线路径共用 ──
    # 三种模式 = 对"背景长什么样"的三个不同假设（物理依据见
    # services/background.py 的模块头注释）：
    #   空扫相减 = 实测：把"没有样品的那个世界"拍一遍逐点减掉
    #   自动基线 = 算法猜：假设背景比峰宽且平滑（滑动窗估计）
    #   手动锚点 = 人判断：用户指出"这几处是纯背景"
    # 锚点列表与空扫曲线不是快照参数（快照只认 QCheckBox/QComboBox/
    # spinbox 三种控件），放窗级属性 window.bg_anchors / window.bg_blank。
    add_caption(form_bg, "背景扣除")

    bg_mode = QComboBox()
    for text, data in (("关闭", "off"),
                       ("空扫相减", "blank"),
                       ("自动基线（推荐）", "auto"),
                       ("手动锚点", "anchor")):
        bg_mode.addItem(text, data)
    bg_mode.setToolTip(
        "背景 = 不含样品结构信息的加性信号（空气散射、非晶漫散射、"
        "荧光、暗电流、直射束光晕）。\n"
        "空扫相减 = 实测：先拍一张没有样品的图，从样品图里逐点减掉"
        "（最干净，但必须真有空扫、曝光/几何一致）。\n"
        "自动基线 = 算法猜：假设背景比峰宽且平滑，按窗口宽度估计"
        "（一键，无需额外数据）。\n"
        "手动锚点 = 人判断：在图上点几个只有背景的位置连成底线"
        "（最可控，适合宽鼓包样品）。")
    window.params["背景扣除模式"] = bg_mode
    form_bg.addRow(bg_mode)

    def bg_group(rows):
        """把若干控件行打包成一个可整体显隐的竖直容器。

        三个模式各有一个容器（空扫/自动/锚点），按模式只放出一个——
        参数坞窄，全部摊开既挤又让人不知道该填哪个。
        """
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        for row in rows:
            lay.addWidget(row)
        return box

    def bg_row(*widgets):
        """一行横排（窄排版：内边距 0、间距 2）。"""
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        for w, stretch in widgets:
            lay.addWidget(w, stretch)
        return row

    # 空扫模式：选图 + 归一化系数
    bg_blank_btn = QPushButton("选择空扫图")
    bg_blank_btn.setObjectName("bg_blank_btn")
    bg_blank_btn.setToolTip("挑一张没有样品的空扫/空白图（同一几何、同一曝光）")
    bg_blank_btn.setStyleSheet("padding: 2px 5px;")
    bg_scale_box = QDoubleSpinBox()
    bg_scale_box.setRange(0.01, 100.0)
    bg_scale_box.setDecimals(2)
    bg_scale_box.setSingleStep(0.1)
    bg_scale_box.setValue(1.0)
    bg_scale_box.setMaximumWidth(84)
    bg_scale_box.setToolTip("空扫归一化系数：样品与空扫的曝光时间/束流不一致"
                            "时填比值（样品÷空扫），一致就保持 1.0")
    window.params["空扫归一化"] = bg_scale_box
    blank_row = bg_group([bg_row((bg_blank_btn, 1), (bg_scale_box, 1))])
    form_bg.addRow(blank_row)

    # 自动模式：窗口宽度（唯一的旋钮）
    bg_window_box = QDoubleSpinBox()
    bg_window_box.setRange(0.1, 10.0)
    bg_window_box.setDecimals(2)
    bg_window_box.setSingleStep(0.1)
    bg_window_box.setValue(1.0)
    bg_window_box.setMaximumWidth(84)
    bg_window_box.setToolTip("窗口宽度：多宽的一段算\"背景\"而不是\"峰\"。"
                             "取最宽峰宽的 3~10 倍（本数据峰宽约 0.1~0.3°，"
                             "默认 1.0°）；取小了峰会被当背景扣掉，取大了"
                             "跟不上背景自身的起伏")
    window.params["背景窗口 (°)"] = bg_window_box
    auto_row = bg_group([bg_row((bg_window_box, 1), (QWidget(), 1))])
    form_bg.addRow(auto_row)

    # 锚点模式：拾取开关 + 清空 + 计数；下一行是拟合方式
    bg_pick_btn = QPushButton("拾取锚点")
    bg_pick_btn.setObjectName("bg_pick_btn")
    bg_pick_btn.setCheckable(True)
    bg_pick_btn.setToolTip("打开后在 1D 图上左键点选\"只有背景\"的位置；"
                           "再点已有关键点即可删除。锚点按文件各记各的")
    bg_clear_btn = QPushButton("清空锚点")
    bg_clear_btn.setObjectName("bg_clear_btn")
    bg_clear_btn.setToolTip("清空当前 1D 面板所对应文件的全部锚点")
    for _b in (bg_pick_btn, bg_clear_btn):
        _b.setStyleSheet("padding: 2px 5px;")
    bg_count_lbl = QLabel("")
    bg_count_lbl.setStyleSheet("color: gray;")
    bg_fit_combo = QComboBox()
    # 默认 = 保单调平滑：三种都严格过锚点，差别在锚点之间——弯背景上
    # 折线偏高（实测中段 +29）、自然样条会过冲（扣过头），pchip 既平滑
    # 又不过冲，合成真值上平均绝对偏差 6.1（折线 28.8、样条 16.4）
    for text, data in (("保单调平滑（默认）", "pchip"),
                       ("折线（直线连锚点）", "linear"),
                       ("样条（可能过冲）", "spline")):
        bg_fit_combo.addItem(text, data)
    bg_fit_combo.setToolTip("三种都严格过锚点，差别在锚点之间："
                            "保单调平滑 = 光滑但不过冲（推荐）；"
                            "折线 = 相邻锚点直线相连（实验室惯例、最透明，"
                            "弯背景上会扣不干净）；样条 = 自然三次样条"
                            "（更平滑，但可能在锚点之间冲到真值以下 = 扣过头）。"
                            "后两种至少 3 个锚点，不足时自动退回折线")
    window.params["锚点拟合方式"] = bg_fit_combo
    anchor_row = bg_group([
        bg_row((bg_pick_btn, 1), (bg_clear_btn, 1), (bg_count_lbl, 0)),
        bg_row((bg_fit_combo, 1)),
    ])
    form_bg.addRow(anchor_row)

    bg_show_raw = QCheckBox("显示原始曲线对比")
    bg_show_raw.setToolTip("实时预览：把未扣背景的原始曲线（虚线）与基线"
                           "（点线）一起画出来，看清扣掉了什么。\n"
                           "只作用于 1D 单曲线面板——对比/瀑布/热图里多条"
                           "曲线叠在一起，再叠一层原始线会看不清")
    window.params["背景显示原始"] = bg_show_raw
    form_bg.addRow(bg_show_raw)

    bg_clip = QCheckBox("负值截断为 0")
    bg_clip.setToolTip("默认不截断：背景是从两侧对称估的，扣完噪声摆到 0 "
                       "以下是正常的（噪声地板露出），强行截断会把噪声平均"
                       "抬高约 1σ。只在出图需要非负值时打开")
    window.params["负值截断为 0"] = bg_clip
    form_bg.addRow(bg_clip)

    # ── 平滑（小节）：先只给最朴素的一种（滑动平均） ──
    # 为什么不给 Savitzky–Golay：先把"平滑了多宽、削掉多少峰高"这件事做
    # 对；方法以后要加就是一个下拉框的事（唯一入口在 services/process）。
    add_caption(form_bg, "平滑")

    smooth_chk = QCheckBox("平滑曲线")
    smooth_chk.setToolTip("滑动平均：窗口内取平均。\n"
                          "窗口按 **2θ** 给（不是点数），所以换输出点数重算"
                          "之后「平滑了多宽」仍然一样。\n"
                          "代价：峰会变矮变宽——窗口要远小于峰宽，"
                          "旁边那个灰度提示会告诉你它折成几个点")
    window.params["平滑曲线"] = smooth_chk
    form_bg.addRow(smooth_chk)

    smooth_box = QDoubleSpinBox()
    smooth_box.setRange(0.0, 2.0)
    smooth_box.setDecimals(2)
    smooth_box.setSingleStep(0.05)
    smooth_box.setValue(0.10)
    smooth_box.setMaximumWidth(84)
    smooth_box.setToolTip("窗口宽度（度）：参与平均的 2θ 跨度。\n"
                          "典型峰宽 0.1~0.3°，窗口取到峰宽量级就会明显削峰；"
                          "先取 0.05~0.15° 试，看削掉多少再定")
    window.params["平滑窗口 (°)"] = smooth_box
    smooth_pts_lbl = QLabel("")           # 折成几个点（刷新时按当前曲线填）
    smooth_pts_lbl.setStyleSheet("color: gray;")
    window.smooth_points_lbl = smooth_pts_lbl
    form_bg.addRow(bg_row((smooth_box, 1), (smooth_pts_lbl, 1)))

    smooth_method = QComboBox()
    for text, data in (("滑动平均", "boxcar"),
                       ("Savitzky–Golay", "savgol")):
        smooth_method.addItem(text, data)
    smooth_method.setToolTip(
        "滑动平均 = 窗口内取平均（最简单，削峰明显）。\n"
        "Savitzky–Golay = 窗口内拟合多项式再取中心值：**同样的窗口宽度削峰"
        "少得多**、峰形保得更住，代价是接触陡边（低角鼓包）时可能压出轻微"
        "负值下冲。窄峰、要做峰形分析时用它。")
    window.params["平滑方法"] = smooth_method

    smooth_order = QSpinBox()
    smooth_order.setRange(2, 6)
    smooth_order.setValue(3)
    smooth_order.setMaximumWidth(60)
    smooth_order.setSuffix(" 阶")
    smooth_order.setToolTip("Savitzky–Golay 的多项式阶数（2~3 常用：阶数越高"
                            "越贴合峰形，但也越容易跟着噪声抖）")
    window.params["平滑阶数"] = smooth_order
    form_bg.addRow(bg_row((smooth_method, 2), (smooth_order, 1)))

    smooth_chk.toggled.connect(lambda _=False: _sync_smooth_rows(window))
    smooth_method.currentIndexChanged.connect(
        lambda _=0: _sync_smooth_rows(window))
    window._sync_smooth_rows = lambda: _sync_smooth_rows(window)

    # ── 裁剪区间（小节）：把一段 2θ 从曲线里挖掉，其余看得清 ──
    add_caption(form_bg, "裁剪区间")

    cut_chk = QCheckBox("裁剪区间")
    cut_chk.setToolTip("把指定 2θ 区间从曲线里挖掉：图上那一段空着，"
                       "纵轴自动范围也跟着跳过它。\n"
                       "典型用途：某个巨峰把其余部分压扁了，挖掉它让其余"
                       "看得清。\n"
                       "**影响的是数据**：处理产物与导出文件里这段同样是空的"
                       "（导出文件头会写明删了哪一段）")
    window.params["裁剪区间"] = cut_chk
    form_bg.addRow(cut_chk)

    cut_lo = QDoubleSpinBox()
    cut_hi = QDoubleSpinBox()
    for box, val in ((cut_lo, 2.0), (cut_hi, 3.0)):
        box.setRange(0.0, 180.0)
        box.setDecimals(3)
        box.setSingleStep(0.1)
        box.setValue(val)
        box.setMaximumWidth(84)
        box.setSuffix(" °")
        box.setToolTip("裁剪区间的起止 2θ（含两端）。起点 ≥ 终点时视为"
                       "不裁剪（不会出错）")
    window.params["裁剪起点 (°)"] = cut_lo
    window.params["裁剪终点 (°)"] = cut_hi
    cut_add_btn = QPushButton("添加")
    cut_add_btn.setObjectName("cut_add_btn")
    cut_add_btn.setStyleSheet("padding: 2px 5px;")
    cut_add_btn.setToolTip("把这一段的起止加进下面的清单——可以删好几段"
                           "（例如同时删 2–3° 和 7–8°）")
    form_bg.addRow(bg_row((cut_lo, 1), (QLabel("~"), 0), (cut_hi, 1),
                          (cut_add_btn, 0)))
    cut_list_lbl = QLabel("清单：空")
    cut_list_lbl.setStyleSheet("color: gray;")
    cut_list_lbl.setToolTip("当前要挖掉的全部区间（屏幕、产物与导出文件"
                            "同一个口径）")
    window.cut_list_lbl = cut_list_lbl
    cut_clear_btn = QPushButton("清空")
    cut_clear_btn.setObjectName("cut_clear_btn")
    cut_clear_btn.setStyleSheet("padding: 2px 5px;")
    cut_clear_btn.setToolTip("清空清单（= 不裁剪）")
    form_bg.addRow(bg_row((cut_list_lbl, 2), (cut_clear_btn, 0)))

    # 面板绑定这组控件（_sync_bg_rows 在小节外也要用）
    window.bg_rows = {"blank": blank_row, "auto": auto_row,
                      "anchor": anchor_row}
    window.bg_blank_btn = bg_blank_btn
    window.bg_pick_btn = bg_pick_btn
    window.bg_clear_btn = bg_clear_btn
    window.bg_count_lbl = bg_count_lbl
    bg_mode.currentIndexChanged.connect(lambda _=0: _on_bg_mode(window))
    bg_window_box.valueChanged.connect(lambda _=0.0: _refresh_proc(window))
    window.params["空扫归一化"].valueChanged.connect(
        lambda _=0.0: _refresh_proc(window))
    bg_show_raw.toggled.connect(lambda _=False: _refresh_proc(window))
    bg_clip.toggled.connect(lambda _=False: _refresh_proc(window))
    bg_fit_combo.currentIndexChanged.connect(lambda _=0: _refresh_proc(window))
    # 处理页的另两项（平滑 / 裁剪）：改参数即重画，与背景扣除同一条实时通路
    smooth_chk.toggled.connect(lambda _=False: _refresh_proc(window))
    smooth_box.valueChanged.connect(lambda _=0.0: _refresh_proc(window))
    cut_chk.toggled.connect(lambda on: _on_cut_toggled(window, on))
    cut_add_btn.clicked.connect(lambda: _on_cut_add(window))
    cut_clear_btn.clicked.connect(lambda: _on_cut_clear(window))
    smooth_method.currentIndexChanged.connect(
        lambda _=0: _refresh_proc(window))
    smooth_order.valueChanged.connect(lambda _=0: _refresh_proc(window))
    bg_blank_btn.clicked.connect(lambda: _on_choose_blank(window))
    bg_pick_btn.toggled.connect(lambda on: _on_pick_anchor(window, on))
    bg_clear_btn.clicked.connect(lambda: _on_clear_anchors(window))
    # 注意：_sync_bg_rows（按模式收起无用行）**不在这里调**——坞的最小
    # 宽度在本函数末尾按 minimumSizeHint 算一次，隐藏的行不计入尺寸；
    # 先收起再量会把宽度量小、切模式时被裁。所以放到末尾、量完之后。

    # 处理页产出：[重画] = 按各面板快照重画全部曲线面板（改锚点/窗口
    # 后手动触发；平时改控件是实时预览）
    btn_bg_redraw = QPushButton("重画")
    btn_bg_redraw.setObjectName("bg_redraw_btn")
    window.bg_redraw_btn = btn_bg_redraw
    btn_bg_redraw.clicked.connect(lambda: _refresh_proc(window))
    btns_bg.addWidget(btn_bg_redraw)
    # [批量处理]：把编辑对象的锚点（只传 2θ 位置）用到所有勾选文件，
    # 各扣各的并存成产物——对比/热图下次直接读它（跨会话秒开）。
    # 绝对强度不能跨文件套，见 plot_views._proc_batch_apply 的说明
    btn_bg_batch = QPushButton("批量处理（勾选文件）")
    btn_bg_batch.setObjectName("proc_batch_btn")
    btn_bg_batch.setToolTip("把当前图上的锚点用到所有勾选文件："
                            "锚点只传 2θ 位置，强度到每张图自己的曲线上"
                            "重新取；扣完存成产物，对比 / 热图直接复用")
    btn_bg_batch.clicked.connect(lambda: _proc_batch_apply(window))
    window.proc_batch_btn = btn_bg_batch
    btns_bg.addWidget(btn_bg_batch)

    # ── 热图显示（小节）：批量热图的显示参数 ──
    # 颜色映射 / 强度归一化 / 对数强度 / 强度范围。归一化与对比
    # 同款语义（热图没有"指定文件"模式）；对数强度 = 弱峰抬起来
    # （XRD 行规）；强度范围与对比度同套路：自动 = 按显示矩阵
    # 1%/99.9% 分位（含归一化后的口径，见 _heat_shown）
    add_caption(form_cmp, "热图显示")

    heat_cmap = QComboBox()
    for text, data in (("magma", "magma"), ("viridis", "viridis"),
                       ("plasma", "plasma"), ("inferno", "inferno"),
                       ("gray", "gray")):
        heat_cmap.addItem(text, data)
    heat_cmap.setToolTip("热图颜色映射（颜色 = 强度）；改完立刻重画")
    window.params["热图色图"] = heat_cmap
    heat_cmap.currentIndexChanged.connect(lambda _i: _refresh_heat(window))
    form_cmp.addRow(heat_cmap)

    heat_norm = QComboBox()
    # "每行各自最强峰"已删（同对比面板：用户 2026-09-26 的规矩）
    for text, data in (("全图最强峰", "global"), ("不归一化", "off")):
        heat_norm.addItem(text, data)
    heat_norm.setCurrentIndex(heat_norm.findData("off"))
    heat_norm.setToolTip("热图归一化：全图最强峰（整批同一个比例）"
                         "/ 不归一化（原样画原始强度）")
    window.params["热图归一化"] = heat_norm
    heat_norm.currentIndexChanged.connect(lambda _i: _refresh_heat(window))
    form_cmp.addRow(heat_norm)

    heat_log = QCheckBox("对数强度")
    heat_log.setToolTip("颜色按对数强度：弱峰抬起来（XRD 行规）")
    window.params["热图对数"] = heat_log
    heat_log.toggled.connect(lambda _on: _refresh_heat(window))
    form_cmp.addRow(heat_log)

    auto_heat = QCheckBox("热图自动范围")
    auto_heat.setChecked(True)
    auto_heat.setToolTip("按显示矩阵 1%~99.9% 分位自动确定强度范围")
    window.params["热图自动范围"] = auto_heat
    form_cmp.addRow(auto_heat)

    add_range(form_cmp, "热图下限", "热图上限", 0.0, 1e9, 1.0, 100000.0,
              label="热图范围", decimals=1,
              tooltip="取消自动后手填的强度范围（下限 ~ 上限）")

    def sync_heatlim(checked):
        window.params["热图下限"].setEnabled(not checked)
        window.params["热图上限"].setEnabled(not checked)
        if checked:
            _apply_auto_heatlim(window)   # 勾回自动：立刻按焦点热图算并填回

    auto_heat.toggled.connect(sync_heatlim)
    sync_heatlim(True)   # 初始状态：自动开 → 输入框置灰

    # 对比页产出：多文件才成立的两种图（对比叠图 / 批量热图）
    cmp_row = QHBoxLayout()
    cmp_row.setSpacing(4)
    btn_plot_cmp = QPushButton("出对比")
    btn_plot_cmp.setObjectName("plot_cmp_btn")
    window.plot_cmp_btn = btn_plot_cmp
    btn_plot_cmp.clicked.connect(lambda: _plot_compare(window))
    btn_plot_heat = QPushButton("出热图")
    btn_plot_heat.setObjectName("plot_heat_btn")
    window.plot_heat_btn = btn_plot_heat
    btn_plot_heat.clicked.connect(lambda: _plot_heatmap(window))
    cmp_row.addWidget(btn_plot_cmp, 1)
    cmp_row.addWidget(btn_plot_heat, 1)
    btns_cmp.addLayout(cmp_row)

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
        "对比归一化": "off",
        "归一化目标": "",
        "曲线配色": "高对比",
        "对比堆叠": False,
        "热图色图": "magma",
        "热图归一化": "off",
        "热图对数": False,
        "热图自动范围": True,
        "热图下限": 1.0,
        "热图上限": 100000.0,
        "背景扣除模式": "off",
        "背景窗口 (°)": 0.5,
        "空扫归一化": 1.0,
        "锚点拟合方式": "pchip",
        "背景显示原始": True,
        "负值截断为 0": False,
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
        window.params["对比归一化"].setCurrentIndex(
            window.params["对比归一化"].findData(img_defaults["对比归一化"]))
        window.params["归一化目标"].setCurrentIndex(0)
        window.params["曲线配色"].setCurrentIndex(
            window.params["曲线配色"].findData(img_defaults["曲线配色"]))
        window.params["对比堆叠"].setChecked(img_defaults["对比堆叠"])
        if window.params["纵轴自动"].isChecked():
            _apply_auto_ylim(window)   # 已勾着 toggled 不响，手动重算填回
        window.params["热图色图"].setCurrentIndex(
            window.params["热图色图"].findData(img_defaults["热图色图"]))
        window.params["热图归一化"].setCurrentIndex(
            window.params["热图归一化"].findData(img_defaults["热图归一化"]))
        window.params["热图对数"].setChecked(img_defaults["热图对数"])
        window.params["热图自动范围"].setChecked(img_defaults["热图自动范围"])
        if window.params["热图自动范围"].isChecked():
            _apply_auto_heatlim(window)   # 已勾着 toggled 不响，手动重算填回
        # 背景扣除：模式回"关闭"即回到不扣（锚点与空扫图属于"用户挑的
        # 数据"不是参数，留着手动清理——[清空锚点] / 重选空扫图）
        window.params["背景扣除模式"].setCurrentIndex(
            window.params["背景扣除模式"].findData(
                img_defaults["背景扣除模式"]))
        window.params["背景窗口 (°)"].setValue(img_defaults["背景窗口 (°)"])
        window.params["空扫归一化"].setValue(img_defaults["空扫归一化"])
        window.params["锚点拟合方式"].setCurrentIndex(
            window.params["锚点拟合方式"].findData(
                img_defaults["锚点拟合方式"]))
        window.params["背景显示原始"].setChecked(img_defaults["背景显示原始"])
        window.params["负值截断为 0"].setChecked(img_defaults["负值截断为 0"])
        _sync_bg_rows(window)
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

    # [恢复默认] 左 [应用] 右并排：与数据参数组同款布局。[应用]
    # 把当前图像参数应用到编辑对象（见 _apply_image_params）
    btn_apply_img = QPushButton("应用")
    btn_apply_img.setObjectName("apply_image_btn")
    btn_apply_img.clicked.connect(lambda: _apply_image_params(window))

    btn_col2 = QHBoxLayout()
    btn_col2.setSpacing(4)
    # 同数据参数组：通栏宽一分为二，[恢复默认] 在左、[应用] 在右
    btn_col2.addWidget(btn_reset_img, 1)
    btn_col2.addWidget(btn_apply_img, 1)
    # 本页三个产出按钮：[出图] 按当前类型对勾选文件出图；[只重画当前]
    # 用已有数据重画编辑对象（不重算）；[导出图片] 走批量存图流程
    btn_plot_now = QPushButton("出图（勾选文件）")
    btn_plot_now.setObjectName("plot_now_btn")
    window.plot_now_btn = btn_plot_now
    btn_plot_now.clicked.connect(lambda: _plot_selected_type(window))
    btn_redraw_now = QPushButton("只重画当前")
    btn_redraw_now.setObjectName("redraw_now_btn")
    window.redraw_now_btn = btn_redraw_now
    btn_redraw_now.clicked.connect(lambda: _redraw_focus(window))
    btn_export_img = QPushButton("导出图片…")
    btn_export_img.setObjectName("export_img_btn")
    window.export_img_btn = btn_export_img
    btn_export_img.clicked.connect(lambda: _save_figures(window))
    plot_row = QHBoxLayout()
    plot_row.setSpacing(4)
    plot_row.addWidget(btn_plot_now, 1)
    plot_row.addWidget(btn_redraw_now, 1)
    btn_clear_cache = QPushButton("清空缓存")
    btn_clear_cache.setObjectName("clear_cache_btn")
    btn_clear_cache.setToolTip("删掉分阶段产物缓存（outputs/_stage）"
                               "——下次出图会重新积分")
    btn_clear_cache.clicked.connect(lambda: _clear_stage_cache(window))
    window.clear_cache_btn = btn_clear_cache
    plot_row.addWidget(btn_export_img, 1)
    plot_row.addWidget(btn_clear_cache, 1)
    btns_draw.addLayout(plot_row)
    btns_draw.addLayout(btn_col2)   # [恢复默认][应用]（显示参数）

    # 拖动坞边框的尺寸下限（上下左右都设）：左右 = 最宽一张表单的
    # 最小宽度 + "壳"（滚动条 + 分组/表单边距，另加少量余量），拖
    # 再窄也到这就停，不会裁掉控件；上下 = 固定件（编辑对象名 +
    # 两组标题/按钮行 + 各几行内容）不被遮没的高度，再矮由各半
    # 自己的滚动条接管。用表单 minimumSizeHint 而不是 content 的
    # sizeHint：转盘限宽后 sizeHint 低估了成对行的最小宽度（实测
    # 会横向裁掉近 20px）
    forms = (form_1d, form_bg, form_cmp, form_draw)
    form_min = max(f.parentWidget().minimumSizeHint().width()
                   for f in forms)   # 量装表单的部件，不是布局
    # 坞顶那两行固定件也要放得下（编辑对象名 / 几何配置行）。几何行里
    # 的 [返回分析模式] 平时是隐藏的，而隐藏件不计入 minimumSize ——
    # 量出来会偏小，一到校准模式按钮现身就被裁，所以显式补上它的宽
    head_min = (max(window.focus_label.minimumSizeHint().width(),
                    window.geom_row.minimumSize().width()
                    + window.calib_exit_btn.sizeHint().width() + 8,
                    window.data_row.minimumSize().width() + 8))
    form_min = max(form_min, head_min)
    # 壳：一页的滚动条宽度 + 那一页表单的左右边距（各页相同）
    # 壳 = 滚动条宽 + 表单左右边距 + 8（原来 QGroupBox 那圈 4 px 内边距，
    # 页面化以后由页面自己的布局接管——漏算它会让校准页最宽的那行
    # 差 5 px 越界，实测抓到）
    chrome = (page_1d.findChild(QScrollArea).verticalScrollBar().sizeHint()
              .width()
              + form_1d.contentsMargins().left()
              + form_1d.contentsMargins().right() + 8)
    dock.setMinimumWidth(form_min + chrome + 2)
    content.setMinimumWidth(form_min + chrome + 2)   # 双保险：坞本身也算上
    # 坞的最小高度：编辑对象名 + 几何配置行 + 一页的固定件（滚动区最低
    # 110 = 露出标题 + 几个控件；按钮行 ~40）。翻页栈的最小尺寸只报当前
    # 页，且嵌套后布局 minimumSize 不再含子件 minimumSizeHint（探针
    # 验证），靠布局算会把下限塌成一行标签的高度
    dock.setMinimumHeight(
        window.focus_label.minimumSizeHint().height()
        + window.geom_row.minimumSize().height()
        + window.data_row.minimumSize().height() + 12 + 110 + 40)

    _sync_cut_label(window)      # 裁剪清单标签：空
    _sync_smooth_rows(window)    # 平滑阶数只在 SG 下可编辑
    # 宽度量完再按当前模式收起"背景扣除"的无用行（隐藏的行不计入
    # minimumSizeHint，先收再量会把坞宽量小、切模式时被裁）
    _sync_bg_rows(window)

    dock.setWidget(content)
    window.addDockWidget(Qt.RightDockWidgetArea, dock)
    return dock

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
    # 批量进度条（[1D] 等一次勾多张时出现）：开面板、后台积分、只算不画
    # 都走它（见 plot_views._progress_show/_batch_step）。平时藏起来，
    # 不占状态栏的地方；宽度固定，出现时右侧那几项不会左右跳。
    window.batch_progress = QProgressBar()
    window.batch_progress.setObjectName("batch_progress")
    window.batch_progress.setFixedWidth(150)
    window.batch_progress.setMaximumHeight(14)
    window.batch_progress.setTextVisible(True)
    window.batch_progress.setFormat("%v/%m")
    window.batch_progress.setVisible(False)
    window.statusBar().addPermanentWidget(window.batch_progress)
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    window.statusBar().addPermanentWidget(sep)
    window.file_label = QLabel("未打开文件")
    window.statusBar().addPermanentWidget(window.file_label)

# ══ 顶部：工具栏 ═══════════════════════════════════════════
def _build_toolbar(window: QMainWindow) -> None:
    """工具栏 = 五个入口 + 面板开关（文件/参数/日志）。

    入口（位置 A = 窗口顶部，用户 2026-09-24 定稿）：
    `[校准] [1D] [处理] [对比] │ [绘图]`——点一个 = 参数坞翻到那一页
    （见 _switch_entrance）；出图动作由各页底部的产出按钮负责
    （[出 1D 图] / [重画] / [出对比][出热图] / [出图]）。

    六个作图类型按钮（2D/剖面/1D/瀑布/对比/热图）都搬进了「绘图」页
    （_build_plot_type_row）：工具栏因此从 7 项收到 5 项，也消掉了
    "1D" 一词两义（入口 vs 视图）。按钮属性名不变（window.view_buttons
    / compare_btn / heat_btn），只是住的地方换了。
    """
    tb = QToolBar("主工具栏", window)
    tb.setMovable(False)
    window.addToolBar(tb)

    # 五个入口：可勾选（当前页高亮）。[校准] 另有一层含义——进出校准
    # 工作台（开/关校准面板），那条走 toggled → _on_mode（calib.py 的
    # [返回分析模式] 也走同一条路：setChecked(False)）
    window.entrance_buttons = {}
    # 退出校准翻回哪一页：None = 还没选过任何一个入口（开局就是这样，
    # 退出校准就回到"什么都没选"，见 _clear_entrance / _on_mode）
    window._last_entrance = None
    for name in ("校准", "1D", "处理", "对比"):
        btn = QPushButton(name)
        btn.setCheckable(True)
        tb.addWidget(btn)
        window.entrance_buttons[name] = btn
        btn.clicked.connect(lambda checked=False, n=name:
                            _switch_entrance(window, n))
    tb.addSeparator()
    btn_plot = QPushButton("绘图")
    btn_plot.setCheckable(True)
    tb.addWidget(btn_plot)
    window.entrance_buttons["绘图"] = btn_plot
    btn_plot.clicked.connect(lambda: _switch_entrance(window, "绘图"))

    # 兼容：旧名字 [校准] 开关（测试与 calib.py 都按 window.calib_btn 找）
    window.calib_btn = window.entrance_buttons["校准"]
    window.calib_btn.setToolTip("进入校准工作台：标样数据定几何"
                               "（束心/距离/倾斜角）；再点别的入口即退出")
    window.calib_btn.toggled.connect(lambda on: _on_mode(window, on))

    tb.addSeparator()

    # 面板开关：[文件][参数][日志] 三个勾选按钮，收起/展开对应坞。
    # 全收起来 = 中央只剩绘图区，看图视野最大。双向同步：按钮点
    # 击 → 坞显隐；坞被标题栏 × 关掉 → 按钮自动弹起（visibilityChanged
    # 信号），下次点按钮还能再展开
    window.panel_toggles = {}   # 登记按钮（测试用）
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

    # 开局谁都不选（参数坞建好才动得了，所以放在这里）：不点亮任何入口
    # + 收起参数坞。用户 2026-09-25 定：开界面时上面什么都不选、右边
    # 参数栏是隐藏的——"选到谁才放谁的参数"。
    _clear_entrance(window)


def _clear_entrance(window: QMainWindow) -> None:
    """"什么都没选"状态：入口全不点亮 + 收起参数坞。

    两个调用点：开局（_build_toolbar 收尾）与"没选过入口就退出校准"
    （_on_mode(False)）。参数坞的显隐与 [参数] 开关双向同步
    （见 _build_toolbar），所以收坞 = 按钮自动弹起，不用额外接线。
    """
    window._last_entrance = None
    _highlight_entrance(window, None)
    # 收起时停在分析侧的默认页：用户手动点 [参数] 展开时看到的是分析页，
    # 而不是上一次停在的校准页（页号唯一来源就在入口切换这一处）
    window.param_stack.setCurrentIndex(window.PARAM_PAGES["1D"])
    window.param_dock.setVisible(False)


def _sync_geom_row(window: QMainWindow) -> None:
    """坞顶"几何配置"那一行的悬停读数（换条目 / 翻页时刷新）。

    一行管一件事：分析/积分用哪条几何条目（_collect_geometry 取
    window.config）。五个入口页共用一个下拉框，选它就换分析用的几何
    （currentIndexChanged → _apply_config → 再回调本函数刷新读数）。

    校准页上**照样能换**：校准用的是那一页自己的「当前配置」（借用 /
    手改 / 校准结果），换这里的条目不会动它——提示里写明这层区别。
    2026-09-26 晚订正：先前把这一行在校准页做成置灰（"只显示不能改"），
    结果 [删除] 正好住在校准页、却在校准页换不了选中项，删条目要
    "去分析页选中 → 回校准页点删除"跨两页（用户问"现在几何配置怎么
    删除"）。用户原话"只留能选的那一行，不能改"指的是几何**数值**不
    能改，不是不能换条目，遂放开置灰。

    读数（像素/波长/距离）来自 window.config（完整条目，_apply_config
    里挂上）；建坞阶段它还没设，此时只留条目名、提示留空。
    """
    combo = window.config_combo
    cfg = getattr(window, "config", None)
    if cfg is None:
        tip = ""
    else:
        g = cfg["geometry"]
        tip = (f"{window.config_name}：{cfg['label']}\n"
               f"像素尺寸 {g['pixel_size_m'] * 1e6:.1f} µm\n"
               f"波长 {g['wavelength_m'] * 1e10:.4f} Å\n"
               f"距离 {g['dist_m'] * 1e3:.2f} mm")
    if window.param_stack.currentIndex() == window.PARAM_PAGES["校准"]:
        tip += ("\n（校准用的是本页「当前配置」那份几何；这里换的是"
                "分析/积分用的条目）")
    combo.setToolTip(tip)
    window.geom_row.setToolTip(tip)


def _sync_data_row(window: QMainWindow) -> None:
    """坞顶"数据参数"那一行（2θ 范围 + 点数）随页显隐：**校准页不显示**。

    这一行管的是 1D 积分区间（1D / 处理 / 对比 / 热图都用它），与校准无关
    ——校准在原始图上定几何，全程不读 2θ 区间（`ring_metrics` 只吃几何）。
    用户 2026-09-27："校准页参数放 2theta 范围干嘛"。
    几何配置那一行相反，校准页要留着：它是借用起点，也是 [加载参数] /
    [保存参数] / [删除] 三个按钮的作用对象。
    """
    calibrating = (window.param_stack.currentIndex()
                   == window.PARAM_PAGES["校准"])
    window.data_row.setVisible(not calibrating)


def _switch_entrance(window: QMainWindow, name: str) -> None:
    """切到某个入口：参数坞翻页 + 五个按钮高亮。

    [校准] 是入口 + 工作台开关：切进去 = 进校准（开校准面板，见
    _on_mode），切出去 = 退校准（翻回最近用过的分析入口）。其余四个
    是纯翻页——页底部的产出按钮才负责出图。

    点入口先让参数坞露出来：开局它是收起的（_clear_entrance），
    "选到谁才放谁的参数"；[校准] 的拉宽要量坞的宽度，所以必须先可见。

    再点已亮着的 [校准] = 退出（Qt 先弹起勾选，那一刻 toggled 已经把
    _on_mode(False) 跑完了）。这里要**认出来并就此打住**：否则 clicked
    又把勾按回去，退出 + 重进在同一个点击里跑完，看着像"点了没反应"，
    还会把选到一半的选点清空（2026-09-26 用户报"没有退出校准的按钮"，
    实为这个陷阱；面板的关闭即遗忘把选点也带走了）。
    """
    pages = getattr(window, "PARAM_PAGES", {})
    if name not in pages or name not in window.entrance_buttons:
        return
    if name == "校准" and not window.calib_btn.isChecked():
        return                      # 这一次点击是"退出"，已由 toggled 处理
    window.param_dock.setVisible(True)
    if name != "校准":
        window._last_entrance = name
    if name == "校准":
        window.calib_btn.setChecked(True)    # 幂等；进入走 toggled
    else:
        if window.calib_btn.isChecked():
            window.calib_btn.setChecked(False)   # → toggled → _on_mode(False)
        window.param_stack.setCurrentIndex(pages[name])
    _highlight_entrance(window, name)


def _highlight_entrance(window: QMainWindow, name: str) -> None:
    """入口高亮：只有当前那一个勾着；name=None = 谁都不勾。"""
    for key, btn in getattr(window, "entrance_buttons", {}).items():
        btn.setChecked(key == name)


def _select_plot_type(window: QMainWindow, name: str) -> None:
    """点某个类型按钮：记住它 + 高亮它 + 立即出图（沿用旧工具栏手感）。"""
    window._plot_type = name
    for key, btn in window.view_buttons.items():
        btn.setChecked(key == name)
    window.compare_btn.setChecked(name == "对比")
    window.heat_btn.setChecked(name == "热图")
    if name == "对比":
        _plot_compare(window)
    elif name == "热图":
        _plot_heatmap(window)
    else:
        _plot_view(window, name)


def _plot_selected_type(window: QMainWindow) -> None:
    """「绘图」页的 [出图（勾选文件）]：按当前选中的类型出图。"""
    name = getattr(window, "_plot_type", "1D")
    _select_plot_type(window, name)


def _clear_stage_cache(window: QMainWindow) -> None:
    """[清空缓存]：删掉分阶段产物（下次出图重新积分）。

    缓存是"省时间"的，删了只会慢一点、不会算错——所以不做二次确认，
    日志如实报删了多少（见 services/stage_cache）。
    """
    from xrd_toolkit.services import stage_cache
    info = stage_cache.describe()
    if not info["files"]:
        _log(window, "缓存本来就是空的（还没有落过产物）")
        return
    n = stage_cache.clear()
    window.refresh_groups()   # 文件栏里的产物分组跟着清空
    _log(window, f"已清空缓存：{n} 个产物、{info['bytes'] / 1e6:.1f} MB"
                 f"（下次出图会重新积分）")


def _redraw_focus(window: QMainWindow) -> None:
    """「绘图」页的 [只重画当前]：用已有数据重画编辑对象（不重算）。

    与面板 [Home] 同一条路（plot_views._redraw_panel）；没选编辑对象
    或还没算完时只记日志。
    """
    from xrd_toolkit.gui.plot_views import _redraw_panel
    key = window.focus_panel
    if key is None:
        _log(window, "先点击要重画的图面板（如 1D），再点 [只重画当前]")
        return
    dock = window.plot_docks.get(key)
    reason = _redraw_panel(window, key)
    if reason:
        _log(window, f"[只重画当前] {dock.windowTitle() if dock else key}"
                     f"：{reason}")
        return
    _log(window, f"[只重画当前] 已重画：{dock.windowTitle()}")


def _build_plot_type_row(window: QMainWindow) -> QWidget:
    """「绘图」页顶部的类型选择行：2D / 剖面 / 1D / 瀑布 / 对比 / 热图。

    点一个 = 选中该类型**并立即出图**（对勾选文件）——沿用旧的工具栏
    手感（点一次算一次，纯动作不是开关）；页底部的 [出图] 再点一次是
    同样的动作，方便"先改显示参数、再出图"的循环。按钮属性名沿用
    window.view_buttons / compare_btn / heat_btn（测试与其它模块按它们
    找按钮）。
    """
    row = QWidget()
    box = QHBoxLayout(row)
    box.setContentsMargins(2, 2, 2, 2)
    box.setSpacing(3)
    window.view_buttons = {}   # 登记按钮（测试用）
    for name in VIEW_NAMES:
        btn = QPushButton(name)
        btn.setCheckable(True)     # 高亮 = 当前选的类型
        btn.setFocusPolicy(Qt.NoFocus)
        box.addWidget(btn)
        window.view_buttons[name] = btn
        btn.clicked.connect(lambda checked=False, n=name:
                            _select_plot_type(window, n))
    btn_compare = QPushButton("对比")
    btn_compare.setCheckable(True)
    btn_compare.setFocusPolicy(Qt.NoFocus)
    box.addWidget(btn_compare)
    window.compare_btn = btn_compare
    btn_compare.clicked.connect(lambda: _select_plot_type(window, "对比"))
    btn_heat = QPushButton("热图")
    btn_heat.setCheckable(True)
    btn_heat.setFocusPolicy(Qt.NoFocus)
    box.addWidget(btn_heat)
    window.heat_btn = btn_heat
    btn_heat.clicked.connect(lambda: _select_plot_type(window, "热图"))
    return row


def _sync_smooth_rows(window: QMainWindow) -> None:
    """平滑阶数只有 Savitzky–Golay 用得上：别的模式下置灰，别让人白填。"""
    sg = window.params["平滑方法"].currentData() == "savgol"
    window.params["平滑阶数"].setEnabled(
        bool(window.params["平滑曲线"].isChecked()) and sg)


def _on_cut_toggled(window: QMainWindow, on: bool) -> None:
    """勾上「裁剪区间」：清单空时把旁边那一段直接加进去（勾了就该有反应）。

    清单才是权威（可以删好几段），但"填了起止、勾上开关"是最自然的手势——
    空清单时按这个手势补一段，符合直觉；[添加] 用于往清单里再加一段。
    """
    if on and not window.cut_list:
        _on_cut_add(window)          # 内部会实时重画
        return
    _refresh_proc(window)


def _on_cut_add(window: QMainWindow) -> None:
    """[添加]：把当前起止加进裁剪清单（同一段重复加只留一份）。"""
    lo = float(window.params["裁剪起点 (°)"].value())
    hi = float(window.params["裁剪终点 (°)"].value())
    if not hi > lo:
        _log(window, "裁剪区间：终点要大于起点")
        return
    cuts = window.cut_list
    if (lo, hi) in cuts:
        _log(window, f"裁剪区间：{lo:g}–{hi:g}° 已经在清单里了")
        return
    cuts.append((lo, hi))
    cuts.sort()
    window.params["裁剪区间"].setChecked(True)   # 添加即生效（勾选框跟着亮）
    _sync_cut_label(window)
    _log(window, f"裁剪区间：加了 {lo:g}–{hi:g}°"
                 f"（共 {len(cuts)} 段：{_cut_text(cuts)}）")
    _refresh_proc(window)


def _on_cut_clear(window: QMainWindow) -> None:
    """[清空]：清掉裁剪清单（勾选框也弹起 = 不裁剪）。"""
    n = len(window.cut_list)
    window.cut_list.clear()
    window.params["裁剪区间"].setChecked(False)
    _sync_cut_label(window)
    _log(window, f"裁剪区间：已清空清单（原有 {n} 段）")
    _refresh_proc(window)


def _cut_text(cuts) -> str:
    """"2–3°、7–8°"。"""
    return "、".join(f"{float(lo):g}–{float(hi):g}°" for lo, hi in cuts)


def _sync_cut_label(window: QMainWindow) -> None:
    """清单标签：非空时顺带写明"怎么改"——勾上以后再改起止框不会自动生效
    （清单才是权威），这句话省掉一次"怎么没反应"。
    """
    cuts = window.cut_list
    window.cut_list_lbl.setText(
        f"清单：{_cut_text(cuts)}（改起止后点 [添加]）" if cuts else "清单：空")


def _on_mode(window: QMainWindow, calibrating: bool) -> None:
    """模式开关：勾选 = 校准工作台，弹起 = 分析工作台。

    进入：参数坞翻到校准页（页 0 的分析参数原样保留），勾选的第一个
    文件开校准面板；没勾文件只记日志提示（不崩）。退出：翻回分析页
    + 关校准面板（校准状态清零，关闭即遗忘）。

    2026-09-24 起入口是**五个页签式按钮**（[校准][1D][处理]
    [对比][绘图]，见 _build_toolbar / _switch_entrance）：不再翻转
    开关文字——"退出校准"可以点另一个入口、可以再点一次已亮着的
    [校准]（见 _switch_entrance 的早退），也可以按坞顶那行常驻的
    [返回分析模式]（三条路都汇到这里的 setChecked(False)）。
    """
    if calibrating:
        window.mode_label.setText("校准模式")
        _log(window, "进入校准模式")
        window.param_stack.setCurrentIndex(window.PARAM_PAGES["校准"])
        _enter_calib(window)
    else:
        window.mode_label.setText("分析模式")
        # 翻回最近用过的分析入口；一个都没选过（开局直接进校准、
        # 或点了坞顶的 [返回分析模式]）→ 回到"什么都没选"：
        # 入口不点亮、参数坞收起（用户 2026-09-25 定）
        last = getattr(window, "_last_entrance", None)
        # 页号总归要翻回分析侧的默认页（坞是收起的，看不见；但用户手动
        # 点 [参数] 展开时该看到分析页，而不是停留在校准页）
        window.param_stack.setCurrentIndex(
            window.PARAM_PAGES.get(last, window.PARAM_PAGES["1D"]))
        _exit_calib(window)     # 先还原坞宽（收起后量不到）
        if last is None:
            _clear_entrance(window)
        else:
            # 高亮跟着回来：再点一次已亮着的 [校准] 退出时，走的是这条路
            # （没有别的入口在替它高亮——以前靠 _switch_entrance 补，
            # 现在那条路在校准分支上直接返回了）
            _highlight_entrance(window, last)
        _log(window, "回到分析模式")

# ══ 保存：勾选已输出的图 → 选分辨率/格式 → 逐个选文件名存图 ══


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
    # 拖文件进窗口任意位置 = 加进文件列表；拖文件夹 = 扫描加入
    # （拖放事件冒泡到顶层窗口）
    window.drop_callback = lambda paths: add_files(window, paths)
    window.drop_folder = lambda folder: _scan_folder(window, folder)

    window._status_timer = None   # _log 里的状态栏恢复计时器（懒创建）
    window.log = lambda text: _log(window, text)
    window.add_files = lambda paths, **kw: add_files(window, paths, **kw)
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

    # 背景扣除的窗级数据（不是快照参数，所以不随面板走、免疫面板
    # 弹出/收回——panels.py 的 _PANEL_ATTRS 白名单只搬少量 dock 属性）：
    # bg_anchors = {文件路径: [(2θ, 强度), ...]}，锚点按文件各记各的；
    # bg_blank = 空扫曲线 {"path", "tth", "intensity"}，整批实验共用一条
    window.bg_anchors = {}
    window.bg_blank = None
    # 裁剪清单（窗口级，不是快照参数：快照只认控件值，列表放不进控件）——
    # 编辑它的是「处理」页的 [添加]/[清空]，实时重画时再拷进各面板快照
    window.cut_list = []
    # 锚点计数标签的刷新入口（plot_views 里点选锚点后回调，避免
    # plot_views 反向 import app）
    window._bg_count_refresh = _update_bg_count
    # 背景扣除专用行的显隐同步入口（panel_state 回放面板快照后回调，
    # 同样避免反向 import）
    window._bg_rows_sync = _sync_bg_rows
    # 焦点切换入口：当前编辑对象那块面板的标题栏深一档（plot_panels
    # 的 _sync_bar_active；panel_state 只负责回调，不反向 import）
    window._on_focus_changed = (
        lambda prev, cur: _sync_bar_active(window, prev, cur))

    _build_center(window)
    # 点任何面板窗口内任何位置都选中该面板（应用级过滤器，原因见
    # _PanelClickTracker）。每个窗口装一个；窗口销毁时过滤器随父
    # 对象销毁，Qt 自动把它从应用事件分发里摘掉
    QApplication.instance().installEventFilter(_PanelClickTracker(window))
    window.file_dock = _build_file_dock(window)
    # 产物分组刷新入口（文件栏里的"阶段文件夹"）：批量处理 / 1D 批量
    # 算完 / 清空缓存之后要重建。挂成窗口回调而不是让 plot_views 反向
    # import 文件坞（同 _bg_count_refresh 的老规矩）
    window.refresh_groups = lambda: refresh_product_groups(window)
    # 文件栏按需开图（双击条目 / 右键 [打开 1D 图] / [打开整组]）：
    # 勾选超过上限的那批只算不画，看哪张点哪张。挂回调而不是让
    # file_dock 反向 import plot_views（同 refresh_groups 的老规矩）
    window.open_view_source = (
        lambda source, name="1D": _open_source_view(window, name, source))
    window.open_view_group = (
        lambda sources, name="1D": _open_source_group(window, name, sources))
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
    # 1595.80 mm），而不是写死的占位默认值；[删除] 按钮随选中条目
    # 同步置灰（默认条目是内置的 → 初始不可删）
    _apply_config(window, window.config_combo.currentIndex())
    _sync_del_config_btn(window)

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
