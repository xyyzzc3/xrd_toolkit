"""中央校准图面板：图像 + 理论环（青线）+ 选点交互。

从 calib.py 拆出来（纯搬迁）：只管「那张图怎么画、点上去算什么」。
"""
from pathlib import Path

import numpy as np
from matplotlib.backend_bases import MouseButton
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import LogNorm
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QInputDialog, QMainWindow, QMdiSubWindow,
                               QPushButton, QVBoxLayout, QWidget)

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.calib_model import (
    CP_COLOR, PANEL_SCALE, RING_COLOR, SNAP_TOL_DEG,
    _calib_state, _geom_px_keys, _metrics_dev, _result_by_name, _slot_label)
from xrd_toolkit.gui.panels import _install_resize_grip, _settle
from xrd_toolkit.gui.panel_state import (_auto_contrast_values,
                                         _collect_geometry, _log)
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.services.integrator import (snap_lab6_ring,
                                             theoretical_ring_paths)


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
    from xrd_toolkit.gui.calib import (_calib_sync, _ensure_current, _refresh_current_metrics,
        _reset_calib_form)   # 破循环：见模块说明
    key = f"校准|{path}"
    if getattr(window, "calib_dock", None) is not None:
        if window.calib_key == key:
            window.calib_dock.raise_()
            return
        _close_calib_panel(window)
    try:
        image = _calib_image(window, path)
    except Exception as err:
        _log(window, f"校准面板：读取 {path.name} 失败（{err}）")
        return
    window.calib_gen = getattr(window, "calib_gen", 0) + 1   # 新面板新代
    _calib_state(window)   # 状态从空白起步（关闭即遗忘）
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
                       "才看得到环在哪儿）")
    chk_fit.toggled.connect(lambda on: _set_fit_rings(window, on))
    fit_row = QHBoxLayout()
    fit_row.setContentsMargins(4, 2, 4, 2)
    fit_row.addWidget(chk_fit)
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
    # 只接鼠标点击：点环 = 选点（mpl_connect 事件模式）。面板销毁
    # 时回调随画布一起失效，无需显式断开
    canvas.mpl_connect(
        "button_press_event", lambda ev: _on_calib_click(window, key, ev))
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
    _draw_calib_image(window, key, image, _calib_draw_geometry(window))
    _log(window, f"打开校准面板：{path.name}（点击衍射环选点）")
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


def _draw_calib_image(window: QMainWindow, key: str, image, geometry,
                      control_points=None, ring_marks=None) -> None:
    """整幅重画校准图：图像 + 理论环路径 + 可选绿点/用户点标记。

    对齐 scripts/view_diffraction.py 的显示：magma + LogNorm、自动
    对比度 1%/99.9% 分位、vmin 下限 1.0、origin="lower"。理论环用
    theoretical_ring_paths 精确反解，不是"圆心 + 半径"的正圆：探测
    器有倾斜时环是椭圆、公共圆心是直射束落点而非 PONI，写正圆会整体
    偏 8~23 px（实测本数据）。用户点 = 青圈 + 环号（点图找环的依据），
    控制点 = 绿点（pyFAI 实际取点，验证精修效果）。

    几何把环全推出图像时（距离/像素/波长填错、校准跑出离谱解）不静默
    画一堆看不见的线，而是红字 + 日志说清楚原因（_warn_rings_off_image）
    ——**视野不动**：图像是这张图上唯一不动的参照系（要放大到看得见环，
    得自己勾面板上的 [看环全貌]，见 _set_fit_rings / _fit_view_to_rings）。
    """
    ax = window.calib_ax
    ax.clear()
    h, w = image.shape
    lo, hi = _auto_contrast_values(image)
    vmin = max(1.0, lo)
    vmax = max(hi, vmin * 10.0)
    ax.imshow(image, cmap="magma", norm=LogNorm(vmin=vmin, vmax=vmax),
              origin="lower")
    ax.set_aspect("equal")
    paths = theoretical_ring_paths(
        pixel_size_m=geometry["pixel_size_m"],
        wavelength_m=geometry["wavelength_m"],
        dist_m=geometry["dist_m"], poni1_px=geometry["poni1_px"],
        poni2_px=geometry["poni2_px"], rot1_deg=geometry["rot1_deg"],
        rot2_deg=geometry["rot2_deg"], image_shape=image.shape)
    for ring, xy in paths["rings"]:
        ax.plot(xy[:, 0], xy[:, 1], color=RING_COLOR, lw=0.7, alpha=0.65)
        # 环号标在"路径上、落在图像内、最靠右"的点（标到图外看不见）
        inside = ((xy[:, 0] >= 0) & (xy[:, 0] < w)
                  & (xy[:, 1] >= 0) & (xy[:, 1] < h))
        cand = np.flatnonzero(inside)
        if cand.size:
            j = cand[int(np.argmax(xy[cand, 0]))]
            ax.annotate(str(ring), (xy[j, 0], xy[j, 1]), color=RING_COLOR,
                        fontsize=7, va="center", ha="left")
    if control_points is not None and len(control_points):
        # 控制点可达数千个：抽稀到 1000 以内（绿点只是视觉验证）
        stride = max(1, len(control_points) // 1000)
        pts = np.asarray(control_points)[::stride]
        ax.plot(pts[:, 0], pts[:, 1], ".", color=CP_COLOR, ms=2.5)
    # 选点标记：整幅重画时按 state 重建，artist 记进 window.calib_marker_artists。
    # 它们是**不可见**的（见 _new_marker_artists）：整幅画里不出现，底图才是
    # 干净的；贴图（_refresh_calib_markers，由 draw_event 触发）时单独画上去
    window.calib_marker_artists = []
    if ring_marks:
        for x, y, ring in ring_marks:
            window.calib_marker_artists += _new_marker_artists(ax, x, y, ring)
    span = ("环半径 %.0f~%.0f px" % (paths["r_min_px"], paths["r_max_px"])
            if np.isfinite(paths["r_min_px"]) else "环半径：无解")
    ax.set_xlabel("横向 (px)")
    ax.set_ylabel("纵向 (px)")
    # 标题里写明"青环是谁画的"（用户 2026-09-30："加一个图上的[说明]……让用户
    # 知道结果就是当前用的数据"）：青环 = 当前配置（哪条结果 + 它现在的环位
    # 偏差）——图上看到的东西和"现在用的几何"从此对得上号
    state = _calib_state(window)
    dev = _metrics_dev(state.get("current_metrics"))
    dev_txt = f" · 环位偏差 {dev:.2f} px" if dev is not None else ""
    ax.set_title(f"{window.calib_display}  ·  青环 = 当前配置："
                 f"{_slot_label(state, 'current')}{dev_txt}  ·  {span}")
    if not paths["n_inside"]:
        _warn_rings_off_image(window, ax, image, paths, geometry)
    # 视野（必须在所有画线之后设）：默认锁死 = 图像那一框；面板上勾了
    # [看环全貌] 才放大到包住环。为什么不默认跟着环走——环画到图像外
    # 时 matplotlib 的自动缩放会把坐标范围撑大（实测像素填 20 µm：视野
    # 2048 → 21258 px，图像在画布上只剩 10% 宽），看着像"图动了/图被迫
    # 变小"（2026-09-26 晚用户报）。图像是这张图上唯一不动的参照系：
    # 环围着它动，跑到框外就老实被裁掉，由红字说明。
    # 范围取 imshow（origin="lower"、无 extent）给的那一框。
    if getattr(window, "calib_fit_rings", False):
        _fit_view_to_rings(ax, image, paths)
    else:
        ax.set_xlim(-0.5, w - 0.5)
        ax.set_ylim(-0.5, h - 0.5)
    window.calib_canvas.draw_idle()


def _set_fit_rings(window: QMainWindow, on: bool) -> None:
    """[看环全貌] 开关：翻状态 + 重画。

    状态记在 window.calib_fit_rings（布尔）而不是读控件：面板一关，
    控件就是悬空对象，读它 RuntimeError（同 calib_ax 那个坑）。
    """
    window.calib_fit_rings = bool(on)
    _log(window, "视野：看环全貌" if on else "视野：锁定图像（看环全貌已关）")
    _redraw_calib_if_open(window)


def _fit_view_to_rings(ax, image, paths) -> None:
    """把视野放大到包住全部环路径（[看环全貌] 勾上时才走这里）。

    环路径全推出图像时这是唯一能看到环在哪的办法（视野比图像大几十倍，
    图像会缩成画布中央一小块——那是这个开关的本意，不再是自动行为）。
    视野多留 5% 余量；一个有效点都没有（几何离谱到反解不出解）就不动。
    """
    h, w = image.shape
    xy = np.vstack([p for _, p in paths["rings"]])
    fin = np.isfinite(xy).all(axis=1)
    if not fin.any():
        return
    x0, x1 = float(xy[fin, 0].min()), float(xy[fin, 0].max())
    y0, y1 = float(xy[fin, 1].min()), float(xy[fin, 1].max())
    pad_x = 0.05 * max(x1 - x0, w)
    pad_y = 0.05 * max(y1 - y0, h)
    ax.set_xlim(min(0.0, x0) - pad_x, max(w, x1) + pad_x)
    ax.set_ylim(min(0.0, y0) - pad_y, max(h, y1) + pad_y)


def _warn_rings_off_image(window: QMainWindow, ax, image, paths,
                          geometry) -> None:
    """守卫：几何把理论环全推出图像时，红字 + 日志说清楚（**不动视野**）。

    静默什么都不显示是最坏的失败方式（用户只会觉得"校准没反应"），
    所以要在图上明说；但**不把视野放大到包住环**——2026-09-26 晚用户
    报"青环变得很大时图会被迫变小"：环跑到图像外时视野被撑到 11 倍宽
    （实测像素填 20 µm：视野 2048 → 22249 px），图像在画布上缩成一小块，
    看上去像"图动了/图变小了"，而真正发生变化的是青环。图像是这张图上
    唯一不动的参照系，编辑几何不该把它挪走；环跑到外面这件事，红字里
    连半径范围一起报出来（一眼看得出差几倍）就够了。
    日志按几何指纹去重（撤销/清空选点的重画不重复刷屏）。
    """
    h, w = image.shape
    head = (f"当前几何下 {len(paths['rings'])} 条环全部落在图像外"
            f"（环半径 {paths['r_min_px']:.0f}~{paths['r_max_px']:.0f} px）")
    if getattr(window, "calib_fit_rings", False):
        how = "视野已按 [看环全貌] 放大到看得见它们"
    else:
        how = "图上不会出现青线（想看环在哪：勾上面的 [看环全貌]）"
    tail = f"图像 {w}×{h} —— 请核对像素尺寸 / 波长 / 距离"
    # 折行写：默认视野锁在图像那一框（不放大），一行写不下会被右边缘裁掉
    ax.text(0.02, 0.98, "\n".join([f"⚠ {head}", how, tail]),
            transform=ax.transAxes, color="#ff6666", fontsize=8,
            va="top", ha="left")
    key = tuple(round(float(geometry[k]), 6) for k in sorted(geometry))
    if getattr(window, "_calib_span_warned", None) != key:
        window._calib_span_warned = key
        _log(window, f"{head}，{how}，{tail}")


def _new_marker_artists(ax, x, y, ring: int) -> list:
    """一个选点标记（青圈 + 环号）的 artist，**先设成不可见**。

    不可见是为了把它们**排除在整幅重画之外**：整幅画一次要给 2048² 图重过
    LogNorm（几百毫秒到十几秒），而标记只是几个圈——它们只在贴图时用
    `draw_artist` 单独画（draw_artist 不看可见性，见 _refresh_calib_markers）。
    这样底图永远是"干净的"（不含标记），撤销/清空只要贴回底图再画剩下的标记。
    """
    ln = ax.plot([x], [y], "o", mfc="none", mec=RING_COLOR, ms=9, mew=1.5)[0]
    txt = ax.annotate(str(ring), (x, y), color=RING_COLOR, fontsize=8,
                      va="bottom", ha="left")
    for art in (ln, txt):
        art.set_visible(False)
    return [ln, txt]


def _refresh_calib_markers(window: QMainWindow) -> None:
    """把选点标记贴到画布上：贴回底图 + 画所有标记 + 上屏（几毫秒）。

    没有底图（面板刚建、后端不支持贴图）就老实整幅画一次——draw_event 里
    会补拍底图，之后又回到贴图这条路。
    """
    ax = window.calib_ax
    canvas = window.calib_canvas
    arts = getattr(window, "calib_marker_artists", None) or []
    bg = getattr(window, "calib_bg", None)
    if bg is None or not hasattr(canvas, "restore_region") \
            or not hasattr(canvas, "blit"):
        canvas.draw_idle()
        return
    canvas.restore_region(bg)
    for art in arts:
        art.set_visible(True)       # draw_artist 不看可见性，但摆明了更省心
        ax.draw_artist(art)
        art.set_visible(False)
    canvas.blit(ax.figure.bbox)


def _rebuild_calib_markers(window: QMainWindow) -> None:
    """按 state["points"] 重建全部标记并贴一次（撤销 / 清空 / 改环号后调）。"""
    ax = getattr(window, "calib_ax", None)
    if ax is None:
        return
    for art in getattr(window, "calib_marker_artists", None) or []:
        try:
            art.remove()
        except Exception:                                  # noqa: BLE001
            pass
    window.calib_marker_artists = []
    for x, y, ring in _calib_state(window)["points"]:
        window.calib_marker_artists += _new_marker_artists(ax, x, y, ring)
    _refresh_calib_markers(window)


def _cache_calib_bg(window: QMainWindow, _event=None) -> None:
    """整幅画完：拍一张底图（干净的，不含标记）+ 把标记贴回去。

    与 plot_panels 的 `_blit_take` 同一个道理：整幅重绘一次要 ~0.6 s
    （2048² 图像的 LogNorm 上色 + 重采样，探针实测，2026-10-01），而选点、
    撤销、清空、改环号都只需要动那几个圈。挂在 draw_event 上——**任何一次
    整幅重绘之后都会重拍**（换几何、改视野都在其中），所以底图永不过期；
    窗口一缩放也会重画 → 重拍。
    """
    canvas = getattr(window, "calib_canvas", None)
    ax = getattr(window, "calib_ax", None)
    if canvas is None or ax is None:
        return
    try:
        window.calib_bg = canvas.copy_from_bbox(ax.figure.bbox)
    except Exception:                                  # noqa: BLE001
        window.calib_bg = None      # 后端不支持贴图：退回整幅重画
        return
    _refresh_calib_markers(window)  # 整幅画里没有标记（不可见），这里贴上去


def _add_calib_marker(window: QMainWindow, x, y, ring: int) -> None:
    """点击成功后加一个青圈 + 环号——**贴图**（几毫秒），不整幅重画。

    用户 2026-10-01："校准功能卡死两次，都是在选点时"：原先这里调
    `draw_idle`，等于每点一下都把整幅 2048² 图像重绘一遍（探针实测 610 ms
    每次，真窗口更慢），事件循环整个被占住。现在只贴标记（见
    _refresh_calib_markers）。
    """
    arts = getattr(window, "calib_marker_artists", None)
    if arts is None:
        arts = window.calib_marker_artists = []
    arts += _new_marker_artists(window.calib_ax, x, y, ring)
    _refresh_calib_markers(window)


def _redraw_calib_if_open(window: QMainWindow) -> None:
    """几何变了就重画青线——但只有面板开着才画。

    为什么要有这个守卫：[编辑当前配置] 只能在校准页点到，可 [加载参数]
    （导入 .poni → 借成当前配置）分析模式下就能用，那时没有画布。

    面板开着的判据是 calib_dock **不是** calib_ax：关面板时只把
    calib_dock 置 None（calib_ax / calib_canvas 是悬空的旧对象，画布
    C++ 侧随子窗口一起删了），拿它判断会在"用过校准、退回分析模式、
    再导入 .poni"时画到已删除的画布上。
    """
    if (getattr(window, "calib_dock", None) is not None
            and getattr(window, "calib_ax", None) is not None):
        _redraw_calib(window)


def _redraw_calib(window: QMainWindow) -> None:
    """按当前状态整幅重画：理论环（当前配置的几何）+ 用户点 + 自动校准
    控制点（若有）。撤销/清空/校准完成/几何被改后调用。"""
    from xrd_toolkit.gui.calib import (_calib_sync, _ensure_current, _refresh_current_metrics,
        _reset_calib_form)   # 破循环：见模块说明
    state = _calib_state(window)
    # 控制点来自 pyFAI extract_cp（自动 / 再精修都会产出）：取"当前配置"
    # 对应的那份，没有就退回最近一条带控制点的结果
    cps = None
    cur_name = state["slots"].get("current")
    item = _result_by_name(state, cur_name) if cur_name else None
    if item is not None and item["result"].get("control_points"):
        cps = item["result"]["control_points"]
    else:
        for it in reversed(state["results"]):
            if it["result"].get("control_points"):
                cps = it["result"]["control_points"]
                break
    _draw_calib_image(window, window.calib_key,
                      _calib_image(window, window.calib_path),
                      _calib_draw_geometry(window), control_points=cps,
                      ring_marks=state["points"])


# LaB₆ 理论环序号上限（snap_lab6_ring 的 max_rings=16 → 0~15）：右键改环号
# 的输入框拿它当上界
_LAB6_MAX_RING = 15
# 右键改环号：落点离某个选点多近算"点中了这个点"（px）。选点标记是 9 px 的
# 圆圈，12 px 给触控板留点余量
_MARKER_PICK_PX = 12.0


def _ask_ring_index(window: QMainWindow, old: int) -> int:
    """问用户"这个点算第几环"。返回 -1 = 取消。

    单独一层是为了测试能替身掉模态框（离屏测试里 QInputDialog 会挂住）。
    """
    val, ok = QInputDialog.getInt(
        window, "改环号",
        f"这个点现在判成环 {old}：改成第几环？\n"
        f"（LaB₆ 理论环 0~{_LAB6_MAX_RING}；位置不动，拟合按新环号算）",
        old, 0, _LAB6_MAX_RING, 1)
    return int(val) if ok else -1


def _edit_ring_of_nearest(window: QMainWindow, x: float, y: float) -> bool:
    """右键某个选点 → 改它的环号。返回 True = 改了（调用方整幅重画）。

    用户 2026-09-30 定：自动判环（含按尺度重判）都没救回来时的**后手**——
    你自己知道这个点是第几环，直接改掉，比再点一遍碰运气可靠。
    """
    state = _calib_state(window)
    pts = state["points"]
    if not pts:
        _log(window, "还没有选点：先在图上点衍射环；右键用来改已有点的环号")
        return False
    dists = [float(np.hypot(p[0] - x, p[1] - y)) for p in pts]
    i = int(np.argmin(dists))
    if dists[i] > _MARKER_PICK_PX:
        _log(window, f"右键要落在某个选点上：最近的点也在 {dists[i]:.0f} px 外")
        return False
    old = int(pts[i][2])
    new = _ask_ring_index(window, old)
    if new < 0 or new == old:
        return False
    pts[i] = (float(pts[i][0]), float(pts[i][1]), new)
    _log(window, f"第 {i + 1} 个点的环号：{old} → {new}（位置没动，拟合按新"
                 f"环号算——改完按 [用选点精修] 重跑）")
    return True


def _on_calib_click(window: QMainWindow, key: str, event) -> None:
    """校准图点击：判环吸附 → 记录点 + 图上标记；吸不上 → 日志忽略。

    **右键**落在某个已有点上 = 改那个点的环号（`_edit_ring_of_nearest`，
    用户 2026-09-30 定的后手）——判环自动修不回来时，你知道它该是第几环。

    判环用**屏幕上画青线的那套几何**（_calib_draw_geometry：最近一次
    校准结果覆盖面板初值）——与 _draw_calib_image 同源。用别的几何判，
    会出现"点着你看到的那条线、却判成隔壁环号"（几何偏差 23 px 在
    r=235 px 处约合 0.17°，而环间距只有 0.24~0.7°）。
    """
    from xrd_toolkit.gui.calib import (_calib_sync, _ensure_current, _refresh_current_metrics,
        _reset_calib_form)   # 破循环：见模块说明
    if event.xdata is None or event.ydata is None:
        return   # 点在坐标轴外
    if event.inaxes is not getattr(window, "calib_ax", None):
        return
    if getattr(window, "calib_dock", None) is None \
            or getattr(window, "calib_key", None) != key:
        return   # 面板已关/换过：迟到点击忽略
    if getattr(event, "button", None) == MouseButton.RIGHT:
        # 后手：自动判环没救回来时，右键某个点直接改它的环号（用户 2026-09-30）
        if _edit_ring_of_nearest(window, float(event.xdata), float(event.ydata)):
            _rebuild_calib_markers(window)      # 只重画标记（不整幅重画）
        return
    g = _calib_draw_geometry(window)
    ring = snap_lab6_ring(
        float(event.xdata), float(event.ydata),
        pixel_size_m=g["pixel_size_m"], wavelength_m=g["wavelength_m"],
        dist_m=g["dist_m"], poni1_m=g["poni1_px"] * g["pixel_size_m"],
        poni2_m=g["poni2_px"] * g["pixel_size_m"],
        rot1_deg=g["rot1_deg"], rot2_deg=g["rot2_deg"],
        tol_deg=SNAP_TOL_DEG)
    state = _calib_state(window)
    if ring is None:
        _log(window, f"({event.xdata:.1f}, {event.ydata:.1f}) "
                     f"不在任何理论环附近（±{SNAP_TOL_DEG:.1f}°），已忽略")
        return
    state["points"].append((float(event.xdata), float(event.ydata), int(ring)))
    _log(window, f"已记录第 {len(state['points'])} 个点："
                 f"({event.xdata:.1f}, {event.ydata:.1f}) → 环 {ring}")
    _add_calib_marker(window, event.xdata, event.ydata, ring)
    _calib_sync(window)


def _undo_calib_point(window: QMainWindow) -> None:
    """撤销最后一个选点：**只重建标记**（不整幅重画）。

    用户 2026-10-01："手动选点的撤销和全清都卡了，就是变深色然后卡住"——
    原先这两个动作都走 `_redraw_calib`（整幅重画：2048² 图再过一遍 LogNorm
    上色，几百毫秒到十几秒），而其实只需要动那几个青圈。
    """
    from xrd_toolkit.gui.calib import (_calib_sync, _ensure_current, _refresh_current_metrics,
        _reset_calib_form)   # 破循环：见模块说明
    state = _calib_state(window)
    if not state["points"]:
        return
    state["points"].pop()
    _log(window, f"已撤销最后一个点（剩 {len(state['points'])} 个）")
    _rebuild_calib_markers(window)
    _calib_sync(window)


def _clear_calib_points(window: QMainWindow) -> None:
    """清空全部选点：**只重建标记**（不整幅重画，理由同 _undo_calib_point）。"""
    from xrd_toolkit.gui.calib import (_calib_sync, _ensure_current, _refresh_current_metrics,
        _reset_calib_form)   # 破循环：见模块说明
    state = _calib_state(window)
    if not state["points"]:
        return
    state["points"] = []
    _log(window, "已清空选点")
    _rebuild_calib_markers(window)
    _calib_sync(window)
