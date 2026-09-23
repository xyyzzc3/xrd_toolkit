"""中央校准图面板：图像 + 理论环（青线）+ 选点交互。

从 calib.py 拆出来（纯搬迁）：只管「那张图怎么画、点上去算什么」。
"""
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import LogNorm
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QMdiSubWindow, QVBoxLayout, QWidget

from xrd_toolkit.gui.calib_model import (
    CP_COLOR, PANEL_SCALE, RING_COLOR, SNAP_TOL_DEG,
    _calib_state, _geom_px_keys, _result_by_name)
from xrd_toolkit.gui.panels import _settle
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
    """标样 = 文件列表勾选的第一个文件（列表顺序第一个对号条目）。

    返回 Path 或 None（一个都没勾）。
    """
    for i in range(window.file_list.count()):
        item = window.file_list.item(i)
        if item.checkState() == Qt.Checked:
            return Path(item.data(Qt.UserRole))
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
    只有采纳（谁拟合得好）才换图。没设定当前配置时退回分析配置。
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
    lay.addWidget(canvas)
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
    _draw_calib_image(window, key, image, _calib_draw_geometry(window))
    _log(window, f"打开校准面板：{path.name}（点击衍射环选点）")
    # 新面板 = 新一轮：当前配置从分析页选中的条目借起（登记成"原始"），
    # 并异步算它的环位偏差（表里那一格先显示 —）
    _ensure_current(window)
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
    # 选点/结果全清（关闭即遗忘）：删属性让 _calib_state 下次懒重建，
    # 否则重开面板会带出旧点标记和旧结果几何
    if hasattr(window, "calib_state"):
        del window.calib_state
    _reset_calib_form(window)
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
    画一堆看不见的线，而是放大视野 + 红字说明（_warn_rings_off_image）。
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
    if ring_marks:
        for x, y, ring in ring_marks:
            ax.plot([x], [y], "o", mfc="none", mec=RING_COLOR, ms=9, mew=1.5)
            ax.annotate(str(ring), (x, y), color=RING_COLOR, fontsize=8,
                        va="bottom", ha="left")
    span = ("环半径 %.0f~%.0f px" % (paths["r_min_px"], paths["r_max_px"])
            if np.isfinite(paths["r_min_px"]) else "环半径：无解")
    ax.set_xlabel("横向 (px)")
    ax.set_ylabel("纵向 (px)")
    ax.set_title(f"{window.calib_display}  ·  {span}")
    if not paths["n_inside"]:
        _warn_rings_off_image(window, ax, image, paths, geometry)
    window.calib_canvas.draw_idle()


def _warn_rings_off_image(window: QMainWindow, ax, image, paths,
                          geometry) -> None:
    """守卫：几何把理论环全推出图像时，明说 + 放大视野让人看见它们。

    静默画一圈看不见的青线是最坏的失败方式（用户只会觉得"校准没
    反应"）。视野扩到包住环路径、左上角红字标注原因；日志按几何指纹
    去重（撤销/清空选点的重画不重复刷屏）。
    """
    h, w = image.shape
    note = (f"当前几何下 {len(paths['rings'])} 条环全部落在图像外"
            f"（环半径 {paths['r_min_px']:.0f}~{paths['r_max_px']:.0f} px，"
            f"图像 {w}×{h}）：请核对像素尺寸/波长/距离")
    xy = np.vstack([p for _, p in paths["rings"]])
    fin = np.isfinite(xy).all(axis=1)
    if fin.any():
        x0, x1 = float(xy[fin, 0].min()), float(xy[fin, 0].max())
        y0, y1 = float(xy[fin, 1].min()), float(xy[fin, 1].max())
        pad_x = 0.05 * max(x1 - x0, w)
        pad_y = 0.05 * max(y1 - y0, h)
        ax.set_xlim(min(0.0, x0) - pad_x, max(w, x1) + pad_x)
        ax.set_ylim(min(0.0, y0) - pad_y, max(h, y1) + pad_y)
    ax.text(0.02, 0.98, "⚠ " + note, transform=ax.transAxes, color="#ff6666",
            fontsize=8, va="top", ha="left")
    key = tuple(round(float(geometry[k]), 6) for k in sorted(geometry))
    if getattr(window, "_calib_span_warned", None) != key:
        window._calib_span_warned = key
        _log(window, note)


def _add_calib_marker(window: QMainWindow, x, y, ring: int) -> None:
    """增量标记：点击成功后只加一个青圈 + 环号（不整幅重画 imshow，
    点起来不卡）。"""
    ax = window.calib_ax
    ax.plot([x], [y], "o", mfc="none", mec=RING_COLOR, ms=9, mew=1.5)
    ax.annotate(str(ring), (x, y), color=RING_COLOR, fontsize=8,
                va="bottom", ha="left")
    window.calib_canvas.draw_idle()


def _redraw_calib(window: QMainWindow) -> None:
    """按当前状态整幅重画：理论环（最近结果几何）+ 用户点 + 自动校准
    控制点（若有）。撤销/清空/校准完成后调用。"""
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


def _on_calib_click(window: QMainWindow, key: str, event) -> None:
    """校准图点击：判环吸附 → 记录点 + 图上标记；吸不上 → 日志忽略。

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
    """撤销最后一个选点：整幅重画（去掉该点标记）。"""
    from xrd_toolkit.gui.calib import (_calib_sync, _ensure_current, _refresh_current_metrics,
        _reset_calib_form)   # 破循环：见模块说明
    state = _calib_state(window)
    if not state["points"]:
        return
    state["points"].pop()
    _log(window, f"已撤销最后一个点（剩 {len(state['points'])} 个）")
    _redraw_calib(window)
    _calib_sync(window)


def _clear_calib_points(window: QMainWindow) -> None:
    """清空全部选点（重画 + 标签复位）。"""
    from xrd_toolkit.gui.calib import (_calib_sync, _ensure_current, _refresh_current_metrics,
        _reset_calib_form)   # 破循环：见模块说明
    state = _calib_state(window)
    if not state["points"]:
        return
    state["points"] = []
    _log(window, "已清空选点")
    _redraw_calib(window)
    _calib_sync(window)
