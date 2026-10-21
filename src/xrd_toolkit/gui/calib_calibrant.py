"""校准标样辅助：选择/别名解析、环数同步、像素尺寸核对、当前几何文本。

2026-10-21 从 calib.py 原样搬出（只改结构，不动逻辑）。
calib 再导出；对 _calib_sync / _refresh_current_metrics 用函数内延迟导入破环。
"""
from pathlib import Path

from PySide6.QtWidgets import QMainWindow, QMessageBox

from xrd_toolkit import config
from xrd_toolkit.gui.calib_model import (
    _add_custom_result, _calib_state, _clear_calib_session,
    _geom_to_result_shape, _next_name, _result_by_name, _result_dev,
    _result_to_geom, _slot_label)
from xrd_toolkit.gui.calib_panel import (
    _calib_image, _redraw_calib, _redraw_calib_if_open)
from xrd_toolkit.gui.panel_state import _log
from xrd_toolkit.services.calibrants import (
    DEFAULT_CALIBRANT, MAX_GUI_RINGS, calibrant_label, calibrant_short_label,
    calibrant_tooltip, complete_ring_count, detect_calibrant_from_stem,
    ring_total)


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
    from xrd_toolkit.gui.calib import _refresh_current_metrics  # 破循环
    _refresh_current_metrics(window)
    # 借用/导入 .poni 换了当前配置的几何 → 青线跟着换（面板没开时是空操作）
    _redraw_calib_if_open(window)
    return state["current_from"]

# ── 标样选择（常用 pyFAI 内置标样；换标样只清会话，不动几何）──────
def _sync_calibrant_widgets(window: QMainWindow) -> None:
    """把 state 的标样/环数刷到两个控件上（程序写入，不触发信号）。"""
    combo = getattr(window, "calib_standard_combo", None)
    spin = getattr(window, "calib_ring_count_spin", None)
    if combo is None or spin is None:
        return
    state = _calib_state(window)
    name = state.get("calibrant") or DEFAULT_CALIBRANT
    total = max(1, min(ring_total(name), MAX_GUI_RINGS))
    for w in (combo, spin):
        w.blockSignals(True)
    try:
        idx = combo.findData(name)
        if idx < 0:                 # 非常用内置名（脚本/手改配置带进来的）
            combo.addItem(calibrant_label(name), name)
            idx = combo.count() - 1
        combo.setCurrentIndex(idx)
        spin.setRange(1, total)
        spin.setValue(int(min(max(state.get("n_rings", 1), 1), total)))
    finally:
        for w in (combo, spin):
            w.blockSignals(False)
    combo.setToolTip(
        "校准用的标准样品。它只决定理论环的 2θ 阶梯；距离/PONI/倾斜角"
        "与标样无关。请按标样证书选到具体 SRM 变体。\n\n"
        + calibrant_tooltip(name))
    spin.setToolTip(
        f"参与画环/判环/精修/指标的理论环数。默认按几何自动算——"
        f"完整落在图像内的环数；{calibrant_short_label(name)} 共 "
        f"{ring_total(name)} 条反射，界面最多 {MAX_GUI_RINGS} 条。"
        f"改它会按新环数重算当前配置的指标。")

def _update_ring_count(window: QMainWindow, force_auto: bool = False) -> None:
    """按当前几何算默认环数（完整落在图像内的环数）；用户手改过就不动。

    只读缓存里的标样图尺寸，不重复解码（_calib_image 有缓存）；拿不到
    几何/图像时退回 16，并把结果夹到反射总数和 GUI 上限。
    """
    state = _calib_state(window)
    name = state.get("calibrant") or DEFAULT_CALIBRANT
    total = max(1, min(ring_total(name), MAX_GUI_RINGS))
    if force_auto or state.get("n_rings_auto", True):
        state["n_rings_auto"] = True
        n = None
        geom = state.get("current_geom")
        path = getattr(window, "calib_path", None)
        if geom is not None and path is not None:
            try:
                image = _calib_image(window, path)
                beam = geom.get("beam_center_rc") \
                    or window.config.get("beam_center")
                if beam is not None and None not in beam:
                    n = complete_ring_count(
                        name,
                        wavelength_m=float(geom["wavelength_m"]),
                        pixel_size_m=float(geom["pixel_size_m"]),
                        dist_m=float(geom["dist_m"]),
                        beam_center_rc=(float(beam[0]), float(beam[1])),
                        image_shape=image.shape)
            except Exception:                          # noqa: BLE001
                n = None
        state["n_rings"] = int(min(max(n if n is not None else 16, 1), total))
    else:
        state["n_rings"] = int(min(max(int(state["n_rings"]), 1), total))
    _sync_calibrant_widgets(window)

def _init_calibrant_for_path(window: QMainWindow, path: Path) -> None:
    """新校准图：按文件名预选标样，认不出就沿用所选配置条目的标样。

    文件名是这个会话里最可靠的证据（图本身就是标样）；配置条目的
    calibrant 只作兜底。都不是就 LaB6（历史默认）。
    """
    state = _calib_state(window)
    name = detect_calibrant_from_stem(Path(path).stem)
    if name is None:
        entry = config.CONFIGS.get(getattr(window, "config_name", "")) or {}
        cand = entry.get("calibrant")
        if isinstance(cand, str) and cand.strip():
            try:
                ring_total(cand)
                name = cand.strip()
            except ValueError:
                name = None
    state["calibrant"] = name or DEFAULT_CALIBRANT
    state["n_rings_auto"] = True
    state["n_rings"] = 16

def _on_calibrant_changed(window: QMainWindow, _index: int) -> None:
    """下拉框换标样：有选点/结果时确认，然后清空本次会话（几何保留）。"""
    combo = getattr(window, "calib_standard_combo", None)
    if combo is None:
        return
    name = combo.currentData()
    state = _calib_state(window)
    old = state.get("calibrant")
    if not name or name == old:
        return
    has_work = bool(state["points"]) or any(
        it.get("kind") != "raw" for it in state["results"])
    if has_work:
        answer = QMessageBox.question(
            window, "换标样",
            f"换标样会清空本次选点和校准结果（当前几何保留）。\n\n"
            f"{calibrant_short_label(old)} → {calibrant_short_label(name)}\n"
            f"继续吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            _sync_calibrant_widgets(window)          # 下拉框还原
            return
    state["calibrant"] = name
    _clear_calib_session(state)
    state["n_rings_auto"] = True
    # 在飞的自动/手动/指标任务作废（它们拿的还是旧标样）
    window.calib_gen = getattr(window, "calib_gen", 0) + 1
    _update_ring_count(window, force_auto=True)
    from xrd_toolkit.gui.calib import _calib_sync, _refresh_current_metrics  # 破循环
    _calib_sync(window)
    _refresh_current_metrics(window)
    _redraw_calib_if_open(window)
    _log(window, f"标样切换为 {calibrant_label(name)}：本次选点/结果已清空，"
                 f"当前几何保留；环数按 {state['n_rings']} 条算")

def _on_ring_count_changed(window: QMainWindow, value: int) -> None:
    """手改环数：记住用户值（不再自动），按新环数重算指标并重画。"""
    state = _calib_state(window)
    total = max(1, min(ring_total(state.get("calibrant") or DEFAULT_CALIBRANT),
                       MAX_GUI_RINGS))
    value = min(max(int(value), 1), total)
    state["n_rings"] = value
    state["n_rings_auto"] = False
    state["current_metrics"] = None     # 旧环数算的指标先撤下，异步重算
    _sync_calibrant_widgets(window)
    from xrd_toolkit.gui.calib import _calib_sync, _refresh_current_metrics  # 破循环
    _calib_sync(window)          # 选中点的环号框上界跟着变
    _refresh_current_metrics(window)
    _redraw_calib_if_open(window)
    over = sorted({int(p[2]) for p in state["points"] if int(p[2]) >= value})
    if over:
        _log(window, f"注意：已有选点的环号（{'、'.join(map(str, over))}）"
                     f"超出新环数 {value}，精修会拒绝——请撤销这些点、"
                     f"改环号，或把环数调大")
    if any(it.get("kind") != "raw" for it in state["results"]):
        _log(window, f"环数改为 {value}：当前配置的指标已按新环数重算；"
                     f"A/B 里旧结果的指标仍是旧环数算的，重新校准可刷新")

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
    cal_txt = calibrant_short_label(
        state.get("calibrant") or DEFAULT_CALIBRANT)
    if geom.get("dist_m") is None:
        return f"当前配置：{from_txt}（标样 {cal_txt}）"
    return (f"当前配置：{from_txt}（标样 {cal_txt}；距离 "
            f"{geom['dist_m'] * 1e3:.2f} mm、"
            f"像素 {(geom.get('pixel_size_m') or 0) * 1e6:.1f} µm）")

