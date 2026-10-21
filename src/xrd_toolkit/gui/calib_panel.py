"""中央校准图面板：图像 + 理论环（青线）+ 选点交互。

从 calib.py 拆出来（纯搬迁）：只管「那张图怎么画、点上去算什么」。
"""
from pathlib import Path

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import (QHBoxLayout, QMainWindow, QMdiSubWindow,
                               QPushButton, QVBoxLayout, QWidget)

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.calib_model import (
    PANEL_SCALE, _calib_state, _geom_px_keys)
from xrd_toolkit.gui.panels import _install_resize_grip, _settle
from xrd_toolkit.gui.panel_state import _collect_geometry, _log
from xrd_toolkit.gui.tasks import user_error_text
from xrd_toolkit.services.calibrants import calibrant_short_label
from xrd_toolkit.services.data_loader import load_diffraction_image
# 绘制 / 交互（2026-10-22 从本文件原样搬出；再导出，外部照旧经
# gui_calib_panel 取用）
from xrd_toolkit.gui.calib_panel_draw import (
    _add_calib_marker, _cache_calib_bg, _calib_zoom_on, _draw_calib_image,
    _fit_view_to_rings, _new_marker_artists, _rebuild_calib_markers,
    _redraw_calib, _redraw_calib_if_open, _refresh_calib_markers,
    _set_calib_zoom, _set_fit_rings, _warn_rings_off_image,
    reset_calib_view)
from xrd_toolkit.gui.calib_panel_interaction import (
    _apply_selected_ring, _blit_calib_box, _clear_calib_points,
    _draw_calib_box, _normalize_selection, _on_calib_click, _on_calib_motion,
    _on_calib_press, _on_calib_release, _on_calib_scroll, _select_nearest,
    _undo_calib_point)

class _CalibSubWindow(QMdiSubWindow):
    """校准图子窗口：× 关闭 = 校准状态全清（关闭即遗忘）。

    不进 plot_docks（不掺和编辑对象焦点/平铺/总缩放），关闭走自己
    的 _close_calib_panel。
    """

    def __init__(self, window: QMainWindow, key: str):
        super().__init__()
        self._window = window
        self.panel_key = key

    def closeEvent(self, event):
        _close_calib_panel(self._window)
        super().closeEvent(event)


def _load_image(path):
    """读一张图（标样/校准用）：**面板与后台 worker 共用这一个入口**。

    为什么收成一个入口：拆分后若 worker 也各持一份
    load_diffraction_image 引用，测试就得同时罩多个模块（Python 的
    patch 只影响"被引用处"那个名字）；统一走这里后罩住本模块一处即可。
    """
    return load_diffraction_image(str(path))


def _calib_standard_path(window: QMainWindow):
    """标样 = 勾选的第一个**原始数据**文件（列表顺序第一个对号条目）。

    产物条目跳过：校准要的是原始图像（要读像素、找环心），产物是 1D
    曲线——勾了产物不算数（用户 2026-09-25 起文件栏里有这种条目）。
    返回 Path 或 None（没有可用的）。
    """
    for src in gui_sources.checked_sources(window):
        if src.kind == gui_sources.RAW:
            return Path(src.path)
    return None


def _calib_image(window: QMainWindow, path: Path):
    """读标样图像（走 window._image_cache，同一文件反复点不重复解码）。"""
    cache = window._image_cache
    image = cache.get(str(path))
    if image is None:
        image = load_diffraction_image(str(path))
        cache[str(path)] = image
        while len(cache) > 3:   # 上限 3 张（与 _apply_auto_contrast 同规则）
            cache.pop(next(iter(cache)))
    return image


def _calib_draw_geometry(window: QMainWindow) -> dict:
    """画图几何（统一 px 键）：**当前配置**的几何。

    图上那圈青线画的就是"当前配置"预测的环——A/B 只用于对比、不动图；
    采纳（谁拟合得好）、[编辑…] 手改、借用条目/导入 .poni 都会换图
    （每个出口自己调 _redraw_calib_if_open，改完即重画）。没设定当前
    配置时退回分析配置。
    """
    state = _calib_state(window)
    geom = state.get("current_geom")
    if geom is None:
        return _geom_px_keys(_collect_geometry(window))
    return _geom_px_keys(geom)


def _open_calib_panel(window: QMainWindow, path: Path) -> None:
    """开（或复用）中央校准图面板：imshow + 理论环，接鼠标点击。

    已开同一文件的面板 → 前置返回；换文件 → 旧面板关闭（即遗忘）
    再开新的。图像在界面线程读一次（进 _image_cache），之后校准
    完成的重画都从缓存拿。
    """
    from xrd_toolkit.gui.calib import (
        _calib_sync, _ensure_current, _init_calibrant_for_path,
        _refresh_current_metrics, _reset_calib_form,
        _update_ring_count)   # 破循环：见模块说明
    key = f"校准|{path}"
    if getattr(window, "calib_dock", None) is not None:
        if window.calib_key == key:
            window.calib_dock.raise_()
            return
        _close_calib_panel(window)
    try:
        image = _calib_image(window, path)
    except Exception as err:
        _log(window, f"校准面板：读取 {path.name} 失败（{user_error_text(err)}）")
        return
    window.calib_gen = getattr(window, "calib_gen", 0) + 1   # 新面板新代
    _calib_state(window)   # 状态从空白起步（关闭即遗忘）
    _init_calibrant_for_path(window, path)   # 按文件名/条目预选标样
    _reset_calib_form(window)

    sub = _CalibSubWindow(window, key)
    sub.setObjectName("calib_panel")
    window.mdi.addSubWindow(sub)
    content = QWidget()
    lay = QVBoxLayout(content)
    lay.setContentsMargins(0, 0, 0, 0)
    h, w = image.shape
    scale = min(1.0, PANEL_SCALE / max(h, w))
    cw, ch = max(200, round(w * scale)), max(200, round(h * scale))
    fig = Figure(figsize=(cw / 100.0, ch / 100.0), dpi=100)
    canvas = FigureCanvasQTAgg(fig)
    # 视野开关：[看环全貌] 勾上才把视野放大到包住全部理论环。默认关
    # ——图像是这张图上唯一不动的参照系（见 _draw_calib_image 的视野锁）；
    # 环跑到图像外时是它主动放大的唯一入口（2026-09-26 晚用户定）。
    # 新面板新起一轮：开关复位成关（关闭即遗忘，同选点/结果）
    window.calib_fit_rings = False
    chk_fit = QPushButton("看环全貌")
    chk_fit.setObjectName("calib_fit_rings")
    chk_fit.setCheckable(True)
    chk_fit.setToolTip("把视野放大到包住全部理论环（默认关：图像始终是"
                       "那一框、不随几何变；环全跑到图像外时勾上它，"
                       "才看得到环在哪儿）。翻它 = 换回这种视野；"
                       "[放大镜] 点亮后可框选放大 / 滚轮缩放")
    chk_fit.toggled.connect(lambda on: _set_fit_rings(window, on))
    fit_row = QHBoxLayout()
    fit_row.setContentsMargins(4, 2, 4, 2)
    fit_row.addWidget(chk_fit)
    # 放大镜（2026-10-13 用户："校准图添加放大功能"）：与普通图面板同一个
    # 姿势——点亮 = 左键拖框选放大、滚轮以光标为中心缩放；未点亮 = 点图
    # 选点（一点即记，老行为）。状态记 window.calib_zoom_on；视野记
    # window.calib_view（手势写，整幅重画时优先回放——不然换几何重画会
    # 把缩放弹回图像框）。新面板新起一轮：开关复位成关。
    window.calib_zoom_on = False
    window.calib_view = None
    chk_zoom = QPushButton("放大镜")
    chk_zoom.setObjectName("calib_zoom_btn")
    chk_zoom.setCheckable(True)
    chk_zoom.setToolTip("<b>点亮</b> = 左键拖 = 框选放大、滚轮 = 以光标为"
                        "中心缩放、左键点 = 选点；<b>未点亮</b> = 点图选点"
                        "（同现在）。放大过头了按 [复位视图]（或 Home 键）"
                        "回到最初的视野")
    chk_zoom.toggled.connect(lambda on: _set_calib_zoom(window, on))
    fit_row.addWidget(chk_zoom)
    # [复位视图]（用户 2026-10-14："校准没有 home 键"——别家面板标题栏
    # 都有这一颗，校准图此前只能翻 [看环全貌] 才出得来，而那是**模式**
    # 不是复位）。与 Home 键同一个落点：reset_calib_view 只清手势缩放；
    # 本来就是最初的视野时按了不动作（同 Home 键，也同普通面板的语义）
    btn_home = QPushButton("复位视图")
    btn_home.setObjectName("calib_home_btn")
    btn_home.setToolTip("回到最初的视野（清掉 [放大镜] 的框选/滚轮缩放；"
                        "Home 键同效）。[看环全貌] 是视野模式，这个按钮不翻它")
    btn_home.clicked.connect(lambda: reset_calib_view(window))
    fit_row.addWidget(btn_home)
    # [保存图片…]（2026-10-10 用户："校准的图保存不了"——这张面板此前
    # 压根没有保存入口：面板顶栏那套按钮只装在普通图面板上）。复用
    # plot_panels._save_panel 的整套流程（分辨率/格式弹窗 + 默认名 +
    # 防覆盖），容器用取用函数传（校准面板不进 plot_docks）。惰性
    # import：calib_panel ↔ plot_panels 别在模块层绕成环。
    btn_save = QPushButton("保存图片…")
    btn_save.setObjectName("calib_save_btn")
    btn_save.setToolTip("把校准图另存为图片（对话框与普通图面板同一个）")

    def _do_save():
        from xrd_toolkit.gui.plot_panels import _save_panel   # 破循环
        _save_panel(window, key,
                    get_dock=lambda: getattr(window, "calib_dock", None))

    btn_save.clicked.connect(lambda: _do_save())
    fit_row.addWidget(btn_save)
    fit_row.addStretch(1)
    lay.addLayout(fit_row)
    lay.addWidget(canvas)
    # 右下角把手（▙）+ 四边/四角抓取带：与 1D 图同一套抓手（用户 2026-10-01：
    # "校准时二维图……没有 1D 图右下角那个标准的缩放，加上这个"）。两处自己给：
    #   ① 容器取用函数——校准面板不进 plot_docks（它有自己的 window.calib_dock，
    #      key 也只是个字符串标签），抓手默认那条路查不到它；
    #   ② 内容上补挂 .canvas——手抓要装在真正的鼠标落点（画布）上，QWidget 的
    #      父过滤器收不到子部件事件（见 _install_resize_grip 的说明）。
    content.canvas = canvas
    content.figure = fig      # _save_panel 读的就是内容上的 .figure（同普通面板）
    _install_resize_grip(window, key, content,
                         get_dock=lambda: getattr(window, "calib_dock", None))
    sub.setWidget(content)
    sub.setWindowTitle(f"校准_{path.name}")
    window.calib_dock = sub
    window.calib_key = key
    window.calib_path = path
    window.calib_display = path.name
    window.calib_canvas = canvas
    window.calib_ax = fig.add_subplot(111)
    # 鼠标事件（mpl_connect 事件模式；面板销毁时回调随画布一起失效，
    # 无需显式断开）：按下/拖动/松开/滚轮四个——按下分流"选点"与
    # "框选放大"（见 _on_calib_press 的分工说明），滚轮只在放大镜
    # 点亮时缩放。
    canvas.mpl_connect(
        "button_press_event", lambda ev: _on_calib_press(window, key, ev))
    canvas.mpl_connect(
        "motion_notify_event", lambda ev: _on_calib_motion(window, key, ev))
    canvas.mpl_connect(
        "button_release_event", lambda ev: _on_calib_release(window, key, ev))
    canvas.mpl_connect(
        "scroll_event", lambda ev: _on_calib_scroll(window, key, ev))
    # 整幅画完就拍一帧当贴图底图（选点走增量贴图，见 _add_calib_marker）
    window.calib_bg = None
    canvas.mpl_connect("draw_event", lambda ev: _cache_calib_bg(window, ev))
    # 显式 resize + 级联摆位（同 _open_plot_panel 套路：子窗口不会
    # 自动适配内容）；不进 plot_docks，级联只数已开的图面板数
    hint = sub.sizeHint()
    sub.resize(max(60, hint.width()), max(40, hint.height()))
    n = sum(1 for d in window.plot_docks.values()
            if isinstance(d, QMdiSubWindow)) + 1
    off = 16 + 24 * ((n - 1) % 6)
    sub.move(off, off)
    sub.show()
    _settle(window)
    # 新面板 = 新一轮：当前配置从分析页选中的条目借起（登记成"原始"），
    # 并异步算它的环位偏差（表里那一格先显示 —）。**借完再画**：反过来
    # 的话第一帧画的是"还没借"的几何，青线与表里的"当前配置"不是同一份。
    # 借用内部已按借到的几何画了一帧，下面这画是兜底——注册表里没有那条
    # 条目时借不出来（current_geom 仍是 None），也不该留一块空图
    _ensure_current(window)
    _update_ring_count(window, force_auto=True)
    _draw_calib_image(window, key, image, _calib_draw_geometry(window))
    state = _calib_state(window)
    _log(window, f"打开校准面板：{path.name}（标样 "
                 f"{calibrant_short_label(state['calibrant'])}，"
                 f"{state['n_rings']} 环；点衍射环即可选点）")
    _calib_sync(window)
    _refresh_current_metrics(window)


def _close_calib_panel(window: QMainWindow) -> None:
    """关闭校准图面板：状态全清（关闭即遗忘）+ 在飞结果作废。

    幂等：没开面板直接返回。退出校准模式也走这里。
    """
    from xrd_toolkit.gui.calib import (_calib_sync, _ensure_current, _refresh_current_metrics, _reset_calib_form)   # 破循环：见本模块说明
    dock = getattr(window, "calib_dock", None)
    if dock is None:
        return
    window.calib_dock = None
    window.calib_key = None
    window.calib_gen = getattr(window, "calib_gen", 0) + 1   # 迟到结果作废
    # 视野开关随面板一起弃：下次开面板回到"锁定图像"（关闭即遗忘）。
    # 控件跟着子窗口销毁，只清状态位（读悬空控件会 RuntimeError）
    window.calib_fit_rings = False
    window.calib_zoom_on = False
    window.calib_view = None
    # 框选可能正拖到一半就被关掉：把拖动期的临时态清干净
    for attr in ("calib_box_start", "calib_box_axes", "calib_box_event",
                 "calib_box_patch", "calib_drag_bg"):
        if hasattr(window, attr):
            setattr(window, attr, None)
    # 选点/结果全清（关闭即遗忘）：删属性让 _calib_state 下次懒重建，
    # 否则重开面板会带出旧点标记和旧结果几何
    if hasattr(window, "calib_state"):
        del window.calib_state
    _reset_calib_form(window)
    # 抓手可能还挂着方向覆盖光标（关面板时鼠标正停在抓取区上，来不及收
    # Leave）：主动撤销，别把斜向箭头留下（与 panels._close_panel 同一条）
    content = dock.widget()
    grip_filter = getattr(content, "_grip_filter", None) if content else None
    if grip_filter is not None:
        grip_filter._release_cursor()
    if dock in window.mdi.subWindowList():
        window.mdi.removeSubWindow(dock)
    dock.deleteLater()
    _log(window, "已关闭校准面板")
