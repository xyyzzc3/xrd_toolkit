"""处理/背景参数：从面板取几何、背景/平滑/裁剪参数、处理曲线。

2026-10-21 从 panel_state.py 原样搬出（只改结构，不动逻辑）。
panel_state 再导出，drawing/compare/refresh 照旧经 gui_panel_state 取用。
"""
import numpy as np
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.state.base import _log, _panel_param
from xrd_toolkit.services import process


# 决定"先扣图再积分 ≡ 先积分再扣"能否成立的几何量（2θ 范围与点数不在
# 其中：网格不一致由 interp_onto_grid 重插值处理，几何不一致没法补）
_BG_GEOM_KEYS = ("pixel_size_m", "wavelength_m", "dist_m", "poni1_m",
                 "poni2_m", "rot1_deg", "rot2_deg")

def _collect_geometry(window: QMainWindow) -> dict:
    """收集当前**几何配置条目**的积分几何（面板上的几何字段是只读显示）。

    分析页只读后这里没有"面板覆盖配置"的隐藏通路了：像素/波长/距离/
    PONI/倾斜角一律取 window.config["geometry"]（下拉框选中哪条就是
    哪条）；要改几何去校准页。2θ 上下限是积分设置（参与计算，不是
    显示窗口），仍从面板取：_compute_integration 原样 **geom 传给
    integrate_1d，改了范围点 [应用] 即按新区间重积分。

    （几何字段的控件仍留在 window.params 里：_apply_config 往它们填
    显示值、快照回放与测试也按这些键找控件——改的是"谁说了算"，
    不是控件本身。）
    """
    geom = window.config["geometry"]
    return dict(
        pixel_size_m=geom["pixel_size_m"],
        wavelength_m=geom["wavelength_m"],
        dist_m=geom["dist_m"],
        poni1_m=geom["poni1_m"],
        poni2_m=geom["poni2_m"],
        rot1_deg=geom["rot1_deg"],
        rot2_deg=geom["rot2_deg"],
        tth_min_deg=window.params["2θ 下限 (°)"].value(),
        tth_max_deg=window.params["2θ 上限 (°)"].value(),
    )

def _bg_geom_sig(geom) -> tuple:
    """几何指纹：只取真正影响积分的几何量，浮点取整避免表示误差误报。"""
    return tuple(round(float(geom.get(k, 0.0)), 12) for k in _BG_GEOM_KEYS)

def _bg_params(window: QMainWindow, dock, path) -> dict:
    """该面板当前的背景扣除参数（显示参数 + 窗级数据 → 纯函数层字典）。

    锚点按文件存（window.bg_anchors[str(path)]）——一套锚点套到不同曲线
    上是误导：每条曲线的背景形状不一样。空扫是整批实验的属性，所以放
    window.bg_blank（窗级），天然免疫面板弹出/收回（panels.py 的
    _PANEL_ATTRS 弹出白名单只搬少量属性，dock 上的新属性会丢）。
    """
    return {
        "mode": _panel_param(window, dock, "背景扣除模式", "off"),
        "window_deg": _panel_param(window, dock, "背景窗口 (°)", 0.5),
        "blank_scale": _panel_param(window, dock, "空扫归一化", 1.0),
        "anchors": (getattr(window, "bg_anchors", None) or {}).get(str(path),
                                                                  []),
        "anchor_method": _panel_param(window, dock, "锚点拟合方式", "linear"),
    }

def _smooth_cut_params(window: QMainWindow, dock) -> dict:
    """「处理」页里平滑与裁剪两项的参数（各自可以关掉）。

    两项都按**面板快照**取（_panel_param），所以整批处理时"每张图用自己
    那份设置"这条口径与背景扣除一致；关掉时返回中性值（0 度 / 无区间），
    链就退化成"只有背景扣除"。

    裁剪是**一串区间**（用户 2026-09-25 要的"同时删 2–3° 和 7–8°"）：
    快照里的 "裁剪区间" 存列表；老快照存的是布尔 + 起止两个数字，这里
    兼容读（不改老数据，只在读的时候折成一段）。
    """
    smooth = bool(_panel_param(window, dock, "平滑曲线", False))
    listed = _panel_param(window, dock, "裁剪区间", False)
    if isinstance(listed, (list, tuple)):
        ranges = [(float(lo), float(hi)) for lo, hi in listed
                  if float(hi) > float(lo)]
        if not ranges:              # 列表为空 = 不裁（勾选框只是开关）
            ranges = []
    elif listed:
        lo = float(_panel_param(window, dock, "裁剪起点 (°)", 0.0) or 0.0)
        hi = float(_panel_param(window, dock, "裁剪终点 (°)", 0.0) or 0.0)
        ranges = [(lo, hi)] if hi > lo else []
    else:
        ranges = []
    return {
        "smooth_deg": (float(_panel_param(window, dock, "平滑窗口 (°)", 0.0)
                             or 0.0) if smooth else 0.0),
        "smooth_method": _panel_param(window, dock, "平滑方法", "boxcar"),
        "smooth_order": int(_panel_param(window, dock, "平滑阶数", 3) or 3),
        "cut_ranges": ranges,
    }

def _cut_list_of(window: QMainWindow) -> list:
    """窗口级的"要裁剪的区间"清单（界面上 [添加]/[清空] 改的就是它）。"""
    if getattr(window, "cut_list", None) is None:
        window.cut_list = []
    return window.cut_list

def _proc_params(window: QMainWindow, dock, path) -> dict:
    """该面板当前的处理参数（背景 + 平滑 + 裁剪）→ services/process.apply_chain。"""
    params = _bg_params(window, dock, path)
    params.update(_smooth_cut_params(window, dock))
    params["clip"] = bool(_panel_param(window, dock, "负值截断为 0", False))
    return params

def _proc_settings(window: QMainWindow, dock, path) -> dict:
    """处理产物**键**用的口径：背景设置 + 平滑/裁剪（开着的才进，见
    services/process.chain_parts）。没开平滑与裁剪时逐位等于 _bg_settings，
    所以老产物继续命中。
    """
    settings = _bg_settings(window, dock, path)
    settings.update(_smooth_cut_params(window, dock))
    return settings

def _bg_settings(window: QMainWindow, dock, path) -> dict:
    """该面板当前的背景设置（bg 产物键用的规范化字典）。

    与 _bg_params 的区别：这里是给**缓存键**用的口径，所以把"能影响
    扣后曲线"的东西全列出来——模式 / 窗口 / 拟合方式 / 锚点（2θ 与强度
    都算：批量应用时每个文件的强度是各自曲线上的重新取样值，所以同一套
    锚点在不同文件上的键本来就不同，这是对的）/ 负值截断 / 空扫图指纹。

    改任意一项 → 键变 → 当场重画（实时预览照旧）；设置没变 → 跨会话读
    产物。这条接缝就是"既实时可调、又能落盘复用"的关键。
    """
    from xrd_toolkit.services import stage_cache
    params = _bg_params(window, dock, path)
    blank = getattr(window, "bg_blank", None)
    blank_sig = None
    if params["mode"] == "blank" and blank is not None:
        try:                       # 空扫只记指纹：曲线本身不入键
            blank_sig = list(stage_cache.fingerprint(blank["path"]))
        except Exception:                                # noqa: BLE001
            blank_sig = None
    return {
        "mode": params["mode"],
        "window_deg": float(params["window_deg"]),
        "anchor_method": params["anchor_method"],
        "clip": bool(_panel_param(window, dock, "负值截断为 0", False)),
        "anchors": [(float(x), float(y)) for x, y in params["anchors"]],
        "blank": blank_sig,
    }

def _proc_curve(window: QMainWindow, dock, path, tth, intensity):
    """按该面板的处理设置跑整条链：**背景扣除 → 平滑 → 裁剪**。

    返回 (tth, 处理后强度, 基线或 None)。三项全关时原样返回（基线 None）
    ——调用方据此决定要不要画"原始/基线"辅助线。

    **缓存里的曲线永远不动**：处理只发生在绘制时，所以参数一变重画即可、
    不需要重新积分（实时预览的前提，见 services/background.py 里关于积分
    线性的说明）。批量处理时写进产物的也是同一份 apply_chain 的输出——
    屏幕与文件同源。
    path 可以是 None（拿不到路径的场景）→ 该曲线按无锚点处理。
    """
    params = _proc_params(window, dock, path)
    blank = getattr(window, "bg_blank", None)
    blank_curve = None
    if params["mode"] == "blank" and blank is not None:
        blank_curve = (blank["tth"], blank["intensity"])
        # 几何不一致时"两图各自积分再相减"不再等价于"两图相减再积分"
        # （前提是同一个 2θ 环带）。只在状态翻转时提示一次——本函数在
        # 每次重画都会跑，每次记日志会刷屏
        sig = blank.get("geom_sig")
        bad_geom = (sig is not None
                    and sig != _bg_geom_sig(_collect_geometry(window)))
        if bad_geom != getattr(window, "_bg_geom_warned", False):
            window._bg_geom_warned = bad_geom
            if bad_geom:
                _log(window, "背景扣除提示：空扫图与当前几何不一致"
                             "（像素/波长/距离/束心/倾斜有变化）。"
                             "请按相同几何重新积分空扫图")
        # 空扫没覆盖到的 2θ 区间不扣（interp_onto_grid 在那里返回 0）——
        # 用户在覆盖范围外看到"没扣"却没有任何提示，会以为功能坏了
        b_tth = np.asarray(blank["tth"], dtype=float)
        uncovered = (b_tth.size > 0
                     and (float(b_tth[0]) > float(tth[0]) + 1e-9
                          or float(b_tth[-1]) < float(tth[-1]) - 1e-9))
        if uncovered != getattr(window, "_bg_cover_warned", False):
            window._bg_cover_warned = uncovered
            if uncovered and b_tth.size:
                _log(window, f"背景扣除提示：空扫图只覆盖 "
                             f"{b_tth[0]:.3f}–{b_tth[-1]:.3f}°，"
                             f"该区间以外的数据未扣背景")
    out, base = process.apply_chain(tth, intensity, params,
                                    blank_curve=blank_curve)
    return tth, out, base

def _compare_shown_curves(window: QMainWindow, dock) -> list:
    """对比面板实际画上去的曲线：[(tth, shown, display, i, ref), ...]。

    shown = 按该面板自己的"对比归一化"设置处理后的显示数据（归一化
    是显示层，原始结果原样保留在 compare_data）；i = 在 compare_files
    里的序号（决定颜色/图例顺序）。画图（_redraw_compare）与自动
    纵轴（_apply_auto_ylim）共用这一份数据——两边口径一致，置灰框
    显示的区间才跟图对得上。热图联动隐藏的样品（dock.compare_hidden
    里的显示名）不参与：画图、图例、自动纵轴同时少掉这条曲线。

    ref = **纵轴范围该看的那份**（2026-09-26 晚加，用户："对比面板的
    纵轴自己在变"）：同一条曲线，但**不跑背景/平滑**——调背景时框就
    不动、只有曲线在框里往下走（与 1D 面板同一招）。两件事例外，必须
    留在 ref 里：**归一化**（它本来就改强度比例，不留就框不住画出来
    的曲线）与**裁剪区间**（剪掉巨峰就该让纵轴放开，那是裁剪的本意）。
    产物条目本身就是处理好的、没有"未处理"版本 → 它的 ref = shown。

    归一化三模式：
      global 全图最强峰：所有曲线除以全部曲线里最高的峰
      file   指定数据：所有曲线除以"归一化目标"那个文件的最强峰
      off    不归一化（默认：原样画原始强度）
    **"各自最强峰"（每条除以自己的峰）已删**（用户 2026-09-26 定的
    规矩："不要按照各自的最高峰归一化，所有的图"）：它把每条曲线都缩到
    同一高度，样品之间的强弱差就没法看了。认不出的模式（旧快照里的
    布尔 True/False 等）一律当 off——不做归一化是安全的那一侧。
    """
    mode = _panel_param(window, dock, "对比归一化", "off")
    if mode not in ("global", "file"):
        mode = "off"
    target_path = _panel_param(window, dock, "归一化目标", "") \
        if mode == "file" else ""
    # 先把原始数据全收起来：global/file 的除数要等所有曲线到齐才算。
    # 热图联动隐藏的样品（dock.compare_hidden）直接跳过：不算除数、
    # 不进图例、不占颜色槽——其他曲线的颜色序号不变（颜色跟着文件
    # 走的承诺在隐藏/恢复来回切时也不破）
    hidden = set(getattr(dock, "compare_hidden", None) or ())
    cuts = (_proc_params(window, dock, None) or {}).get("cut_ranges") or []
    raw_curves = []
    for i, src in enumerate(dock.compare_files):
        display = src.display
        if display in hidden:
            continue
        if display not in dock.compare_data:
            continue   # 这条还没算成（本函数只在全部到齐后调用）
        tth, raw = dock.compare_data[display]
        raw = np.asarray(raw, dtype=float)
        # 背景扣除在归一化**之前**：先扣掉不含结构信息的加性背景，
        # 再谈"相对强度"才有意义（归一化会把这个尺度信息抹掉）。
        # 处理产物条目（"· 处理后"）本身已经是处理完的：再来一遍就是二次处理
        ref = raw          # 纵轴范围的参考：只挖裁剪区间、不跑背景/平滑
        if src.kind == gui_sources.RAW:
            if cuts:
                ref = np.asarray(process.cut_ranges(tth, ref, cuts),
                                 dtype=float)
            _, raw, _ = _proc_curve(window, dock, src.path, tth, raw)
        raw_curves.append((tth, raw, display, i, src.path, ref))
    divisor = 1.0
    if mode == "global":
        divisor = max((float(np.nanmax(c[1])) if c[1].size else 0.0
                       for c in raw_curves), default=0.0)
    elif mode == "file":
        # 按路径找目标文件的最强峰（快照记字符串路径）；找不到
        # （快照过期防御）= 除数保持 1.0 = 不归一化
        for _tth, shown_src, _display, _i, path, _ref in raw_curves:
            if str(path) == str(target_path):
                divisor = float(np.nanmax(shown_src)) \
                    if shown_src.size else 0.0
                break
    if divisor <= 0:
        divisor = 1.0
    curves = []
    for tth, shown_src, display, i, _path, ref in raw_curves:
        # global/file 共享同一个除数（不是各除各的）；off 原样。
        # ref 用**同一个除数**（否则框的刻度和画出来的曲线对不上）
        if mode != "off":
            curves.append((tth, shown_src / divisor, display, i,
                           ref / divisor))
        else:
            curves.append((tth, shown_src, display, i, ref))
    return curves

def _heat_shown(matrix, mode):
    """热图实际画上去的强度矩阵（归一化是显示层，原始结果原样保留）。

    mode 两选（与对比归一化同款语义，热图没有"指定文件"）：
      global 全图最强峰：全体除以最强样品的最强峰
      off    不归一化（默认：原样画原始强度）
    "每行各自最强峰"已删（同对比面板：用户 2026-09-26 的规矩——按各自
    最高峰归一化会把样品之间的强弱差抹平，而那正是一张图要看的东西）。
    认不出的模式一律当 off。
    画图（_draw_heatmap）与自动强度范围（_apply_auto_heatlim）共用
    这一份口径，置灰框显示的区间才跟图对得上。
    """
    shown = np.asarray(matrix, dtype=float)
    if mode == "global":
        peak = float(np.nanmax(shown)) if np.isfinite(shown).any() else 0.0
        if peak > 0:
            shown = shown / peak
    return shown

