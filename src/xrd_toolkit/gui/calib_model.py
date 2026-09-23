"""校准页的纯逻辑（零 Qt）：状态模型 + 采纳判据 + 表格取值。

从 calib.py 拆出来（纯搬迁）。这里的东西**不许 import Qt**：只吃
dict、吐 dict/字符串，所以能脱离界面直接单测（TestCalibModel），
将来也能给 CLI 复用。界面层（calib / calib_table / calib_panel）
只负责把判断显示出来、把用户动作翻译成对 state 的写入。
"""
from __future__ import annotations      # 注解延迟求值 → 类型注解不 import Qt

import numpy as np

from typing import TYPE_CHECKING

if TYPE_CHECKING:      # 只为类型注解：本模块运行时零 Qt
    from PySide6.QtWidgets import QMainWindow


# 结果种类（校准功能页的累积命名前缀）。原始 = 起点（借来的条目 / 手输 /
# 手改的几何），其余三个是校准动作产出的结果。
KIND_LABELS = {"raw": "原始", "auto": "自动", "manual": "手动",
               "refined": "精修"}
SLOT_LABELS = {"current": "当前配置", "A": "A", "B": "B"}
# "当前配置"的替换门槛（px）：新结果的环位偏差要比当前配置好**这么多**
# 才自动采纳。依据：同一张图重复跑，几何参数会抖（PONI 1.6~2.2 px）而环
# 位偏差只抖 0.014~0.029 px——改善小于 0.05 px 时"变好"是跑动噪声。
SOURCE_IMPROVE_MIN_PX = 0.05

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

    纯函数（不碰 window），便于直接单测。规则四条：
      * 手改/手输过（custom）→ 永不自动替换，只说明；
      * 当前配置还是**借来的出发点**（槽为空）→ 直接采纳：借来的几何是在
        别的批次的图上量出来的，它的环位偏差在这张图上没有可比性——它是
        起点，不是候选者（新批次的第一条结果总是采纳）；
      * 两者都是"跑出来的"（当前配置指向某条结果）→ 新结果要赢过门槛
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
    if state["slots"]["current"] is None:
        return True, (f"当前配置 → {name}（新批次的第一条结果，直接采纳；"
                      f"原先是{cur_txt}，{dev_txt}）")
    if state.get("current_geom") is None or cur_dev is None or dev is None:
        # 没有当前几何（第一次）或两边比不出来 → 采纳
        return True, f"当前配置 → {name}（{dev_txt}）"
    if cur_dev - dev >= SOURCE_IMPROVE_MIN_PX:
        return True, (f"当前配置 → {name}（环位偏差 {dev:.2f} px，优于 "
                      f"{cur_txt} 的 {cur_dev:.2f} px）")
    return False, (f"当前配置保持 {cur_txt}（环位偏差 {cur_dev:.2f} px vs "
                   f"{name} 的 {dev:.2f} px，改善不足 "
                   f"{SOURCE_IMPROVE_MIN_PX:.2f} px）")


def _geom_to_result_shape(geom: dict) -> dict:
    """当前配置几何（_collect_geometry 形状）→ 结果形状（px 键），
    这样三个槽里的东西长得一样，对比代码只认一种形状。"""
    g = _geom_px_keys(geom)
    return {"dist_m": g["dist_m"], "poni1_px": g["poni1_px"],
            "poni2_px": g["poni2_px"], "rot1_deg": g["rot1_deg"],
            "rot2_deg": g["rot2_deg"],
            "beam_center_rc": geom.get("beam_center_rc")}


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


def _geom_px_keys(g: dict) -> dict:
    """_collect_geometry 的输出 → 画图/指标共用的 px 键几何（PONI 米→px）。"""
    return dict(
        pixel_size_m=g["pixel_size_m"], wavelength_m=g["wavelength_m"],
        dist_m=g["dist_m"], poni1_px=g["poni1_m"] / g["pixel_size_m"],
        poni2_px=g["poni2_m"] / g["pixel_size_m"],
        rot1_deg=g["rot1_deg"], rot2_deg=g["rot2_deg"])


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


# ── 校准页与校准图面板共用的常量 ──────────────────────────
SNAP_TOL_DEG = 0.5      # 判环容差（2θ 度；与 snap_lab6_ring 默认一致）

MIN_POINTS = 3          # 手动校准最低点数

MIN_RINGS = 2           # 手动校准最低覆盖环数

PANEL_SCALE = 560       # 校准图面板最长边（像素，图太大就按此缩小）

RING_COLOR = "#00e5ff"  # 理论环 / 用户点标记色（青）

CP_COLOR = "#3dff3d"    # pyFAI 控制点标记色（绿）

# 校准模式的坞宽上限：给绘图区留出的宽度（校准图面板 560 px 缩放目标 +
# 边距）。点环选点是在图上做的，坞再宽就把图挤到没法点了。
CALIB_PANEL_RESERVE_PX = 620


def _row_spec(key_: str):
    """按行键取 (行名, 缩放, 格式, 有无 Δ 行)。"""
    for spec in COMPARE_ROWS:
        if spec[0] == key_:
            return spec
    raise KeyError(key_)
