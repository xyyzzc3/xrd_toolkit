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
  - 参数坞第 0 页 = _build_calib_form（自上而下：配置条目操作区 +
    结果三列区 自动|手动|Δ偏差 + 自动/手动两个动作区）。各来源共用
    同一结果区与保存机制："当前使用"的结果可直接存成命名用户条目
    （config_user.json，不进 git），保存后坞顶"几何配置"下拉框
    立即出现并自动选中（几何填进参数坞）。内置 config.py 注册表
    仍走 CLI 模板人工登记（见 config.py 文件头）。
  - 中央校准图面板 = _CalibSubWindow（MDI 子窗口，imshow + 理论环
    路径 + 控制点/用户点标记 + 一个 [看环全貌] 视野开关，只接鼠标
    点击）。理论环由 theoretical_ring_paths 精确反解（倾斜时是椭圆、
    圆心是直射束落点），不用"圆心 + 半径"的正圆近似——后者会整体偏
    8~23 px。视野默认锁在图像那一框（图像是唯一不动的参照系，环跑
    到框外由红字说明），要放大到看得见环得自己勾 [看环全貌]。
    不进 plot_docks：不掺和编辑对象焦点、平铺、总缩放；无手势无
    抓手（v1 从简）。

状态（都挂在 window 上）：
  calib_dock / calib_key / calib_path / calib_display / calib_canvas
  / calib_ax   面板与画布（None = 没开面板）
  calib_gen    代计数：面板关闭/重开/退出模式时 +1，在飞任务回调
               核对代数，迟到结果静默作废
  calib_state  选点 + 累积结果 + 三个槽（懒创建）——关闭面板即全清
                （关闭即遗忘）；各键说明见 calib_model._calib_state
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
    QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

# 常量都住在 calib_model（纯逻辑层）。这里把面板/表格用的几个也一并
# 导入，保持『经 gui_calib 取用』的历史引用可用（同 app.py 的再导出惯例）。
from xrd_toolkit.gui.calib_model import (
    CALIB_PANEL_RESERVE_PX, COMPARE_HINT, COMPARE_ROWS, CP_COLOR,
    KIND_LABELS, MIN_POINTS, MIN_RINGS, PANEL_SCALE, RING_COLOR,
    SLOT_LABELS, SNAP_TOL_DEG,
    SOURCE_IMPROVE_MIN_PX, _add_custom_result, _add_result, _adopt_decision,
    _calib_state, _fill_slot, _geom_to_result_shape, _next_name,
    _result_by_name, _result_dev, _result_to_geom, _slot_label, _verdict)
from xrd_toolkit.gui.calib_table import (
    _on_slot_changed, _refresh_table, _sync_slot_combos)
from xrd_toolkit.gui.calib_panel import (
    _CalibSubWindow, _LAB6_MAX_RING, _apply_selected_ring,
    _calib_standard_path, _clear_calib_points, _close_calib_panel,
    _geom_px_keys, _load_image, _open_calib_panel, _redraw_calib,
    _redraw_calib_if_open, _undo_calib_point, _warn_rings_off_image)
from xrd_toolkit.gui.config_ops import (
    _delete_config, _import_poni, _save_calib_config, _save_poni,
    _suggest_config_key, _sync_del_config_btn)

from xrd_toolkit import config
from xrd_toolkit.core.processor import find_ring_center, fit_center_from_rings
from xrd_toolkit.gui.panels import _settle
from xrd_toolkit.gui.panel_state import (
    _auto_contrast_values, _collect_geometry, _log, _reload_config_combo)
from xrd_toolkit.gui.tasks import BackgroundTask, user_error_text
from xrd_toolkit.services.integrator import (
    calibrate_lab6, refine_lab6_from_points, snap_lab6_ring)
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
    # 重新选中"自定义"那条 = 又把手输的几何拿回来用了：保护标记要跟着回来，
    # 否则下一次自动结果就会把它悄悄替换掉（用户手输的东西不该被覆盖）
    state["custom"] = item.get("kind") == "custom"
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
    state["current_from"] = f"预填自 {key}"
    state["base_key"] = key          # 血缘：这一批的起点是从哪条借的
    state["custom"] = False
    state["current_metrics"] = None
    state["current_error"] = None
    if not state["results"]:          # 累积列表的第一条 = "原始"
        state["results"].append(
            {"name": _next_name(state, "raw"), "kind": "raw",
             "result": _geom_to_result_shape(geom)})
    if not silent:
        _log(window, f"当前配置 ← 预填自条目 {key}"
                     f"（距离 {geom['dist_m'] * 1e3:.2f} mm）")
    _refresh_current_metrics(window)
    # 借用/导入 .poni 换了当前配置的几何 → 青线跟着换（面板没开时是空操作）
    _redraw_calib_if_open(window)
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
        _log(window, f"已核对像素尺寸：{pixel * 1e6:.1f} µm"
                     f"（当前配置：{_slot_label(_calib_state(window), 'current')}）")


def _warn_pixel_unchecked(window: QMainWindow, doing: str = "校准") -> None:
    """像素尺寸没核对时写一条提醒（**不拦动作**）。

    用户 2026-10-05："改为提醒的样式，不要求用户必须选择了"——原来这道门
    是硬门禁（没勾按钮直接不干活）；现在照常继续，只把风险写清楚：像素
    尺寸与波长、距离同比例缩放时环一模一样，填错时拟合会把距离凑回来，
    环位偏差、a 离散度全都正常，只有报出来的距离是错的（还会写进 .poni
    与配置条目）。界面上另有一行常显的橙色提示（calib_pixel_warn）。
    """
    if _pixel_ok(window):
        return
    _log(window, "提醒：像素尺寸还没核对（[已核对像素尺寸] 没勾）——"
                 "像素填错时拟合会把距离同比例凑错：环位偏差看着正常，"
                 f"报出来的距离是错的。这次{doing}照常进行，结果请自行核对")


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

    两处按用户 2026-09-28 第 1、2 条改的：
      * **束心行/列也在这里改**（`beam_center_rc`，表里的"环心行/环心列"
        两行）。改之前对话框里根本没有这两个框，`_beam` 存了却从没被读回
        ——手输几何的束心永远是接手时那一个（"从条目预填"也只搬 7 个数字、
        留下旧束心），而束心正是校准最常动的东西；
      * 确定之后这条几何**进累积结果列表**（`_add_custom_result`）：于是
        A/B 两个下拉（选项 = 结果名）里能选到它、能钉进槽、能被采纳成
        「当前配置」，切走再切回来也还在——改之前它只活在 current_geom
        这个没有户口的变量里。
    """
    state = _calib_state(window)
    geom = dict(state["current_geom"] or {})
    if not geom:
        _log(window, "当前配置还没设定（先选一个标准文件进校准模式）")
        return
    px = _geom_px_keys(geom)
    beam = geom.get("beam_center_rc") or (None, None)
    row0, col0 = (beam + (None, None))[:2] if beam else (None, None)
    dlg = QDialog(window)
    dlg.setWindowTitle("编辑当前配置")
    form = QFormLayout(dlg)
    pre = QComboBox()
    pre.addItem("（不改名称，只编辑数值）", None)
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
    # 束心（行/列，像素）：表里的"环心行/环心列"两行就是它。留 0 位小数
    # （它是像素坐标；默认值取当前几何，没有就 0——图上点一下就能改）
    for text, key_, init in (("束心行 (px)", "beam_row", row0),
                             ("束心列 (px)", "beam_col", col0)):
        box = QDoubleSpinBox()
        box.setRange(-1e5, 1e5)
        box.setDecimals(2)
        box.setValue(float(init) if init is not None else 0.0)
        box.setObjectName(f"{key_}_spin")     # 测试按名字找（不靠索引顺序）
        form.addRow(text, box)
        boxes[key_] = box

    def prefill(_idx):
        key = pre.currentData()
        if key is None:
            return
        entry = config.CONFIGS[key]
        g = entry["geometry"]
        boxes["pixel_um"].setValue(g["pixel_size_m"] * 1e6)
        boxes["wavelength_a"].setValue(g["wavelength_m"] * 1e10)
        boxes["dist_mm"].setValue(g["dist_m"] * 1e3)
        boxes["poni1_px"].setValue(g["poni1_m"] / g["pixel_size_m"])
        boxes["poni2_px"].setValue(g["poni2_m"] / g["pixel_size_m"])
        boxes["rot1_deg"].setValue(g["rot1_deg"])
        boxes["rot2_deg"].setValue(g["rot2_deg"])
        # 束心也跟着预填（改之前这里存进 boxes["_beam"] 就再没人读过：
        # "从条目预填"于是只搬 7 个数字、留下旧束心）
        bc = entry.get("beam_center") or (None, None)
        if bc[0] is not None:
            boxes["beam_row"].setValue(float(bc[0]))
        if bc[1] is not None:
            boxes["beam_col"].setValue(float(bc[1]))

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
        "beam_center_rc": (boxes["beam_row"].value(), boxes["beam_col"].value()),
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
    # 进累积列表（用户 2026-09-28 第 1 条）：A/B 下拉与基准都能选到它
    name = _add_custom_result(state, new_geom)
    _log(window, f"当前配置已手动修改（{name}，自定义）：距离 "
                 f"{new_geom['dist_m'] * 1e3:.2f} mm、像素 {pixel * 1e6:.1f} µm"
                 f"、束心 {new_geom['beam_center_rc'][1]:.1f} 列 / "
                 f"{new_geom['beam_center_rc'][0]:.1f} 行"
                 f"——已进结果列表（对比位 A、B 的下拉里能选到它）")
    _refresh_current_metrics(window)
    _calib_sync(window)
    # 改完即重画：不然表里数字换了、图上的青线还是旧几何的（2026-09-26
    # 用户报的"编辑当前配置青环不动"——改的是状态、漏了这一笔）
    _redraw_calib_if_open(window)


def _metrics_worker(path_str: str, geom: dict) -> dict:
    """后台线程：算一份几何的指标（当前配置的环位偏差专用）。

    `_attach_metrics(result, ...)` 的第一个参数就是**被量的那份几何**，
    所以这里用几何本身当载体（空 dict 会 KeyError: 'dist_m'——踩过）。
    """
    image = _load_image(path_str)
    out = dict(geom)
    _attach_metrics(out, image, geom)     # initial=None：不重复算初值那一份
    return out


def _stack_custom_metrics(state: dict) -> None:
    """把当前配置的指标**也写进最后那条"自定义"结果**。

    为什么需要（用户 2026-09-28 第 1 条的后半截）：手输几何现在会进结果
    列表，用户就能把它放进 A / B 槽去跟自动/手动结果比。但指标是异步算
    出来、记在 `state["current_metrics"]` 里的（那是"当前配置"那一列的
    数据源），结果条目自带的那份还是 None——不补这一笔，放进槽里就显示
    "无可用环位偏差、判不了"，等于选了个哑巴。

    只在"当前配置就是那条手输几何"时补（`slots["current"]` 为空 + custom
    = True）：一旦采纳了别的结果，当前配置与那条自定义结果就不是一回事了。
    """
    if state.get("slots", {}).get("current") is not None \
            or not state.get("custom"):
        return
    for item in reversed(state.get("results") or []):
        if item.get("kind") == "custom":
            item["result"]["metrics"] = state.get("current_metrics")
            item["result"]["metrics_error"] = state.get("current_error")
            return


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
        _stack_custom_metrics(state)
        _calib_sync(window)

    def error(msg):
        window._tasks.remove(task)
        window._latest_task.pop(key, None)
        if gen != getattr(window, "calib_gen", -1):
            return
        state["current_metrics"] = None
        state["current_error"] = msg
        _stack_custom_metrics(state)
        _calib_sync(window)

    task = BackgroundTask(_metrics_worker, str(path), dict(geom),
                          on_done=done, on_error=error)
    window._latest_task[key] = task
    window._tasks.append(task)
    task.start()


# ══ 中央校准图面板 ═══════════════════════════════════════════


# ══ 参数坞第 0 页：校准表单 ═══════════════════════════════════
def _build_calib_form(window: QMainWindow) -> QWidget:
    """参数坞第 0 页：校准功能。

    布局（自上而下）：
      操作区  [编辑…] [导入][保存] / [删除][存为配置] + 条目 key/备注
              + **像素尺寸确认**（单独一行：校准的前置门禁，得在按钮之前
              看得见；2026-10-01 从数据表里搬回来）
      自动取点  定位束心并精修
      手动    选点计数 + 撤销/清空 + 用选点精修（右键点 = 选中它改环号）
      三列表  当前配置说明行（框顶）+ 灰字列头 + 三个槽下拉（当前配置 /
              A / B——各贴自己那一列；都从累积结果里选；当前配置还能借
              条目或手输，只是不在这个下拉里表达）+ 8 行数值 + 2 行 Δ
              （相对「当前配置」）+ 结论行 + ⚠ 说明
    整页套滚动区；进校准模式时参数坞会按本页内容拉宽（见 _enter_calib）。

    数据表在**最下**（用户 2026-09-30："校准数据放最下，跟自动手动换位置"）：
    流程上先跑自动/手动、再回头看结果，所以动作区在上、结果区在下。

    操作区排在最上面（用户 2026-09-26 晚定）：里面的 [加载参数][保存参数]
    [删除] 作用的就是坞顶「几何配置」那一行选中的条目，紧挨着它才看得出
    "先选条目、再对条目动手"；原先这栏压在 13 行对比表下面，得先滚下去。

    出口 [返回分析模式] 与几何配置一行不在这里：两者都固定在参数坞顶部
    （app._build_param_dock 建，window.calib_exit_btn）——出口原先钉在本页
    最底部，窗口不高时要滚好几百像素才看得见（用户 2026-09-26 报"没有
    退出校准的按钮了"）。像素确认也搬上来挨着"当前配置"那行（它确认的
    就是那行末尾写的像素值），原先夹在「操作」组的按钮堆里。
    """
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.setSpacing(4)

    # ── 操作区：编辑 / 配置条目进出 / 保存 ──────────────────
    # 本页第一栏，紧跟坞顶「几何配置」那一行（用户 2026-09-26 晚定）：
    # [加载参数][保存参数][删除] 作用的就是那一行选中的条目，摆在一起
    # 才看得出"先在上面选条目、再对条目动手"这层关系；原先它们压在
    # 13 行对比表下面，要滚下去才够得着，而选中项却远在坞顶。
    ops_box = QGroupBox("操作")
    ops = QVBoxLayout(ops_box)
    btn_edit = QPushButton("编辑当前配置…")
    btn_edit.setObjectName("edit_current_btn")
    btn_edit.setToolTip("用已有条目预填，或直接改像素/波长/距离/"
                        "PONI/倾斜角；改过就是「自定义」，不再被自动替换")
    btn_edit.clicked.connect(lambda: _edit_current(window))
    ops.addWidget(btn_edit)
    row1 = QHBoxLayout()
    row1.setSpacing(2)
    btn_poni = QPushButton("加载几何…")
    btn_poni.setObjectName("poni_btn")    # 保持历史 objectName（测试引用）
    btn_poni.setToolTip("加载 .poni：读 pyFAI 交换格式几何文件，存成配置"
                        "条目并自动选中；同时作为当前配置的起点")
    btn_poni.clicked.connect(lambda: _import_poni(window))
    btn_save_poni = QPushButton("保存几何…")
    btn_save_poni.setObjectName("save_poni_btn")
    btn_save_poni.setToolTip("保存 .poni 几何文件：把<b>当前配置</b>（本页表里"
                             "第一列那个，含手输/自定义）的几何写成 pyFAI 交换"
                             "格式——与 [保存为配置] 取的是同一份几何")
    btn_save_poni.clicked.connect(lambda: _save_poni(window))
    btn_del = QPushButton("删除")
    btn_del.setObjectName("del_config_btn")
    btn_del.setToolTip("删除参数面板「几何配置」里当前选中的<b>用户</b>"
                       "配置条目（内置条目不可删）")
    btn_del.clicked.connect(lambda: _delete_config(window))
    window.del_config_btn = btn_del
    row2 = QHBoxLayout()
    row2.setSpacing(2)
    btn_save_cfg = QPushButton("保存为配置")
    btn_save_cfg.setObjectName("save_calib_config")
    btn_save_cfg.setToolTip("把<b>当前配置</b>存成命名配置条目（本地文件，"
                            "不进 git），保存后参数面板「几何配置」下拉框"
                            "自动选中")
    btn_save_cfg.clicked.connect(lambda: _save_calib_config(window))
    window.calib_save_btn = btn_save_cfg
    for btn in (btn_poni, btn_save_poni, btn_del, btn_save_cfg):
        btn.setStyleSheet("padding: 2px 5px;")   # 紧凑内边距
    for btn in (btn_poni, btn_save_poni, btn_del):
        row1.addWidget(btn, 1)
    row2.addWidget(btn_save_cfg, 1)
    ops.addLayout(row1)
    ops.addLayout(row2)
    lay.addWidget(ops_box)

    # 像素尺寸确认：单独一行，钉在「操作」组下面（页面顶部区域）。
    # **2026-10-05 起它只是提醒，不是门禁**（用户："改为提醒的样式，不要求
    # 用户必须选择了"）：没勾也照常跑/照常存，只是这行旁边常显一条橙色
    # 提示、动作开始时日志再提醒一句（见 _warn_pixel_unchecked）。
    # 位置来回搬过三趟：原挤在「操作」按钮堆里（用户 2026-09-26 报"容易被
    # 忽视"，因为卡在参数坞可见区最下沿）→ 搬进数据表上方 → 2026-09-30
    # 数据表挪到页面最下，它又跟着沉下去了（用户 2026-10-01："把像素尺寸
    # 放上面，太下面了不方便"）→ 现在单独一行。
    chk_pix = QCheckBox("已核对像素尺寸")
    chk_pix.setToolTip("环的位置只由 λ、像素尺寸、距离的组合决定：像素填错"
                       "时拟合会把距离凑回来，环位偏差看着正常但报出的距离"
                       "是错的。只在像素值变了时才清掉核对标记")
    chk_pix.toggled.connect(lambda on: (_set_pixel_ok(window, on),
                                        _calib_sync(window)))
    # 短提醒（原因写在勾选框 tooltip 与动作时的日志里）：不把校准页撑宽
    # ——参数坞宽度是按内容算的（_enter_calib），内容宽度关系到给绘图区
    # 留的位置（CALIB_PANEL_RESERVE_PX），这行只当一个醒目的记号。
    warn_pix = QLabel("⚠ 未核对，建议先核对")
    warn_pix.setWordWrap(True)
    warn_pix.setStyleSheet("color: #c0392b;")
    pix_row = QHBoxLayout()
    pix_row.addWidget(chk_pix)
    pix_row.addWidget(warn_pix, 1)
    lay.addLayout(pix_row)
    window.calib_pixel_chk = chk_pix
    window.calib_pixel_warn = warn_pix

    # 「条目名称」一个框（用户 2026-10-07："一个框，预填文件名的不含-
    # 后的长数字部分……两个框保存后最后变成一个连在一起的"）：名字本身
    # 是备注，命令行用的标识在存盘时自动洗出来（config_ops.
    # _sanitize_config_key）；进校准/换标样时按文件名重填预填值
    # （_refresh_config_name_prefill，用户自己改过的名字不覆盖）。
    name_edit = QLineEdit()
    name_edit.setPlaceholderText("条目名称（按标样文件名预填，可改）")
    name_edit.setToolTip("存成一个几何配置条目：这个名字就是备注；命令行"
                         " --config 用的标识由它自动生成（非字母数字换成"
                         "下划线）。下拉框里显示成「标识_备注」。")
    name_edit.setText(_default_config_name(window))
    window._calib_name_auto = name_edit.text()   # 上次预填值：用户改过就不再覆盖
    save_hint = QLabel("还没有校准结果")
    save_hint.setStyleSheet("color: gray;")
    save_hint.setWordWrap(True)
    for w_ in (name_edit, save_hint):
        ops.addWidget(w_)
    window.calib_name_edit = name_edit
    window.calib_save_hint = save_hint

    # 三列表的说明：讲的是下面那张表，所以跟着表走
    intro = QLabel("用标样图标定几何（束心、距离、倾斜角）；跑出的结果"
                   "<b>直接成为当前配置</b>，并与对比位 A、B 三列并排对照")
    intro.setWordWrap(True)

    # ── 三列表 ─────────────────────────────────────────────
    table_box = QGroupBox("数据")
    tb = QVBoxLayout(table_box)

    # 当前配置说明行放**框顶**（用户 2026-10-08："放到最上面"）：先看见
    # "现在用的是哪一份几何"，再往表里看——它就是"往下看对比表之前必须
    # 先看见的"那件事；放框顶还有个好处：下拉框下面直接就是自己列的数字，
    # 中间不再隔一行。（像素尺寸确认曾经也住这儿，2026-10-01 单独搬回
    # 页面顶部了，见上面。）
    cur_lbl = QLabel("当前配置：—")
    cur_lbl.setWordWrap(True)
    tb.addWidget(cur_lbl)
    window.calib_current_lbl = cur_lbl

    # 三个槽下拉框进网格：灰字列头正下方、各贴自己那一列（用户
    # 2026-10-08："三个选择框和对应的当前 A/B 太远了"——原先三框单独占
    # 一行等宽摊开，跟下面的列对不上：实测左偏 31~91 px、中间还隔两行）。
    # 水平 SizePolicy=Ignored：列宽照旧由数字和行名定（和加下拉框之前
    # 一样），下拉框只占用自己那一格、长了省略——不然它按最长条目要宽度，
    # 会把列撑到 102 px、参数坞整体从 359 顶宽到 465（实测）。
    grid = QGridLayout()
    grid.setHorizontalSpacing(6)
    window.calib_slot_combo = {}
    for c, (slot, text) in enumerate(
            (("current", "当前配置"), ("A", "A"), ("B", "B")), start=1):
        hdr = QLabel(text)
        hdr.setAlignment(Qt.AlignCenter)
        hdr.setStyleSheet("color: gray;")
        grid.addWidget(hdr, 0, c)
        combo = QComboBox()
        combo.setToolTip({
            "current": "当前配置：选一条结果即采纳为当前配置；"
                       "[编辑…] 可用条目预填或手输",
            "A": "对比位 A：从累积结果里选；手动选过之后新结果不再覆盖它",
            "B": "对比位 B：同上"}[slot])
        combo.setSizePolicy(QSizePolicy.Ignored,
                            combo.sizePolicy().verticalPolicy())
        combo.currentIndexChanged.connect(
            lambda _i, s=slot: _on_slot_changed(window, s))
        grid.addWidget(combo, 1, c)
        window.calib_slot_combo[slot] = combo

    window.calib_vals = {"current": {}, "A": {}, "B": {}, "delta": {}}
    row_idx = 2
    for key_, name, _scale, _fmt, has_delta in COMPARE_ROWS:
        grid.addWidget(QLabel(name), row_idx, 0)
        for c, slot in enumerate(("current", "A", "B"), start=1):
            label = QLabel("—")
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(label, row_idx, c)
            window.calib_vals[slot][key_] = label
        row_idx += 1
        if has_delta:                     # Δ 行紧跟在它下面
            grid.addWidget(QLabel(f"Δ{name}"), row_idx, 0)
            for c, slot in enumerate(("current", "A", "B"), start=1):
                label = QLabel("—")
                label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                label.setStyleSheet("color: gray;")
                grid.addWidget(label, row_idx, c)
                window.calib_vals["delta"].setdefault(key_, {})[slot] = label
            row_idx += 1
    tb.addLayout(grid)

    # 「对比基准」下拉框已删（用户 2026-09-30："直接把对比基准删了，直接出
    # 结论当前配置和 a、b 对比分别怎么样，随着用户选择 ab 当前进行变化"）。
    # 参照系固定 = 当前配置：Δ 行与结论都朝它比，A/B 槽换了内容结论就跟着变。
    # 历史：2026-09-27 加它是因为"不知道谁在和谁比"；2026-09-28 让它同时管
    # 结论与 Δ；现在干脆去掉这一层——一屏只讲"当前配置 vs A、vs B"这一件事，
    # 比"选一个基准再看结论"少一步，也不会再出现"基准列写着 A、结论在讲 B"。
    delta_note = QLabel("Δ = 该列 − 当前配置；结论同样以「当前配置」为准")
    delta_note.setWordWrap(True)
    delta_note.setStyleSheet("color: gray;")
    tb.addWidget(delta_note)

    verdict = QLabel("结论：—")
    verdict.setWordWrap(True)
    tb.addWidget(verdict)
    hint = QLabel(COMPARE_HINT)
    hint.setWordWrap(True)
    hint.setStyleSheet("color: gray;")
    tb.addWidget(hint)
    lay.addWidget(intro)
    # 数据表（三列表 + Δ + 结论）**放最下**（用户 2026-09-30："校准数据放最下，
    # 跟自动手动换位置"）：页面顺序 = 介绍 → 自动 → 手动 → 数据。
    # 它在最后才 addWidget（见下），这里只留引用
    window.calib_verdict = verdict

    # ── 自动取点 ─────────────────────────────────────────────
    # 2026-10-08：删掉旧的 [再精修]（以当前配置的束心为初值直接再精修）
    # ——真 lab6 实测两个按钮产出等价（环位偏差 0.24 vs 0.23 px，在重复
    # 跑 0.014~0.029 px 的抖动以内），连按两个按钮 = 空转，收敛成一个。
    auto_box = QGroupBox("自动取点")
    al = QVBoxLayout(auto_box)
    auto_hint = QLabel("从<b>当前配置</b>出发，自动定位束心并精修；结果"
                       "<b>直接成为当前配置</b>（图上青环立刻跟着动），"
                       "同时进下方列表")
    auto_hint.setWordWrap(True)
    al.addWidget(auto_hint)
    btn_auto = QPushButton("定位束心并精修")
    btn_auto.setObjectName("start_auto_calib")
    btn_auto.clicked.connect(lambda: _start_auto_calib(window))
    al.addWidget(btn_auto)
    lay.addWidget(auto_box)
    window.calib_start_auto = btn_auto
    # （数据表在手动区之后才加进布局——见本函数末尾，用户要求放最下）

    # ── 手动选点 ─────────────────────────────────────────────
    manual_box = QGroupBox("手动选点")
    ml = QVBoxLayout(manual_box)
    manual_hint = QLabel("在中央图上点衍射环（自动吸附最近的环，±0.5°）；"
                         "<b>右键某个点</b>可选中并在下方改环号；"
                         "至少 3 个点、覆盖 2 个环")
    manual_hint.setWordWrap(True)
    ml.addWidget(manual_hint)
    points_label = QLabel("已选 0 个点 / 0 个环")
    points_label.setWordWrap(True)      # 不够格时它会写一长句原因（见 _calib_sync）
    ml.addWidget(points_label)
    # 选中点的环号：右键选中 → 在这里改（2026-10-03 起不弹对话框——
    # 模态框在 macOS 全屏下"开着切走再切回"会把应用卡成收不到输入）
    sel_row = QHBoxLayout()
    sel_lbl = QLabel("选中点：—（右键图上某个点来选）")
    sel_lbl.setWordWrap(True)
    sel_row.addWidget(sel_lbl, 1)
    ring_spin = QSpinBox()
    ring_spin.setObjectName("calib_ring_spin")
    ring_spin.setRange(0, _LAB6_MAX_RING)
    ring_spin.setToolTip(f"选中的点该算第几环（LaB₆ 理论环 0–{_LAB6_MAX_RING}）"
                         f"——位置不动，拟合按新环号算")
    ring_spin.setEnabled(False)
    btn_ring = QPushButton("改")
    btn_ring.setObjectName("calib_ring_apply_btn")
    btn_ring.setToolTip("把选中点的环号改成左边这个数字")
    btn_ring.setEnabled(False)
    sel_row.addWidget(ring_spin)
    sel_row.addWidget(btn_ring)
    ml.addLayout(sel_row)
    row = QHBoxLayout()
    btn_undo = QPushButton("撤销一点")
    btn_clear = QPushButton("清空选点")
    row.addWidget(btn_undo)
    row.addWidget(btn_clear)
    ml.addLayout(row)
    btn_manual = QPushButton("用选点精修")
    btn_manual.setObjectName("start_manual_calib")
    btn_manual.setToolTip(f"按你选的点反推几何。至少要 {MIN_POINTS} 个点、"
                          f"覆盖 {MIN_RINGS} 个<b>不同的环</b>才能点（灰着时看上面"
                          f"那行说明；都判成同一个环号了：右键选中那个点，"
                          f"把环号改掉）")
    ml.addWidget(btn_manual)
    lay.addWidget(manual_box)
    lay.addWidget(table_box)      # 数据表放最下（用户 2026-09-30 定）
    window.calib_points_label = points_label
    window.calib_selected_lbl = sel_lbl
    window.calib_ring_spin = ring_spin
    window.calib_ring_apply = btn_ring
    window.calib_undo_btn = btn_undo
    window.calib_clear_btn = btn_clear
    window.calib_start_manual = btn_manual
    btn_ring.clicked.connect(lambda: _apply_selected_ring(window))
    btn_undo.clicked.connect(lambda: _undo_calib_point(window))
    btn_clear.clicked.connect(lambda: _clear_calib_points(window))
    btn_manual.clicked.connect(lambda: _start_manual_calib(window))

    # 出口按钮不在这儿：原来放在本页最底部，窗口 1000 高时它落在内容
    # y=1236（要往下滚 434 px 才看得见，用户 2026-09-26 报"没有退出校准
    # 的按钮了"）。现在固定在参数坞顶部那一行（app._build_param_dock 建，
    # window.calib_exit_btn），永远可见。

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
    enough = len(points) >= MIN_POINTS and n_rings >= MIN_RINGS
    # 不够格时**把原因写出来**（用户 2026-10-01："手选完了点击用选点精修无效"）：
    # 那时按钮是灰的，点下去 Qt 直接丢掉、一个字都不写——而最可能的原因正是
    # 判环挤在一起（同一个环号好几个点），顺手把出路（右键选中改环号）也指出来
    # 只有"环数不够"才多嘴解释（那才是让人看不懂的那种：点明明点了一堆，
    # 按钮却灰着）。单纯点还不够多时保持简短——上方提示本来就写着"至少 3 个
    # 点"，计数也在眼前。一个点都没点时更不必解释。
    window.calib_points_label.setText(
        f"已选 {len(points)} 个点 / {n_rings} 个环"
        if (n_rings >= MIN_RINGS or not points) else
        f"已选 {len(points)} 个点 / {n_rings} 个环——至少要 {MIN_POINTS} 个点、"
        f"覆盖 {MIN_RINGS} 个不同的环才能精修（都判成同一个环号了？"
        f"右键选中那个点，把环号改掉）")
    # 选中点的环号行：谁被选中、现在几环；没选中（或越界）就禁用
    sel_lbl = getattr(window, "calib_selected_lbl", None)
    if sel_lbl is not None:
        sel = state.get("selected")
        has_sel = sel is not None and 0 <= sel < len(points)
        if has_sel:
            sel_lbl.setText(f"选中点：第 {sel + 1} 个"
                            f"（现在判成环 {int(points[sel][2])}）")
            sel_key = (sel, int(points[sel][2]))
            # 只在"换了选中点 / 环号变了"时回填数字框：后台任务随时会调
            # _calib_sync，每次都回填会把用户正拨的数字打回去（2026-10-03）
            if sel_key != getattr(window, "_calib_ring_loaded", None):
                window.calib_ring_spin.setValue(int(points[sel][2]))
            window._calib_ring_loaded = sel_key
        else:
            sel_lbl.setText("选中点：—（右键图上某个点来选）")
            window._calib_ring_loaded = None
        window.calib_ring_spin.setEnabled(has_sel)
        window.calib_ring_apply.setEnabled(has_sel)
    window.calib_start_manual.setEnabled(enough)
    window.calib_undo_btn.setEnabled(bool(points))
    window.calib_clear_btn.setEnabled(bool(points))
    window.calib_start_auto.setEnabled(state["current_geom"] is not None)
    window.calib_current_lbl.setText(_current_geom_text(window))
    # 像素确认：标记只在像素值真的变了时才被清掉（规则 (b)）；
    # 没核对时那行橙色提醒常显（不拦动作，2026-10-05 起）
    cur_px = (state["current_geom"] or {}).get("pixel_size_m")
    window.calib_pixel_chk.setText(
        f"已核对像素尺寸（{(cur_px or 0) * 1e6:.1f} µm）"
        if cur_px is not None else "已核对像素尺寸")
    window.calib_pixel_chk.setChecked(_pixel_ok(window))
    window.calib_pixel_warn.setVisible(
        cur_px is not None and not _pixel_ok(window))
    # 保存区
    has_cur = state["current_geom"] is not None
    window.calib_save_btn.setEnabled(has_cur)
    window.calib_save_hint.setText(
        f"将保存：「{_slot_label(state, 'current')}」的几何"
        if has_cur else "还没有可保存的几何")
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
            # 措辞走统一出口：内部错误说明"不是你操作的问题"（2026-10-05）
            result["metrics_error"] = user_error_text(exc)


def _metrics_note(result: dict) -> str:
    """结果日志的中文指标后缀（没指标时尽量说明原因，不静默）。

    措辞约定：几何离谱时宁可说"无可用环信号 + 贴窗边比例"——**不用
    _warn_rings_off_image 那句"全部落在图像外"**，那句有守卫测试在数
    出现次数，混用会让计数含义变糊。
    """
    if result.get("metrics_error"):
        return f"｜指标不可用：{result['metrics_error']}"
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
    a_txt = (f"晶格常数 a 的离散度 {a['spread_ppm']:.0f} ppm"
             if np.isfinite(a["spread_ppm"]) else "晶格常数 a 的离散度 —")
    init = result.get("metrics_initial")
    init_txt = (f"（初值 {init['dev_px']:.2f} px）"
                if init is not None and np.isfinite(init["dev_px"]) else "")
    return (f"｜环位偏差中位 {m['dev_px']:.2f} px{init_txt}、"
            f"完整环 {m['n_complete']}/{n}、{a_txt}")


# ══ 后台任务：自动 / 手动（_spawn 同款守卫）═══════════════════
def _auto_calib_worker(path_str: str, geom: dict) -> dict:
    """后台线程纯计算：读标样 → 自动定环心 → pyFAI 精修。

    环心先用取点拟合自动定位（fit_center_from_rings），失败再 FFT
    兜底（find_ring_center）。（2026-10-08 删 [再精修] 前这里还收一个
    给定的环心跳过定位——实测那条路与自动定位等价，就不留了。）

    结果附 beam_center_rc：(行, 列) 像素——这次新拟合的环心就是直射
    束落点 B（比沿用配置条目的旧 B 更准），[保存为配置] 用它入条目。

    图像已经在手，顺手附引擎指标（metrics / metrics_initial）——环位
    偏差量的是"精修后几何把理论环放到图像真环上了没有"，是用户在校
    准图上看得见的那件事。
    """
    image = _load_image(path_str)
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

    判环可疑时（同一环号被点在明显不同的半径上）先按尺度重判一遍再拟合
    ——见 _reindex_by_scale（用户 2026-09-30："第 0 青环里套了两个真实的环，
    点它们都判成第 0 环"）。重判赢了就连**赢家那套的起点距离**一起用
    （dist0_m = 原距离 × 赢家尺度）：只换判环、起点还用原来那个错的，拟合
    会落回另一支更差的解（真数据实测 0.31 vs 7.55 px，2026-10-05）。
    """
    image = None
    load_error = None
    if path_str is not None:
        try:
            image = _load_image(path_str)
        except Exception as exc:                      # noqa: BLE001
            load_error = user_error_text(exc)
    rings, note, dist0_m = _reindex_by_scale(points, rings, geom, center0_px, image)
    result = refine_lab6_from_points(
        points, rings, pixel_size_m=geom["pixel_size_m"],
        wavelength_m=geom["wavelength_m"], dist0_m=dist0_m,
        center0_px=center0_px)
    result["beam_center_rc"] = (center0_px[1], center0_px[0])
    if note:
        result["reindex_note"] = note    # 主线程写进日志（不悄悄换判法）
    if path_str is not None:
        if image is None:
            result["metrics"] = None
            result["metrics_initial"] = None
            result["metrics_error"] = load_error
        else:
            _attach_metrics(result, image, geom, initial=geom)
    return result


# 判环可疑时按哪些距离尺度重试（见 _reindex_by_scale）。距离是这套几何里
# **整体缩放环位置**的那个量：它错一个比例，低角侧的真实环就会被压到"第 0
# 环"以下、判环全挤在一起。
_MANUAL_SCALE_PROBE = (0.85, 0.90, 0.95, 1.0, 1.05, 1.10, 1.20)
# 同一环号被点到的两个半径差超过这个比例 = 判环挤在一起了
# （同一个环上点两次半径只差几个百分点，不会误报）
_SUSPICIOUS_RADIUS_RATIO = 1.15


def _ring_radius(p, center_px) -> float:
    """点到束心的半径（px）——判"同一个环号是不是横跨了两个半径"。"""
    return float(np.hypot(float(p[0]) - float(center_px[0]),
                          float(p[1]) - float(center_px[1])))


def _rings_look_suspicious(points, rings, center_px) -> bool:
    """同一个环号被点在明显不同的半径上 → 判环很可能挤在一起了。

    用户 2026-09-30 报的正是这个：第 0 青环里套了两个真实环，点它们都判成
    环 0（半径差一倍）。这是"几何尺度偏了"的火警，触发下面的尺度扫描。
    """
    by_ring = {}
    for p, k in zip(points, rings):
        by_ring.setdefault(int(k), []).append(_ring_radius(p, center_px))
    for radii in by_ring.values():
        if len(radii) >= 2 \
                and max(radii) / max(min(radii), 1e-9) > _SUSPICIOUS_RADIUS_RATIO:
            return True
    return False


def _rings_at_scale(points, geom, center_px, scale) -> list:
    """把距离乘 scale 后重新判环；有任何一点判不到环就返回空表（那套不能用）。

    geom 的形状约定与 _attach_metrics 相同（GUI 交出来的是米制键
    poni1_m/poni2_m），判环要的却是 px 键——先归一化再读，两种形状都收。
    别忘了这一步（2026-10-05 踩过：只认 px 键 → 手动校准一触发尺度扫描
    就 KeyError: 'poni1_px'，正好把这个兜底功能该干活的时候打死了）。
    """
    g = _geom_px_keys(geom) if "poni1_m" in geom else dict(geom)
    g["dist_m"] = float(geom["dist_m"]) * float(scale)
    px = float(g["pixel_size_m"])
    out = []
    for x, y in points:
        k = snap_lab6_ring(
            float(x), float(y), pixel_size_m=px,
            wavelength_m=float(g["wavelength_m"]), dist_m=float(g["dist_m"]),
            poni1_m=float(g["poni1_px"]) * px, poni2_m=float(g["poni2_px"]) * px,
            rot1_deg=float(g.get("rot1_deg") or 0.0),
            rot2_deg=float(g.get("rot2_deg") or 0.0), tol_deg=SNAP_TOL_DEG)
        if k is None:
            return []
        out.append(int(k))
    return out


def _reindex_by_scale(points, rings, geom, center0_px, image):
    """判环可疑时：扫几个尺度重判环、各拟合一次，挑**环位偏差最小**的那套。

    返回 (rings, 说明, dist0_m)；不需要扫 / 扫不动时返回 (rings, "", 原距离)
    ——不猜、不动。换了判环就**连赢家那套的起点距离一起交出来**：只换判环、
    起点还用原来那个错的，重拟合会从错的初值出发落回另一支更差的解——真数据
    实测 0.31 px vs 7.55 px，日志写的和最终拿到的对不上（2026-10-05 查修）。

    为什么扫尺度：判环是"拿当前几何把点击点换算成 2θ、再找最近的理论环"，
    而低角侧没有更低的环可判——距离错一个比例时最里面几个真实环全被压到
    第 0 环。距离是唯一整体缩放环位置的量，所以按比例扫它、每个候选重新判环
    再拟合，用 16 个环的环位偏差挑解（单看 2~3 个点会过拟合，指标才有分辨力）。

    门槛：只有比原尺度（1.0）好 **0.05 px 以上**才换——与"当前配置要不要
    换"同一个噪声口径（calib_model.SOURCE_IMPROVE_MIN_PX），免得在噪声里跳。
    """
    if image is None or not _rings_look_suspicious(points, rings, center0_px):
        return rings, "", float(geom["dist_m"])

    def _fit(scale, screen=True):
        cand = _rings_at_scale(points, geom, center0_px, scale)
        if not cand:
            return None
        if screen and _rings_look_suspicious(points, cand, center0_px):
            # 便宜的先筛一遍（几步判环，~10 ms）：环号还是挤在一起的那套
            # 不可能对，别花 200 ms 去拟合它（用户 2026-10-01："按优化来"）
            return None
        try:
            res = refine_lab6_from_points(
                points, cand, pixel_size_m=float(geom["pixel_size_m"]),
                wavelength_m=float(geom["wavelength_m"]),
                dist0_m=float(geom["dist_m"]) * float(scale),
                center0_px=center0_px)
        except Exception:                              # noqa: BLE001
            return None
        _attach_metrics(res, image, geom, initial=geom)
        dev = _result_dev(res)
        return None if dev is None else (dev, scale, cand)

    # 基准（1.0）**不筛**：它就是"原判法"，得量出它的环位偏差当比较基准
    base = _fit(1.0, screen=False)
    if base is None:
        return rings, "", float(geom["dist_m"])   # 指标算不出来 = 没有判据：老实地不动
    best = base
    for s in _MANUAL_SCALE_PROBE:
        if abs(s - 1.0) < 1e-9:
            continue
        got = _fit(s)
        if got is not None and got[0] <= best[0] - SOURCE_IMPROVE_MIN_PX:
            best = got
    if best[1] == base[1]:
        return rings, "", float(geom["dist_m"])
    dev, scale, cand = best
    return cand, (f"判环可疑（同一环号横跨了两个半径）：按距离尺度 {scale:g} 重判"
                  f"更合理——环位偏差 {dev:.2f} px，原判法 {base[0]:.2f} px"), \
        float(geom["dist_m"]) * float(scale)


def _start_auto_calib(window: QMainWindow) -> None:
    """[定位束心并精修]：从当前配置出发，自动定位环心（取点拟合，FFT
    兜底）→ pyFAI 精修。

    2026-10-08：删掉旧的 [再精修]（target="refined"，绕过定位、直接以
    当前配置的束心与距离为初值再精修一轮）——真 lab6 实测两者等价：
    环位偏差 0.24 vs 0.23 px（重复跑本身就抖 0.014~0.029 px），连按
    两个按钮结果不变，两个入口收敛成一个。
    """
    path = _calib_standard_path(window)
    if path is None:
        _log(window, "请先在文件列表勾选标样文件")
        return
    _open_calib_panel(window, path)   # 面板关了/没开过：重开
    if getattr(window, "calib_dock", None) is None:
        return   # 图像读取失败（_open_calib_panel 已记日志）
    _warn_pixel_unchecked(window)     # 没核对像素只提醒，不拦（2026-10-05）
    geom = dict(_calib_state(window).get("current_geom") or {})
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
        _on_calib_result(window, "auto", result)

    def error(msg):
        window._tasks.remove(task)
        if window._latest_task.get(key) is not task:
            return
        del window._latest_task[key]
        if gen != getattr(window, "calib_gen", -1) \
                or getattr(window, "calib_dock", None) is None:
            return
        _log(window, f"校准失败（定位束心并精修）：{msg}")

    task = BackgroundTask(_auto_calib_worker, str(path), geom,
                          on_done=done, on_error=error)
    window._latest_task[key] = task
    window._tasks.append(task)
    _log(window, f"开始定位束心并精修：{path.name}（后台运行）")
    task.start()


def _start_manual_calib(window: QMainWindow) -> None:
    """[用选点精修]：用户点后台精修（初值 = 当前配置；环心 = 分析条目束心）。"""
    state = _calib_state(window)
    _pts = state["points"]
    if len(_pts) < MIN_POINTS or len({p[2] for p in _pts}) < MIN_RINGS:
        _log(window, f"手动校准至少需要 {MIN_POINTS} 个点、覆盖 {MIN_RINGS} 个"
                     f"不同的环——现在是 {len(_pts)} 个点 / "
                     f"{len({p[2] for p in _pts})} 个环；都挤在同一个环号上时，"
                     f"右键选中那个点，把环号改掉")
        return
    _warn_pixel_unchecked(window)     # 没核对像素只提醒，不拦（2026-10-05）
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
        _log(window, f"校准失败（手动选点）：{msg}")

    path = getattr(window, "calib_path", None)   # 指标要图像；没面板时为 None
    task = BackgroundTask(_manual_calib_worker, str(path) if path else None,
                          points, rings, geom, center0_px,
                          on_done=done, on_error=error)
    window._latest_task[key] = task
    window._tasks.append(task)
    _log(window, f"开始手动校准（{len(points)} 个点，后台运行）")
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
    if result.get("reindex_note"):
        # 判环换过一套（尺度扫描赢的）：先写这句，下面的结果才读得懂
        _log(window, result["reindex_note"])
    _log(window, f"{label}完成（{name}）：距离 "
                 f"{result['dist_m'] * 1000:.2f} mm，"
                 f"PONI ({result['poni1_px']:.2f}, {result['poni2_px']:.2f}) px，"
                 f"残差 {result['residual_deg']:.4f}°"
                 f"{_metrics_note(result)}")
    take, note = _adopt_decision(state, name)
    if take:
        # 一律采纳（用户 2026-09-30："只要是用户操作的……都填入当前，
        # 让用户看到变化"）——青环立刻跟着动，说明里写明好了/差了多少
        _adopt_result(window, name, why="自动采纳")
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
def _default_config_name(window: QMainWindow) -> str:
    """「条目名称」的预填值：标样文件名去掉末尾的「-数字」段。

    用户 2026-10-07："预填文件名的不含-后的长数字部分"——lab6-00024.tif
    → lab6；LMFP_1_atten0-00029.tif → LMFP_1_atten0。没有标样（只在
    校准页里借条目 / 手输几何）时退回按当前条目递推（lmfp1_lab6 →
    lmfp2_lab6，见 config_ops._suggest_config_key）。
    """
    path = getattr(window, "calib_path", None)
    if path:
        stem = Path(path).stem
        cleaned = re.sub(r"-\d+$", "", stem)
        return cleaned or stem
    return _suggest_config_key(config.DEFAULT_CONFIG)


def _refresh_config_name_prefill(window: QMainWindow) -> None:
    """进入校准时把「条目名称」按当前标样文件名重填。

    只覆盖"还挂着上一次预填值"的框（或空框）——用户自己改过的名字不动。
    """
    box = getattr(window, "calib_name_edit", None)
    if box is None:
        return
    if box.text().strip() not in ("", getattr(window, "_calib_name_auto", None)):
        return   # 用户改过：不动
    box.setText(_default_config_name(window))
    window._calib_name_auto = box.text()


def _enter_calib(window: QMainWindow) -> None:
    """进入校准模式（[校准] 按下）：开校准面板 + 按校准页内容拉宽参数坞。

    没勾文件只记日志提示（不崩）——用户勾好文件后点 [定位束心并精修]
    也能开面板。宽度只在够得着时拉：给绘图区留 CALIB_PANEL_RESERVE_PX
    （点环选点是在图上做的，坞太宽就没法点了）。
    """
    # 先让参数坞露出来再量宽：开局它是收起的（_clear_entrance），
    # 隐藏时量到的是旧值/默认值 → 拉宽和"退出还原"都会拿假数字
    # （[校准] 的 toggled 先于 clicked 触发，所以不能指望入口那边先
    # 露坞——2026-09-26 实测：以前量出来 327 px，远没到该有的宽度）
    window.param_dock.setVisible(True)
    # 出口按钮（坞顶那一行）：本模式的显式退出口，进来就亮出来。
    # 放在"没勾文件"的提前返回之前——没文件也要能退出去
    window.calib_exit_btn.setVisible(True)
    path = _calib_standard_path(window)
    if path is None:
        _log(window, "请先在文件列表勾选标样文件")
        return
    _open_calib_panel(window, path)
    _refresh_config_name_prefill(window)   # 「条目名称」跟着这个标样重填
    _widen_dock_for_calib(window)


def _exit_calib(window: QMainWindow) -> None:
    """退出校准模式：关校准面板（关闭即遗忘）+ 参数坞宽度还原。"""
    window.calib_exit_btn.setVisible(False)
    _close_calib_panel(window)
    _restore_dock_width(window)


def _widen_dock_for_calib(window: QMainWindow) -> None:
    """校准模式下把参数坞拉宽到"放下校准页所有内容"（不超过窗口上限）。"""
    dock = getattr(window, "param_dock", None)
    page = getattr(window, "calib_scroll", None)
    if dock is None or page is None:
        return
    if getattr(window, "_dock_w_before", None) is None:
        # 记住分析模式的宽度。参数坞开局是收起的（app._clear_entrance，
        # 用户 2026-09-25 定），点 [校准] 时才刚由隐藏转可见、还没走
        # 布局，此刻 width() 是旧值/默认值 → 先手动走一遍布局再量，
        # 否则"退出校准还原宽度"会还原成一个假数字
        lay = window.layout()
        if lay is not None:
            lay.activate()
        window._dock_w_before = dock.width()
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
