"""对比/热图的锚点拾取：开关、按下/松开、右键删除、原始 y 读数。

2026-10-21 从 plot_compare.py 原样搬出（只改结构，不动逻辑）。
plot_compare 再导出，tests/scripts 照旧经 gui_plot_compare 取用。
"""
import numpy as np
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui.panel_state import (
    _AUX_GID_PREFIX, _log, _panel_param, _set_focus)
from xrd_toolkit.gui.plot_panels import _data_lines
from xrd_toolkit.gui.plot_views import _bg_path_of, _refresh_proc


def _anchor_enabled(window: QMainWindow, key: str) -> bool:
    """这个 1D 面板现在该不该响应锚点点选。

    两个条件都要满足：参数坞的模式是"手动锚点"、[拾取锚点] 开着。
    两个开关分开是故意的——模式决定"用什么算背景"（画图时生效），
    拾取开关决定"鼠标现在是在画图还是在选点"，不让人在只想看图时
    误点出锚点。
    """
    if key.split("|", 1)[0] != "1D":
        return False
    btn = getattr(window, "bg_pick_btn", None)
    return bool(btn is not None and btn.isChecked())

def _anchor_press(window: QMainWindow, key: str, event) -> None:
    """1D 面板按下：记账候选锚点（没拖动才算"点击"，松手再决定）。

    与热图行点击同套路：左键拖 = 平移（通用手势），点按 = 加/删锚点。
    """
    dock = window.plot_docks.get(key)
    if dock is None or event.inaxes is None or event.button != 1:
        return
    if not _anchor_enabled(window, key):
        return
    if event.xdata is None or event.ydata is None:
        return
    # 这里**不切焦点**：按下时还不知道是点击还是拖拽平移，而切焦点会回放
    # 该面板的快照（模式多半是"关闭"）→ 拾取开关被 _sync_bg_rows 收掉，
    # 用户只是想在别的图上拖一下就把"拾取锚点"弄丢了（实测日志末行变成
    # "背景扣除：关闭"）。切焦点挪到 _anchor_release、确认是点击之后
    dock._anchor_press = (event.x, event.y)

def _anchor_release(window: QMainWindow, key: str, event) -> None:
    """1D 面板松手：按下点没怎么挪（阈值 5 px）→ 在最近的曲线上取一个
    锚点；点到已有关键点附近（阈值 8 px）→ 删掉它。挪多了 = 平移，不动。

    取点复用悬停那套"最近曲线 + 吸附最近真实数据点"（_hover_motion）：
    报的是真算出来的值，不是鼠标的原始位置。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return
    info = getattr(dock, "_anchor_press", None)
    dock._anchor_press = None
    if info is None:
        return
    x0, y0 = info
    if (event.x - x0) ** 2 + (event.y - y0) ** 2 > 5 ** 2:
        return   # 拖过了 = 平移手势，不是点击
    if not _anchor_enabled(window, key):
        return
    if event.inaxes is None or event.xdata is None or event.ydata is None:
        return
    path = _bg_path_of(dock)
    if path is None:
        return
    # 点哪张图就编辑哪张图（与点面板选中编辑对象一致）：_refresh_proc 把参数
    # 坞控件值推进的正是编辑对象的快照，不切焦点会出现"点了没反应"。放在
    # 这里而不是按下时——此刻已确认是点击，不会误伤平移手势（见 _anchor_press）
    _set_focus(window, key, dock.panel_display)
    # 注意不能写 `(... or {}).setdefault(...)`：空 dict 是 falsy，`or`
    # 会换成临时新字典，锚点全加进临时对象里去（探针实测：日志报"加了
    # 1 个"而 window.bg_anchors 还是空的）
    if getattr(window, "bg_anchors", None) is None:
        window.bg_anchors = {}
    anchors = window.bg_anchors.setdefault(str(path), [])
    # 点到已有关键点附近 → 删除（再点恢复的开关语义）
    ax = event.inaxes
    for i, (ax_, ay_) in enumerate(anchors):
        px, py = ax.transData.transform((ax_, ay_))
        if (px - event.x) ** 2 + (py - event.y) ** 2 <= 8 ** 2:
            anchors.pop(i)
            _anchor_changed(window, f"删除锚点：2θ {ax_:.3f}°")
            return
    # 取最近曲线上的最近真实数据点
    lines = [ln for ln in _data_lines(ax) if len(ln.get_xdata()) > 1]
    if not lines:
        return
    best = None
    for ln in lines:
        xd = ln.get_xdata()
        if xd.size < 2 or not (xd[0] <= event.xdata <= xd[-1]):
            continue
        yline = float(np.interp(event.xdata, xd, ln.get_ydata()))
        px, py = ax.transData.transform((event.xdata, yline))
        d2 = (px - event.x) ** 2 + (py - event.y) ** 2
        if best is None or d2 < best[0]:
            best = (d2, xd, ln)
    if best is None:
        return
    _, xd, ln = best
    i = int(np.argmin(np.abs(xd - event.xdata)))
    x = float(xd[i])
    y = _anchor_raw_y(window, dock, ax, xd, i, ln)
    anchors.append((x, y))
    _anchor_changed(window, f"加锚点：2θ {x:.3f}°（{len(anchors)} 个）")

def _anchors_visible(window: QMainWindow, dock) -> bool:
    """这张 1D 图上现在画着锚点圆圈吗（与 _draw_bg_overlay 同一个门槛）。

    圆圈只在**手动锚点**与**自动基线（自动 + 锚点校正）**两种模式下画
    （见 _draw_bg_overlay）。右键删除只认"看得见"的锚点——看不见的东西
    不该被点掉。
    """
    path = _bg_path_of(dock)
    if path is None or not getattr(window, "bg_anchors", {}).get(str(path)):
        return False
    return _panel_param(window, dock, "背景扣除模式", "off") in ("anchor", "auto")

def _anchor_right_click(window: QMainWindow, key: str, event) -> None:
    """1D 图上**右键点锚点 = 删掉它**（用户 2026-10-02 第 2 条）。

    原先删单个锚点只有一条窄路：先点亮 [拾取锚点]，再左键点那个圆圈
    （8 屏幕像素以内）——手势隐形、目标小。右键在 1D 图上是空的（校准图
    那边右键改环号已有先例），拿它当"删除"最顺；**不要求拾取开关**：
    它只删不加，误伤的代价是重新点一个回来。

    命中判定与左键删除同一套（transData 之后的 8 px）：同一个圆圈，
    两个键不该有两种手感。
    """
    dock = window.plot_docks.get(key)
    if dock is None or event.inaxes is None or event.button != 3:
        return
    if key.split("|", 1)[0] != "1D" or not _anchors_visible(window, dock):
        return
    if event.xdata is None or event.ydata is None:
        return
    path = _bg_path_of(dock)
    anchors = getattr(window, "bg_anchors", {}).get(str(path), [])
    ax = event.inaxes
    for i, (ax_, ay_) in enumerate(anchors):
        px, py = ax.transData.transform((ax_, ay_))
        if (px - event.x) ** 2 + (py - event.y) ** 2 <= 8 ** 2:
            anchors.pop(i)
            _anchor_changed(window, f"删除锚点：2θ {ax_:.3f}°（右键）")
            return

def _anchor_raw_y(window: QMainWindow, dock, ax, xd, i, line) -> float:
    """锚点的 y：**原始**曲线的值，不是屏幕上那条扣过的曲线。

    锚点描述的是原始曲线的背景形状。屏幕上的实线是扣完的（在已有关键点
    处已经接近 0），照它取值会把新锚点记成 0/负值 → 该处背景等于没扣，
    而画面上看不出错；锚点还会持久化，把"显示原始曲线对比"重新勾上也不
    会自愈。

    取法按可靠性排队：
      ① 画出来的原始叠加线（bg:raw）—— 它是"这张图当时用的原始数据"的
         权威副本；
      ② 面板缓存的 last_intensity —— 用户关掉原始叠加时走这条（此时
         轴上没有 bg:raw，而缓存里的原始曲线仍在，且绘制层从不改它）；
      ③ 最后的兜底：就用屏幕上那条线的值（只有前两者都拿不到才走到，
         例如测试直接拿自定义数组调 _draw_1d）。
    """
    raw = next((ln_ for ln_ in ax.lines
                if ln_.get_gid() == _AUX_GID_PREFIX + "raw"), None)
    if raw is not None and len(raw.get_ydata()) == len(xd):
        return float(raw.get_ydata()[i])
    cached_y = getattr(dock, "last_intensity", None)
    cached_x = getattr(dock, "last_tth", None)
    if cached_y is not None and cached_x is not None \
            and len(cached_y) == len(xd) and np.allclose(cached_x, xd):
        return float(np.asarray(cached_y, dtype=float)[i])
    return float(line.get_ydata()[i])

def _anchor_changed(window: QMainWindow, msg: str) -> None:
    """锚点变动后的统一收尾：刷新计数 + 立刻重画 + 记日志。"""
    updater = getattr(window, "_bg_count_refresh", None)
    if updater is not None:
        updater(window)
    _refresh_proc(window)
    _log(window, msg)

