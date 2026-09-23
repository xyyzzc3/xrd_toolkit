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
from datetime import datetime
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import LogNorm
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMdiSubWindow, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget)

# 常量都住在 calib_model（纯逻辑层）。这里把面板/表格用的几个也一并
# 导入，保持『经 gui_calib 取用』的历史引用可用（同 app.py 的再导出惯例）。
from xrd_toolkit.gui.calib_model import (
    CALIB_PANEL_RESERVE_PX, COMPARE_HINT, COMPARE_ROWS, CP_COLOR,
    KIND_LABELS, MIN_POINTS, MIN_RINGS, PANEL_SCALE, RING_COLOR,
    SLOT_LABELS, SNAP_TOL_DEG,
    SOURCE_IMPROVE_MIN_PX, _add_result, _adopt_decision, _calib_state,
    _fill_slot, _geom_to_result_shape, _next_name, _result_by_name,
    _result_dev, _result_to_geom, _slot_label, _verdict)
from xrd_toolkit.gui.calib_table import (
    _on_slot_changed, _refresh_table, _sync_slot_combos)
from xrd_toolkit.gui.calib_panel import (
    _CalibSubWindow, _calib_standard_path, _clear_calib_points,
    _close_calib_panel, _geom_px_keys, _load_image, _open_calib_panel,
    _redraw_calib, _undo_calib_point, _warn_rings_off_image)
from xrd_toolkit.gui.config_ops import (
    _delete_config, _import_poni, _save_calib_config, _save_poni,
    _suggest_config_key, _sync_del_config_btn)

from xrd_toolkit import config
from xrd_toolkit.core.processor import find_ring_center, fit_center_from_rings
from xrd_toolkit.gui.panels import _settle
from xrd_toolkit.gui.panel_state import (
    _auto_contrast_values, _collect_geometry, _log, _reload_config_combo)
from xrd_toolkit.gui.tasks import BackgroundTask
from xrd_toolkit.services.integrator import (
    calibrate_lab6, refine_lab6_from_points)
from xrd_toolkit.services.ring_metrics import ring_metrics


# ── 结果列表与三个槽 ────────────────────────────────────────


def _adopt_result(window: QMainWindow, name: str, why: str = "自动采纳") -> None:
    """把当前配置换成这条结果（几何 / 指标 / 来处一起换）+ 重画青线。"""
    state = _calib_state(window)
    item = _result_by_name(state, name)
    if item is None:
        return
    state["slots"]["current"] = name
    state["current_geom"] = _result_to_geom(item["result"],
                                            state["current_geom"])
    state["current_metrics"] = item["result"].get("metrics")
    state["current_error"] = item["result"].get("metrics_error")
    state["current_from"] = name
    state["custom"] = False
    if why:
        dev = _result_dev(item["result"])
        dev_txt = f"环位偏差 {dev:.2f} px" if dev is not None else "无可用环信号"
        _log(window, f"当前配置 ← {name}（{why}，{dev_txt}）")
    _redraw_calib(window)


# ── 当前配置：起点（借用条目 / 手输手改）、像素确认、指标 ──────
def _ensure_current(window: QMainWindow) -> None:
    """当前配置还没设定时：默认借分析页选中的配置条目。

    借来的这条几何同时登记成累积列表的第一条（"原始"）——这样 A/B 随时
    能拿精修结果跟"没校准过"的样子比，回答"校准到底有没有用"。
    """
    state = _calib_state(window)
    if state["current_geom"] is None:
        _borrow_entry(window, getattr(window, "config_name",
                                      config.DEFAULT_CONFIG), silent=True)


def _borrow_entry(window: QMainWindow, key: str, silent: bool = False) -> str:
    """当前配置 ← 借一条配置条目的几何（已在累积的结果不动）。"""
    entry = config.CONFIGS.get(key)
    if entry is None:
        return f"没有配置条目 {key}"
    state = _calib_state(window)
    geom = dict(entry["geometry"])
    geom["beam_center_rc"] = tuple(entry.get("beam_center", (None, None)))
    state["current_geom"] = geom
    state["slots"]["current"] = None
    state["current_from"] = f"借用 {key}"
    state["base_key"] = key          # 血缘：这一批的起点是从哪条借的
    state["custom"] = False
    state["current_metrics"] = None
    state["current_error"] = None
    if not state["results"]:          # 累积列表的第一条 = "原始"
        state["results"].append(
            {"name": _next_name(state, "raw"), "kind": "raw",
             "result": _geom_to_result_shape(geom)})
    if not silent:
        _log(window, f"当前配置 ← 借用条目 {key}"
                     f"（距离 {geom['dist_m'] * 1e3:.2f} mm）")
    _refresh_current_metrics(window)
    return state["current_from"]


def _pixel_ok(window: QMainWindow) -> bool:
    """像素尺寸确认（规则 (b)）：只在**像素值真的变了**时才要重确认。

    为什么非要这一步：环落在哪个像素半径上，只由 λ、像素尺寸、距离三者
    的**组合**决定（tan 2θ = 半径 × 像素 / 距离）——三者同比例缩放时环
    一模一样。λ 与像素尺寸是能从光源/探测器规格确认的已知量；两个都确认
    了，精修出的距离才是真距离。像素尺寸填错时拟合会把距离按同比例凑
    回来：环位偏差、a 离散度、完整环数**全都正常**，只有报出来的距离是
    错的（还会一路写进 .poni 与报告）。
    """
    want = (_calib_state(window)["current_geom"] or {}).get("pixel_size_m")
    ok = getattr(window, "calib_pixel_ok_m", None)
    return want is not None and ok is not None and abs(ok - want) < 1e-12


def _set_pixel_ok(window: QMainWindow, on: bool) -> None:
    """勾选/取消「像素尺寸已确认」（记下确认时的像素值，规则 (b)）。"""
    geom = _calib_state(window)["current_geom"] or {}
    pixel = geom.get("pixel_size_m")
    window.calib_pixel_ok_m = pixel if on else None
    if on and pixel is not None:
        _log(window, f"像素尺寸已确认：{pixel * 1e6:.1f} µm"
                     f"（当前配置：{_slot_label(_calib_state(window), 'current')}）")


def _initial_ready(window: QMainWindow) -> bool:
    """能不能开跑校准：当前配置的像素尺寸必须确认过（否则只提示、不建任务）。"""
    if _pixel_ok(window):
        return True
    _log(window, "请先确认「当前配置」的像素尺寸（勾上确认框）——像素尺寸"
                 "与波长、距离同比例缩放时环一模一样，填错时拟合会把距离"
                 "凑回来：环位偏差看着正常，但报出来的距离是错的")
    return False


def _current_geom_text(window: QMainWindow) -> str:
    """当前配置一行的说明文字（表头下面的小字）。"""
    state = _calib_state(window)
    geom = state["current_geom"] or {}
    from_txt = _slot_label(state, "current")
    if geom.get("dist_m") is None:
        return f"当前配置：{from_txt}"
    return (f"当前配置：{from_txt}（距离 {geom['dist_m'] * 1e3:.2f} mm、"
            f"像素 {(geom.get('pixel_size_m') or 0) * 1e6:.1f} µm）")


def _edit_current(window: QMainWindow) -> None:
    """[编辑…]：对话框里改当前配置的几何（改完标成"自定义"）。

    对话框顶部可以先"从条目预填"（= 借另一条条目的几何），再逐个改数值
    ——于是"借用其它批次"与"手输"是同一件事的两个入口，页面上不占地方。
    """
    state = _calib_state(window)
    geom = dict(state["current_geom"] or {})
    if not geom:
        _log(window, "当前配置还没设定（先选一个标准文件进校准模式）")
        return
    px = _geom_px_keys(geom)
    dlg = QDialog(window)
    dlg.setWindowTitle("编辑当前配置")
    form = QFormLayout(dlg)
    pre = QComboBox()
    pre.addItem("（不改，只逐个编辑数值）", None)
    for name in config.CONFIGS:
        pre.addItem(f"从条目预填：{name}", name)
    form.addRow("预填", pre)
    fields = (
        ("像素尺寸 (µm)", "pixel_um", px["pixel_size_m"] * 1e6, 1, " µm"),
        ("波长 (Å)", "wavelength_a", px["wavelength_m"] * 1e10, 4, " Å"),
        ("距离 (mm)", "dist_mm", px["dist_m"] * 1e3, 2, " mm"),
        ("PONI1 (px)", "poni1_px", px["poni1_px"], 2, ""),
        ("PONI2 (px)", "poni2_px", px["poni2_px"], 2, ""),
        ("rot1 (°)", "rot1_deg", px["rot1_deg"], 4, " °"),
        ("rot2 (°)", "rot2_deg", px["rot2_deg"], 4, " °"),
    )
    boxes = {}
    for text, key_, value, dec, suffix in fields:
        box = QDoubleSpinBox()
        box.setRange(-1e5, 1e5)
        box.setDecimals(dec)
        if suffix:
            box.setSuffix(suffix)
        box.setValue(float(value))
        form.addRow(text, box)
        boxes[key_] = box

    def prefill(_idx):
        key = pre.currentData()
        if key is None:
            return
        g = config.CONFIGS[key]["geometry"]
        boxes["pixel_um"].setValue(g["pixel_size_m"] * 1e6)
        boxes["wavelength_a"].setValue(g["wavelength_m"] * 1e10)
        boxes["dist_mm"].setValue(g["dist_m"] * 1e3)
        boxes["poni1_px"].setValue(g["poni1_m"] / g["pixel_size_m"])
        boxes["poni2_px"].setValue(g["poni2_m"] / g["pixel_size_m"])
        boxes["rot1_deg"].setValue(g["rot1_deg"])
        boxes["rot2_deg"].setValue(g["rot2_deg"])
        boxes["_beam"] = tuple(config.CONFIGS[key].get("beam_center", (None, None)))

    pre.currentIndexChanged.connect(prefill)
    row = QHBoxLayout()
    ok_btn = QPushButton("确定")
    cancel_btn = QPushButton("取消")
    ok_btn.setObjectName("edit_current_ok")
    ok_btn.clicked.connect(dlg.accept)
    cancel_btn.clicked.connect(dlg.reject)
    row.addWidget(ok_btn)
    row.addWidget(cancel_btn)
    form.addRow(row)
    if dlg.exec() != QDialog.Accepted:
        return
    pixel = boxes["pixel_um"].value() * 1e-6
    new_geom = {
        "pixel_size_m": pixel,
        "wavelength_m": boxes["wavelength_a"].value() * 1e-10,
        "dist_m": boxes["dist_mm"].value() * 1e-3,
        "poni1_m": boxes["poni1_px"].value() * pixel,
        "poni2_m": boxes["poni2_px"].value() * pixel,
        "rot1_deg": boxes["rot1_deg"].value(),
        "rot2_deg": boxes["rot2_deg"].value(),
        "beam_center_rc": geom.get("beam_center_rc"),
    }
    for key in ("tth_min_deg", "tth_max_deg"):
        if key in geom:
            new_geom[key] = geom[key]
    state["current_geom"] = new_geom
    state["slots"]["current"] = None
    state["current_from"] = "手输"
    state["custom"] = True                    # 改过就是"自定义"
    state["current_metrics"] = None
    state["current_error"] = None
    _log(window, f"当前配置已手动修改（自定义）：距离 "
                 f"{new_geom['dist_m'] * 1e3:.2f} mm、像素 {pixel * 1e6:.1f} µm")
    _refresh_current_metrics(window)
    _calib_sync(window)


def _metrics_worker(path_str: str, geom: dict) -> dict:
    """后台线程：算一份几何的指标（当前配置的环位偏差专用）。

    `_attach_metrics(result, ...)` 的第一个参数就是**被量的那份几何**，
    所以这里用几何本身当载体（空 dict 会 KeyError: 'dist_m'——踩过）。
    """
    image = _load_image(path_str)
    out = dict(geom)
    _attach_metrics(out, image, geom)     # initial=None：不重复算初值那一份
    return out


def _refresh_current_metrics(window: QMainWindow) -> None:
    """异步算当前配置的环位偏差（换条目 / 手改之后要重算）。

    算不出来（没开面板、图读不到、几何离谱）只让那一格显示"—"，绝不阻塞
    界面、也不动别的槽。
    """
    state = _calib_state(window)
    path = getattr(window, "calib_path", None)
    geom = state.get("current_geom")
    if path is None or geom is None:
        return
    gen = getattr(window, "calib_gen", 0)
    key = "calib_metrics"
    task = None

    def done(result):
        window._tasks.remove(task)
        if window._latest_task.get(key) is not task:
            return
        del window._latest_task[key]
        if gen != getattr(window, "calib_gen", -1):
            return
        state["current_metrics"] = result.get("metrics")
        state["current_error"] = result.get("metrics_error")
        _calib_sync(window)

    def error(msg):
        window._tasks.remove(task)
        window._latest_task.pop(key, None)
        if gen != getattr(window, "calib_gen", -1):
            return
        state["current_metrics"] = None
        state["current_error"] = msg
        _calib_sync(window)

    task = BackgroundTask(_metrics_worker, str(path), dict(geom),
                          on_done=done, on_error=error)
    window._latest_task[key] = task
    window._tasks.append(task)
    task.start()


# ══ 中央校准图面板 ═══════════════════════════════════════════


# ══ 参数坞第 2 页：校准表单 ═══════════════════════════════════
def _build_calib_form(window: QMainWindow) -> QWidget:
    """参数坞第 2 页：校准功能。

    布局（自上而下）：
      三列表  表头三个下拉（当前配置 / A / B——都从累积结果里选；当前
              配置还能借条目或手输，只是不在这个下拉里表达）+ 8 行数值
              + 2 行 Δ（相对"对比基准"）+ 基准下拉 + 结论行 + ⚠ 说明
      操作区  [编辑…] [导入][保存] / [删除][存为配置] + 像素尺寸确认
      自动    定位环心并精修 / 在当前配置上再精修
      手动    选点计数 + 撤销/清空 + 用选点精修
      出口    返回分析模式
    整页套滚动区；进校准模式时参数坞会按本页内容拉宽（见 _enter_calib）。
    """
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.setSpacing(4)

    intro = QLabel("校准功能：用标样定几何（束心 / 距离 / 倾斜角）。"
                   "三列 = 当前配置（要用的那份）与 A / B 两个对比位；"
                   "跑完自动/手动后，结果进列表并按环位偏差决定要不要"
                   "替换当前配置。")
    intro.setWordWrap(True)
    lay.addWidget(intro)

    # ── 三列表 ─────────────────────────────────────────────
    table_box = QGroupBox("数据")
    tb = QVBoxLayout(table_box)
    head = QHBoxLayout()
    head.setSpacing(4)
    window.calib_slot_combo = {}
    for slot in ("current", "A", "B"):
        combo = QComboBox()
        combo.setToolTip({
            "current": "当前配置：选一条结果即采纳为当前配置；"
                       "[编辑…] 可借条目或手输",
            "A": "对比位 A：从累积结果里选；手动选过之后新结果不再覆盖它",
            "B": "对比位 B：同上"}[slot])
        combo.currentIndexChanged.connect(
            lambda _i, s=slot: _on_slot_changed(window, s))
        head.addWidget(combo, 1 if slot == "current" else 1)
        window.calib_slot_combo[slot] = combo
    tb.addLayout(head)

    grid = QGridLayout()
    grid.setHorizontalSpacing(6)
    window.calib_vals = {"current": {}, "A": {}, "B": {}, "delta": {}}
    for c, text in enumerate(("当前配置", "A", "B"), start=1):
        hdr = QLabel(text)
        hdr.setAlignment(Qt.AlignCenter)
        hdr.setStyleSheet("color: gray;")
        grid.addWidget(hdr, 0, c)
    row_idx = 1
    for key_, name, _scale, _fmt, has_delta in COMPARE_ROWS:
        grid.addWidget(QLabel(name), row_idx, 0)
        for c, slot in enumerate(("current", "A", "B"), start=1):
            label = QLabel("—")
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(label, row_idx, c)
            window.calib_vals[slot][key_] = label
        row_idx += 1
        if has_delta:                     # Δ 行紧跟在它下面
            grid.addWidget(QLabel(f"Δ{name.split(' (')[0]}"), row_idx, 0)
            for c, slot in enumerate(("current", "A", "B"), start=1):
                label = QLabel("—")
                label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                label.setStyleSheet("color: gray;")
                grid.addWidget(label, row_idx, c)
                window.calib_vals["delta"].setdefault(key_, {})[slot] = label
            row_idx += 1
    tb.addLayout(grid)

    base_row = QHBoxLayout()
    base_row.addWidget(QLabel("对比基准"))
    combo_base = QComboBox()
    for slot in ("current", "A", "B"):
        combo_base.addItem(SLOT_LABELS[slot], slot)
    combo_base.currentIndexChanged.connect(
        lambda _i: _refresh_table(window))
    base_row.addWidget(combo_base, 1)
    tb.addLayout(base_row)

    verdict = QLabel("结论：—")
    verdict.setWordWrap(True)
    tb.addWidget(verdict)
    hint = QLabel(COMPARE_HINT)
    hint.setWordWrap(True)
    hint.setStyleSheet("color: gray;")
    tb.addWidget(hint)
    cur_lbl = QLabel("当前配置：—")
    cur_lbl.setWordWrap(True)
    tb.addWidget(cur_lbl)
    lay.addWidget(table_box)
    window.calib_verdict = verdict
    window.calib_current_lbl = cur_lbl
    window.calib_base_combo = combo_base

    # ── 操作区：编辑 / 配置条目进出 / 保存 / 像素确认 ────────
    ops_box = QGroupBox("操作")
    ops = QVBoxLayout(ops_box)
    btn_edit = QPushButton("编辑当前配置…")
    btn_edit.setObjectName("edit_current_btn")
    btn_edit.setToolTip("借一条已有条目预填，或直接改像素/波长/距离/"
                        "PONI/倾斜角；改过就是「自定义」，不再被自动替换")
    btn_edit.clicked.connect(lambda: _edit_current(window))
    ops.addWidget(btn_edit)
    row1 = QHBoxLayout()
    row1.setSpacing(2)
    btn_poni = QPushButton("加载参数")
    btn_poni.setObjectName("poni_btn")    # 保持历史 objectName（测试引用）
    btn_poni.setToolTip("加载 .poni：读 pyFAI 交换格式几何文件，存成配置"
                        "条目并自动选中；同时作为当前配置的起点")
    btn_poni.clicked.connect(lambda: _import_poni(window))
    btn_save_poni = QPushButton("保存参数")
    btn_save_poni.setObjectName("save_poni_btn")
    btn_save_poni.setToolTip("保存 .poni：把分析页当前选中配置的几何写成"
                             "pyFAI 交换格式文件")
    btn_save_poni.clicked.connect(lambda: _save_poni(window))
    btn_del = QPushButton("删除")
    btn_del.setObjectName("del_config_btn")
    btn_del.setToolTip("删除分析页当前选中的**用户**配置条目（内置条目不可删）")
    btn_del.clicked.connect(lambda: _delete_config(window))
    window.del_config_btn = btn_del
    row2 = QHBoxLayout()
    row2.setSpacing(2)
    btn_save_cfg = QPushButton("保存为配置")
    btn_save_cfg.setObjectName("save_calib_config")
    btn_save_cfg.setToolTip("把**当前配置**存成命名配置条目（本地文件，"
                            "不进 git），保存后分析页下拉框自动选中")
    btn_save_cfg.clicked.connect(lambda: _save_calib_config(window))
    window.calib_save_btn = btn_save_cfg
    for btn in (btn_poni, btn_save_poni, btn_del, btn_save_cfg):
        btn.setStyleSheet("padding: 2px 5px;")   # 紧凑内边距
    for btn in (btn_poni, btn_save_poni, btn_del):
        row1.addWidget(btn, 1)
    row2.addWidget(btn_save_cfg, 1)
    ops.addLayout(row1)
    ops.addLayout(row2)
    chk_pix = QCheckBox("像素尺寸已确认")
    chk_pix.setToolTip("环的位置只由 λ、像素尺寸、距离的组合决定：像素填错"
                       "时拟合会把距离凑回来，环位偏差看着正常但报出的距离"
                       "是错的。只在像素值变了时才要求重新确认。")
    chk_pix.toggled.connect(lambda on: (_set_pixel_ok(window, on),
                                        _calib_sync(window)))
    ops.addWidget(chk_pix)
    lay.addWidget(ops_box)
    window.calib_pixel_chk = chk_pix

    key_edit = QLineEdit()
    key_edit.setPlaceholderText("条目 key，如 lmfp2_lab6")
    key_edit.setText(_suggest_config_key(config.DEFAULT_CONFIG))
    label_edit = QLineEdit()
    label_edit.setPlaceholderText("批次备注（label）")
    save_hint = QLabel("尚未有校准结果")
    save_hint.setStyleSheet("color: gray;")
    save_hint.setWordWrap(True)
    for w_ in (key_edit, label_edit, save_hint):
        ops.addWidget(w_)
    window.calib_key_edit = key_edit
    window.calib_label_edit = label_edit
    window.calib_save_hint = save_hint

    # ── 自动 ───────────────────────────────────────────────
    auto_box = QGroupBox("自动")
    al = QVBoxLayout(auto_box)
    auto_hint = QLabel("从**当前配置**出发：定位环心（取点拟合，FFT 兜底）"
                       "→ pyFAI 精修；或在当前几何上再精修一遍。结果进"
                       "列表，并按环位偏差决定要不要替换当前配置。")
    auto_hint.setWordWrap(True)
    al.addWidget(auto_hint)
    btn_auto = QPushButton("定位环心并精修")
    btn_auto.setObjectName("start_auto_calib")
    btn_auto.clicked.connect(lambda: _start_auto_calib(window, "auto"))
    btn_refined = QPushButton("在当前配置上再精修")
    btn_refined.setObjectName("start_refined_calib")
    btn_refined.clicked.connect(lambda: _start_auto_calib(window, "refined"))
    al.addWidget(btn_auto)
    al.addWidget(btn_refined)
    lay.addWidget(auto_box)
    window.calib_start_auto = btn_auto
    window.calib_start_refined = btn_refined

    # ── 手动 ───────────────────────────────────────────────
    manual_box = QGroupBox("手动")
    ml = QVBoxLayout(manual_box)
    manual_hint = QLabel("在中央校准图上点衍射环：点自动吸附最近的理论环"
                         "（±0.5°）；至少 3 个点、覆盖 2 个不同的环。")
    manual_hint.setWordWrap(True)
    ml.addWidget(manual_hint)
    points_label = QLabel("已选 0 个点 / 0 个环")
    ml.addWidget(points_label)
    row = QHBoxLayout()
    btn_undo = QPushButton("撤销一点")
    btn_clear = QPushButton("清空")
    row.addWidget(btn_undo)
    row.addWidget(btn_clear)
    ml.addLayout(row)
    btn_manual = QPushButton("用选点精修")
    btn_manual.setObjectName("start_manual_calib")
    ml.addWidget(btn_manual)
    lay.addWidget(manual_box)
    window.calib_points_label = points_label
    window.calib_undo_btn = btn_undo
    window.calib_clear_btn = btn_clear
    window.calib_start_manual = btn_manual
    btn_undo.clicked.connect(lambda: _undo_calib_point(window))
    btn_clear.clicked.connect(lambda: _clear_calib_points(window))
    btn_manual.clicked.connect(lambda: _start_manual_calib(window))

    # 出口：本页唯一的返回路径（与工具栏 [校准] 开关同源）
    btn_exit = QPushButton("返回分析模式")
    btn_exit.setObjectName("exit_calib_btn")
    btn_exit.clicked.connect(lambda: window.calib_btn.setChecked(False))
    lay.addWidget(btn_exit)
    window.calib_exit_btn = btn_exit

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)   # 窄窗口兜底
    scroll.setWidget(page)
    window.calib_scroll = scroll
    _ensure_current(window)
    _calib_sync(window)
    return scroll


def _reset_calib_form(window: QMainWindow) -> None:
    """表单复位：表格清空、结论/标签复位（按钮状态由 _calib_sync 管）。"""
    for col in ("current", "A", "B"):
        for label in window.calib_vals[col].values():
            label.setText("—")
    for per_slot in window.calib_vals["delta"].values():
        for label in per_slot.values():
            label.setText("—")
    window.calib_verdict.setText("结论：—")
    _calib_sync(window)


def _calib_sync(window: QMainWindow) -> None:
    """把状态刷到界面：槽下拉框 / 表格 / Δ / 结论 / 按钮 / 像素确认。"""
    state = _calib_state(window)
    points = state["points"]
    n_rings = len({p[2] for p in points})
    window.calib_points_label.setText(f"已选 {len(points)} 个点 / {n_rings} 个环")
    window.calib_start_manual.setEnabled(
        len(points) >= MIN_POINTS and n_rings >= MIN_RINGS)
    window.calib_undo_btn.setEnabled(bool(points))
    window.calib_clear_btn.setEnabled(bool(points))
    window.calib_start_refined.setEnabled(state["current_geom"] is not None)
    window.calib_start_auto.setEnabled(state["current_geom"] is not None)
    window.calib_current_lbl.setText(_current_geom_text(window))
    # 像素确认：只在像素值变了才要求重确认（规则 (b)）
    cur_px = (state["current_geom"] or {}).get("pixel_size_m")
    window.calib_pixel_chk.setText(
        f"像素尺寸已确认（{(cur_px or 0) * 1e6:.1f} µm）"
        if cur_px is not None else "像素尺寸已确认")
    window.calib_pixel_chk.setChecked(_pixel_ok(window))
    # 保存区
    has_cur = state["current_geom"] is not None
    window.calib_save_btn.setEnabled(has_cur)
    window.calib_save_hint.setText(
        f"将保存：「{_slot_label(state, 'current')}」的几何"
        if has_cur else "尚未有可保存的几何")
    _sync_slot_combos(window)
    _refresh_table(window)
    _sync_del_config_btn(window)


# ══ 手动选点：点击判环 / 撤销 / 清空 ══════════════════════════


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
def _auto_calib_worker(path_str: str, geom: dict,
                       center0_px: tuple = None) -> dict:
    """后台线程纯计算：读标样 → （可选定环心）→ pyFAI 精修。

    center0_px 为 None（①自动定位）时先自动定环心（取点拟合，FFT
    兜底）；给定时（③二次精修）直接用给定的环心——那是"当前使用"
    的解，比重新定位更可信。形参是 (列, 行)，与 calibrate_lab6 一致。

    结果附 beam_center_rc：(行, 列) 像素——这次新拟合的环心就是直射
    束落点 B（比沿用配置条目的旧 B 更准），[保存为配置] 用它入条目。

    图像已经在手，顺手附引擎指标（metrics / metrics_initial）——环位
    偏差量的是"精修后几何把理论环放到图像真环上了没有"，是用户在校
    准图上看得见的那件事。
    """
    image = _load_image(path_str)
    if center0_px is None:
        center = fit_center_from_rings(image)
        if center is None:
            cy, cx = find_ring_center(image)
        else:
            cy, cx = center["cy"], center["cx"]
    else:
        cx, cy = center0_px
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
            image = _load_image(path_str)
        except Exception as exc:                      # noqa: BLE001
            result["metrics"] = None
            result["metrics_initial"] = None
            result["metrics_error"] = f"{type(exc).__name__}: {exc}"
        else:
            _attach_metrics(result, image, geom, initial=geom)
    return result


def _start_auto_calib(window: QMainWindow, target: str = "auto") -> None:
    """[定位环心并精修]（target="auto"）与 [在当前配置上再精修]（"refined"）。

    auto     从当前配置出发：自动定位环心（取点拟合，FFT 兜底）→ 精修
    refined  不重新定位环心，直接以**当前配置**的环心与距离为初值再精修
             一轮（首轮初值偏时，从更好的解出发能收敛到另一支）
    """
    path = _calib_standard_path(window)
    if path is None:
        _log(window, "请先在文件列表勾选标样文件")
        return
    _open_calib_panel(window, path)   # 面板关了/没开过：重开
    if getattr(window, "calib_dock", None) is None:
        return   # 图像读取失败（_open_calib_panel 已记日志）
    if not _initial_ready(window):
        return   # 像素尺寸没确认：只提示，不建任务
    geom = dict(_calib_state(window).get("current_geom") or {})
    center0 = None
    label = "自动定位" if target == "auto" else "在当前配置上再精修"
    if target == "refined":
        bc = geom.get("beam_center_rc")
        # beam_center_rc 是 (行, 列)；引擎的 center0_px 要 (列, 行)
        center0 = ((bc[1], bc[0]) if bc and bc[0] is not None else None)
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
        _on_calib_result(window, target, result)

    def error(msg):
        window._tasks.remove(task)
        if window._latest_task.get(key) is not task:
            return
        del window._latest_task[key]
        if gen != getattr(window, "calib_gen", -1) \
                or getattr(window, "calib_dock", None) is None:
            return
        _log(window, f"校准失败（{label}）：{msg}")

    task = BackgroundTask(_auto_calib_worker, str(path), geom, center0,
                          on_done=done, on_error=error)
    window._latest_task[key] = task
    window._tasks.append(task)
    _log(window, f"开始{label} {path.name}（后台线程）")
    task.start()


def _start_manual_calib(window: QMainWindow) -> None:
    """[用选点精修]：用户点后台精修（初值 = 当前配置；环心 = 分析条目束心）。"""
    state = _calib_state(window)
    if len(state["points"]) < MIN_POINTS \
            or len({p[2] for p in state["points"]}) < MIN_RINGS:
        _log(window, f"手动校准至少需要 {MIN_POINTS} 个点、"
                     f"覆盖 {MIN_RINGS} 个不同的环")
        return
    if not _initial_ready(window):
        return   # 像素尺寸没确认：只提示，不建任务
    geom = dict(state.get("current_geom") or {})
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
        _on_calib_result(window, "manual", result)

    def error(msg):
        window._tasks.remove(task)
        if window._latest_task.get(key) is not task:
            return
        del window._latest_task[key]
        if gen != getattr(window, "calib_gen", -1) \
                or getattr(window, "calib_dock", None) is None:
            return
        _log(window, f"校准失败（手动）：{msg}")

    path = getattr(window, "calib_path", None)   # 指标要图像；没面板时为 None
    task = BackgroundTask(_manual_calib_worker, str(path) if path else None,
                          points, rings, geom, center0_px,
                          on_done=done, on_error=error)
    window._latest_task[key] = task
    window._tasks.append(task)
    _log(window, f"开始手动（{len(points)} 个点，后台线程）")
    task.start()


# ══ 结果 / Δ / 保存为配置 ════════════════════════════════════
def _on_calib_result(window: QMainWindow, kind: str, result: dict) -> None:
    """一次校准结果落地（主线程）：进累积列表 → 判要不要换当前配置 → 填槽。

    顺序有讲究：先把结果挂进列表（这样日志与槽都有名字可用），再判
    "拟合得好不好"（_adopt_decision），采纳就换当前配置并重画青线，最后
    按 A→B 轮换填没被钉住的槽。
    """
    state = _calib_state(window)
    name = _add_result(state, kind, result)
    label = KIND_LABELS.get(kind, kind)
    _log(window, f"{label}完成（{name}）：距离 "
                 f"{result['dist_m'] * 1000:.2f} mm，"
                 f"PONI ({result['poni1_px']:.2f}, {result['poni2_px']:.2f}) px，"
                 f"残差 {result['residual_deg']:.4f}°"
                 f"{_metrics_note(result)}")
    take, note = _adopt_decision(state, name)
    if take:
        _adopt_result(window, name, why="自动采纳")
        _log(window, note)
    else:
        _log(window, note)
    _log(window, _fill_slot(state, name))
    _calib_sync(window)
    if kind == "manual" and state.get("manual_n", 99) < 6:
        # 点数少时最小二乘对单点点击误差敏感，残差小也不代表可信
        _log(window, "提示：点数较少（<6）时手动结果可能不稳，建议多点"
                     "几个环上的点再跑一次，以环位偏差判断可信度")


# ══ 几何配置条目：加载 .poni / 保存 .poni / 删除 ═══════════════
# （从 app.py 搬来：几何配置的增删改查归校准页——分析页只读，下拉框
# 选条目；按钮的工作对象仍是"分析页当前选中的那一条"。）


# ══ 模式进出（app._on_mode 调用）══════════════════════════════
def _enter_calib(window: QMainWindow) -> None:
    """进入校准模式（[校准] 按下）：开校准面板 + 按校准页内容拉宽参数坞。

    没勾文件只记日志提示（不崩）——用户勾好文件后点 [定位环心并精修]
    也能开面板。宽度只在够得着时拉：给绘图区留 CALIB_PANEL_RESERVE_PX
    （点环选点是在图上做的，坞太宽就没法点了）。
    """
    path = _calib_standard_path(window)
    if path is None:
        _log(window, "请先在文件列表勾选标样文件")
        return
    _open_calib_panel(window, path)
    _widen_dock_for_calib(window)


def _exit_calib(window: QMainWindow) -> None:
    """退出校准模式：关校准面板（关闭即遗忘）+ 参数坞宽度还原。"""
    _close_calib_panel(window)
    _restore_dock_width(window)


def _widen_dock_for_calib(window: QMainWindow) -> None:
    """校准模式下把参数坞拉宽到"放下校准页所有内容"（不超过窗口上限）。"""
    dock = getattr(window, "param_dock", None)
    page = getattr(window, "calib_scroll", None)
    if dock is None or page is None:
        return
    if getattr(window, "_dock_w_before", None) is None:
        window._dock_w_before = dock.width()      # 记住分析模式的宽度
    need = page.widget().sizeHint().width() + 24  # 滚动区边框/条余量
    limit = max(360, window.width() - CALIB_PANEL_RESERVE_PX)
    target = int(min(need, limit))
    if dock.isFloating():
        dock.resize(target, dock.height())
    else:
        window.resizeDocks([dock], [target], Qt.Horizontal)
    if need > limit:
        _log(window, f"校准页需要 {need} px，但为保住绘图区（点环用）只给到 "
                     f"{target} px：窄窗口下校准页里可以横向滚动")


def _restore_dock_width(window: QMainWindow) -> None:
    """出校准模式：参数坞宽度还原成进之前那一条。"""
    was = getattr(window, "_dock_w_before", None)
    dock = getattr(window, "param_dock", None)
    if was is None or dock is None:
        return
    if dock.isFloating():
        dock.resize(was, dock.height())
    else:
        window.resizeDocks([dock], [was], Qt.Horizontal)
    window._dock_w_before = None
