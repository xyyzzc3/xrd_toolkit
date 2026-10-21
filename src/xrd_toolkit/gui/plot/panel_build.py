"""面板构建：自绘标题栏、画布面板、视图 builder 注册表、保存。

2026-10-21 从 plot_panels.py 原样搬出（只改结构，不动逻辑）。
plot_panels 再导出，plot_views 与测试照旧经 gui_plot_panels 取用。
"""
from pathlib import Path

import time
from contextlib import contextmanager

import numpy as np
from matplotlib import get_data_path
from matplotlib.backend_tools import Cursors
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.patches import Rectangle
from matplotlib.figure import Figure
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMainWindow,
                               QMdiSubWindow, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

from xrd_toolkit import paths
from xrd_toolkit.gui.customize import _default_texts, _open_customize_dialog
from xrd_toolkit.gui.panel_state import (_AUX_GID_PREFIX, _content,
                                            _log, _param_box_set,
                                            _panel_param, CALIB_HOVER_KEY)
from xrd_toolkit.gui.panels import (_apply_area_zoom, _apply_panel_chrome,
                                    _close_panel, _install_resize_grip,
                                    _PanelBarFilter, _PanelResizeFilter,
                                    _panel_extra, _PlotSubWindow, _settle,
                                    _toggle_pop_out)
from xrd_toolkit.gui.plot_export import _ask_save_options
# 画布辅助与交互层（2026-10-21 从本文件原样搬出；再导出，plot_views 与
# 测试照旧经 gui_plot_panels 取用）
from xrd_toolkit.gui.plot.canvas import (
    _apply_text_guards, _blit_box, _blit_drop, _blit_take, _data_lines,
    _is_aux_line, _note_hover, _panel_axes, _restore_line_styles,
    _settle_scale, _snapshot_canvas)
from xrd_toolkit.gui.plot.interaction import (
    BOX_MIN_PX, _INTENSITY_Y_VIEWS, _TTH_X_VIEWS, _connect_axis_sync,
    _draw_box, _home_key_reset, _hover_label, _hover_leave, _hover_motion,
    _magnifier_on, _on_xlim_changed, _on_ylim_changed, _pan_motion,
    _pan_press, _pan_release, _refresh_home, _wheel_zoom)

PLOT_OPEN_W, PLOT_OPEN_H = 500, 300   # 新面板默认画布尺寸（画布真 5:3，
                                      # 面板总高 = 画布 + 标题栏一行 26 px）
PANEL_BAR_H = 26   # 自绘标题栏高度：标题 + 四个按钮 + [弹出][关闭] 一行。
# 面板在绘图区里时容器是 frameless（panels._PlotSubWindow），这行就是
# 唯一的壳——原先"原生标题栏 36 + 工具栏 47 = 83 px"，现在 26 px，
# 同样的面板高度里画布多拿 57 px。工具栏对象保留（测试与 _magnifier_on
# 都从它取 action），只是藏起来不出高度，见 _build_slim_bar。

# 标题栏配色（2026-09-24 用户反馈"没有背景、子窗口太丑"）：
# 面板去掉了原生标题栏后，顶端那条默认苍白底（Qt 窗口色 239）夹在
# 工作区（159 灰）与画布（255 白）之间，只差 16 级、没有分界线，看着
# 像条空槽。给一个真正的底色 + 一条与画布的分隔线 + 按钮悬停反馈。
PANEL_BAR_BG = "#dde1e6"        # 比画布暗、比工作区亮的一档灰
PANEL_BAR_ACTIVE = "#c6d0da"    # 当前编辑对象的标题栏深一档（活动/非活动）
PANEL_BAR_LINE = "#b4bac1"      # 标题栏与画布之间的分隔线
PANEL_BAR_TEXT = "#2b2f33"      # 标题文字（11px 小字要够深才读得清）
PANEL_BAR_HOVER = "#c8ced6"     # 按钮悬停底
PANEL_BAR_PRESS = "#b0b7c0"     # 按钮按下底
PANEL_BAR_CLOSE_HOVER = "#d64545"   # 关闭按钮悬停（危险动作给危险色）

class _SlimToolbar(QWidget):
    """面板的 [Home][Zoom][Customize][Save]：**自己拿四个 QAction**，
    本体隐藏（按钮都在自绘标题栏里，见 _build_slim_bar）。

    放大/平移改成鼠标手势，放大镜按钮当开关（与用户讨论定稿）：
    点亮 = 滚轮（触摸板两指滚动）以光标为中心缩放（每格 10%）
    + 左键拖 = **框选放大**（自己画的框、只改坐标范围，见
    _pan_press 里 2026-09-18 那个 bug 的说明）；熄灭 = 滚轮还给绘图区
    滚动、左键拖 = 平移。抓手/前进/后退/子图按钮退休；双击不回全图
    ——回首页只有 Home 一个入口；Customize = 自绘轴属性对话框（标题/
    轴标签/纵轴刻度/图边距，见 _open_customize_dialog；mpl 自带子图
    配置器被替换——那是英文技术术语，且 hspace/wspace/Export values
    对单图无用、字段还挤）；Save = 本面板另存为图片。

    **为什么不再继承 matplotlib 的 NavigationToolbar2QT**（2026-09-24
    换掉）：那是个 QToolBar，构造时按工具清单逐个 addAction——offscreen
    下建够多面板（实测累计约 80 块）之后，下一次构造会偶发在 Qt 的
    action 事件里无限递归、永不返回（栈 5000+ 帧、100% CPU；复现脚本
    scripts/stress_panels.py 里写着排查记录）。本项目**只借它的四个
    action**（工具栏一直是隐藏的，按钮在自绘标题栏上），所以自己建：
    图标仍用 mpl 自带的 PNG，外观与换掉之前逐字一致，那条病路径整个
    不在场了。

    以前靠父类拿到的东西各就各位：
      - **mpl 的 ZOOM/PAN 模式机件不再存在**——放大镜只是开关，框选
        放大是我们自己画的 Rectangle（见 _draw_box），所以"永不进 mpl
        zoom mode"这句话都不必说了：那块机器整个不在场（测试原来断言
        `toolbar.mode.name == "NONE"`，现在断言"没有 mode 这个机件"）；
      - 历史栈（父类 `_nav_stack`/`push_current`）退休：Home 按
        `dock.view_home` 走（见 _refresh_home），不读栈——手势里那两处
        "给 mpl 的 Home 记账"随之删掉；
      - `update()` 也不再需要（没有要刷新的原生按钮）。

    Save 走 save_figure → _save_panel：存完置 figure_saved，关窗询问
    "未保存"时不会再问已经存过盘的面板（旧版工具栏 Save 绕过记账，
    存过还问）。

    对外契约（手势 / 测试 / scripts/check_gui.py 都按
    `content.toolbar` 取）：`_actions` 这四个键、`isHidden()`、
    `save_figure()`——**不能把这个类删成局部变量**。
    """

    # (键, 文本, 悬停提示, 图标名)；图标取 mpl 自带的 PNG（mpl-data/
    # images），外观与换掉之前一致——栏上按钮 setDefaultAction 后显示的
    # 就是这些图。键名沿用 mpl 工具清单里的回调名（edit_parameters /
    # save_figure），既有调用点与测试不用改。
    ACTION_SPECS = (
        ("home", "复位视图",
         "回到这张图最初的视野（只动视野，不动设置）", "home"),
        ("zoom", "放大镜",
         "<b>点亮</b> = 滚轮以光标为中心缩放、左键拖 = 框选放大；"
         "<b>未点亮</b> = 滚轮滚动绘图区、左键拖 = 平移；"
         "调好后用 [保存图片…] 存下的就是当前画面", "zoom_to_rect"),
        ("edit_parameters", "外观…", "编辑标题、坐标轴与曲线属性",
         "qt4_editor_options"),
        ("save_figure", "保存图片…", "把这张图另存为图片", "filesave"),
    )

    def __init__(self, canvas, parent=None, window=None, key=None):
        super().__init__(parent)
        self.canvas = canvas
        self._window = window
        self._panel_key = key
        icon_dir = Path(get_data_path()) / "images"
        self._actions = {}
        for name, text, tip, icon in self.ACTION_SPECS:
            action = QAction(QIcon(str(icon_dir / f"{icon}.png")), text, self)
            action.setToolTip(tip)
            self._actions[name] = action
        # 放大镜只当纯开关：点击只翻亮灭 + 写日志（旧版还要断开 mpl 的
        # triggered→zoom()；现在那边没有可断的东西）
        self._actions["zoom"].setCheckable(True)
        self._actions["zoom"].toggled.connect(self._log_zoom_toggle)
        # [Home] = 回到这张面板的"家"视图（dock.view_home）。旧版这里要
        # 断开 mpl 的历史栈：程序重画（[应用]/重算/实时预览）会清空那个
        # 栈，之后按 Home 常常"没反应"、或只回到"上次画的位置"。现在是
        # **按面板自己的"家"**回（用户 2026-09-24 要求"回到最初的样子，
        # 而不是上次画的位置"）
        self._actions["home"].triggered.connect(self._reset_view)
        self._actions["edit_parameters"].triggered.connect(
            self._open_customize)
        self._actions["save_figure"].triggered.connect(self.save_figure)
        # 旧父类在它的 __init__ 里把这一笔挂在画布上（调用点按
        # canvas.toolbar / _content(dock).toolbar 取），自己挂
        canvas.toolbar = self

    def _open_customize(self):
        if self._window is not None and self._panel_key is not None:
            _open_customize_dialog(self._window, self._panel_key)

    @contextmanager
    def _wait_cursor_for_draw_cm(self):
        """整帧重绘期间给个等待光标——**mpl 自己会来要这个上下文**，不是
        我们调的：`FigureCanvasAgg.draw()` 里写着"画布上有工具栏就问它要
        等待光标"（旧父类提供的，搬过来行为不变；不提供的话每次 draw 都
        会 AttributeError）。

        一秒内连着画就不来回切（照抄 mpl 的节流）：拖动/缩放期每帧都画，
        不节流会闪成噪声。macOS 上部件级光标会被应用级覆盖光标压住
        （项目的光标都走 QApplication 覆盖光标，见 panels.py 的说明），
        所以它只在没有覆盖光标时露一下。
        """
        self._draw_time, last_draw = (
            time.time(), getattr(self, "_draw_time", -np.inf))
        if self._draw_time - last_draw > 1:
            try:
                self.canvas.set_cursor(Cursors.WAIT)
                yield
            finally:
                self.canvas.set_cursor(Cursors.POINTER)
        else:
            yield

    def _reset_view(self):
        """[Home]：回到这张面板的"家"视图（`dock.view_home`）。

        "家" = 最近一次**算出结果**时的视图（见 _refresh_home）：手势缩放/
        平移、改显示参数后 [应用]，都不动它——所以按 Home 就是回到
        "最初的样子"，而不是"上次画的位置"（用户 2026-09-24 的要求）。
        还没算过（没有"家"）就退回"按参数重画一张"（_redraw_panel）。

        **Home 只动画面、不动设置**（用户 2026-09-27："home键不动设置，
        只管理识图"）：设范围时挂上 `_setting_limits`（"程序自己在画图"
        同一个旗标）挡住"范围 → 参数"的写回。不挡的话，下面两次 set_*lim
        会触发 _on_xlim_changed/_on_ylim_changed，把"纵轴自动"取消勾选、
        把纵轴上下限填成家的值——按一次 Home 就悄悄把面板改成手动纵轴，
        而且不可逆。

        **堆叠中的对比面板用堆叠那份"家"**（2026-10-15）：家的 y 分两份
        记（平铺的 `view_home` / 堆叠的 `view_home_stack`，见
        plot_compare._redraw_compare）——Home 永远给**当前模式**的完整
        视野，取消堆叠后回的是平铺那份（此前共用一份、堆叠的 y 覆写了
        它，取消堆叠后 Home 还是堆叠坐标："视图不能回来"的另一半）。
        """
        if self._window is None or self._panel_key is None:
            return
        window = self._window
        dock = window.plot_docks.get(self._panel_key)
        if dock is None:
            return
        title = dock.windowTitle()
        view = self._panel_key.split("|", 1)[0]
        stacked = (view == "对比"
                   and _panel_param(window, dock, "对比堆叠", False))
        home = (getattr(dock, "view_home_stack", None) if stacked
                else getattr(dock, "view_home", None))
        ax = _panel_axes(dock)
        if home is None or ax is None:
            from xrd_toolkit.gui.plot_views import _redraw_panel   # 破循环
            reason = _redraw_panel(window, self._panel_key)
            if reason:
                _log(window, f"[复位视图] 暂时回不去：{reason}")
            else:
                _log(window, f"[复位视图] 已回到参数定义的视图：{title}")
            return
        (xlo, xhi), (ylo, yhi), yscale = home
        # 旗标 save/restore（同 plot_views._draw_1d）：万一有重入，
        # 内层恢复外层取值、不许踩灭外层保护
        prev_limits = getattr(window, "_setting_limits", False)
        window._setting_limits = True
        try:
            ax.set_xlim(xlo, xhi)
            ax.set_ylim(ylo, yhi)
            if ax.get_yscale() != yscale:
                ax.set_yscale(yscale)
        finally:
            window._setting_limits = prev_limits
        # 但"视图窗口"仍要写回快照（缩放/平移写回的也是这一处，快照才是
        # 权威）：不写的话，下一次重画会按快照里那份缩放的窗口画，Home
        # 等于白按。纵轴**只在本来就是手动时**跟着写——绝不替用户翻
        # "纵轴自动"开关（那是用户的选择，不是 Home 的事）。
        _on_xlim_changed(window, self._panel_key, ax)
        # y 同样要写回快照（缩放/平移写回的也是这一处，快照才是权威）。
        # 强度轴视图（1D/对比/剖面）仍只在**本来就是手动纵轴**时写：那支
        # 回调会顺带把"纵轴自动"关掉、把上下限框填成家的值——Home 不许动
        # 设置。像素/行号轴视图（2D/瀑布/热图）的 y 写回只落"视图 y 范围"
        # 这一个键（视图窗口本身，与 x 同性质），照写——不然缩放过的图按
        # 过 Home 之后，下一次重画（如瀑布调行距、2D 换对比度）会拿快照里
        # 的旧缩放窗口接着画，等于 Home 白按（2026-10-13 修"先缩放后调
        # 行距"时一并修出）。
        if stacked:
            # 堆叠的"家" = 自然量程：驻留的缩放窗口与家重合，直接丢弃
            # （同瀑布"缩放后按 [复位视图] → 窗口丢弃"的先例），免得
            # 下一次重画又把旧窗口套回来
            snap = getattr(dock, "params_snapshot", None)
            if snap is not None:
                snap.pop("堆叠视图 y 范围", None)
        elif (view not in _INTENSITY_Y_VIEWS
                or not _panel_param(window, dock, "纵轴自动", True)):
            _on_ylim_changed(window, self._panel_key, ax)
        ax.figure.canvas.draw_idle()
        _log(window, f"[复位视图] 已回到最初的样子：{title}")

    def _log_zoom_toggle(self, on: bool) -> None:
        """开关翻面时把"现在这套手势是什么"写进日志（与按钮 tooltip 同一句话）。

        左键拖的两副面孔跟着开关走：点亮 = 框选放大、熄灭 = 平移。
        原先两半都写"左键拖 = 平移"——点亮时那句是错的（用户 2026-10-01
        报"日志错误"）。
        """
        if self._window is not None:
            _log(self._window,
                 f"放大镜已{'开启' if on else '关闭'}："
                 + ("滚轮以光标为中心缩放，左键拖可框选放大" if on
                    else "滚轮滚动绘图区，左键拖可平移"))

    def save_figure(self, *args):
        # 形参收下 QAction.triggered 的 checked 布尔
        if self._window is not None and self._panel_key is not None:
            _save_panel(self._window, self._panel_key)

def _save_panel(window: QMainWindow, key: str, get_dock=None) -> None:
    """单面板保存（工具栏 [Save] 走这里）：选分辨率/格式 → 选文件名存图。

    成功即置 figure_saved = True——这张面板在关窗询问里不再算
    "未保存"；用户取消（选项弹窗或文件名框）不动记账。

    `get_dock`（2026-10-10 加）：容器现查函数（同 _install_resize_grip
    的套路）——**校准面板不进 plot_docks**（它有自己的 window.calib_dock），
    它那颗新 [保存图片…] 就是把取用函数传进来走同一条保存流程的。
    """
    dock = (get_dock or (lambda: window.plot_docks.get(key)))()
    if dock is None:
        return
    figure = getattr(_content(dock), "figure", None)
    if figure is None:
        _log(window, "该面板还没有可保存的图")
        return
    options = _ask_save_options(window)
    if options is None:
        return   # 选项弹窗取消：不动已保存记账
    ext = options["fmt"]
    default = str(paths.OUTPUTS_DIR / f"{dock.windowTitle()}.{ext}")
    name, _ = QFileDialog.getSaveFileName(
        window, f"保存 {dock.windowTitle()}", default,
        f"{ext.upper()} 图片 (*.{ext})")
    if not name:
        return   # 用户取消：不动已保存记账
    if not name.lower().endswith(f".{ext}"):
        name += f".{ext}"
    try:
        Path(name).parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(name, dpi=options["dpi"])
    except OSError as err:
        _log(window, f"保存失败 {dock.windowTitle()} → {name}（{err}）")
        return
    dock.figure_saved = True
    _log(window, f"已保存 {dock.windowTitle()} → {name}"
                 f"（{options['dpi']} dpi）")

def _build_view_widget(window: QMainWindow, name: str, key: str,
                       title: str) -> QWidget:
    """按视图名创建面板内容（注册表分发器）。

    查 _VIEW_BUILDERS 找对应 builder；未注册视图 = 占位标签（面板
    照常开出，接线后自动变成真内容）。每张面板内容独立（自己的
    画布/坐标轴，挂在控件上供 _draw_1d 使用）；以后 2D/剖面/瀑布
    接线 = 往表里加条目，本函数不用动。每个面板都装 _FocusMarker：
    点它即成为参数面板的编辑对象。
    """
    builder = _VIEW_BUILDERS.get(name)
    if builder is None:
        placeholder = QLabel(f"{title}（暂未开放）")
        placeholder.setAlignment(Qt.AlignCenter)
        widget = placeholder
    else:
        widget = builder(window, key)
    # 焦点切换改挂容器（子窗口/弹出窗口）上：点标题栏/边框也选中
    # （见 _FocusMarker 与两个容器的 __init__），这里不再挂内容上
    _install_resize_grip(window, key, widget)
    return widget

def _build_slim_bar(window: QMainWindow, key: str, content) -> QWidget:
    """面板的单行标题栏：标题 + [Home][Zoom][Customize][Save] + [弹出][关闭]。

    取代"原生标题栏 + 一整个工具栏"两行（实测 36 + 47 = 83 px）。
    面板在绘图区里时容器是 frameless 子窗口（panels._PlotSubWindow），
    这行是唯一的壳；弹出去成独立窗口时系统标题栏回来负责移动/最大
    化，这行继续提供四个按钮和 [收回]（弹出后仍是唯一有按钮的地方
    ——工具栏是藏着的）。

    按钮直接绑工具栏自己的 QAction（setDefaultAction）：放大镜的
    点亮状态、[Customize] 换过的回调、[Save] 走的 _save_panel 全部
    共用一份，不重复实现。工具栏对象本体留在内容里但 hide()——测试
    和 _magnifier_on 都按 content.toolbar 取 action，不能删。

    拖动/双击最大化由 panels._PanelBarFilter 挂在**本行**上，所以
    按在按钮上不会误拖（见该过滤器说明）。
    """
    bar = QWidget()
    bar.setObjectName("panel_bar")
    bar.setFixedHeight(PANEL_BAR_H)
    # 纯 QWidget 要显式开这个才让样式表画背景（Qt 的老规矩）
    bar.setAttribute(Qt.WA_StyledBackground, True)
    bar.setStyleSheet(f"""
        QWidget#panel_bar {{
            background: {PANEL_BAR_BG};
            border-bottom: 1px solid {PANEL_BAR_LINE};
        }}
        QWidget#panel_bar[active="true"] {{
            background: {PANEL_BAR_ACTIVE};
        }}
        QWidget#panel_bar QLabel {{
            color: {PANEL_BAR_TEXT}; font-size: 11px;
        }}
        QWidget#panel_bar QToolButton {{
            border: none; border-radius: 3px; padding: 1px 3px;
        }}
        QWidget#panel_bar QToolButton:hover {{ background: {PANEL_BAR_HOVER}; }}
        QWidget#panel_bar QToolButton:pressed {{
            background: {PANEL_BAR_PRESS};
        }}
        QWidget#panel_bar QToolButton:checked {{
            background: {PANEL_BAR_PRESS};
            border: 1px solid {PANEL_BAR_LINE};
        }}
        QWidget#panel_bar QToolButton#panel_bar_close:hover {{
            background: {PANEL_BAR_CLOSE_HOVER}; color: white;
        }}
        QWidget#panel_bar QPushButton {{
            border: none; border-radius: 3px; padding: 1px 5px;
            background: transparent; color: {PANEL_BAR_TEXT};
            font-size: 11px;
        }}
        QWidget#panel_bar QPushButton:hover {{ background: {PANEL_BAR_HOVER}; }}
        QWidget#panel_bar QPushButton:pressed {{
            background: {PANEL_BAR_PRESS};
        }}
    """)
    row = QHBoxLayout(bar)
    row.setContentsMargins(6, 0, 2, 0)
    row.setSpacing(1)
    # 2026-09-24 用户：名字在屏幕上出现了三处（参数坞的"编辑对象"、
    # 这一行、图上标题）→ 这一行不再显示名字，只留按钮；是哪块面板
    # 看图上的标题，鼠标悬停在这行的空白处也能看到（tooltip 在
    # 容器的 windowTitleChanged 里同步，见 _sync_bar_tooltip）
    row.addStretch(1)
    toolbar = getattr(content, "toolbar", None)
    if toolbar is not None:
        for name in ("home", "zoom", "edit_parameters", "save_figure"):
            action = toolbar._actions.get(name)
            if action is None:
                continue
            btn = QToolButton()
            btn.setDefaultAction(action)
            btn.setAutoRaise(True)
            btn.setIconSize(QSize(16, 16))   # 工具栏图标 32 px 太大
            btn.setFocusPolicy(Qt.NoFocus)
            row.addWidget(btn)
    popout = QPushButton("弹出")     # 文本随弹出/收回切换（见 _toggle_pop_out）
    popout.setFocusPolicy(Qt.NoFocus)
    popout.clicked.connect(lambda: _toggle_pop_out(window, key))
    row.addWidget(popout)
    close = QToolButton()
    close.setObjectName("panel_bar_close")
    close.setText("✕")
    close.setToolTip("关闭这张图面板（关闭后显示设置不保留"
                     "——数据在文件栏里不受影响）")
    close.setAutoRaise(True)
    close.setFocusPolicy(Qt.NoFocus)
    close.clicked.connect(lambda: _close_panel(window, key))
    row.addWidget(close)
    bar.installEventFilter(_PanelBarFilter(window, key, bar))
    content.slim_bar = bar
    content.popout_btn = popout      # 名字不变：测试与 _toggle_pop_out 按它取
    content.bar_close_btn = close
    return bar

def _sync_bar_active(window: QMainWindow, prev: str, cur: str) -> None:
    """当前编辑对象那块面板的标题栏深一档（原生窗口的活动/非活动惯例）。

    由焦点切换驱动：panel_state._set_focus 调 window._on_focus_changed
    钩子（回调挂在 window 上，避免最底层反向 import 本模块），app 在
    建窗收尾时接上。面板开了又关、焦点自动移交都走同一条路。
    """
    for key, active in ((prev, False), (cur, True)):
        if not key:
            continue
        dock = window.plot_docks.get(key)
        bar = getattr(_content(dock), "slim_bar", None) if dock else None
        if bar is None:
            continue
        bar.setProperty("active", "true" if active else "false")
        # 动态属性变了要重新求值样式表（Qt 不自动重画）
        bar.style().unpolish(bar)
        bar.style().polish(bar)

def _build_canvas_panel(window: QMainWindow, key: str, ax_attr: str,
                        hover: bool, sync: str) -> QWidget:
    """面板内容骨架（1D/2D/剖面/瀑布共用）：画布 + 精简工具栏 +
    弹出按钮 + 手势。

    ax_attr = 坐标轴挂到容器上的属性名（axes_1d / axes_2d /
    axes_profile / axes_waterfall）——各 _draw_* 按名取轴。hover =
    是否接悬停取点（2D 是图没有曲线）；sync = 接哪些方向的范围
    写回："xy" = x/y 都写（1D），"y" = 只写纵轴（剖面：x 是像素
    距离），"" = 都不接（2D 像素轴无参数语义 / 瀑布行偏移由数据
    决定）。手势（拖 = 平移、滚轮 = 以光标为中心缩放）是通用的，
    全部视图都接。

    每张面板自己的精简工具栏 [Home][Customize][Save] + [弹出]，
    只作用于本面板的图。放大/平移改成鼠标手势（拖 = 平移、滚轮
    = 以光标为中心缩放），放大镜/抓手/前进后退/子图按钮全砍掉；
    回首页不绑双击——Home 按钮就是回首页。工具栏放画布上方，
    面板标题栏不动。
    """
    fig = Figure(figsize=(5, 3), tight_layout=True)
    canvas = FigureCanvasQTAgg(fig)
    ax = fig.add_subplot(111)
    setattr(canvas, ax_attr, ax)
    toolbar = _SlimToolbar(canvas, canvas, window, key)
    # 容器 = 自绘标题栏 + 画布竖排（工具栏藏起来不出高度，见
    # _build_slim_bar）。把画布原有属性挂到容器上（坐标轴 / figure
    # / draw），其余代码仍按 _content(dock) 直取，不必改调用点
    widget = QWidget()
    box = QVBoxLayout(widget)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(0)
    setattr(widget, ax_attr, ax)
    widget.figure = fig
    widget.canvas = canvas
    widget.toolbar = toolbar
    widget.draw = canvas.draw   # _content(dock).draw() 仍直接落到画布
    widget.panel_key = key   # 弹出/收回按钮经它找面板
    # 自绘标题栏要在 widget 的属性就位之后建：它按 content.toolbar
    # 取 action，并往 content 上挂 slim_bar / title_label / popout_btn
    box.addWidget(_build_slim_bar(window, key, widget))
    toolbar.hide()   # 按钮都进了标题栏，这行只当 action 仓库（不出高度）
    box.addWidget(toolbar)
    box.addWidget(canvas)
    # 画布尺寸变化 = 用户拖了面板边框（或程序平铺/开局）→ 记
    # 比例记忆。过滤器装在画布上而不是容器上：弹出/收回换容器
    # 不用重挂（逻辑见 _on_canvas_resized）
    canvas.installEventFilter(_PanelResizeFilter(window, key, canvas))
    # 任何一次整帧重绘都作废拖动的位图会话（缓存背景已过期，再贴会
    # 把画面贴回旧样子）；见 _blit_take。注意会话挂在**容器**上
    # （dock），不是这个内容部件——弹出/收回会换容器
    canvas.mpl_connect("draw_event",
                       lambda ev, w=window, k=key: _blit_drop(w, k))
    # 悬停取点：鼠标移动 → 曲线上出点 + 状态栏出坐标；
    # 移出坐标轴 → 清空（细节见 _hover_motion/_hover_leave）
    if hover:
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
    # 鼠标最后停留过的面板（Home 键认它，见 _home_key_reset）。单独挂一条
    # 而不是塞进 _pan_motion：那个函数没按键时立刻返回，记不到"停留"。
    canvas.mpl_connect("motion_notify_event",
                       lambda ev, w=window, k=key: _note_hover(w, k, ev))
    # 范围同步写回：缩放/平移/Home/Customize 对话框改动 x/y 范围
    # → 写回该面板快照 + 焦点时同步参数坞控件（x/y 分开处理：
    # 动 x 只写视图范围，动 y 才关纵轴自动，见两个处理函数）。
    # 每次重画 ax.clear() 都会清掉这些回调，画完由 _draw_* 重连
    # （见 _connect_axis_sync）
    if sync:
        _connect_axis_sync(window, key, ax, sync_x=(sync == "xy"))
    return widget

def _build_1d_widget(window: QMainWindow, key: str) -> QWidget:
    """1D 面板内容：画布 + 精简工具栏 + 弹出按钮 + 悬停取点 +
    手势 + 范围写回（x/y 都写）+ 背景锚点点选。"""
    # 锚点拾取的处理在 plot_compare（多文件视图那层），面板壳在本模块
    # ——模块级导入会成环，函数内延迟导入（同 _build_heat_widget）
    from xrd_toolkit.gui.plot_compare import (_anchor_press,
                                              _anchor_release,
                                              _anchor_right_click)
    widget = _build_canvas_panel(window, key, "axes_1d", hover=True,
                                 sync="xy")
    # 锚点点选：额外挂一组按/放事件（与热图行点击同一扩展点——canvas 上
    # 的 mpl_connect 不会被 ax.clear() 清掉，通用平移手势照旧并存）。
    # 按下事件里同时接右键删除（用户 2026-10-02 第 2 条）：只删单个锚点，
    # 不需要点亮 [拾取锚点]，见 _anchor_right_click。
    canvas = getattr(widget, "canvas", None)
    if canvas is not None:
        canvas.mpl_connect("button_press_event",
                           lambda ev, k=key: _anchor_press(window, k, ev))
        canvas.mpl_connect("button_press_event",
                           lambda ev, k=key: _anchor_right_click(window, k, ev))
        canvas.mpl_connect("button_release_event",
                           lambda ev, k=key: _anchor_release(window, k, ev))
    return widget

def _build_2d_widget(window: QMainWindow, key: str) -> QWidget:
    """2D 面板内容：同 1D 骨架，无悬停取点、无范围写回（像素轴
    没有 2θ/纵轴参数语义）。"""
    # sync="xy"：像素轴没有语义参数，但范围要写回快照（通用键），
    # 否则重画按数据自动缩放、缩放白做（用户 2026-09-28）
    return _build_canvas_panel(window, key, "axes_2d", hover=False, sync="xy")

def _build_profile_widget(window: QMainWindow, key: str) -> QWidget:
    """剖面面板内容：同 1D 骨架，只写回纵轴（x = 像素距离）。"""
    return _build_canvas_panel(window, key, "axes_profile",
                               hover=True, sync="xy")

def _build_waterfall_widget(window: QMainWindow, key: str) -> QWidget:
    """瀑布面板内容：同 1D 骨架，无范围写回（行偏移由数据决定）。"""
    return _build_canvas_panel(window, key, "axes_waterfall",
                               hover=True, sync="xy")

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
    # 壳：画布面板换成自绘标题栏（frameless，省 57 px），占位面板
    # 保留原生标题栏（见 _apply_panel_chrome）
    _apply_panel_chrome(sub, content)
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
    # 文件栏里给这个条目打上"正在打开"的记号（淡色 + 小点，见
    # file_dock.refresh_open_marks）。经窗级回调，避免反向 import 文件坞
    mark = getattr(window, "mark_open_rows", None)
    if mark is not None:
        mark()
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

def _build_heat_widget(window: QMainWindow, key: str) -> QWidget:
    """热图面板内容：同 1D 骨架，无悬停取点、无范围写回（y 轴 =
    样品序号，不是强度；缩放/平移仍是通用的，只看图）。额外挂
    行点击联动：点某行 = 该样品在含它的对比面板里隐藏/显示切换
    （任务六：热图与多曲线叠加配合使用）。"""
    # 行点击的处理在 plot_compare（热图的数据与交互都在那边），面板壳
    # 在本模块——模块级导入会成环，函数内延迟导入（同 _build_1d_widget）
    from xrd_toolkit.gui.plot_compare import (_heat_row_press,
                                              _heat_row_release)
    # sync="xy"：热图的 x 就是 2θ（与 1D/对比同口径），y 是样品行——
    # 缩放了就得写回快照，否则任何一次重画都会按数据自动缩放、把缩放顶掉。
    # 用户 2026-09-28："热图和对比图的画框放大还是有bug"，热图这边的原因是
    # 这里原本 sync=""（范围从不写回）+ _draw_heatmap 里也没有"按快照摆回
    # 视图"的那几行，两处一起补上。
    widget = _build_canvas_panel(window, key, "axes_heat", hover=False,
                                 sync="xy")
    canvas = getattr(widget, "canvas", None)
    if canvas is not None:
        canvas.mpl_connect(
            "button_press_event",
            lambda event: _heat_row_press(window, key, event))
        canvas.mpl_connect(
            "button_release_event",
            lambda event: _heat_row_release(window, key, event))
    return widget

_VIEW_BUILDERS = {"2D": _build_2d_widget, "剖面": _build_profile_widget,
                  "1D": _build_1d_widget, "瀑布": _build_waterfall_widget,
                  "热图": _build_heat_widget}

