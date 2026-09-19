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

界面分工：
  - 参数坞第 2 页 = _build_calib_form（模式单选 + 自动/手动按钮区 +
    结果三列区 自动|手动|Δ偏差 + 保存为配置区）。两种模式共用同一
    结果区与保存机制：最近完成模式的结果可直接存成命名用户条目
    （config_user.json，不进 git），保存后分析页"几何配置"下拉框
    立即出现并自动选中（几何填进参数坞）。内置 config.py 注册表
    仍走 CLI 模板人工登记（见 config.py 文件头）。
  - 中央校准图面板 = _CalibSubWindow（MDI 子窗口，imshow + 理论环
    圆圈 + 控制点/用户点标记，只接鼠标点击）。不进 plot_docks：
    不掺和编辑对象焦点、平铺、总缩放；无手势无抓手（v1 从简）。

状态（都挂在 window 上）：
  calib_dock / calib_key / calib_path / calib_display / calib_canvas
  / calib_ax   面板与画布（None = 没开面板）
  calib_gen    代计数：面板关闭/重开/退出模式时 +1，在飞任务回调
               核对代数，迟到结果静默作废
  calib_state  {"points": [(x, y, 环号), ...], "auto": 结果|None,
                "manual": 结果|None, "last": 最近完成模式}——关闭
                面板即全清（关闭即遗忘）
"""
import re
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import LogNorm
from matplotlib.figure import Figure
from matplotlib.patches import Circle
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
    calibrate_lab6, lab6_theoretical_2theta, refine_lab6_from_points,
    snap_lab6_ring)

SNAP_TOL_DEG = 0.5      # 判环容差（2θ 度；与 snap_lab6_ring 默认一致）
MIN_POINTS = 3          # 手动校准最低点数
MIN_RINGS = 2           # 手动校准最低覆盖环数
PANEL_SCALE = 560       # 校准图面板最长边（像素，图太大就按此缩小）
RING_COLOR = "#00e5ff"  # 理论环 / 用户点标记色（青）
CP_COLOR = "#3dff3d"    # pyFAI 控制点标记色（绿）


# ══ 小工具：状态 / 文件 / 图像 ══════════════════════════════════
def _calib_state(window: QMainWindow) -> dict:
    """校准状态（懒创建）：选点列表 + 两种模式的结果。"""
    state = getattr(window, "calib_state", None)
    if state is None:
        state = window.calib_state = {"points": [], "auto": None,
                                      "manual": None, "last": None}
    return state


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
    """画图几何（统一 px 键）：最近一次校准结果覆盖参数面板初值。

    自动/手动校准完成后理论环按新几何重画（用户验证精修效果）；
    没跑过校准则按参数面板当前值画预测环。
    """
    g = _collect_geometry(window)
    draw = dict(
        pixel_size_m=g["pixel_size_m"], wavelength_m=g["wavelength_m"],
        dist_m=g["dist_m"], poni1_px=g["poni1_m"] / g["pixel_size_m"],
        poni2_px=g["poni2_m"] / g["pixel_size_m"],
        rot1_deg=g["rot1_deg"], rot2_deg=g["rot2_deg"])
    state = _calib_state(window)
    mode = state.get("last")
    result = state.get(mode) if mode else None
    if result is not None:
        for key in ("dist_m", "poni1_px", "poni2_px", "rot1_deg", "rot2_deg"):
            draw[key] = result[key]
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
    """整幅重画校准图：图像 + 理论环圆圈 + 可选绿点/用户点标记。

    对齐 scripts/view_diffraction.py 的显示：magma + LogNorm、自动
    对比度 1%/99.9% 分位、vmin 下限 1.0、origin="lower"。理论环
    半径 r = dist·tan(2θ)/pixel、圆心 = PONI px（geometry 里的 px
    键）；用户点 = 青圈 + 环号（点图找环的依据），控制点 = 绿点
    （pyFAI 实际取点，验证精修效果）。
    """
    ax = window.calib_ax
    ax.clear()
    lo, hi = _auto_contrast_values(image)
    vmin = max(1.0, lo)
    vmax = max(hi, vmin * 10.0)
    ax.imshow(image, cmap="magma", norm=LogNorm(vmin=vmin, vmax=vmax),
              origin="lower")
    ax.set_aspect("equal")
    theo = lab6_theoretical_2theta(geometry["wavelength_m"])
    for i, tth_deg in enumerate(theo):
        r_px = geometry["dist_m"] * np.tan(np.radians(tth_deg)) \
            / geometry["pixel_size_m"]
        ax.add_patch(Circle((geometry["poni1_px"], geometry["poni2_px"]),
                            r_px, fill=False, color=RING_COLOR, lw=0.7,
                            alpha=0.65))
        ax.annotate(str(i), (geometry["poni1_px"] + r_px,
                             geometry["poni2_px"]),
                    color=RING_COLOR, fontsize=7, va="center")
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
    ax.set_xlabel("横向 (px)")
    ax.set_ylabel("纵向 (px)")
    ax.set_title(window.calib_display)
    window.calib_canvas.draw_idle()


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
    cps = None
    if state.get("auto"):
        cps = state["auto"].get("control_points")
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
    [保存为配置]）。整个页面套滚动区（参数坞窄，放不下时滚动）。
    模式单选只是意图表达（两种模式可都跑、结果并列显示），不锁按钮。
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
    save_hint = QLabel("把最近完成的校准结果存成命名配置条目：保存后"
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

    手动按钮：≥3 点且 ≥2 环才可开始；保存按钮：最近一次完成的校准
    结果存在才可存（无结果时置灰，来源提示随"最近完成模式"更新）。
    """
    state = _calib_state(window)
    points = state["points"]
    n_rings = len({p[2] for p in points})
    window.calib_points_label.setText(f"已选 {len(points)} 个点 / {n_rings} 个环")
    ok = len(points) >= MIN_POINTS and n_rings >= MIN_RINGS
    window.calib_start_manual.setEnabled(ok)
    window.calib_undo_btn.setEnabled(bool(points))
    window.calib_clear_btn.setEnabled(bool(points))
    mode = state.get("last")
    result = state.get(mode) if mode else None
    if result is None:
        window.calib_save_btn.setEnabled(False)
        window.calib_save_hint.setText("尚未有校准结果")
    else:
        window.calib_save_btn.setEnabled(True)
        window.calib_save_hint.setText(
            f"将保存：{'自动' if mode == 'auto' else '手动'}校准"
            f"（距离 {result['dist_m'] * 1000:.2f} mm，"
            f"残差 {result['residual_deg']:.4f}°）")


# ══ 手动选点：点击判环 / 撤销 / 清空 ══════════════════════════
def _on_calib_click(window: QMainWindow, key: str, event) -> None:
    """校准图点击：判环吸附 → 记录点 + 图上标记；吸不上 → 日志忽略。

    判环用参数面板当前几何（自动校准完成后用户点仍按面板初值判——
    判环只需要大致几何，容差内不受影响）。
    """
    if event.xdata is None or event.ydata is None:
        return   # 点在坐标轴外
    if event.inaxes is not getattr(window, "calib_ax", None):
        return
    if getattr(window, "calib_dock", None) is None \
            or getattr(window, "calib_key", None) != key:
        return   # 面板已关/换过：迟到点击忽略
    g = _collect_geometry(window)
    ring = snap_lab6_ring(
        float(event.xdata), float(event.ydata),
        pixel_size_m=g["pixel_size_m"], wavelength_m=g["wavelength_m"],
        dist_m=g["dist_m"], poni1_m=g["poni1_m"], poni2_m=g["poni2_m"],
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


# ══ 后台任务：自动 / 手动（_spawn 同款守卫）═══════════════════
def _auto_calib_worker(path_str: str, geom: dict) -> dict:
    """后台线程纯计算：读标样 → 自动定环心（FFT 兜底）→ pyFAI 精修。

    结果附 beam_center_rc：(行, 列) 像素——这次新拟合的环心就是直射
    束落点 B（比沿用配置条目的旧 B 更准），[保存为配置] 用它入条目。
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
    result["beam_center_rc"] = (cy, cx)
    return result


def _manual_calib_worker(points, rings, geom: dict, center0_px: tuple) -> dict:
    """后台线程纯计算：用户点 → pyFAI refine2 单轮精修。

    结果附 beam_center_rc：手动精修不动束心（初值 = 配置条目 B），
    保存时沿用初值 B（与旧模板机制一致）。"""
    result = refine_lab6_from_points(
        points, rings, pixel_size_m=geom["pixel_size_m"],
        wavelength_m=geom["wavelength_m"], dist0_m=geom["dist_m"],
        center0_px=center0_px)
    result["beam_center_rc"] = (center0_px[1], center0_px[0])
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

    task = BackgroundTask(_manual_calib_worker, points, rings, geom,
                          center0_px, on_done=done, on_error=error)
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
    state["last"] = "auto"
    _fill_calib_result(window, "auto", result)
    _fill_calib_delta(window)
    _redraw_calib(window)
    _calib_sync(window)
    _log(window, f"自动校准完成：距离 {result['dist_m'] * 1000:.2f} mm，"
                 f"PONI ({result['poni1_px']:.2f}, {result['poni2_px']:.2f}) px，"
                 f"残差 {result['residual_deg']:.4f}°")


def _on_manual_done(window: QMainWindow, result: dict) -> None:
    """手动校准完成（主线程）：手动列填值 + Δ 列 + 图按新几何重画。"""
    state = _calib_state(window)
    state["manual"] = result
    state["last"] = "manual"
    _fill_calib_result(window, "manual", result)
    _fill_calib_delta(window)
    _redraw_calib(window)
    _calib_sync(window)
    _log(window, f"手动校准完成：距离 {result['dist_m'] * 1000:.2f} mm，"
                 f"残差 {result['residual_deg']:.4f}°")
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
    """[保存为配置]：最近完成的校准结果 → 命名用户条目（本地落盘）。

    校验 key/label → 与内置条目撞名拒绝、与已存用户条目撞名弹确认
    覆盖 → config.save_user_config 落盘 → 下拉框重建并自动选中新
    条目（_apply_config 把几何填进参数坞，保存即生效）。

    保存的 geometry = 校准结果（距离/PONI/倾斜角）+ 参数坞当前像素
    /波长输入；束心 = 结果附带的 beam_center_rc（自动 = 新拟合环心，
    手动 = 初值 B）；residual_deg 一并存入作诊断量（消费方忽略）。
    """
    state = _calib_state(window)
    mode = state.get("last")
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
