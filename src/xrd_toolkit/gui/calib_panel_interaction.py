"""校准图交互：选点、右键选中、改环号、撤销/清空、框选与滚轮缩放。

2026-10-22 从 calib_panel.py 原样搬出（只改结构，不动逻辑）。
calib_panel 再导出；对 _calib_draw_geometry 用函数内延迟导入破环。
"""
import numpy as np
from matplotlib.backend_bases import MouseButton
from matplotlib.patches import Rectangle
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui.calib_model import SNAP_TOL_DEG, _calib_state
from xrd_toolkit.gui.panel_state import CALIB_HOVER_KEY, _log
from xrd_toolkit.gui.calib_panel_draw import (
    _add_calib_marker, _calib_zoom_on, _rebuild_calib_markers,
    _refresh_calib_markers)
from xrd_toolkit.services.integrator import snap_ring


# 右键选中：落点离某个选点多近算"点中了这个点"（px）。选点标记是 9 px 的
# 圆圈，12 px 给触控板留点余量
_MARKER_PICK_PX = 12.0
# 框选放大：位移小于它 = 一次点击（按松开判，与 plot_panels.BOX_MIN_PX
# 同一口径的 6 px；那边从模块常量取，这里同值自持，不想为一个数把整个
# plot_panels 拉进本模块的 import 链）
_BOX_MIN_PX = 6.0
# 滚轮每格 10%（1.1 的倒数；与 plot_panels._wheel_zoom 同口径——1.25
# 太猛，触摸板两指一滑是连续好多小格）
_ZOOM_STEP = 1.1


def _draw_calib_box(window: QMainWindow, ax, x0: float, y0: float,
                    x1: float, y1: float) -> None:
    """框选放大的选框：画成"坐标轴分数坐标"的矩形（跟着轴缩放），拖动
    期只贴图（见 _blit_calib_box），松开时由 _on_calib_release 撤掉。
    """
    inv = ax.transAxes.inverted()
    (u0, v0), (u1, v1) = inv.transform([(x0, y0), (x1, y1)])
    left, bottom = min(u0, u1), min(v0, v1)
    box = getattr(window, "calib_box_patch", None)
    if box is None or box.axes is not ax:
        if box is not None:
            try:
                box.remove()
            except Exception:                              # noqa: BLE001
                pass
        box = Rectangle((left, bottom), abs(u1 - u0), abs(v1 - v0),
                        transform=ax.transAxes, facecolor="none",
                        edgecolor="#2a78d6", linewidth=1.0, linestyle="--",
                        zorder=20)
        ax.add_patch(box)
        window.calib_box_patch = box
    else:
        box.set_bounds(left, bottom, abs(u1 - u0), abs(v1 - v0))
    _blit_calib_box(window, ax, box)


def _blit_calib_box(window: QMainWindow, ax, box) -> None:
    """框选拖动期的一帧：贴回按下时拍的底图 → 只画选框 → 上屏（几毫秒）。

    底图在按下时拍（window.calib_drag_bg），**含**已贴上去的选点标记
    ——拖动期选框过去、标记都还在。没有底图（后端不支持贴图）就不画
    预览帧：松开时框选照样生效，只是拖动期看不到框。
    """
    canvas = getattr(window, "calib_canvas", None)
    bg = getattr(window, "calib_drag_bg", None)
    if canvas is None or bg is None:
        return
    canvas.restore_region(bg)
    ax.draw_artist(box)
    canvas.blit(ax.figure.bbox)


def _on_calib_press(window: QMainWindow, key: str, event) -> None:
    """左键按下。放大镜**点亮**：起框选（松开且位移小于阈值 = 一次点击，
    按按下处选点）；熄灭：立即走选点（一点即记，老行为原样）。
    右键按老规矩：选中最近的已有点（改环号的前半步）。
    """
    if event.xdata is None or event.ydata is None:
        return   # 点在坐标轴外
    if event.inaxes is not getattr(window, "calib_ax", None):
        return
    if getattr(window, "calib_dock", None) is None \
            or getattr(window, "calib_key", None) != key:
        return   # 面板已关/换过：迟到点击忽略
    if getattr(event, "button", None) == MouseButton.RIGHT:
        _on_calib_click(window, key, event)   # 右键选中（原路径，不动）
        return
    if event.button != 1:
        return
    if not _calib_zoom_on(window):
        _on_calib_click(window, key, event)   # 熄灭：一点即记（老行为）
        return
    # 点亮：起框选。拍底图 → 画零尺寸框（拖动期只贴这一帧+选框）。
    # 同时记下**按下事件**：位移小于阈值时按按下处的位置选点
    # （松开事件的数据坐标可能已经偏出去了）
    window.calib_box_start = (event.x, event.y)
    window.calib_box_axes = event.inaxes
    window.calib_box_event = event
    window.calib_drag_bg = _calib_take_shot(window, event.inaxes)
    _draw_calib_box(window, event.inaxes, event.x, event.y, event.x, event.y)


def _calib_take_shot(window: QMainWindow, ax):
    """拍一张拖动期底图（整图 bbox，含当时已贴上的标记）；不支持就 None。"""
    canvas = getattr(window, "calib_canvas", None)
    if canvas is None or not hasattr(canvas, "copy_from_bbox"):
        return None
    try:
        return canvas.copy_from_bbox(ax.figure.bbox)
    except Exception:                                      # noqa: BLE001
        return None


def _on_calib_motion(window: QMainWindow, key: str, event) -> None:
    """鼠标在图上移动：记"最后停留"（Home 键认它，2026-10-14）；左键拖动
    且放大镜点亮时拉伸选框（熄灭时没有拖动动作）。"""
    # Home 键的认领标记：同普通图面板的 plot_panels._note_hover——鼠标在
    # 哪张图上，Home 复位哪张图（这里标哨兵值，_home_key_reset 见到它就
    # 转给 reset_calib_view）
    window._hover_panel_key = CALIB_HOVER_KEY
    start = getattr(window, "calib_box_start", None)
    if start is None or getattr(event, "button", None) != 1:
        return
    ax = getattr(window, "calib_box_axes", None)
    if ax is None:
        return
    _draw_calib_box(window, ax, start[0], start[1], event.x, event.y)


def _on_calib_release(window: QMainWindow, key: str, event) -> None:
    """松开左键：位移够大 = 放大到框内（只改坐标范围，不碰数据点）；
    位移小于阈值 = 其实是一次点击 → 按**按下处**选点（走老入口）。
    """
    start = getattr(window, "calib_box_start", None)
    if start is None:
        return
    ax = getattr(window, "calib_box_axes", None)
    patch = getattr(window, "calib_box_patch", None)
    press_ev = getattr(window, "calib_box_event", None)
    window.calib_box_start = None
    window.calib_box_axes = None
    window.calib_box_event = None
    window.calib_box_patch = None
    window.calib_drag_bg = None
    if patch is not None:
        try:
            patch.remove()          # 选框是临时的：松开就撤，不留痕迹
        except Exception:                                  # noqa: BLE001
            pass
    if ax is None:
        return
    if (abs(event.x - start[0]) < _BOX_MIN_PX
            or abs(event.y - start[1]) < _BOX_MIN_PX):
        # 其实是点击：按按下处选点（老入口原样），随后贴回底图把拖动期
        # 的框帧擦掉（成功选点会自带一次贴图，这里兜住"没吸上环"那支）
        if press_ev is not None:
            _on_calib_click(window, key, press_ev)
        _refresh_calib_markers(window)
        return
    inv = ax.transData.inverted()
    (cx0, cy0), (cx1, cy1) = inv.transform([start, (event.x, event.y)])
    ax.set_xlim(min(cx0, cx1), max(cx0, cx1))
    ax.set_ylim(min(cy0, cy1), max(cy0, cy1))
    window.calib_view = (tuple(ax.get_xlim()), tuple(ax.get_ylim()))
    _log(window, "视野：已框选放大（想换视野再框一处；[看环全貌] 复位）")
    ax.figure.canvas.draw_idle()


def _on_calib_scroll(window: QMainWindow, key: str, event) -> None:
    """滚轮：放大镜点亮时以光标为中心缩放（每格 10%）；熄灭时不缩。"""
    if not _calib_zoom_on(window):
        return   # 熄灭：滚轮不归这张图管（事件照旧穿透给绘图区）
    gui = getattr(event, "guiEvent", None)
    if gui is not None and gui.modifiers() & Qt.ControlModifier:
        return   # 总缩放不归这里（Ctrl+滚轮在校准图上照旧无效果）
    ax = getattr(window, "calib_ax", None)
    if ax is None or event.xdata is None or event.ydata is None:
        return
    if event.inaxes is not ax:
        return
    if getattr(window, "calib_dock", None) is None \
            or getattr(window, "calib_key", None) != key:
        return
    factor = 1.0 / _ZOOM_STEP if event.button == "up" else _ZOOM_STEP
    x, y = float(event.xdata), float(event.ydata)
    xlo, xhi = ax.get_xlim()
    ylo, yhi = ax.get_ylim()
    ax.set_xlim(x - (x - xlo) * factor, x + (xhi - x) * factor)
    ax.set_ylim(y - (y - ylo) * factor, y + (yhi - y) * factor)
    window.calib_view = (tuple(ax.get_xlim()), tuple(ax.get_ylim()))
    ax.figure.canvas.draw_idle()


def _normalize_selection(window: QMainWindow) -> None:
    """选点列表变了以后把选中项修好：越界/没了就清空。"""
    state = _calib_state(window)
    i = state.get("selected")
    if i is not None and not (0 <= i < len(state["points"])):
        state["selected"] = None


def _select_nearest(window: QMainWindow, x: float, y: float) -> bool:
    """右键某个选点 → 选中它（改环号的前半步）。返回 True = 选中变了。

    用户 2026-09-30 定的**后手**（自动判环含按尺度重判都没救回来时，
    你自己知道它该是第几环）；2026-10-03 改成"选中 + 在「手动」小节里
    改"。原版右键弹 QInputDialog——macOS 全屏下"对话框开着时切走再切回"
    会把整个应用卡成收不到任何输入（AppKit 的模态窗口收尾没做完，
    NSApp.modalWindow 一直挂着，实测复现）；改成两步之后模态窗口彻底
    不存在，也顺带解决了离屏测试里这个对话框会挂住的问题。
    """
    state = _calib_state(window)
    pts = state["points"]
    if not pts:
        _log(window, "还没有选点：先在图上点衍射环；右键用来选中一个已有点")
        return False
    dists = [float(np.hypot(p[0] - x, p[1] - y)) for p in pts]
    i = int(np.argmin(dists))
    if dists[i] > _MARKER_PICK_PX:
        _log(window, f"右键要落在某个选点上：最近的点也在 {dists[i]:.0f} px 外")
        return False
    state["selected"] = i
    _log(window, f"已选中第 {i + 1} 个点（现在判成环 {int(pts[i][2])}）"
                 f"——在「手动」小节里改它的环号")
    return True


def _apply_selected_ring(window: QMainWindow) -> None:
    """[改]：把「选中点的环号」数字框的值应用到选中的那个点。

    位置不动、只改环号（拟合按新环号算）——与老对话框版同一套语义，
    只是"问数字"从弹窗换成了面板上的一行。
    """
    from xrd_toolkit.gui.calib import _calib_sync   # 破循环：见模块说明
    state = _calib_state(window)
    i = state.get("selected")
    pts = state["points"]
    if i is None or not (0 <= i < len(pts)):
        _log(window, "还没有选中的点：右键图上某个选点来选它")
        return
    old = int(pts[i][2])
    new = int(window.calib_ring_spin.value())
    if new == old:
        _log(window, f"第 {i + 1} 个点现在就是环 {old}，没改")
        return
    pts[i] = (float(pts[i][0]), float(pts[i][1]), new)
    _log(window, f"第 {i + 1} 个点的环号：{old} → {new}（位置没动，拟合按新"
                 f"环号算——改完按 [用选点精修] 重跑）")
    _rebuild_calib_markers(window)
    _calib_sync(window)


def _on_calib_click(window: QMainWindow, key: str, event) -> None:
    """校准图点击：判环吸附 → 记录点 + 图上标记；吸不上 → 日志忽略。

    **右键**落在某个已有点上 = **选中**它（`_select_nearest`，用户
    2026-09-30 定的后手，2026-10-03 起不弹对话框）——选中后在「手动」
    小节里改它的环号；判环自动修不回来时，你知道它该是第几环。

    判环用**屏幕上画青线的那套几何**（_calib_draw_geometry：最近一次
    校准结果覆盖面板初值）——与 _draw_calib_image 同源。用别的几何判，
    会出现"点着你看到的那条线、却判成隔壁环号"（几何偏差 23 px 在
    r=235 px 处约合 0.17°，而环间距只有 0.24~0.7°）。
    """
    from xrd_toolkit.gui.calib import (_calib_sync, _ensure_current, _refresh_current_metrics,
        _reset_calib_form)   # 破循环：见模块说明
    from xrd_toolkit.gui.calib_panel import _calib_draw_geometry  # 破循环
    if event.xdata is None or event.ydata is None:
        return   # 点在坐标轴外
    if event.inaxes is not getattr(window, "calib_ax", None):
        return
    if getattr(window, "calib_dock", None) is None \
            or getattr(window, "calib_key", None) != key:
        return   # 面板已关/换过：迟到点击忽略
    if getattr(event, "button", None) == MouseButton.RIGHT:
        # 后手：自动判环没救回来时，右键选中那个点、在「手动」小节里改
        # 环号（用户 2026-10-03 起不弹对话框——模态框在 macOS 全屏下有
        # 卡死输入的前科，见 _select_nearest）
        if _select_nearest(window, float(event.xdata), float(event.ydata)):
            _rebuild_calib_markers(window)      # 只重画标记（不整幅重画）
        _calib_sync(window)                     # 面板那行（选中/数值）跟着变
        return
    g = _calib_draw_geometry(window)
    state = _calib_state(window)
    ring = snap_ring(
        float(event.xdata), float(event.ydata),
        pixel_size_m=g["pixel_size_m"], wavelength_m=g["wavelength_m"],
        dist_m=g["dist_m"], poni1_m=g["poni1_px"] * g["pixel_size_m"],
        poni2_m=g["poni2_px"] * g["pixel_size_m"],
        rot1_deg=g["rot1_deg"], rot2_deg=g["rot2_deg"],
        tol_deg=SNAP_TOL_DEG, calibrant=state["calibrant"],
        max_rings=state["n_rings"])
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
    _normalize_selection(window)      # 撤掉的正是选中那个 → 选中清空
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
    _normalize_selection(window)      # 点都没了，选中自然清空
    _log(window, "已清空选点")
    _rebuild_calib_markers(window)
    _calib_sync(window)
