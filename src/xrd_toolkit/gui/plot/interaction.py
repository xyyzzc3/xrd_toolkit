"""画布交互层：平移、滚轮缩放、框选放大、悬停取点、轴同步、Home。

2026-10-21 从 plot_panels.py 原样搬出（只改结构，不动逻辑）。plot_panels
再导出，plot_views 与测试照旧经 gui_plot_panels 取用。
"""
import numpy as np
from matplotlib.patches import Rectangle
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui.panel_state import (
    CALIB_HOVER_KEY, _AUX_GID_PREFIX, _content, _panel_param, _param_box_set)
from xrd_toolkit.gui.panels import _apply_area_zoom
from xrd_toolkit.gui.plot.canvas import (
    _blit_box, _blit_take, _data_lines, _panel_axes)

BOX_MIN_PX = 6     # 框选放大：拖出来的框小于这个尺寸（像素）= 误碰，当点击

# x 轴是 2θ 的视图：范围写进"视图 2θ 范围"语义键（热图 2026-10-08 晚起
# 是它自己的一对"热图视图 2θ …"，对比与 1D 仍用老键；见回调里的分派）。
# 其余视图（2D / 剖面 / 瀑布）的 x 是**像素**，没有 2θ 语义，
# 走通用键"视图 x 范围"（见下面的两个回调与 _apply_plain_view）。
_TTH_X_VIEWS = ("1D", "对比", "热图")
# y 轴是**强度**的视图（剖面也是）：范围写进"纵轴自动/上下限"。
# 热图的 y 是样品行、2D 与瀑布是像素/行偏移 → 走通用键"视图 y 范围"。
_INTENSITY_Y_VIEWS = ("1D", "对比", "剖面")


def _home_key_reset(window: QMainWindow) -> None:
    """Home 键：把"你正在看的这张图"复位到**生成它时**的视图。

    复用的就是面板标题栏那个 [Home]（`_reset_view` → `dock.view_home`），
    所以"最初的样子"的定义只有一处，键盘与按钮不会各说各话。

    认哪张图：鼠标最后停留过的面板，没有就退回高亮面板。鼠标停在**校准
    图**上时（哨兵 CALIB_HOVER_KEY，2026-10-14 用户："校准图添加 home
    键，还是回到最初的视图"）转给校准视野的复位——校准图不进 plot_docks，
    复位的是它自己的那条线（calib_panel.reset_calib_view）。
    作用范围由 app.create_window 里那个 QShortcut 限定：焦点在输入框里时
    Home 根本到不了这里（QLineEdit 会把 Home 吃掉变成"光标到行首"），
    所以在数值框里打字不会突然被复位。
    """
    docks = getattr(window, "plot_docks", {}) or {}
    key = getattr(window, "_hover_panel_key", None)
    if key == CALIB_HOVER_KEY:
        from xrd_toolkit.gui.calib_panel import reset_calib_view
        reset_calib_view(window)
        return
    if key not in docks:
        key = getattr(window, "focus_panel", None)
    dock = docks.get(key)
    if dock is None:
        return
    toolbar = getattr(_content(dock), "toolbar", None)
    action = getattr(toolbar, "_actions", {}).get("home")
    if action is not None:
        action.trigger()

def _refresh_home(dock, ax=None, mode_y=False, mode_ylim=None):
    """程序自己重画后：维护这张面板的"家"视图（`dock.view_home`）。

    面板自己那条 [Home] 不跟任何历史栈（程序重画会把栈清掉，按下去常常
    "没反应"），用的是 `dock.view_home`，规则一句话：
    **手势改的视图不算"家"**。
      - 手势（滚轮/框选/平移）把范围写进了参数快照（那是"两处入口一套
        真相"的既定设计），所以不能拿参数当"家"——三个手势处理器都置
        `dock._view_from_gesture`，本次重画就不更新"家"；
      - 其余的重画（首画、重算、[应用] 改显示参数、换文件/几何）都更新
        "家"：那才是"这张图本来的样子"。
    于是 Home 永远回得到最初（用户 2026-09-24：回到最初的样子，而不是
    上次画的位置）。

    **`mode_y=True`（2026-10-09 加）**：这次画的 y 是**按当前显示模式算
    出来的**（错开行高——每行的 y 含义都换了），家的 y 必须跟着换，否则
    按 Home 恢复的是"没叠放时"的旧 y 窗口、把上半截裁掉（用户："对比
    子窗口的 home 键在先放大后开启堆叠后失效"——实测 8 条：数据到 6165、
    Home 后只到 1045，可见 17%）。只换 y，**x 仍用家的 x**（手势缩出来
    的 x 不算家，这条不变）。y 是模式算的，与"这次重画是不是手势来的"
    无关，所以 mode_y 分支不理会 from_gesture 旗标。
    2026-10-15 起这分支只服务**瀑布**（行偏移轴，永久"堆叠"）；对比的
    堆叠模式改用**两份家**（`dock.view_home_stack`，见 plot_compare.
    _redraw_compare）——一份家的做法取消堆叠时会留在堆叠坐标上（用户
    当天："堆叠后取消堆叠会导致视图不能回来"）。

    **`mode_ylim`（2026-10-13 加）**：mode_y 分支记的 y 改从它取——传的是
    "钉住缩放窗口**之前**"的自然量程。以前直接取 `ax.get_ylim()`，一旦
    缩放窗口已套上，家的 y 就变成缩放窗口本身（瀑布"先缩放后调行距"的
    拍尾巴：窗口会被封成家，Home 也回不去）。不传仍取当前 ylim，旧行为
    不变。

    旧版这里还顺手刷一下 mpl 工具栏的按钮状态、清它的历史栈——面板工具栏
    换成自绘的 _SlimToolbar（自己拿四个 QAction）之后，那套机件不在场了。
    """
    from_gesture = getattr(dock, "_view_from_gesture", False)
    dock._view_from_gesture = False
    if ax is None:
        return
    home = getattr(dock, "view_home", None)
    if mode_y and home is not None:
        y = tuple(mode_ylim) if mode_ylim is not None else tuple(ax.get_ylim())
        dock.view_home = (home[0], y, ax.get_yscale())
        return
    if not from_gesture:
        dock.view_home = (tuple(ax.get_xlim()), tuple(ax.get_ylim()),
                          ax.get_yscale())

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
    _blit_box(dock, ax, box)   # 拖动期只贴选框（见 _blit_take）

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
        dock._blit = _blit_take(dock, event.inaxes)   # 拖动期贴位图
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
    dock._view_from_gesture = True   # 手势视图不算"家"（见 _refresh_home）
    ax.figure.canvas.draw_idle()     # 平移每次移动整帧重画（原因见 _pan_press）

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
            dock._blit = None
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
            dock._view_from_gesture = True   # 框选不算"家"（见 _refresh_home）
        dock._blit = None
        ax.figure.canvas.draw_idle()   # 松手：整帧重画（撤选框 + 刻度更新）
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
    dock._view_from_gesture = True   # 滚轮缩放不算"家"（见 _refresh_home）
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
    view = key.split("|", 1)[0]
    if view in _TTH_X_VIEWS:
        # 热图 2026-10-08 晚起有**自己的一对键**（用户："对比与热图分开
        # 管理"）：热图上缩放/平移只写「热图视图 2θ」，对比与 1D 共用老
        # 那对（1D 页不摆框；两个框只做展示——焦点是谁就显示谁）
        if view == "热图":
            lo_key, hi_key = "热图视图 2θ 下限 (°)", "热图视图 2θ 上限 (°)"
        else:
            lo_key, hi_key = "视图 2θ 下限 (°)", "视图 2θ 上限 (°)"
        snap[lo_key] = float(xlo)
        snap[hi_key] = float(xhi)
        if window.plot_docks.get(window.focus_panel) is dock:
            _param_box_set(window, lo_key, xlo)
            _param_box_set(window, hi_key, xhi)
    else:
        # 像素轴（2D / 剖面 / 瀑布）：没有 2θ 语义，但"我缩到哪了"也得留住
        # ——否则下一次重画按数据自动缩放，缩放白做（用户 2026-09-28：
        # "别的图的放大检查一下"）
        snap["视图 x 范围"] = [float(xlo), float(xhi)]

def _on_ylim_changed(window: QMainWindow, key: str, ax) -> None:
    """y 范围被改动 → 写回纵轴窗口 + 关掉纵轴自动。

    用户手动定过的纵轴窗口由用户接管（自动不再覆盖）；纯 x 方向
    的缩放/平移不碰这里——只有真的动了纵轴才关自动。焦点面板
    同时刷新参数坞控件；程序自己画图（_setting_limits）跳过。

    **堆叠中的对比面板走自己的视图键**（2026-10-15）：堆叠的 y 是
    "第几行"（行偏移），不是强度——窗口记进「堆叠视图 y 范围」，
    不碰「纵轴自动/上下限」（那是平铺的设置）。用户当天："堆叠后
    取消堆叠会导致视图不能回来"——此前这份堆叠坐标的窗口冒充平铺
    设置，取消堆叠时被原样套用（真数据下画面直接空白/贴底）。
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
    view = key.split("|", 1)[0]
    if view == "对比" and _panel_param(window, dock, "对比堆叠", False):
        # 堆叠：视图窗口一类（同"视图 y 范围"），平铺的设置原封不动
        snap["堆叠视图 y 范围"] = [float(ylo), float(yhi)]
    elif view in _INTENSITY_Y_VIEWS:
        snap["纵轴自动"] = False
        snap["纵轴下限"] = float(ylo)
        snap["纵轴上限"] = float(yhi)
        if window.plot_docks.get(window.focus_panel) is dock:
            # 挂 _param_box_sync 旗标：这几个框的"即改即画"钩子
            # （plot_views._refresh_1d_display）必须把这次程序同步当成
            # 非用户改动跳过——否则框的 1 位小数会把缩放值四舍五入
            # （1.02→1.0）、再经 _display_snapshot 写回快照，连续缩放
            # 几步就卡住（2026-10-07 全量套件三条缩放/Home 测试当场红）。
            # setChecked(False) 触发 sync_ylim → 上下限框解除置灰
            prev = getattr(window, "_param_box_sync", False)
            window._param_box_sync = True
            try:
                window.params["纵轴自动"].setChecked(False)
                window.params["纵轴下限"].setValue(ylo)
                window.params["纵轴上限"].setValue(yhi)
            finally:
                window._param_box_sync = prev
    else:
        # 画像/行号轴（2D 像素、热图样品行、瀑布行偏移）：同上，走通用键。
        # 往"纵轴上下限"写会把**强度**的语义（默认 1.0/100000.0）串进来
        snap["视图 y 范围"] = [float(ylo), float(yhi)]

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
        # 按容器上挂着的轴属性找（1D/2D/剖面/瀑布/热图各不相同）——
        # 写死 axes_1d 会在热图上直接 AttributeError（2026-09-28 踩到）
        ax = _panel_axes(dock)
        if ax is None:
            return
    if sync_x:
        ax.callbacks.connect("xlim_changed",
                             lambda a, k=key: _on_xlim_changed(window, k, a))
    ax.callbacks.connect("ylim_changed",
                         lambda a, k=key: _on_ylim_changed(window, k, a))

def _hover_label(dock, name: str, x: float, y: float) -> str:
    """悬停读数文本：坐标名按视图走。

    剖面 x = 到束心的像素距离；瀑布 y = 堆叠后的强度（已加行偏移）。
    其余（1D/对比）维持 2θ/强度。

    **name 为空就不带"某某："前缀**（2026-10-07 用户："坐标现在显示不完全，
    框变大到能完全展示"）：单曲线面板（1D/剖面）的曲线没有自己的名字，
    原来回退成面板标题（"1D_LMFP_…tif：2θ …"），前缀把读数拉长到 ~400px、
    框里只能省略——而那条曲线就是眼前这张图，前缀是废话。对比（多条曲线）
    与瀑布（各扇区）的曲线自带标签（文件名 / χ），照旧带着认曲线。
    """
    view = dock.panel_key.split("|", 1)[0]
    prefix = f"{name}：" if name else ""
    if view == "剖面":
        return f"{prefix}距离 {x:.4g} px，强度 {y:.4g}"
    text = f"{prefix}2θ {x:.4g}°，强度 {y:.4g}"
    if view == "1D":
        # 密集区标签可能没地方画：鼠标靠近某根峰时把它的 FWHM 报出来
        # （2026-10-24 讨论：静态标签放不下就靠悬停/峰表补全）
        peaks = getattr(dock, "detected_peaks", None) or []
        if peaks:
            near = min(peaks, key=lambda p: abs(p.center - x))
            if abs(near.center - x) <= max(5.0 * near.fwhm, 0.05):
                text += f"；峰 {near.center:.3f}°，FWHM {near.fwhm:.4f}°"
    return text

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
    if (getattr(dock, "_pan_start", None) is not None
            or getattr(dock, "_box_start", None) is not None):
        # 正在按住左键平移/框选：悬停点退场，别乱跳（也省掉一次整帧重绘）
        _hover_leave(window, key)
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
    # 悬停只挪一个点、坐标范围不变 → 和框选一样贴图：起手缓存一帧
    # （一次整帧重绘 ~30 ms），之后每次移动只贴"背景 + 圆点"（~1 ms）。
    # 用户反馈"图一多就很卡"里，鼠标移动是最频繁的动作，整帧重绘一次
    # 30 ms 在 60 Hz 鼠标流下会把主线程占满（见 _blit_take）。
    if not getattr(dock, "_blit", None):
        dock._blit = _blit_take(dock, ax)
    _blit_box(dock, ax, marker)
    # 坐标前缀 = 曲线名（对比图 = 文件名）；1D 没设图例名时
    # matplotlib 会默认给 _childN，不算数 → 视作"没有名字"（读数不带前缀，
    # 见 _hover_label 的说明；单曲线面板的曲线本来就没有标签）
    name = line.get_label()
    if not name or name.startswith("_child"):
        name = ""
    window.coord_label.setText(_hover_label(dock, name, x, y))

def _hover_leave(window: QMainWindow, key: str, event=None) -> None:
    """鼠标离开曲线/坐标轴：藏起圆点、清空坐标标签（常驻标签留空）。"""
    dock = window.plot_docks.get(key)
    if dock is not None:
        marker = getattr(dock, "hover_marker", None)
        if marker is not None:
            was_visible = marker.get_visible()
            marker.set_data([], [])
            marker.set_visible(False)
            if was_visible and marker.axes is not None:
                # ax.clear() 后标记已与轴断开；只有真的画过才需要擦
                # （贴回背景 = 擦掉圆点，同 _hover_motion 的贴图路径）
                _blit_box(dock, marker.axes, marker)
    window.coord_label.setText("")

