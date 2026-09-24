"""面板壳：画布容器、自绘标题栏、手势、轴同步、悬停、开面板。

从 plot_views.py 拆出来（纯搬迁）：这一层只关心"一个画布长什么样、
怎么拖怎么缩放、鼠标放在上面显示什么"，不知道数据从哪来。
依赖方向：plot_views / plot_compare 都用本模块，本模块不反向依赖它们。

壳的形状（2026-09-24，用户要求"边框、小工具栏做小一点"）：面板 =
一行 26 px 自绘标题栏（[Home][Zoom][Customize][Save] + [弹出][关闭]，
_build_slim_bar；不显示名字——名字在参数坞"编辑对象"与图上标题里
已经有两处，悬停提示才给）+ 画布；容器是 frameless 子窗口
（panels._apply_panel_chrome），原先"原生标题栏 36 + 工具栏 47 =
83 px"的壳瘦到 26 px——同样高度的面板里画布多拿 57 px。matplotlib
的工具栏对象仍然存在（_SlimToolbar）但 hide()，只当 action 仓库：
测试与 _magnifier_on 都按 content.toolbar 取 action。
"""
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import (FigureCanvasQTAgg,
                                              NavigationToolbar2QT)
from matplotlib.patches import Rectangle
from matplotlib.figure import Figure
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMainWindow,
                               QMdiSubWindow, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

from xrd_toolkit.gui.customize import _default_texts, _open_customize_dialog
from xrd_toolkit.gui.panel_state import _AUX_GID_PREFIX, _content, _log
from xrd_toolkit.gui.panels import (_apply_area_zoom, _apply_panel_chrome,
                                    _close_panel, _install_resize_grip,
                                    _PanelBarFilter, _PanelResizeFilter,
                                    _panel_extra, _PlotSubWindow, _settle,
                                    _toggle_pop_out)
from xrd_toolkit.gui.plot_export import _ask_save_options


PLOT_OPEN_W, PLOT_OPEN_H = 500, 300   # 新面板默认画布尺寸（画布真 5:3，
                                      # 面板总高 = 画布 + 标题栏一行 26 px）
BOX_MIN_PX = 6     # 框选放大：拖出来的框小于这个尺寸（像素）= 误碰，当点击
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


def _is_aux_line(line) -> bool:
    """这条线是不是背景扣除的辅助线（不是数据曲线）。"""
    gid = line.get_gid()
    return isinstance(gid, str) and gid.startswith(_AUX_GID_PREFIX)


def _data_lines(ax):
    """坐标轴上真正的数据曲线（排除背景扣除的辅助线与悬停点）。"""
    return [ln for ln in ax.lines if not _is_aux_line(ln)]


def _snapshot_canvas(ax):
    """重画前把会被 ax.clear() 抹掉的状态拍下来（Customize 保护用）。

    返回 (标题, x 标签, y 标签, 纵轴刻度, [(图例名,颜色,线型,线宽,
    标记), ...])。曲线只收有数据的（悬停圆点是空数据假线，不算），
    背景扣除的辅助线也不收——快照与回填都按线号索引，收进来会错位。
    """
    lines = [(line.get_label(), line.get_color(), line.get_linestyle(),
              line.get_linewidth(), line.get_marker())
             for line in _data_lines(ax) if len(line.get_xdata()) > 0]
    return (ax.get_title(), ax.get_xlabel(), ax.get_ylabel(),
            ax.get_yscale(), lines)


def _settle_scale(dock, cur_scale, param_log):
    """纵轴刻度记账：返回本次应使用的刻度名（与用户讨论定稿）。

    cur_scale = 重画前画布上的刻度（ax.clear() 会重置成 linear，须
    提前拍下）。规则：用户在 Customize 对话框改过刻度 → 以用户为准；
    除非参数里的 [对数纵轴] 又改过了（上次生效时记下的 _yscale_param
    与现在不同）→ 参数为准。首画无条件用参数值。
    """
    want = "log" if param_log else "linear"
    ours = getattr(dock, "_yscale_ours", None)
    ours_param = getattr(dock, "_yscale_param", None)
    if ours is None or ours_param is None or ours_param != param_log:
        final = want          # 首画 / 参数又改过：参数为准
    elif cur_scale != ours:
        final = cur_scale     # 用户在 Customize 改过：用户为准
    else:
        final = ours          # 维持上次（参数值或用户值）
    dock._yscale_ours = final
    dock._yscale_param = param_log
    return final


def _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel,
                       texts=None):
    """标题/轴标签保护：用户在 Customize 里改过的保留，其余照默认。

    texts = (默认标题, 默认 x 标签, 默认 y 标签)；None = 按面板的
    视图名查 _default_texts（1D/对比 = 积分图默认，2D/剖面/瀑布 =
    各自默认）。标题的"参数源"= 显示名（panel_display）：显示名没
    变 → 用户手改的标题以用户为准；显示名变过 → 参数为准（默认
    标题跟新名字）。轴标签没有参数源 → 用户改过一次就永远以用户
    为准。
    """
    if texts is None:
        view = dock.panel_key.split("|", 1)[0]
        texts = _default_texts(view, dock.panel_display)
    default_title, default_xlabel, default_ylabel = texts
    ours = getattr(dock, "_title_ours", None)
    ours_display = getattr(dock, "_title_display", None)
    if (ours is not None and keep_title != ours
            and ours_display == dock.panel_display):
        ax.set_title(keep_title)   # 用户为准（不更新 ours：它仍是默认基准）
    else:
        ax.set_title(default_title)
        dock._title_ours = default_title
        dock._title_display = dock.panel_display
    ours = getattr(dock, "_xlabel_ours", None)
    if ours is not None and keep_xlabel != ours:
        ax.set_xlabel(keep_xlabel)
    else:
        ax.set_xlabel(default_xlabel)
        dock._xlabel_ours = default_xlabel
    ours = getattr(dock, "_ylabel_ours", None)
    if ours is not None and keep_ylabel != ours:
        ax.set_ylabel(keep_ylabel)
    else:
        ax.set_ylabel(default_ylabel)
        dock._ylabel_ours = default_ylabel


def _restore_line_styles(ax, old_lines, restore_color=True):
    """把重画前拍下的曲线样式原样套回新画的曲线（Customize 保护：
    用户改过的线型/线宽/标记/图例名不被重画盖掉）。数量变了
    就按顺序对前面几条（新多的曲线用默认样式）。

    restore_color=False = 1D/剖面/对比视图：颜色由"曲线配色"参数
    + 逐条自定义色（dock.curve_colors）管——参数本身就是用户改色
    的入口，重画时按新参数着色；旧快照的颜色套回会把换配色方案
    的 [应用] 打回原形。用户颜色保护不丢——自定义色存在
    curve_colors 里，画图时优先于色板（瀑布的 χ 渐变色是程序
    自定、与配色参数无关，仍走 restore_color=True）。
    """
    for i, line in enumerate(_data_lines(ax)):
        if i >= len(old_lines):
            break
        label, color, ls, lw, marker = old_lines[i]
        line.set_label(label)
        if restore_color:
            line.set_color(color)
        line.set_linestyle(ls)
        line.set_linewidth(lw)
        line.set_marker(marker)


def _refresh_home(dock):
    """程序自己重画后清空视图账本：新画好的视图 = 新的"家"（Home）。

    mpl 只在用户手势（框选/平移）里记账，程序重画不自动刷新——
    不刷的话 Home 会跳回重画前的老视图（与用户讨论定稿）。
    """
    canvas = getattr(_content(dock), "canvas", None)
    toolbar = getattr(canvas, "toolbar", None)
    if toolbar is not None:
        toolbar.update()


class _SlimToolbar(NavigationToolbar2QT):
    """只留 [Home][Zoom][Customize][Save] 的精简工具栏（过滤父类工具清单）。

    放大/平移改成鼠标手势，放大镜按钮当开关（与用户讨论定稿）：
    点亮 = 滚轮（触摸板两指滚动）以光标为中心缩放（每格 10%）
    + 左键拖 = **框选放大**（自己画的框、只改坐标范围，见
    _pan_press 里 2026-09-18 那个 bug 的说明）；熄灭 = 滚轮还给绘图区
    滚动、左键拖 = 平移。抓手/
    前进/后退/子图按钮退休；双击不回全图——回首页只有 Home
    一个入口；Customize = 自绘轴属性对话框（标题/轴标签/纵轴刻度/
    图边距，见 _open_customize_dialog；mpl 自带子图配置器被替换
    ——那是英文技术术语，且 hspace/wspace/Export values 对单图
    无用、字段还挤）；Save = 本面板另存为图片。父类 __init__ 按
    toolitems 表逐个建按钮，覆盖成只含这四个的表即可；放大镜
    QAction mpl 自带 checkable，点击自动亮灭翻转——把 mpl 的
    triggered→zoom() 断开：按钮只当纯开关，**永不进 mpl 的 zoom
    mode**（框选放大的选框是我们自己画的 Rectangle，见 _draw_box），
    toggled 信号接日志提示。

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
        # 放大镜按钮只当纯开关：断开 mpl 的 zoom()（会切框选模式），
        # 点击只剩亮灭翻转；toggled 信号写日志
        self._actions["zoom"].triggered.disconnect()
        self._actions["zoom"].toggled.connect(self._log_zoom_toggle)
        # Customize 断开 mpl 自带的图选项编辑器（edit_parameters =
        # toolitem 的回调名，也是 _actions 的键），换成自绘轴属性对话框
        self._actions["edit_parameters"].triggered.disconnect()
        self._actions["edit_parameters"].triggered.connect(
            self._open_customize)

    def _open_customize(self):
        if self._window is not None and self._panel_key is not None:
            _open_customize_dialog(self._window, self._panel_key)

    def _log_zoom_toggle(self, on: bool) -> None:
        if self._window is not None:
            _log(self._window,
                 f"放大镜已{'开启' if on else '关闭'}："
                 f"{'滚轮以光标为中心缩放' if on else '滚轮滚动绘图区'}，"
                 f"左键拖 = 平移")

    def save_figure(self, *args):
        if self._window is not None and self._panel_key is not None:
            _save_panel(self._window, self._panel_key)
        else:
            super().save_figure(*args)


def _save_panel(window: QMainWindow, key: str) -> None:
    """单面板保存（工具栏 [Save] 走这里）：选分辨率/格式 → 选文件名存图。

    成功即置 figure_saved = True——这张面板在关窗询问里不再算
    "未保存"；用户取消（选项弹窗或文件名框）不动记账。
    """
    dock = window.plot_docks.get(key)
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
    default = str(Path("outputs") / f"{dock.windowTitle()}.{ext}")
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


def _magnifier_on(dock) -> bool:
    """该面板的放大镜开关是否点亮。

    点亮 = 滚轮以光标为中心缩放 + 左键拖 = 框选放大；熄灭 = 滚轮
    滚动绘图区、左键拖 = 平移。一个开关管住"这张图上的缩放"，
    不看图时不会误缩（与用户讨论定稿）。
    """
    toolbar = getattr(_content(dock), "toolbar", None)
    if toolbar is None:
        return False
    action = toolbar._actions.get("zoom")
    return bool(action is not None and action.isChecked())


def _draw_box(dock, ax, x0: float, y0: float, x1: float, y1: float) -> None:
    """框选放大的选框：画成"坐标轴分数坐标"的矩形（跟着轴缩放，不受
    数据范围影响），松开时由 _pan_release 删掉。
    """
    inv = ax.transAxes.inverted()
    (u0, v0), (u1, v1) = inv.transform([(x0, y0), (x1, y1)])
    left, bottom = min(u0, u1), min(v0, v1)
    box = getattr(dock, "_box_patch", None)
    if box is None or box.axes is not ax:
        if box is not None:
            box.remove()
        box = Rectangle((left, bottom), abs(u1 - u0), abs(v1 - v0),
                        transform=ax.transAxes, facecolor="none",
                        edgecolor="#2a78d6", linewidth=1.0, linestyle="--",
                        zorder=20)
        ax.add_patch(box)
        dock._box_patch = box
    else:
        box.set_bounds(left, bottom, abs(u1 - u0), abs(v1 - v0))
    ax.figure.canvas.draw_idle()


def _pan_press(window: QMainWindow, key: str, event) -> None:
    """左键按下：放大镜点亮 = 起框选放大的框；熄灭 = 记平移起点。

    框选放大是自己实现的（画框 + 松开时只改 set_xlim/set_ylim），
    **不碰 matplotlib 的 zoom mode**。2026-09-18 用户报过旧版的 bug：
    "画完框再缩小，会把画框内的数据变小"——那是 mpl 的 zoom 模式
    与我们的平移手势抢同一串鼠标事件、又各自记账造成的。现在这条
    路径只动坐标范围、永远不碰曲线数据，并且和滚轮缩放共用同一套
    范围写回（改动实时同步进参数快照）。
    """
    dock = window.plot_docks.get(key)
    if dock is None or event.inaxes is None or event.button != 1:
        return
    if _magnifier_on(dock):
        dock._box_start = (event.x, event.y)
        dock._box_axes = event.inaxes
        _draw_box(dock, event.inaxes, event.x, event.y, event.x, event.y)
        return
    dock._pan_start = (event.x, event.y)
    dock._pan_limits = (event.inaxes.get_xlim(), event.inaxes.get_ylim())


def _pan_motion(window: QMainWindow, key: str, event) -> None:
    """左键拖动：放大镜点亮 = 拉伸框选矩形；熄灭 = 整图平移。

    平移：起点之后每次移动都从"按下时的显示范围"重算（绝对位移，
    不累计误差）。像素差换算：把起始范围的两个角换算成像素坐标，
    平移后再反算回数据坐标——对数轴也精确（数据坐标直接相减在
    对数轴上会变形）。范围变化走 xlim_changed/ylim_changed →
    自动同步写回参数快照。
    """
    dock = window.plot_docks.get(key)
    if dock is None or event.button != 1:
        return
    box_start = getattr(dock, "_box_start", None)
    if box_start is not None:
        # 框选：鼠标拖出画框外也照画（按住不放时出轴是常态），
        # 用按下时那个坐标轴
        ax = getattr(dock, "_box_axes", None)
        if ax is not None:
            _draw_box(dock, ax, box_start[0], box_start[1], event.x, event.y)
        return
    if event.inaxes is None:
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
    """松开左键：放大镜点亮且框够大 = 放大到框内；否则结束平移。

    只改坐标范围（set_xlim / set_ylim），**一个数据点都不碰**——这是
    2026-09-18 那个 bug（画完框再缩小、框内数据变小）的根治办法：
    框选只表达"我要看这一块"，看的仍是原来那条曲线。框太小
    （< BOX_MIN_PX）= 其实是一次点击，什么都不做（避免误缩到极限）。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return
    box_start = getattr(dock, "_box_start", None)
    if box_start is not None:
        ax = getattr(dock, "_box_axes", None)
        patch = getattr(dock, "_box_patch", None)
        dock._box_start = None
        dock._box_axes = None
        dock._box_patch = None
        if patch is not None:
            patch.remove()      # 选框是临时的：松开就撤，不留痕迹
        if ax is None:
            return
        if (abs(event.x - box_start[0]) >= BOX_MIN_PX
                and abs(event.y - box_start[1]) >= BOX_MIN_PX):
            inv = ax.transData.inverted()
            (cx0, cy0), (cx1, cy1) = inv.transform(
                [box_start, (event.x, event.y)])
            xlo, xhi = min(cx0, cx1), max(cx0, cx1)
            ylo, yhi = min(cy0, cy1), max(cy0, cy1)
            if ax.get_yscale() == "log" and ylo <= 0:
                # 对数轴下限必须 > 0：不然 matplotlib 会把整条
                # set_ylim 忽略掉（x 放大了、y 没动，看着像只做了一半）
                ylo = yhi / 1e6 if yhi > 0 else None
            ax.set_xlim(xlo, xhi)
            if ylo is not None:
                ax.set_ylim(ylo, yhi)
            # 同滚轮缩放：给 Home 记账（账本空着 Home 会无事可做）
            toolbar = getattr(ax.figure.canvas, "toolbar", None)
            if toolbar is not None and toolbar._nav_stack() is None:
                toolbar.push_current()
        ax.figure.canvas.draw_idle()   # 撤掉的选框要重画一次
        return
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
    # 懒记账（照抄 mpl 框选/平移手势的做法）：滚轮直接改坐标轴范围、
    # 绕过 mpl 的记账，账本一直空着 Home 就无事可做——首次滚轮缩放
    # 前把当前视图记成"家"（程序重画时账本会被 _refresh_home 清空，
    # 所以"家"= 最近一次画好的视图）
    toolbar = getattr(ax.figure.canvas, "toolbar", None)
    if toolbar is not None and toolbar._nav_stack() is None:
        toolbar.push_current()
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


def _connect_axis_sync(window: QMainWindow, key: str, ax=None,
                       sync_x: bool = True) -> None:
    """把 x/y 范围同步写回回调连到面板的坐标轴。

    sync_x=False = 只连纵轴（剖面：x 轴是像素距离，没有视图 2θ
    范围的概念）。matplotlib 3.11 起 ax.clear()（cla）会把 ax 的
    回调注册表整个清空 → 每次重画完都必须重连（清空后重连只有
    一套，不会叠罗汉）。闭包只抓 key 不抓容器对象：面板弹出/收回
    会换容器（子窗口 ↔ 弹出窗口），抓 key 回调永远现查到当前
    容器；面板关了则 None 守卫静默跳过。构建面板时内容还没挂进
    容器 → 调用方直接把 ax 传进来。
    """
    if ax is None:
        dock = window.plot_docks.get(key)
        if dock is None:
            return
        ax = _content(dock).axes_1d
    if sync_x:
        ax.callbacks.connect("xlim_changed",
                             lambda a, k=key: _on_xlim_changed(window, k, a))
    ax.callbacks.connect("ylim_changed",
                         lambda a, k=key: _on_ylim_changed(window, k, a))


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
        placeholder = QLabel(f"{title} — 尚未接线")
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
    close.setToolTip("关闭面板（关闭即遗忘）")
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
                                              _anchor_release)
    widget = _build_canvas_panel(window, key, "axes_1d", hover=True,
                                 sync="xy")
    # 锚点点选：额外挂一组按/放事件（与热图行点击同一扩展点——canvas 上
    # 的 mpl_connect 不会被 ax.clear() 清掉，通用平移手势照旧并存）
    canvas = getattr(widget, "canvas", None)
    if canvas is not None:
        canvas.mpl_connect("button_press_event",
                           lambda ev, k=key: _anchor_press(window, k, ev))
        canvas.mpl_connect("button_release_event",
                           lambda ev, k=key: _anchor_release(window, k, ev))
    return widget


def _build_2d_widget(window: QMainWindow, key: str) -> QWidget:
    """2D 面板内容：同 1D 骨架，无悬停取点、无范围写回（像素轴
    没有 2θ/纵轴参数语义）。"""
    return _build_canvas_panel(window, key, "axes_2d", hover=False, sync="")


def _build_profile_widget(window: QMainWindow, key: str) -> QWidget:
    """剖面面板内容：同 1D 骨架，只写回纵轴（x = 像素距离）。"""
    return _build_canvas_panel(window, key, "axes_profile",
                               hover=True, sync="y")


def _build_waterfall_widget(window: QMainWindow, key: str) -> QWidget:
    """瀑布面板内容：同 1D 骨架，无范围写回（行偏移由数据决定）。"""
    return _build_canvas_panel(window, key, "axes_waterfall",
                               hover=True, sync="")


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


def _hover_label(dock, name: str, x: float, y: float) -> str:
    """悬停读数文本：坐标名按视图走。

    剖面 x = 到束心的像素距离；瀑布 y = 堆叠后的强度（已加行偏移）。
    其余（1D/对比）维持 2θ/强度。
    """
    view = dock.panel_key.split("|", 1)[0]
    if view == "剖面":
        return f"{name}　距离 = {x:.4g} px, 强度 = {y:.4g}"
    if view == "瀑布":
        return f"{name}　2θ = {x:.4g}°, 堆叠强度 = {y:.4g}"
    return f"{name}　2θ = {x:.4g}°, 强度 = {y:.4g}"


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
    # 只在数据曲线上选线：背景扣除的原始曲线/基线/锚点标记不参与，
    # 否则悬停点会在"扣除后曲线"和"原始曲线"之间跳
    lines = [ln for ln in _data_lines(ax) if len(ln.get_xdata()) > 1]
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
        # 归入辅助线：它是标记不是曲线，快照/样式回填/选线都要跳过它
        marker.set_gid(_AUX_GID_PREFIX + "hover")
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
    window.coord_label.setText(_hover_label(dock, name, x, y))


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


# 视图注册表：视图名 → 内容 builder（签名 window/key → QWidget）。
# 新视图接线 = 加条目，分发骨架不动（热图不占 VIEW_NAMES 工具栏
# 按钮：它是多文件 → 一张面板，走 _plot_heatmap，同 [对比]）


def _build_heat_widget(window: QMainWindow, key: str) -> QWidget:
    """热图面板内容：同 1D 骨架，无悬停取点、无范围写回（y 轴 =
    样品序号，不是强度；缩放/平移仍是通用的，只看图）。额外挂
    行点击联动：点某行 = 该样品在含它的对比面板里隐藏/显示切换
    （任务六：热图与多曲线叠加配合使用）。"""
    # 行点击的处理在 plot_compare（热图的数据与交互都在那边），面板壳
    # 在本模块——模块级导入会成环，函数内延迟导入（同 _build_1d_widget）
    from xrd_toolkit.gui.plot_compare import (_heat_row_press,
                                              _heat_row_release)
    widget = _build_canvas_panel(window, key, "axes_heat", hover=False,
                                 sync="")
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
