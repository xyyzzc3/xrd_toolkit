"""校准工作台：自动校准 + 手动选点校准（参数坞第 2 页 + 中央校准图面板）。

模块图见 app.py docstring（本模块与 plot_views.py 并列，只依赖下层
panels / panel_state / tasks 与服务层引擎，单向无环）。

两种模式（按数据质量任选一种，也可都跑、结果三列并列对比）：
  - 自动校准 [开始自动校准]：后台线程 fit_center_from_rings 自动定
    环心（失败 find_ring_center 兜底）→ calibrate_lab6 pyFAI 迭代
    精修，输出距离/束心/倾斜角/残余误差；
  - 手动选点校准：中央校准图上点击至少 3 个点、覆盖 ≥2 个不同衍射
    环，点自动吸附最近理论环（snap_lab6_ring，容差 0.5°），
    [开始手动校准] 交给 refine_lab6_from_points 反推几何。

校准完成后**引擎指标**（services/ring_metrics：环位偏差中位 px、完整
环数、a 离散 ppm）附在结果 dict 上并写进日志后缀。它量的是"几何有没
有把理论环放到图像的真环上"，与 pyFAI 的收敛残差互补——后者只说明
迭代自洽（两种解分支都能报 0.004°），不说明环对不对。指标算不出来时
只记 metrics_error、日志如实说明，不影响校准结果本身。

三条来源（自动定位 / 手动选点 / 二次精修）各出一个结果，**"当前使用"
由"谁好用谁"决定**：新来源的环位偏差比当前的好 ≥ SOURCE_IMPROVE_MIN_PX
才替换（用户手动指定过的则永不自动替换，标成"自定义"）。画图与
[保存为配置] 都取"当前使用"那一份。

界面分工：
  - 参数坞第 2 页 = _build_calib_form（模式单选 + 自动/手动按钮区 +
    结果三列区 自动|手动|Δ偏差 + 保存为配置区）。各来源共用同一
    结果区与保存机制："当前使用"的结果可直接存成命名用户条目
    （config_user.json，不进 git），保存后分析页"几何配置"下拉框
    立即出现并自动选中（几何填进参数坞）。内置 config.py 注册表
    仍走 CLI 模板人工登记（见 config.py 文件头）。
  - 中央校准图面板 = _CalibSubWindow（MDI 子窗口，imshow + 理论环
    路径 + 控制点/用户点标记，只接鼠标点击）。理论环由
    theoretical_ring_paths 精确反解（倾斜时是椭圆、圆心是直射束
    落点），不用"圆心 + 半径"的正圆近似——后者会整体偏 8~23 px。
    不进 plot_docks：不掺和编辑对象焦点、平铺、总缩放；无手势无
    抓手（v1 从简）。

状态（都挂在 window 上）：
  calib_dock / calib_key / calib_path / calib_display / calib_canvas
  / calib_ax   面板与画布（None = 没开面板）
  calib_gen    代计数：面板关闭/重开/退出模式时 +1，在飞任务回调
               核对代数，迟到结果静默作废
  calib_state  {"points": [(x, y, 环号), ...], "auto"/"manual"/"refined":
                各来源结果|None, "current": 当前使用的来源键,
                "custom": 用户是否手动指定过}——关闭面板即全清
                （关闭即遗忘）
"""
import re
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import LogNorm
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMdiSubWindow,
    QMessageBox, QPushButton, QRadioButton, QScrollArea, QVBoxLayout,
    QWidget)

from xrd_toolkit import config
from xrd_toolkit.core.processor import find_ring_center, fit_center_from_rings
from xrd_toolkit.gui.panels import _settle
from xrd_toolkit.gui.panel_state import (
    _auto_contrast_values, _collect_geometry, _log, _reload_config_combo)
from xrd_toolkit.gui.tasks import BackgroundTask
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.services.integrator import (
    calibrate_lab6, refine_lab6_from_points, snap_lab6_ring,
    theoretical_ring_paths)
from xrd_toolkit.services.ring_metrics import ring_metrics

SNAP_TOL_DEG = 0.5      # 判环容差（2θ 度；与 snap_lab6_ring 默认一致）
MIN_POINTS = 3          # 手动校准最低点数
MIN_RINGS = 2           # 手动校准最低覆盖环数
PANEL_SCALE = 560       # 校准图面板最长边（像素，图太大就按此缩小）
RING_COLOR = "#00e5ff"  # 理论环 / 用户点标记色（青）
CP_COLOR = "#3dff3d"    # pyFAI 控制点标记色（绿）

# 校准来源（三步流程的三条路径 = 结果区可对比的来源集合）。
# 键同时是 state 里的结果键（state[key] = 该来源的结果 dict）。
SOURCE_LABELS = {"auto": "自动定位", "manual": "手动选点",
                 "refined": "二次精修"}
# "当前使用"的替换门槛（px）：新来源的环位偏差要比当前的好**这么多**才
# 替换。依据：同一张图重复跑，几何参数会抖（PONI 1.6~2.2 px）而环位偏差
# 只抖 0.014~0.029 px——改善小于 0.05 px 时"变好"是跑动噪声，不是真的。
SOURCE_IMPROVE_MIN_PX = 0.05


# ══ 小工具：状态 / 文件 / 图像 ══════════════════════════════════
def _calib_state(window: QMainWindow) -> dict:
    """校准状态（懒创建）：选点列表 + 各来源结果 + 当前使用。

    键：
      points    [(x, y, 环号), ...] 用户点的点
      auto/manual/refined  各来源的结果 dict（None = 没跑过）
      current   "当前使用"的来源键（None = 还没有可用几何）——画图、
                [保存为配置] 都用它
      custom    True = 用户手动指定过"当前使用"（标成"自定义"，此后新
                结果不自动抢位）
    """
    state = getattr(window, "calib_state", None)
    if state is None:
        state = window.calib_state = {
            "points": [], "auto": None, "manual": None, "refined": None,
            "current": None, "custom": False}
    return state


# ── 来源模型："谁好用谁" ────────────────────────────────────
def _source_dev(result) -> float:
    """该来源的环位偏差（px）。没有可用指标（缺 metrics / 无环信号）→ None。"""
    m = (result or {}).get("metrics") or {}
    dev = m.get("dev_px")
    return float(dev) if dev is not None and np.isfinite(dev) else None


def _update_current(state: dict, key: str) -> str:
    """来源完成后的"谁好用谁"：必要时切换"当前使用"，返回一句日志说明。

    纯函数（不碰 window），便于直接单测。规则三条：
      * 用户手动指定过（custom）→ 永不自动替换，只说明；
      * 两边**都**有可用指标时才比：新来源要赢过门槛
        SOURCE_IMPROVE_MIN_PX 才替换（"没变好就不替换"，避免在噪声里
        来回跳）；
      * 比不出来（任一侧无可用指标）→ 采用刚完成的这条：它是用户刚点
        的那条路径，而没有任何证据说它更差。
    """
    label = SOURCE_LABELS[key]
    cur = state.get("current")
    dev = _source_dev(state.get(key))
    cur_dev = _source_dev(state.get(cur)) if cur else None
    dev_txt = f"环位偏差 {dev:.2f} px" if dev is not None else "无可用环信号"

    if state.get("custom"):
        return (f"{label}完成；当前使用仍是你指定的 "
                f"{SOURCE_LABELS.get(cur, '—')}（自定义，不自动替换）")
    if cur is None or cur == key or dev is None or cur_dev is None:
        state["current"] = key
        tail = ("；之前的 " + SOURCE_LABELS[cur] + " 无可用环信号"
                if cur and cur != key and cur_dev is None and dev is not None
                else "")
        return f"当前使用 → {label}（{dev_txt}{tail}）"
    if cur_dev - dev >= SOURCE_IMPROVE_MIN_PX:
        state["current"] = key
        return (f"当前使用 → {label}（环位偏差 {dev:.2f} px，优于 "
                f"{SOURCE_LABELS[cur]} 的 {cur_dev:.2f} px）")
    return (f"当前使用保持 {SOURCE_LABELS[cur]}（环位偏差 {cur_dev:.2f} px "
            f"vs {label} 的 {dev:.2f} px，改善不足 {SOURCE_IMPROVE_MIN_PX:.2f} px）")


def _set_current_custom(state: dict, key: str) -> str:
    """用户手动指定"当前使用"→ 标成"自定义"（此后不自动替换）。返回日志说明。"""
    if state.get(key) is None:
        return f"该来源还没有结果：{SOURCE_LABELS[key]}"
    state["current"] = key
    state["custom"] = True
    dev = _source_dev(state.get(key))
    dev_txt = f"环位偏差 {dev:.2f} px" if dev is not None else "无可用环信号"
    return (f"当前使用改为 {SOURCE_LABELS[key]}（{dev_txt}，"
            f"自定义——之后的新结果不再自动替换）")


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


def _geom_px_keys(g: dict) -> dict:
    """_collect_geometry 的输出 → 画图/指标共用的 px 键几何（PONI 米→px）。"""
    return dict(
        pixel_size_m=g["pixel_size_m"], wavelength_m=g["wavelength_m"],
        dist_m=g["dist_m"], poni1_px=g["poni1_m"] / g["pixel_size_m"],
        poni2_px=g["poni2_m"] / g["pixel_size_m"],
        rot1_deg=g["rot1_deg"], rot2_deg=g["rot2_deg"])


def _calib_draw_geometry(window: QMainWindow) -> dict:
    """画图几何（统一 px 键）：**当前使用**的来源覆盖参数面板初值。

    校准完成后理论环按"当前使用"的几何重画（用户验证精修效果）；
    没跑过校准则按参数面板当前值画预测环。
    """
    draw = _geom_px_keys(_collect_geometry(window))
    state = _calib_state(window)
    key = state.get("current")
    result = state.get(key) if key else None
    if result is not None:
        for k in ("dist_m", "poni1_px", "poni2_px", "rot1_deg", "rot2_deg"):
            draw[k] = result[k]
    return draw


# ══ 中央校准图面板 ═══════════════════════════════════════════
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


def _open_calib_panel(window: QMainWindow, path: Path) -> None:
    """开（或复用）中央校准图面板：imshow + 理论环，接鼠标点击。

    已开同一文件的面板 → 前置返回；换文件 → 旧面板关闭（即遗忘）
    再开新的。图像在界面线程读一次（进 _image_cache），之后校准
    完成的重画都从缓存拿。
    """
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


def _close_calib_panel(window: QMainWindow) -> None:
    """关闭校准图面板：状态全清（关闭即遗忘）+ 在飞结果作废。

    幂等：没开面板直接返回。退出校准模式也走这里。
    """
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
    state = _calib_state(window)
    # 控制点来自 pyFAI extract_cp（自动/二次精修都会产出）：优先当前
    # 使用的那份，没有就退回自动定位的
    cps = None
    for key in (state.get("current"), "auto"):
        res = state.get(key) if key else None
        if res and res.get("control_points"):
            cps = res["control_points"]
            break
    _draw_calib_image(window, window.calib_key,
                      _calib_image(window, window.calib_path),
                      _calib_draw_geometry(window), control_points=cps,
                      ring_marks=state["points"])


# ══ 参数坞第 2 页：校准表单 ═══════════════════════════════════
def _build_calib_form(window: QMainWindow) -> QWidget:
    """参数坞第 2 页：校准工作台表单。

    布局（自上而下）：说明文字 → 模式单选（自动/手动）→ 自动区
    [开始自动校准] → 手动区（点数标签 + [撤销一点][清空] +
    [开始手动校准]，≥3 点且 ≥2 环才启用）→ 结果区（三列：自动 |
    手动 | Δ偏差）→ 保存区（key/label 输入 + 保存来源提示 +
    [保存为配置]）→ 底部 [返回分析模式] 出口。整个页面套滚动区
    （参数坞窄，放不下时滚动）。模式单选只是意图表达（两种模式可
    都跑、结果并列显示），不锁按钮。
    """
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.setSpacing(4)

    intro = QLabel("校准工作台：标样数据定几何（束心 / 距离 / 倾斜角）。"
                   "自动一键精修，或手动点图微调；两种模式共用结果区与"
                   "保存机制，按数据质量任选。")
    intro.setWordWrap(True)
    lay.addWidget(intro)

    # 模式单选：意图表达（两种模式可都跑），默认自动
    mode_box = QGroupBox("模式")
    mode_lay = QHBoxLayout(mode_box)
    radio_auto = QRadioButton("自动校准")
    radio_manual = QRadioButton("手动选点校准")
    radio_auto.setChecked(True)
    radio_auto.setToolTip("一键：自动找环心 → pyFAI 精修（约数秒）")
    radio_manual.setToolTip("在中央校准图上点环：≥3 个点、覆盖 ≥2 个环")
    mode_lay.addWidget(radio_auto)
    mode_lay.addWidget(radio_manual)
    mode_group = QButtonGroup(page)
    mode_group.addButton(radio_auto)
    mode_group.addButton(radio_manual)
    lay.addWidget(mode_box)
    window.calib_radio_auto = radio_auto
    window.calib_radio_manual = radio_manual

    # 自动区
    auto_box = QGroupBox("自动校准")
    auto_lay = QVBoxLayout(auto_box)
    auto_hint = QLabel("读取勾选的第一个标样文件：自动定位环心"
                       "（取点拟合，FFT 兜底）→ pyFAI 迭代精修。")
    auto_hint.setWordWrap(True)
    auto_lay.addWidget(auto_hint)
    btn_auto = QPushButton("开始自动校准")
    btn_auto.setObjectName("start_auto_calib")
    auto_lay.addWidget(btn_auto)
    lay.addWidget(auto_box)
    window.calib_start_auto = btn_auto
    btn_auto.clicked.connect(lambda: _start_auto_calib(window))

    # 手动区
    manual_box = QGroupBox("手动选点校准")
    manual_lay = QVBoxLayout(manual_box)
    manual_hint = QLabel("在中央校准图上点击衍射环：点自动吸附最近的"
                         "理论环（±0.5°），环号标在图上；至少 3 个点、"
                         "覆盖 2 个不同的环。")
    manual_hint.setWordWrap(True)
    manual_lay.addWidget(manual_hint)
    points_label = QLabel("已选 0 个点 / 0 个环")
    manual_lay.addWidget(points_label)
    row = QHBoxLayout()
    btn_undo = QPushButton("撤销一点")
    btn_clear = QPushButton("清空")
    row.addWidget(btn_undo)
    row.addWidget(btn_clear)
    manual_lay.addLayout(row)
    btn_manual = QPushButton("开始手动校准")
    btn_manual.setObjectName("start_manual_calib")
    manual_lay.addWidget(btn_manual)
    lay.addWidget(manual_box)
    window.calib_points_label = points_label
    window.calib_undo_btn = btn_undo
    window.calib_clear_btn = btn_clear
    window.calib_start_manual = btn_manual
    btn_undo.clicked.connect(lambda: _undo_calib_point(window))
    btn_clear.clicked.connect(lambda: _clear_calib_points(window))
    btn_manual.clicked.connect(lambda: _start_manual_calib(window))

    # 结果区：三列 自动 | 手动 | Δ偏差（两列都在才算 Δ）
    result_box = QGroupBox("结果")
    grid = QGridLayout(result_box)
    grid.setHorizontalSpacing(6)
    for c, text in enumerate(("自动", "手动", "Δ偏差"), start=1):
        hdr = QLabel(text)
        hdr.setAlignment(Qt.AlignCenter)
        hdr.setStyleSheet("color: gray;")
        grid.addWidget(hdr, 0, c)
    window.calib_vals = {"auto": {}, "manual": {}, "delta": {}}
    rows = (("距离 (mm)", "dist"), ("PONI (px)", "poni"),
            ("rot1 (°)", "rot1"), ("rot2 (°)", "rot2"), ("残差 (°)", "resid"))
    for r, (name, key_) in enumerate(rows, start=1):
        grid.addWidget(QLabel(name), r, 0)
        for c, col in enumerate(("auto", "manual", "delta"), start=1):
            label = QLabel("—")
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(label, r, c)
            window.calib_vals[col][key_] = label
    lay.addWidget(result_box)

    # 保存区：校准结果直接存成命名配置条目（本地用户文件，替换旧
    # 模板区——不再复制粘贴，保存即登记；保存后下拉框自动选中新条目）
    save_box = QGroupBox("保存为几何配置")
    save_lay = QVBoxLayout(save_box)
    save_hint = QLabel("把「当前使用」的校准结果存成命名配置条目：保存后"
                       "立即出现在分析页“几何配置”下拉框（重启后仍在，"
                       "命令行脚本同样可用 --config 选取）。条目写入"
                       "本地文件 config_user.json（不进 git），与人工"
                       "登记的内置条目互不干扰。")
    save_hint.setWordWrap(True)
    save_lay.addWidget(save_hint)
    key_edit = QLineEdit()
    key_edit.setPlaceholderText("条目 key，如 lmfp2_lab6")
    key_edit.setText(_suggest_config_key(config.DEFAULT_CONFIG))
    save_lay.addWidget(key_edit)
    label_edit = QLineEdit()
    label_edit.setPlaceholderText(
        "批次备注（label），如：lmfp 第 2 批（LaB₆ 标样标定）")
    save_lay.addWidget(label_edit)
    source_label = QLabel("尚未有校准结果")
    source_label.setStyleSheet("color: gray;")
    save_lay.addWidget(source_label)
    btn_save = QPushButton("保存为配置")
    btn_save.setObjectName("save_calib_config")
    save_lay.addWidget(btn_save)
    lay.addWidget(save_box)
    lay.addStretch(1)
    window.calib_key_edit = key_edit
    window.calib_label_edit = label_edit
    window.calib_save_hint = source_label
    window.calib_save_btn = btn_save
    btn_save.clicked.connect(lambda: _save_calib_config(window))

    # 出口：本页唯一的返回路径（与工具栏 [校准] 开关同源——把开关
    # 弹起 = toggled(False) → _on_mode 翻回分析页 + 关校准面板）。
    # 钉在页面底部：用户在当前页找出口，出口就在当前页。
    btn_exit = QPushButton("返回分析模式")
    btn_exit.setObjectName("exit_calib_btn")
    btn_exit.setToolTip("回到常规参数面板（校准图面板关闭，选点/结果"
                        "清空，关闭即遗忘）")
    btn_exit.clicked.connect(lambda: window.calib_btn.setChecked(False))
    lay.addWidget(btn_exit)
    window.calib_exit_btn = btn_exit

    # 套滚动区：参数坞窄，放不下时滚动（与分析页滚动区同一套路）
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(page)
    _calib_sync(window)   # 初始按钮状态（0 点 → 手动按钮置灰）
    return scroll


def _reset_calib_form(window: QMainWindow) -> None:
    """表单复位：结果三列清空、点数标签归零、按钮复位。"""
    for col in ("auto", "manual", "delta"):
        for label in window.calib_vals[col].values():
            label.setText("—")
    _calib_sync(window)


def _calib_sync(window: QMainWindow) -> None:
    """点数标签 + 手动按钮启用逻辑 + 保存按钮/来源提示同步。

    手动按钮：≥3 点且 ≥2 环才可开始；保存按钮："当前使用"的来源存在
    才可存（无结果时置灰，来源提示写明是哪条来源、"自定义"时标出来）。
    """
    state = _calib_state(window)
    points = state["points"]
    n_rings = len({p[2] for p in points})
    window.calib_points_label.setText(f"已选 {len(points)} 个点 / {n_rings} 个环")
    ok = len(points) >= MIN_POINTS and n_rings >= MIN_RINGS
    window.calib_start_manual.setEnabled(ok)
    window.calib_undo_btn.setEnabled(bool(points))
    window.calib_clear_btn.setEnabled(bool(points))
    mode = state.get("current")
    result = state.get(mode) if mode else None
    if result is None:
        window.calib_save_btn.setEnabled(False)
        window.calib_save_hint.setText("尚未有校准结果")
    else:
        window.calib_save_btn.setEnabled(True)
        mark = "（自定义）" if state.get("custom") else ""
        window.calib_save_hint.setText(
            f"将保存：{SOURCE_LABELS[mode]}{mark}"
            f"（距离 {result['dist_m'] * 1000:.2f} mm，"
            f"残差 {result['residual_deg']:.4f}°）")


# ══ 手动选点：点击判环 / 撤销 / 清空 ══════════════════════════
def _on_calib_click(window: QMainWindow, key: str, event) -> None:
    """校准图点击：判环吸附 → 记录点 + 图上标记；吸不上 → 日志忽略。

    判环用**屏幕上画青线的那套几何**（_calib_draw_geometry：最近一次
    校准结果覆盖面板初值）——与 _draw_calib_image 同源。用别的几何判，
    会出现"点着你看到的那条线、却判成隔壁环号"（几何偏差 23 px 在
    r=235 px 处约合 0.17°，而环间距只有 0.24~0.7°）。
    """
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
    state = _calib_state(window)
    if not state["points"]:
        return
    state["points"].pop()
    _log(window, f"已撤销最后一个点（剩 {len(state['points'])} 个）")
    _redraw_calib(window)
    _calib_sync(window)


def _clear_calib_points(window: QMainWindow) -> None:
    """清空全部选点（重画 + 标签复位）。"""
    state = _calib_state(window)
    if not state["points"]:
        return
    state["points"] = []
    _log(window, "已清空选点")
    _redraw_calib(window)
    _calib_sync(window)


# ══ 引擎指标：环位偏差 / 完整度 / a 离散（结果日志后缀）══════════
def _attach_metrics(result: dict, image, geom: dict,
                    initial: dict = None) -> None:
    """给校准结果就地附引擎指标，**绝不抛出**（失败记 metrics_error）。

    geom / initial 都是 _collect_geometry 形状的字典（initial = 预精修
    的初值几何，米制 PONI）；result 是引擎结果（px 键，**不含**像素
    尺寸与波长——它们不参与拟合）。指标要的像素尺寸/波长一律取自
    geom，距离/PONI/倾斜角取自被评估的那一份。

    写三个键：
      metrics          结果几何下的指标（ring_metrics 的输出）
      metrics_initial  初值几何下的指标；没给初值时 None——两者并排才
                       看得出"精修到底把环往图像的真环上挪了多少像素"
      metrics_error    失败原因（成功时不写这个键）

    设计：指标是**显示器**，不是校准本身。算不出来（几何键缺失、几何
    离谱、图像太小、引擎内部异常）时校准结果照常有效，日志如实说明——
    静默失败会让用户以为"指标说没问题"。
    """
    def _one(src: dict) -> dict:
        # 米制面板几何先转 px 键（转换也在守卫内：残缺字典不许穿透）
        g = _geom_px_keys(src) if "poni1_m" in src else src
        return ring_metrics(
            image, pixel_size_m=geom["pixel_size_m"],
            wavelength_m=geom["wavelength_m"], dist_m=g["dist_m"],
            poni1_px=g["poni1_px"], poni2_px=g["poni2_px"],
            rot1_deg=g["rot1_deg"], rot2_deg=g["rot2_deg"])

    result["metrics"] = None
    result["metrics_initial"] = None
    for key, src in (("metrics", result), ("metrics_initial", initial)):
        if src is None:
            continue
        try:
            result[key] = _one(src)      # 引擎结果本来不带指标，只管覆盖
        except Exception as exc:                      # noqa: BLE001
            result[key] = None
            result["metrics_error"] = f"{type(exc).__name__}: {exc}"


def _metrics_note(result: dict) -> str:
    """结果日志的中文指标后缀（没指标时尽量说明原因，不静默）。

    措辞约定：几何离谱时宁可说"无可用环信号 + 贴窗边比例"——**不用
    _warn_rings_off_image 那句"全部落在图像外"**，那句有守卫测试在数
    出现次数，混用会让计数含义变糊。
    """
    if result.get("metrics_error"):
        return f"｜指标不可用（{result['metrics_error']}）"
    m = result.get("metrics")
    if m is None:
        return ""
    n = len(m["rings"])
    if not np.isfinite(m["dev_px"]):
        # clip_frac 也可能无值（连搜索窗都放不进图像）——不能给用户看 nan%
        clip = m["clip_frac"]
        clip_txt = (f"峰值贴搜索窗边界 {clip:.0%}" if np.isfinite(clip)
                    else "搜索窗在图像内放不下")
        return (f"｜无可用环信号（{clip_txt}、完整环 {m['n_complete']}/{n}）")
    a = m["a"]
    a_txt = (f"a 离散 {a['spread_ppm']:.0f} ppm"
             if np.isfinite(a["spread_ppm"]) else "a 离散 —")
    init = result.get("metrics_initial")
    init_txt = (f"（初值 {init['dev_px']:.2f}）"
                if init is not None and np.isfinite(init["dev_px"]) else "")
    return (f"｜环位偏差中位 {m['dev_px']:.2f} px{init_txt}、"
            f"完整环 {m['n_complete']}/{n}、{a_txt}")


# ══ 后台任务：自动 / 手动（_spawn 同款守卫）═══════════════════
def _auto_calib_worker(path_str: str, geom: dict) -> dict:
    """后台线程纯计算：读标样 → 自动定环心（FFT 兜底）→ pyFAI 精修。

    结果附 beam_center_rc：(行, 列) 像素——这次新拟合的环心就是直射
    束落点 B（比沿用配置条目的旧 B 更准），[保存为配置] 用它入条目。

    图像已经在手，顺手附引擎指标（metrics / metrics_initial）——环位
    偏差量的是"精修后几何把理论环放到图像真环上了没有"，是用户在校
    准图上看得见的那件事。
    """
    image = load_diffraction_image(path_str)
    center = fit_center_from_rings(image)
    if center is None:
        cy, cx = find_ring_center(image)
    else:
        cy, cx = center["cy"], center["cx"]
    result = calibrate_lab6(
        image, pixel_size_m=geom["pixel_size_m"],
        wavelength_m=geom["wavelength_m"], dist0_m=geom["dist_m"],
        center0_px=(cx, cy))
    _attach_metrics(result, image, geom, initial=geom)
    result["beam_center_rc"] = (cy, cx)
    return result


def _manual_calib_worker(path_str, points, rings, geom: dict,
                         center0_px: tuple) -> dict:
    """后台线程纯计算：用户点 → pyFAI refine2 单轮精修 → 附引擎指标。

    结果附 beam_center_rc：手动精修不动束心（初值 = 配置条目 B），
    保存时沿用初值 B（与旧模板机制一致）。

    path_str = 标样文件路径（window.calib_path）：手动链路本身只要点
    坐标，指标却需要图像本身，所以这里多读一次图（后台线程，读失败
    只让指标缺失，不影响校准结果）。
    """
    result = refine_lab6_from_points(
        points, rings, pixel_size_m=geom["pixel_size_m"],
        wavelength_m=geom["wavelength_m"], dist0_m=geom["dist_m"],
        center0_px=center0_px)
    result["beam_center_rc"] = (center0_px[1], center0_px[0])
    if path_str is not None:
        try:
            image = load_diffraction_image(path_str)
        except Exception as exc:                      # noqa: BLE001
            result["metrics"] = None
            result["metrics_initial"] = None
            result["metrics_error"] = f"{type(exc).__name__}: {exc}"
        else:
            _attach_metrics(result, image, geom, initial=geom)
    return result


def _start_auto_calib(window: QMainWindow) -> None:
    """[开始自动校准]：确保标样面板开着 → 后台跑自动链路。"""
    path = _calib_standard_path(window)
    if path is None:
        _log(window, "请先在文件列表勾选标样文件")
        return
    _open_calib_panel(window, path)   # 面板关了/没开过：重开
    if getattr(window, "calib_dock", None) is None:
        return   # 图像读取失败（_open_calib_panel 已记日志）
    geom = _collect_geometry(window)
    gen = window.calib_gen
    key = "calib_auto"
    task = None

    def done(result):
        window._tasks.remove(task)
        if window._latest_task.get(key) is not task:
            return   # 已有更新的任务：旧结果静默
        del window._latest_task[key]
        if gen != getattr(window, "calib_gen", -1) \
                or getattr(window, "calib_dock", None) is None:
            return   # 面板关过/退出过模式：迟到结果作废
        _on_auto_done(window, result)

    def error(msg):
        window._tasks.remove(task)
        if window._latest_task.get(key) is not task:
            return
        del window._latest_task[key]
        if gen != getattr(window, "calib_gen", -1) \
                or getattr(window, "calib_dock", None) is None:
            return
        _log(window, f"自动校准失败：{msg}")

    task = BackgroundTask(_auto_calib_worker, str(path), geom,
                          on_done=done, on_error=error)
    window._latest_task[key] = task
    window._tasks.append(task)
    _log(window, f"开始自动校准 {path.name}（后台线程）")
    task.start()


def _start_manual_calib(window: QMainWindow) -> None:
    """[开始手动校准]：用户点后台精修（初值 = 配置条目 beam_center）。"""
    state = _calib_state(window)
    if len(state["points"]) < MIN_POINTS \
            or len({p[2] for p in state["points"]}) < MIN_RINGS:
        _log(window, f"手动校准至少需要 {MIN_POINTS} 个点、"
                     f"覆盖 {MIN_RINGS} 个不同的环")
        return
    geom = _collect_geometry(window)
    beam = window.config["beam_center"]   # (行, 列) = 直射束落点 B
    center0_px = (beam[1], beam[0])       # (列, 行) 换序
    # 快照当前选点（任务运行中点列表可能被撤销/清空，不影响本次计算）
    points = [(float(p[0]), float(p[1])) for p in state["points"]]
    rings = [int(p[2]) for p in state["points"]]
    state["manual_n"] = len(points)   # 完成回调里按点数提示可信度
    gen = window.calib_gen
    key = "calib_manual"
    task = None

    def done(result):
        window._tasks.remove(task)
        if window._latest_task.get(key) is not task:
            return
        del window._latest_task[key]
        if gen != getattr(window, "calib_gen", -1) \
                or getattr(window, "calib_dock", None) is None:
            return
        _on_manual_done(window, result)

    def error(msg):
        window._tasks.remove(task)
        if window._latest_task.get(key) is not task:
            return
        del window._latest_task[key]
        if gen != getattr(window, "calib_gen", -1) \
                or getattr(window, "calib_dock", None) is None:
            return
        _log(window, f"手动校准失败：{msg}")

    path = getattr(window, "calib_path", None)   # 指标要图像；没面板时为 None
    task = BackgroundTask(_manual_calib_worker, str(path) if path else None,
                          points, rings, geom, center0_px,
                          on_done=done, on_error=error)
    window._latest_task[key] = task
    window._tasks.append(task)
    _log(window, f"开始手动校准（{len(points)} 个点，后台线程）")
    task.start()


# ══ 结果 / Δ / 保存为配置 ════════════════════════════════════
def _on_auto_done(window: QMainWindow, result: dict) -> None:
    """自动校准完成（主线程）：自动列填值 + 图按新几何重画（环圈 +
    绿点控制点）+ 模板刷新。"""
    state = _calib_state(window)
    state["auto"] = result
    _fill_calib_result(window, "auto", result)
    _fill_calib_delta(window)
    # 先定"当前使用"再重画/同步——画的是当前几何、保存按钮看的是当前结果
    note = _update_current(state, "auto")
    _redraw_calib(window)
    _calib_sync(window)
    _log(window, f"自动校准完成：距离 {result['dist_m'] * 1000:.2f} mm，"
                 f"PONI ({result['poni1_px']:.2f}, {result['poni2_px']:.2f}) px，"
                 f"残差 {result['residual_deg']:.4f}°"
                 f"{_metrics_note(result)}")
    _log(window, note)


def _on_manual_done(window: QMainWindow, result: dict) -> None:
    """手动校准完成（主线程）：手动列填值 + Δ 列 + 图按新几何重画。"""
    state = _calib_state(window)
    state["manual"] = result
    _fill_calib_result(window, "manual", result)
    _fill_calib_delta(window)
    note = _update_current(state, "manual")
    _redraw_calib(window)
    _calib_sync(window)
    _log(window, f"手动校准完成：距离 {result['dist_m'] * 1000:.2f} mm，"
                 f"残差 {result['residual_deg']:.4f}°"
                 f"{_metrics_note(result)}")
    _log(window, note)
    if state.get("manual_n", 99) < 6:
        # 点数少时最小二乘对单点点击误差敏感（可能滑向退化解），
        # 残差小也不代表可信——如实提示，Δ 列让用户自己判断
        _log(window, "提示：点数较少（<6）时手动结果可能不稳，建议多点"
                     "几个环上的点再跑一次，以 Δ 偏差判断可信度")


def _fill_calib_result(window: QMainWindow, col: str, result: dict) -> None:
    """把校准结果填进结果区的某一列（auto/manual）。"""
    vals = window.calib_vals[col]
    vals["dist"].setText(f"{result['dist_m'] * 1000:.2f}")
    vals["poni"].setText(f"({result['poni1_px']:.2f}, {result['poni2_px']:.2f})")
    vals["rot1"].setText(f"{result['rot1_deg']:.4f}")
    vals["rot2"].setText(f"{result['rot2_deg']:.4f}")
    vals["resid"].setText(f"{result['residual_deg']:.4f}")


def _fill_calib_delta(window: QMainWindow) -> None:
    """Δ 列 = 手动 − 自动（两列都在才算）。PONI 偏差 = 两点距离 (px)，
    其余行 = 数值差（带符号，看得出方向）。"""
    state = _calib_state(window)
    a, m = state["auto"], state["manual"]
    vals = window.calib_vals["delta"]
    if a is None or m is None:
        for label in vals.values():
            label.setText("—")
        return
    vals["dist"].setText(f"{m['dist_m'] * 1000 - a['dist_m'] * 1000:+.2f}")
    vals["poni"].setText(
        f"{np.hypot(m['poni1_px'] - a['poni1_px'],
                    m['poni2_px'] - a['poni2_px']):.2f}")
    vals["rot1"].setText(f"{m['rot1_deg'] - a['rot1_deg']:+.4f}")
    vals["rot2"].setText(f"{m['rot2_deg'] - a['rot2_deg']:+.4f}")
    vals["resid"].setText(f"{m['residual_deg'] - a['residual_deg']:+.4f}")


def _suggest_config_key(current_key: str) -> str:
    """从当前配置 key 递推新条目建议名：lmfp1_lab6 → lmfp2_lab6。

    懒前缀匹配**第一个**数字段 +1（新批次编号顺延，命名约定见
    config.py；后缀里再出现数字不受影响，如 "_lab6" 的 6 不动）；
    对不上该模式就退回通用提示名。
    """
    m = re.match(r"^(.*?)(\d+)(.*)$", current_key)
    if m:
        return f"{m.group(1)}{int(m.group(2)) + 1}{m.group(3)}"
    return "lab6_calib"


def _confirm_overwrite(window: QMainWindow, key: str) -> bool:
    """用户条目重名确认：覆盖返回 True（用户自己拍板，点击即复核）。"""
    return QMessageBox.question(
        window, "覆盖已有配置",
        f"用户配置 {key} 已存在。用这次的校准结果覆盖它吗？"
    ) == QMessageBox.Yes


def _save_calib_config(window: QMainWindow) -> None:
    """[保存为配置]："当前使用"的校准结果 → 命名用户条目（本地落盘）。

    校验 key/label → 与内置条目撞名拒绝、与已存用户条目撞名弹确认
    覆盖 → config.save_user_config 落盘 → 下拉框重建并自动选中新
    条目（_apply_config 把几何填进参数坞，保存即生效）。

    保存的 geometry = 校准结果（距离/PONI/倾斜角）+ 参数坞当前像素
    /波长输入；束心 = 结果附带的 beam_center_rc（自动 = 新拟合环心，
    手动 = 初值 B）；residual_deg 一并存入作诊断量（消费方忽略）。
    """
    state = _calib_state(window)
    mode = state.get("current")
    result = state.get(mode) if mode else None
    if result is None:
        _log(window, "没有可保存的校准结果（先跑一次自动或手动校准）")
        return
    key = window.calib_key_edit.text().strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        _log(window, "key 无效：只允许字母/数字/下划线，且以字母或"
                     "下划线开头（如 lmfp2_lab6）")
        return
    if key in config.BUILTIN_CONFIGS:
        _log(window, f"key {key} 与内置条目重名（内置条目人工登记，"
                     "不可覆盖），换一个名字")
        return
    label = window.calib_label_edit.text().strip()
    if not label:
        _log(window, "请先填写批次备注（label）")
        return
    if key in config.USER_CONFIGS and not _confirm_overwrite(window, key):
        return
    g = _collect_geometry(window)
    pixel = g["pixel_size_m"]
    entry = {
        "label": label,
        "geometry": {
            "pixel_size_m": pixel,
            "wavelength_m": g["wavelength_m"],
            "dist_m": result["dist_m"],
            "poni1_m": result["poni1_px"] * pixel,
            "poni2_m": result["poni2_px"] * pixel,
            "rot1_deg": result["rot1_deg"],
            "rot2_deg": result["rot2_deg"],
        },
        "beam_center": tuple(
            result.get("beam_center_rc", window.config["beam_center"])),
        "residual_deg": result["residual_deg"],
    }
    try:
        is_new = config.save_user_config(key, entry)
    except ValueError as err:
        _log(window, f"保存失败：{err}")
        return
    _reload_config_combo(window, key)
    if is_new:
        _log(window, f"已保存新配置条目 {key} 并自动选中；重启后仍在，"
                     f"命令行脚本可用 --config {key} 取用")
    else:
        _log(window, f"已覆盖配置条目 {key} 并自动选中")


# ══ 模式进出（app._on_mode 调用）══════════════════════════════
def _enter_calib(window: QMainWindow) -> None:
    """进入校准模式（[校准] 按下）：勾选的第一个文件开校准面板。

    没勾文件只记日志提示（不崩）——用户勾好文件后点 [开始自动校准]
    也能开面板。
    """
    path = _calib_standard_path(window)
    if path is None:
        _log(window, "请先在文件列表勾选标样文件")
        return
    _open_calib_panel(window, path)


def _exit_calib(window: QMainWindow) -> None:
    """退出校准模式：关校准面板（状态清零，关闭即遗忘）。"""
    _close_calib_panel(window)
