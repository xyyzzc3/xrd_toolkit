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

# 结果种类（校准功能页的累积命名前缀）。原始 = 起点（借来的条目 / 手输 /
# 手改的几何），其余三个是校准动作产出的结果。
KIND_LABELS = {"raw": "原始", "auto": "自动", "manual": "手动",
               "refined": "精修"}
SLOT_LABELS = {"current": "当前配置", "A": "A", "B": "B"}
# "当前配置"的替换门槛（px）：新结果的环位偏差要比当前配置好**这么多**
# 才自动采纳。依据：同一张图重复跑，几何参数会抖（PONI 1.6~2.2 px）而环
# 位偏差只抖 0.014~0.029 px——改善小于 0.05 px 时"变好"是跑动噪声。
SOURCE_IMPROVE_MIN_PX = 0.05
# 校准模式的坞宽上限：给绘图区留出的宽度（校准图面板 560 px 缩放目标 +
# 边距）。点环选点是在图上做的，坞再宽就把图挤到没法点了。
CALIB_PANEL_RESERVE_PX = 620


# ══ 小工具：状态 / 文件 / 图像 ══════════════════════════════════
def _calib_state(window: QMainWindow) -> dict:
    """校准状态（懒创建）：选点 + 累积结果 + 三个槽（当前配置 / A / B）。

    键：
      points        [(x, y, 环号), ...] 用户点的点
      results       累积的几何结果，按产生顺序：
                    [{"name": 原始/自动1/手动1/…, "kind": raw|auto|manual|
                     refined, "result": 几何 dict（带 metrics）}]
      slots         {"current"/"A"/"B": 结果名 | None}——三个槽各指向一条
                    结果；A/B 默认空。current 为 None 时"当前配置"是手输/
                    手改的独立几何（显示"自定义"）
      pinned        {"A": bool, "B": bool} 槽被用户手动选过 → 新结果不覆盖
      next_slot     轮换指针（A→B→A…），新结果按它填没被钉住的槽
      current_geom  当前配置的几何（_collect_geometry 的 7 键 + beam_center_rc）
      current_from  它的来处说明（"借用 lmfp1_lab6" / "手输" / 结果名）
      current_metrics / current_error  当前配置的指标（异步算）
      custom        True = 手输或手改过（表头显示"自定义"）
      counters      {"auto": n, ...} 累积命名计数
    """
    state = getattr(window, "calib_state", None)
    if state is None:
        state = window.calib_state = {
            "points": [], "results": [],
            "slots": {"current": None, "A": None, "B": None},
            "pinned": {"A": False, "B": False}, "next_slot": "A",
            "current_geom": None, "current_from": None,
            "current_metrics": None, "current_error": None,
            "custom": False, "counters": {}}
    return state


# ── 结果列表与三个槽 ────────────────────────────────────────
def _metrics_dev(metrics) -> float:
    """指标 dict 的环位偏差（px）。没有指标 / 无可用环信号 → None。"""
    dev = (metrics or {}).get("dev_px")
    return float(dev) if dev is not None and np.isfinite(dev) else None


def _result_dev(result) -> float:
    """这条结果的环位偏差（px）——取它附带的 metrics。没有 → None。

    注意口径：结果 dict 里**套着** metrics；而"当前配置"的指标本身就是
    metrics（state["current_metrics"]）——混用会让比较永远判"比不出来"
    （踩过：cur_dev 恒为 None，于是每条新结果都被无条件采纳）。
    """
    return _metrics_dev((result or {}).get("metrics"))


def _result_by_name(state: dict, name):
    """按名字取结果条目（{"name","kind","result"}）；没有 → None。"""
    for item in state["results"]:
        if item["name"] == name:
            return item
    return None


def _next_name(state: dict, kind: str) -> str:
    """累积命名：自动1、手动1、自动2…（每种各数各的）。"""
    n = state["counters"].get(kind, 0) + 1
    state["counters"][kind] = n
    return f"{KIND_LABELS[kind]}{n}"


def _add_result(state: dict, kind: str, result: dict) -> str:
    """把一次校准结果挂进累积列表，返回它的名字。"""
    name = _next_name(state, kind)
    state["results"].append({"name": name, "kind": kind, "result": result})
    return name


def _fill_slot(state: dict, name: str) -> str:
    """新结果按 A→B→A… 填进**没被钉住**的槽，返回日志说明片段。

    槽被手动选过（pinned）就跳过；两个都钉住 → 只进列表、不占槽（否则
    你正在看的对比会被悄悄换掉）。
    """
    order = ("A", "B")
    start = order.index(state.get("next_slot", "A"))
    for i in range(2):
        slot = order[(start + i) % 2]
        if not state["pinned"][slot]:
            state["slots"][slot] = name
            state["next_slot"] = order[(start + i + 1) % 2]
            return f"填进 {slot} 槽"
    return "A/B 槽都被你钉住了，只进列表"


def _adopt_decision(state: dict, name: str) -> tuple:
    """"拟合得好不好"决定当前配置要不要换成这条结果：返回 (是否采纳, 说明)。

    纯函数（不碰 window），便于直接单测。规则三条：
      * 手改/手输过（custom）→ 永不自动替换，只说明；
      * 两边**都**有可用指标时才比：新结果要赢过门槛
        SOURCE_IMPROVE_MIN_PX 才采纳（"没变好就不替换"，避免在噪声里
        来回跳）；
      * 比不出来（任一侧无指标）→ 采纳：它是用户刚跑出来的，没有证据
        说它更差。
    """
    item = _result_by_name(state, name)
    if item is None:
        return False, f"没有这条结果：{name}"
    dev = _result_dev(item["result"])
    cur_dev = _metrics_dev(state.get("current_metrics"))
    cur_txt = state.get("current_from") or "当前配置"
    dev_txt = f"环位偏差 {dev:.2f} px" if dev is not None else "无可用环信号"
    if state.get("custom"):
        return False, (f"当前配置是你手动改过的几何（自定义），"
                       f"不自动替换为 {name}")
    if state.get("current_geom") is None or cur_dev is None or dev is None:
        # 没有当前几何（第一次）或两边比不出来 → 采纳
        return True, f"当前配置 → {name}（{dev_txt}）"
    if cur_dev - dev >= SOURCE_IMPROVE_MIN_PX:
        return True, (f"当前配置 → {name}（环位偏差 {dev:.2f} px，优于 "
                      f"{cur_txt} 的 {cur_dev:.2f} px）")
    return False, (f"当前配置保持 {cur_txt}（环位偏差 {cur_dev:.2f} px vs "
                   f"{name} 的 {dev:.2f} px，改善不足 "
                   f"{SOURCE_IMPROVE_MIN_PX:.2f} px）")


def _result_to_geom(result: dict, base: dict) -> dict:
    """结果（px 键）→ 当前配置几何（_collect_geometry 形状）。

    像素尺寸与波长沿用当前配置（它们不参与拟合），距离 / PONI / 倾斜角 /
    束心取结果。
    """
    out = dict(base or {})
    pixel = out.get("pixel_size_m")
    out["dist_m"] = result["dist_m"]
    out["rot1_deg"] = result["rot1_deg"]
    out["rot2_deg"] = result["rot2_deg"]
    out["beam_center_rc"] = result.get("beam_center_rc")
    if pixel:
        out["poni1_m"] = result["poni1_px"] * pixel
        out["poni2_m"] = result["poni2_px"] * pixel
    return out


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


def _geom_to_result_shape(geom: dict) -> dict:
    """当前配置几何（_collect_geometry 形状）→ 结果形状（px 键），
    这样三个槽里的东西长得一样，对比代码只认一种形状。"""
    g = _geom_px_keys(geom)
    return {"dist_m": g["dist_m"], "poni1_px": g["poni1_px"],
            "poni2_px": g["poni2_px"], "rot1_deg": g["rot1_deg"],
            "rot2_deg": g["rot2_deg"],
            "beam_center_rc": geom.get("beam_center_rc")}


def _slot_result(state: dict, slot: str):
    """某个槽当前指向的结果 dict（结果形状）；空 → None。

    "当前配置"槽为空时返回手输/手改的几何（按结果形状转换，指标取
    current_metrics）——三个槽在对比表里因此是同一种东西。
    """
    name = state["slots"].get(slot)
    if name:
        item = _result_by_name(state, name)
        if item is not None:
            out = dict(item["result"])
            out.setdefault("metrics", None)
            return out
    if slot == "current" and state.get("current_geom") is not None:
        out = _geom_to_result_shape(state["current_geom"])
        out["metrics"] = state.get("current_metrics")
        return out
    return None


def _slot_label(state: dict, slot: str) -> str:
    """槽在表头显示的说明：结果名 / "自定义" / "借用 …"。"""
    name = state["slots"].get(slot)
    if name:
        return name
    if slot == "current":
        return "自定义" if state.get("custom") else (state.get("current_from")
                                                      or "—")
    return "—"


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
    image = load_diffraction_image(path_str)
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


# ── 对比：三列（当前配置 / A / B）+ 基准可选的 Δ 行 ───────────
# 量白名单：一行一个标量。前四个是"判拟合好坏"要看的（环位偏差）与
# "几何尺度差多少"要看的（距离 / 环心）；PONI 与倾斜角标 ⚠ —— 它们与
# 距离/波长近简并，差异大不等于更准。自洽残差**不入表**：它量的是迭代
# 收没收敛，不是拟合得好不好。
# (行键, 行名, 显示缩放, 格式, 是否给 Δ 行)
COMPARE_ROWS = (
    ("dist", "距离 (mm)", 1e3, ".2f", True),
    ("center_r", "环心行 (px)", 1.0, ".2f", False),
    ("center_c", "环心列 (px)", 1.0, ".2f", False),
    ("dev", "环位偏差 (px)", 1.0, ".2f", True),
    ("poni1", "PONI1 (px) ⚠", 1.0, ".2f", False),
    ("poni2", "PONI2 (px) ⚠", 1.0, ".2f", False),
    ("rot1", "rot1 (°) ⚠", 1.0, ".4f", False),
    ("rot2", "rot2 (°) ⚠", 1.0, ".4f", False),
)
COMPARE_HINT = ("⚠ = 退化方向：距离与波长、PONI 与倾斜角近简并，差异大"
                "不等于更准；判优劣只看环位偏差（以及你在图上看到的重合度）。")


def _row_spec(key_: str):
    """按行键取 (行名, 缩放, 格式, 有无 Δ 行)。"""
    for spec in COMPARE_ROWS:
        if spec[0] == key_:
            return spec
    raise KeyError(key_)


def _row_values(res) -> dict:
    """一条结果（结果形状，可为 None）的各行数值：行键 → float|None。"""
    if res is None:
        return {key: None for key, *_ in COMPARE_ROWS}
    bc = res.get("beam_center_rc") or (None, None)
    return {"dist": res.get("dist_m"),
            "center_r": bc[0] if len(bc) == 2 else None,
            "center_c": bc[1] if len(bc) == 2 else None,
            "dev": _result_dev(res),
            "poni1": res.get("poni1_px"), "poni2": res.get("poni2_px"),
            "rot1": res.get("rot1_deg"), "rot2": res.get("rot2_deg")}


def _fmt_row(key_: str, value) -> str:
    """某行数值的显示串（无值 / NaN → "—"）。"""
    if value is None or not np.isfinite(value):
        return "—"
    _key, _name, scale, fmt, _d = _row_spec(key_)
    return f"{value * scale:{fmt}}"


def _delta_text(base_res, res, key_: str) -> str:
    """Δ 行：该列 − 基准列（只给开了 Δ 的行；基准列自身显示 "—"）。"""
    if base_res is res or not _row_spec(key_)[4]:
        return "—"
    b = _row_values(base_res)[key_]
    v = _row_values(res)[key_]
    if b is None or v is None or not (np.isfinite(b) and np.isfinite(v)):
        return "—"
    return f"{(v - b) * _row_spec(key_)[2]:+.2f}"


def _verdict(base_res, res, base_name: str, res_name: str) -> str:
    """结论行：只按环位偏差判"谁拟合得更好"（门槛 SOURCE_IMPROVE_MIN_PX）。"""
    b, v = _result_dev(base_res), _result_dev(res)
    if b is None or v is None:
        return (f"结论：{res_name} 或 {base_name} 没有可用环位偏差，判不了"
                f"（看上面两列的数值自行判断）")
    diff = v - b
    if abs(diff) < SOURCE_IMPROVE_MIN_PX:
        return (f"结论：{res_name} 与 {base_name} 拟合得差不多（环位偏差 "
                f"{v:.2f} vs {b:.2f} px，差 {abs(diff):.2f} px 小于门槛 "
                f"{SOURCE_IMPROVE_MIN_PX:.2f}）")
    better, worse, bd, wd = ((res_name, base_name, v, b) if diff < 0
                             else (base_name, res_name, b, v))
    return (f"结论：{better} 拟合得更好（环位偏差 {bd:.2f} vs {wd:.2f} px，"
            f"差 {abs(diff):.2f} px > 门槛 {SOURCE_IMPROVE_MIN_PX:.2f}）")


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
    """画图几何（统一 px 键）：**当前配置**的几何。

    图上那圈青线画的就是"当前配置"预测的环——A/B 只用于对比、不动图；
    只有采纳（谁拟合得好）才换图。没设定当前配置时退回分析配置。
    """
    state = _calib_state(window)
    geom = state.get("current_geom")
    if geom is None:
        return _geom_px_keys(_collect_geometry(window))
    return _geom_px_keys(geom)



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
    # 新面板 = 新一轮：当前配置从分析页选中的条目借起（登记成"原始"），
    # 并异步算它的环位偏差（表里那一格先显示 —）
    _ensure_current(window)
    _calib_sync(window)
    _refresh_current_metrics(window)


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


def _sync_slot_combos(window: QMainWindow) -> None:
    """把累积结果填进三个槽的下拉框（状态是唯一真相，重填时保留选择）。"""
    state = _calib_state(window)
    names = [item["name"] for item in state["results"]]
    for slot, combo in window.calib_slot_combo.items():
        combo.blockSignals(True)
        combo.clear()
        if slot == "current":
            combo.addItem(_slot_label(state, "current"), None)
        else:
            combo.addItem("—（空）", None)
        for name in names:
            combo.addItem(name, name)
        idx = combo.findData(state["slots"].get(slot))
        combo.setCurrentIndex(max(0, idx))
        combo.blockSignals(False)


def _on_slot_changed(window: QMainWindow, slot: str) -> None:
    """某个槽的下拉框选了一条结果（或清空）。"""
    state = _calib_state(window)
    name = window.calib_slot_combo[slot].currentData()
    if name is None:
        if slot != "current" and state["slots"][slot] is not None:
            state["slots"][slot] = None
            state["pinned"][slot] = False
            _log(window, f"{slot} 槽清空（取消钉住）")
            _calib_sync(window)
        return
    if slot == "current":
        if state["slots"]["current"] != name:
            _adopt_result(window, name, why="手动选择")
    else:
        state["slots"][slot] = name
        state["pinned"][slot] = True
        _log(window, f"{slot} 槽 → {name}（钉住：新结果不再覆盖它）")
    _calib_sync(window)


def _refresh_table(window: QMainWindow) -> None:
    """按三个槽的当前内容刷新数值表 + Δ 行 + 结论。"""
    state = _calib_state(window)
    res = {slot: _slot_result(state, slot) for slot in ("current", "A", "B")}
    base_slot = window.calib_base_combo.currentData()
    base_res = res.get(base_slot)
    for key_, _name, _scale, _fmt, has_delta in COMPARE_ROWS:
        vals = {slot: _row_values(res[slot])[key_] for slot in ("current", "A", "B")}
        for slot in ("current", "A", "B"):
            window.calib_vals[slot][key_].setText(_fmt_row(key_, vals[slot]))
        if has_delta:
            for slot in ("current", "A", "B"):
                window.calib_vals["delta"][key_][slot].setText(
                    "—" if slot == base_slot
                    else _delta_text(base_res, res[slot], key_))
    other = "A" if base_slot != "A" else "B"
    window.calib_verdict.setText(
        _verdict(base_res, res[other], SLOT_LABELS[base_slot],
                 SLOT_LABELS[other]))


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
    image = load_diffraction_image(path_str)
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
            image = load_diffraction_image(path_str)
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
def _import_poni(window: QMainWindow) -> None:
    """[加载参数]：读 .poni 交换格式几何文件 → 存成用户配置条目。

    .poni 是 pyFAI 生态通用的几何交换格式（别的工具/命令行标定的
    结果常以这种文件交付）。导入 = 解析出几何 → 照 GUI 配置条目的
    形状存进本地 config_user.json（与 [保存为配置] 同源，重启仍
    在）→ 下拉框重建并自动选中（_apply_config 立即生效）。pyFAI
    只在点击时导入：CLI 用户与纯测试环境不为此多背启动依赖。
    """
    path_str, _ = QFileDialog.getOpenFileName(
        window, "选择 .poni 几何文件", "data",
        "pyFAI 几何 (*.poni);;所有文件 (*)")
    if not path_str:
        return
    p = Path(path_str)
    try:
        import pyFAI
        ai = pyFAI.load(str(p))
    except Exception as err:
        _log(window, f".poni 读取失败 {p.name}（{err}）")
        return
    # 必备几何字段缺一不可（探测器库不认识旧型号时 pixel 可能缺失）
    missing = [field for field, val in (
        ("dist", ai.dist), ("poni1", ai.poni1), ("poni2", ai.poni2),
        ("rot1", ai.rot1), ("rot2", ai.rot2),
        ("wavelength", ai.wavelength),
        ("pixel", getattr(ai, "pixel1", None) or getattr(ai, "pixel2", None)),
    ) if val is None]
    if missing:
        _log(window, f".poni 缺少几何字段：{', '.join(missing)}，无法导入")
        return
    pixel = float(ai.pixel1)   # 配置只有单一像素尺寸：非方像素取 pixel1
    if float(ai.pixel2) != pixel:
        _log(window, "注意：.poni 像素非方形（pixel1≠pixel2），配置只"
                     "存单一像素尺寸，已取 pixel1")
    # 束心 = getFit2D 的直射束落点（含倾斜修正）：正是配置条目的 B
    # 语义（B ≠ PONI，探测器有倾斜时两者差可达 23 px，见 config.py
    # 注释）——不能直接用 poni/pixel 投影。约定核实过：pyFAI 里
    # centerX = 列、centerY = 行，与内置 lmfp1_lab6 条目的实测值吻合。
    fit2d = ai.getFit2D()
    entry = {
        "label": p.stem,
        "geometry": {
            "pixel_size_m": pixel,
            "wavelength_m": float(ai.wavelength),
            "dist_m": float(ai.dist),
            "poni1_m": float(ai.poni1),
            "poni2_m": float(ai.poni2),
            "rot1_deg": float(np.degrees(ai.rot1)),
            "rot2_deg": float(np.degrees(ai.rot2)),
        },
        "beam_center": (float(fit2d.centerY), float(fit2d.centerX)),
    }
    # key = 文件名清洗（只留字母数字下划线）；数字开头补前缀，
    # 撞名依次补 _poni1/_poni2…（注册表含内置，循环避开全部重名）
    key = re.sub(r"[^A-Za-z0-9_]", "_", p.stem)
    if not key or key[0].isdigit():
        key = "poni_" + key
    base, n = key, 1
    while key in config.CONFIGS:
        key = f"{base}_poni{n}"
        n += 1
    try:
        is_new = config.save_user_config(key, entry)
    except ValueError as err:
        _log(window, f".poni 导入失败（{err}）")
        return
    _reload_config_combo(window, key)
    # 导入的几何同时成为"当前配置"的起点（新批次的导入通道），并把像素
    # 确认清掉——像素值换了必须重新确认
    window.calib_pixel_ok_m = None
    _borrow_entry(window, key)
    _log(window, f"已导入 .poni → 配置条目 {key}"
                 f"（{'新增' if is_new else '覆盖同名条目'}，已自动选中，"
                 f"重启后仍在）")
    _calib_sync(window)


def _save_poni(window: QMainWindow) -> None:
    """[保存参数]：把当前选中的几何配置写成标准 .poni 文件。

    保存内容 = 探测器距离 / 中心点 / 像素尺寸 / 波长 / 倾斜角。
    中心点在 .poni 标准里就是 poni1/poni2 米制坐标（像素束心含
    显示语义、不含倾斜修正，不属于几何量——加载回来时由
    getFit2D 重算，往返探测已验证自洽）。作业规格里的"掩膜文件
    路径"是可选项：引擎尚未支持掩膜，且 pyFAI .poni 格式本身没
    有掩膜字段，故不写。保存成功记日志（列出保存内容，供核对）。
    默认文件名 = {配置名}.poni、默认目录 outputs/，同 [加载参数]
    共用一套读写口径（pyFAI 只在点击时导入，CLI/测试不为启动背
    依赖）。
    """
    cfg = window.config   # 当前选中条目（label / geometry / beam_center）
    geom = cfg["geometry"]
    default = str(Path("outputs") / f"{window.config_name}.poni")
    path_str, _ = QFileDialog.getSaveFileName(
        window, "保存几何参数（.poni）", default,
        "pyFAI 几何 (*.poni);;所有文件 (*)")
    if not path_str:
        return   # 用户取消
    if not path_str.lower().endswith(".poni"):
        path_str += ".poni"
    try:
        from pyFAI.geometry import Geometry
        g = Geometry(
            dist=float(geom["dist_m"]),
            poni1=float(geom["poni1_m"]),
            poni2=float(geom["poni2_m"]),
            rot1=float(np.radians(geom["rot1_deg"])),
            rot2=float(np.radians(geom["rot2_deg"])),
            pixel1=float(geom["pixel_size_m"]),
            pixel2=float(geom["pixel_size_m"]),
            wavelength=float(geom["wavelength_m"]))
        Path(path_str).parent.mkdir(parents=True, exist_ok=True)
        g.save(path_str)
    except Exception as err:
        _log(window, f".poni 保存失败（{err}）")
        return
    _log(window, f"已保存几何参数 → {path_str}"
                 f"（距离 {geom['dist_m'] * 1e3:.2f} mm，"
                 f"中心 poni1={geom['poni1_m']:.6g} m, "
                 f"poni2={geom['poni2_m']:.6g} m，"
                 f"像素 {geom['pixel_size_m'] * 1e6:.1f} µm，"
                 f"波长 {geom['wavelength_m'] * 1e10:.4f} Å，"
                 f"倾斜 rot1={geom['rot1_deg']:.4f}°, "
                 f"rot2={geom['rot2_deg']:.4f}°）")


def _sync_del_config_btn(window: QMainWindow) -> None:
    """[删除] 按钮置灰同步：选中内置条目时不可删（人工登记注册表）。

    下拉框当前索引变化时由连接调用；_reload_config_combo 重建下拉
    框后索引不变不触发信号，调用方（_delete_config / _calib_sync）
    再显式补一次。
    """
    btn = getattr(window, "del_config_btn", None)
    combo = getattr(window, "config_combo", None)
    if btn is None or combo is None:
        # 版面还没建全（校准页先于分析页建，此刻没有下拉框）——安全忽略，
        # 分析页建好后会自己再同步一次
        return
    idx = combo.currentIndex()
    btn.setEnabled(idx >= 0 and
                   combo.itemData(idx) not in config.BUILTIN_CONFIGS)


def _delete_config(window: QMainWindow) -> None:
    """[删除]：把当前选中的用户配置条目从注册表与磁盘移除。

    只删用户条目（.poni 导入 / [保存为配置] 产生）——内置条目是
    config.py 人工登记的注册表，按钮置灰 + 处理函数双保险拒绝。
    删除前弹确认框；删后下拉框重建并切回默认条目（删除的对象是
    "当前选中"条目，删完当前选中已不存在）。
    """
    combo = window.config_combo
    key = combo.itemData(combo.currentIndex())
    if key in config.BUILTIN_CONFIGS:
        _log(window, f"内置条目 {key} 不可删除（人工登记的注册表）")
        return
    entry = config.USER_CONFIGS.get(key)
    if entry is None:
        _log(window, f"用户条目 {key} 不存在，无需删除")
        return
    answer = QMessageBox.question(
        window, "删除配置",
        f"删除用户配置条目 {key}（{entry['label']}）？\n"
        "删除后不可恢复（内置条目不受影响）。",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    if answer != QMessageBox.Yes:
        return
    try:
        removed = config.remove_user_config(key)
    except ValueError as err:
        _log(window, f"删除失败（{err}）")
        return
    if not removed:
        _log(window, f"用户条目 {key} 不存在，无需删除")
        return
    _reload_config_combo(window, config.DEFAULT_CONFIG)
    _sync_del_config_btn(window)
    _log(window, f"已删除配置条目 {key}（{entry['label']}），"
                 f"已切回默认条目 {config.DEFAULT_CONFIG}")


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
    """[存为配置]：把**当前配置**的几何 → 命名用户条目（本地落盘）。

    校验 key/label → 与内置条目撞名拒绝、与已存用户条目撞名弹确认
    覆盖 → config.save_user_config 落盘 → 下拉框重建并自动选中新
    条目（_apply_config 把几何填进参数坞，保存即生效）。

    血缘一并写入：method = 这份几何是哪个动作产出的（raw/auto/manual/
    refined/custom）、derived_from = 这一批的起点借自哪条、created =
    保存时间。
    """
    state = _calib_state(window)
    if state.get("current_geom") is None:
        _log(window, "还没有可保存的几何（先选一条配置或用 [编辑…] 填）")
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
    state = _calib_state(window)
    g = state["current_geom"]
    pixel = g["pixel_size_m"]
    name = state["slots"]["current"]
    item = _result_by_name(state, name) if name else None
    entry = {
        "label": label,
        "geometry": {
            "pixel_size_m": pixel,
            "wavelength_m": g["wavelength_m"],
            "dist_m": g["dist_m"],
            "poni1_m": g["poni1_m"],
            "poni2_m": g["poni2_m"],
            "rot1_deg": g["rot1_deg"],
            "rot2_deg": g["rot2_deg"],
        },
        "beam_center": tuple(
            g.get("beam_center_rc") or window.config["beam_center"]),
        # 血缘：哪个动作产出的（raw/auto/manual/refined/custom）、
        # 这一批的起点借自哪条、何时保存
        "method": item["kind"] if item else
                  ("custom" if state.get("custom") else "raw"),
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    if item is not None and item["result"].get("residual_deg") is not None:
        entry["residual_deg"] = item["result"]["residual_deg"]
    if state.get("base_key") in config.CONFIGS:
        entry["derived_from"] = state["base_key"]
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
