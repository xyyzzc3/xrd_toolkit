"""曲线/配方层：面板曲线来源、配方读写、锚点叠加、按快照重画处理链。

2026-10-21 从 plot_views.py 原样搬出（只改结构，不动逻辑）。
plot_views 再导出，测试与探针照旧经 gui_plot_views 取用。
"""
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.panel_state import (
    DATA_PARAM_DEFAULTS, _display_snapshot, _drop_pending_display, _log,
    _note_params_consumed, _proc_settings, _refresh_pending_labels,
    _stash_pending_display)
from xrd_toolkit.gui.plot.drawing import (
    _bg_path_of, _draw_1d, _draw_waterfall, _update_smooth_points)
from xrd_toolkit.gui.tasks import user_error_text
from xrd_toolkit.services import process, stage_cache


def _panel_1d_key(panel, path):
    """面板里那条曲线是按**哪把 1D 键**算的（读它自己的快照）。

    快照里记着算这张图时用的几何条目 / 点数 / 2θ 范围（`_data_snapshot`
    的口径），拿它们重算一把键即可——与"取数"用的是同一套算法，不靠肉眼
    比浮点。面板还没算过 / 快照缺项 / 文件读不了 → None（一律当"对不上"，
    安全的一侧）。
    """
    snap = getattr(panel, "params_snapshot", None) or {}
    lo, hi = snap.get("2θ 下限 (°)"), snap.get("2θ 上限 (°)")
    npt = snap.get("输出点数")
    if lo is None or hi is None or npt is None:
        return None
    try:
        return stage_cache.key_of_1d(str(path),
                                     config=str(snap.get("config") or ""),
                                     npt=int(npt), tth_min=float(lo),
                                     tth_max=float(hi))
    except (TypeError, ValueError, OSError):
        return None

def _curve_source(window, path, kw: dict):
    """取某个文件**按当前设置算好的**那条 1D 曲线：面板里那条优先，其次 1D 产物。

    返回 (tth, intensity, why)：取到 → why=None；取不到 → why 是一句人话
    （批量处理据此记账，不静默）。批量扣背景要拿原始曲线去重取锚点强度。

    **为什么"面板里那条"要同一把键才算数**（2026-09-28 用户报"处理阶段
    2θ 范围无效"查出的真 bug）：旧版只要那个文件的面板开着就直接用它，
    **完全无视 kw**——面板是按 2θ 1–8° 算的、用户改成 2–7° 再 [批量处理]，
    产物会按 2–7° 的键写盘、里面装的却是 1–8° 的数据：键与数据对不上，
    而且以后按 2–7° 查还会"命中"它（文件栏的行尾是从曲线自己读的，于是
    界面上露馅成"明明选了 2–7° 却列着 1–8°"）。
    """
    if getattr(window, "current_mask", None) is not None:
        # 遮罩生效时，stage_cache 里的 1D/产物都没有 mask 标记，可能是
        # 遮罩前算的，不能拿来凑数。只认这一代 mask 算出的曲线：
        #   1) 批量处理临时留的遮罩曲线（_proc_ensure_curves 写入）；
        #   2) 当前 1D 面板，且它是这一代 mask 算出来的。
        saved = (getattr(window, "_masked_curves", {}) or {}).get(str(path))
        if saved is not None:
            return saved[0], saved[1], None
        panel = window.plot_docks.get("1D|" + str(path))
        epoch = int(getattr(window, "mask_epoch", 0))
        if (panel is not None
                and getattr(panel, "last_tth", None) is not None
                and getattr(panel, "mask_epoch", -1) == epoch
                and getattr(panel, "mask_on", False)):
            return panel.last_tth, panel.last_intensity, None
        return None, None, ("已应用 Mask：这条曲线还没按当前 mask 重算"
                            "（先点 [1D] 出图）")
    try:
        want = stage_cache.key_of_1d(str(path),
                                     config=str(kw.get("config") or ""),
                                     npt=int(kw.get("npt")),
                                     tth_min=kw.get("tth_min"),
                                     tth_max=kw.get("tth_max"))
    except (TypeError, ValueError, OSError):
        want = None
    panel = window.plot_docks.get("1D|" + str(path))
    if panel is not None and getattr(panel, "last_tth", None) is not None:
        if want is not None and _panel_1d_key(panel, path) == want:
            return panel.last_tth, panel.last_intensity, None
    got = stage_cache.load_1d(path, **kw)
    if got is not None:
        return got[0], got[1], None
    if panel is not None and getattr(panel, "last_tth", None) is not None:
        # 面板里有曲线、但不是当前设置这一份：把"面板那份是什么范围"说清楚，
        # 用户才知道该按哪个范围重算（最常见的就是 2θ 改过还没重算）
        snap = getattr(panel, "params_snapshot", None) or {}
        lo, hi = snap.get("2θ 下限 (°)"), snap.get("2θ 上限 (°)")
        cur = f"（现在填的是 2θ {kw['tth_min']:g}–{kw['tth_max']:g}°）" \
            if kw.get("tth_min") is not None and kw.get("tth_max") is not None \
            else ""
        if lo is not None and hi is not None:
            return None, None, (f"面板里那条是按 2θ {float(lo):g}–{float(hi):g}° "
                                f"算的，按当前设置还没算过{cur}")
        return None, None, "面板里那条是别的设置算的，按当前设置还没算过"
    return None, None, "还没有 1D 曲线（先点 [1D] 出图）"

def _consume_data_params(window: QMainWindow) -> None:
    """坞顶那组数据参数（2θ 上下限 / 输出点数）刚被**用掉**。

    调用点 = 真正拿这把范围去取数/积分的地方：派积分任务（_spawn）、
    [采用这份结果]、[批量处理]（后两个即使命中缓存没真积分，也算"按这把
    范围取过数"）。**对比 / 热图不在其列**（2026-10-03）：它们按每个文件
    自己的范围取数（_file_data_params），坞顶那行在那里不起作用。

    为什么要标：用户填 2θ=2.5 出一张新图 / 打一批产物，那 2.5 是**指令**、
    已经被用掉了——不标的话，切回上一张图会凭空冒出一个"未应用"
    （见 panel_state._capture_pending 与 _note_params_consumed）。
    """
    _note_params_consumed(window, "数据")

def _file_data_params(path) -> tuple:
    """这个文件取数用的 (2θ 下限, 上限, 点数)：**它自己上次用过的**（甲）。

    从没算过 → 出厂默认（panel_state.DATA_PARAM_DEFAULTS）。对比 / 热图
    取数走它（见 _curve_for）：这两张图的数据参数 2026-10-03 起不再从
    坞顶那行拿——用户："对对比图改 2θ 会影响所有参与对比的图，这不合适"。
    """
    mem = stage_cache.last_range_for(path)
    lo, hi = mem.get("tth_min"), mem.get("tth_max")
    return (float(lo) if lo is not None else DATA_PARAM_DEFAULTS["2θ 下限 (°)"],
            float(hi) if hi is not None else DATA_PARAM_DEFAULTS["2θ 上限 (°)"],
            int(mem.get("npt") or DATA_PARAM_DEFAULTS["输出点数"]))

def _curve_for(window, path):
    """对比 / 热图取曲线：**产物优先**（扣背景产物 → 1D 产物），否则 None。

    返回 (tth, intensity, 来源说明)。产物齐了就不用再积分——跨会话秒开
    （用户 2026-09-24 第 5 条：对比直接用上一步扣完背景的产物）。
    来源说明进日志：用哪一份**看得见**，不是悄悄发生的。

    数据参数（2θ / 点数）用**这个文件自己上次用过的**（`_file_data_params`，
    甲）：对比是"看已经算好的东西"，不该为看一眼把整批按坞顶那行重算
    （2026-10-03 用户定；坞顶那行在对比页也藏起来了）。
    """
    if getattr(window, "current_mask", None) is not None:
        # 遮罩生效：落盘的 1D/处理产物可能没遮过，不能当"已算好"
        return None
    lo, hi, npt = _file_data_params(path)
    kw = dict(config=window.config_name, npt=npt, tth_min=lo, tth_max=hi)
    dock = window.plot_docks.get(window.focus_panel)
    if dock is not None:
        # 设置模板 = 当前编辑对象那份（背景三项 + 平滑 + 裁剪），锚点按**该
        # 文件自己的**取（_proc_settings 内部按 path 查 window.bg_anchors）
        settings = _proc_settings(window, dock, path)
        if settings["mode"] != "off" or process.chain_parts(settings):
            got = stage_cache.load_proc(path, **kw, settings=settings)
            if got is not None:
                return got[0], got[1], "处理产物"
    got = stage_cache.load_1d(path, **kw)
    if got is not None:
        return got[0], got[1], "1D 产物"
    return None

def _ready_curve(window, path, display):
    """这个文件**现在就能给对比 / 热图用**的那条曲线（"已经算好"的唯一判据）。

    用户 2026-10-16："原始数据不允许直接进行对比，必须先去 1d 积分，日志
    里记得写"——对比 / 热图从此**只画已经算好的**，现场积分那条路退休。
    "已经算好"，两把来源按可靠度排：

      1. 处理产物 / 1D 产物（`_curve_for`，按文件自己上次用过的参数键）
         ——落盘的、跨会话都在的那份；
      2. 本会话开着的 1D 面板上那条曲线（老热图 cache_of 的口径）——算完
         就在这一趟里，哪怕产物还没落盘（写盘是尽力而为）。

    栅栏（_plot_compare / _plot_heatmap 的拒绝判定）与取数用**同一把尺**：
    能取到就放行、取不到就拦，免得"拦了画得出的 / 放了画不出的"。
    顺序是选定的（2026-10-16）：**落盘那份优先**——两把来源不一致只会在
    "写盘静默失败"这类异常态出现（见于 plot_views._spawn 的 worker），
    那时宁可给旧的那份、也不让只看一眼就换数据（老热图 cache_of 是
    面板优先，这里统一到产物优先，与对比的老口径 2026-09-24 同源）。
    返回 (tth, intensity, 来源说明)，来源说明进日志；取不到 → None。
    """
    mask_on = getattr(window, "current_mask", None) is not None
    if mask_on:
        # 落盘产物可能没遮过：遮罩生效时只认这一代 mask 的曲线
        saved = (getattr(window, "_masked_curves", {}) or {}).get(str(path))
        if saved is not None:
            return saved[0], saved[1], "遮罩后的 1D"
        epoch = int(getattr(window, "mask_epoch", 0))
        for k in (f"1D|{path}", f"1D|{path}|{display}"):
            d = window.plot_docks.get(k)
            if (d is not None
                    and getattr(d, "last_tth", None) is not None
                    and getattr(d, "mask_epoch", -1) == epoch
                    and getattr(d, "mask_on", False)):
                return d.last_tth, d.last_intensity, "1D 面板（mask）"
        return None
    got = _curve_for(window, path)
    if got is not None:
        return got
    for k in (f"1D|{path}", f"1D|{path}|{display}"):
        d = window.plot_docks.get(k)
        if (d is not None and getattr(d, "last_tth", None) is not None
                and not getattr(d, "mask_on", False)):
            return d.last_tth, d.last_intensity, "1D 面板"
    return None

def _proc_recipe(settings: dict, source: str = None) -> dict:
    """一条**文件级**处理配方：设置那套（模式/窗口/锚点/平滑/裁剪）+ 来处 + 摘要。

    用户 2026-09-27："给 a 扣完，然后应用到其余批量选图上，在双击查看这些
    选图时，在扣背景的参数栏可以显示这个图是以 a 的锚点为基准进行扣除的。
    **按文件**。"——锚点本来就按文件存（window.bg_anchors），模式/窗口原先
    却只写进"当时开着"的面板快照：批量扣完再点开别的文件，它显示"不扣"。
    现在一起进 window.proc_recipes[path]。

    **形状就是 `_proc_settings` 那一套**（不是显示控件的键名）：这样
    "本图配方"与"存下来的命名配方"（services/recipes）是同一个东西，
    保存与套用都只需一份转换——`chain` 是给人看的那行摘要，`anchor_source`
    是锚点来处（手点的记 None，界面上说"本图手点"）。

    **只作记录与展示，不回填面板**（用户 2026-09-27 晚："原始数据现在会被
    改变…按理来说原始数据应该打不开的，因为是原始 tif"）：打开原始条目就
    该看到原始曲线，想套配方要显式按 [套用]（见 apply_recipe）。
    """
    return {**settings,
            "chain": process.chain_label(settings),
            "anchor_source": source}

def recipe_text(recipe: dict) -> str:
    """配方给人看的一行字（处理页那行提示用）。"""
    if not recipe:
        return "本图还没处理过（本页参数只作用于当前这张图）"
    head = f"本图配方：{recipe.get('chain') or '未处理'}"
    if not recipe.get("anchors"):
        # 零锚点就没有"锚点来处"这回事（自动基线 / 空扫都不靠锚点）——
        # 别挂着"（锚点本图手点）"让人以为动过点（2026-10-09 重拍背景图
        # 时看到：自动基线、零锚点，尾巴还在）
        return head
    from_src = recipe.get("anchor_source")
    tail = f"（锚点来处：{from_src}）" if from_src else "（锚点本图手点）"
    return f"{head}{tail}"

def note_proc_recipe(window: QMainWindow, path, recipe: dict) -> None:
    """把配方记到该文件名下（批量处理与手调都走这里）。"""
    if getattr(window, "proc_recipes", None) is None:
        window.proc_recipes = {}
    window.proc_recipes[str(path)] = dict(recipe)

def _anchors_on_curve(xs, tth, intensity):
    """把锚点**位置**落到某条曲线上：返回 [(x, y), ...] 与被丢掉的 x 列表。

    位置在数据范围之外就**丢掉**（返回在第二项里，调用方记一行日志）——
    `np.interp` 会静默钳到端点值，等于拿边缘那一点的强度当锚点，图上完全
    看不出来（用户 2026-09-27 讨论跨批次复用配方时翻出来的隐患）。
    """
    tth = np.asarray(tth, dtype=float)
    keep, dropped = [], []
    for x in xs:
        x = float(x)
        if tth.size and tth[0] <= x <= tth[-1]:
            keep.append((x, float(np.interp(x, tth, intensity))))
        else:
            dropped.append(x)
    return keep, dropped

def apply_recipe(window: QMainWindow, dock, path, recipe: dict) -> None:
    """把一份配方**显式**套到当前面板这张图上（[套用] 按钮 / 配方下拉）。

    与"打开就自动按配方回填"是两回事（那个已经删掉，见 _proc_recipe 的
    说明）：这里是用户按下去的动作，按了才动。
      - 锚点：只用配方里的 **2θ 位置**，强度在本图自己的曲线上重新取
        （跨批次复用的核心，见 services/recipes 的模块说明）；
      - 其余（模式 / 窗口 / 拟合 / 平滑 / 裁剪）填进参数坞控件；
      - 填完调 _refresh_proc 立刻按新设置重画（与手改控件的路径完全同一条）。
    """
    if dock is None or not recipe:
        return
    tth = getattr(dock, "last_tth", None)
    intensity = getattr(dock, "last_intensity", None)
    if tth is None or intensity is None:
        _log(window, "这张图还没算出来，先出图再套配方")
        return
    xs = [float(x) for x, _y in (recipe.get("anchors") or [])]
    dropped = []
    if xs:
        keep, dropped = _anchors_on_curve(xs, tth, intensity)
        window.bg_anchors = getattr(window, "bg_anchors", None) or {}
        window.bg_anchors[str(path)] = keep
    _set_recipe_controls(window, recipe)
    _refresh_proc(window)
    if dropped:
        _log(window, f"配方里有 {len(dropped)} 个锚点落在本图 2θ 范围外，"
                     f"已跳过（{min(dropped):.2f}–{max(dropped):.2f}°）")
    _log(window, f"已套用配方：{chain_label_of(recipe)}"
                 f"（锚点 {len(xs) - len(dropped)} 个）")

def chain_label_of(recipe: dict) -> str:
    """配方 → 一行摘要（归档/日志用）。"""
    return process.chain_label({
        "mode": recipe.get("mode"), "window_deg": recipe.get("window_deg"),
        "anchors": recipe.get("anchors") or [],
        "clip": recipe.get("clip"), "smooth_deg": recipe.get("smooth_deg"),
        "smooth_method": recipe.get("smooth_method"),
        "smooth_order": recipe.get("smooth_order"),
        "cut_ranges": recipe.get("cut_ranges") or []})

def _set_recipe_controls(window: QMainWindow, recipe: dict) -> None:
    """把配方填进「处理」页的控件（**不**触发重画，调用方统一收尾）。

    在参数坞里按控件类型逐个落值：下拉用 findData、复选框 setChecked、
    转盘 setValue。控件不在场（还没有参数坞）就静默跳过——与
    panel_state._param_box_set 一个态度。
    """
    def combo(name, data):
        box = window.params.get(name)
        if box is not None and data is not None:
            box.setCurrentIndex(max(0, box.findData(data)))

    def check(name, on):
        box = window.params.get(name)
        if box is not None:
            box.setChecked(bool(on))

    def spin(name, value):
        box = window.params.get(name)
        if box is not None and value is not None:
            box.setValue(float(value))

    combo("背景扣除模式", recipe.get("mode"))
    spin("背景窗口 (°)", recipe.get("window_deg"))
    combo("锚点拟合方式", recipe.get("anchor_method"))
    check("负值截断为 0", recipe.get("clip"))
    deg = float(recipe.get("smooth_deg") or 0.0)
    check("平滑曲线", deg > 0)
    if deg > 0:
        spin("平滑窗口 (°)", deg)
        combo("平滑方法", recipe.get("smooth_method"))
        spin("平滑阶数", recipe.get("smooth_order"))
    cuts = [tuple(c) for c in (recipe.get("cut_ranges") or [])]
    window.cut_list = list(cuts)
    check("裁剪区间", bool(cuts))
    from xrd_toolkit.gui.app import _sync_cut_label        # 破循环：只取这一个
    _sync_cut_label(window)                                # 清单标签跟着换

def _retarget_product_panel_to_source(window, dock) -> bool:
    """把"处理产物"面板就地换成**它底下那条原始 1D 曲线**（换配方重做的入口）。

    用户 2026-09-28 第 3 条："套用别的配方的处理图不能二次处理了，修正"。
    改之前产物面板是一堵墙：[套用] 直接拒绝、[采用这份结果] 也拒绝（都怕
    二次相减），用户想"换套参数重做"在界面上无路可走——只有一句"打开它
    对应的原始条目"，而那样会另开一张面板、当前这张的视图状态就丢了。

    这里就地换：面板此后就是"这个文件的一条 1D 曲线"（`panel_source` 清掉），
    配方照常往上套、结果照常 [采用这份结果]。**二次相减的隐患由"换输入"消除**
    ——重做吃的正是没扣过背景的那条曲线（见 stage_cache.load_proc_source），
    而不是在扣完的曲线上再扣一遍。

    两个调用方：面板上按 [套用]（换配方重做，用户 2026-09-28 第 3 条）与
    [重算这张图]（`_apply_params`，用户 2026-09-30：产物面板按新范围重算前
    必须先摘掉产物身份，否则原始文件会被重积分画进这张产物面板）。

    返回 True = 可以用（本来就不是产物面板，或换成功）；False = 换不了（已记日志）。
    """
    src = getattr(dock, "panel_source", None)
    if src is None or src.kind != gui_sources.BG:
        return True
    got = stage_cache.load_proc_source(src.key)
    if "why" in got:
        _log(window, f"换配方重做不了：{got['why']}")
        return False
    dock.last_tth, dock.last_intensity = got["tth"], got["intensity"]
    dock.panel_source = None            # 变回"这个文件的一条曲线"
    dock.panel_display = f"1D_{Path(src.path).name}"
    dock.setWindowTitle(dock.panel_display)
    _log(window, f"这一条是处理产物：已换成它底下的原始 1D 曲线"
                 f"（来源：{got['from']}）——配方按它重做，不是二次相减")
    return True

def _refresh_proc(window: QMainWindow) -> None:
    """「处理」页任一参数一变就立刻重画（不重新积分）。

    锚点点选是点击驱动的，每点一次都要按一下 [应用] 不可接受，所以处理
    这三项（背景扣除 / 平滑 / 裁剪）一直走实时。三项都是纯函数、
    毫秒级（基线估计实测 3000 点 1.4 ms；滑动平均是卷积，更快），直接拿
    缓存里的曲线重画一遍就够。（显示参数 2026-10-08 起也全部即改即画，
    这条路不再是唯一。）

    三步：① 把参数坞里处理控件的当前值推进**编辑对象**面板的快照
    （显示参数按面板各记各的，不推进去的话画图读到的还是旧快照）；② 顺手把"平滑
    窗口折成几个点"的灰度提示填上（教学用：窗口宽度是度、曲线是点）；
    ③ 按各面板自己的快照重画全部曲线面板，编辑对象跟着控件实时走，其余
    面板维持各自已设的显示参数。
    """
    # 对比/热图的重画入口在 plot_compare（模块级导入成环），只取本函数
    # 用得到的三个
    from xrd_toolkit.gui.plot_compare import (
        _draw_heatmap, _heat_data, _redraw_compare)
    # 「待应用」账本（2026-10-13，同各家显示参数 handler）：没有编辑对象
    # 时处理参数变了原本谁都没接（配方要按文件记、写不进去；下一张图的
    # 快照也不会带上它）——记进账本，出图时当新面板的起点；有编辑对象
    # 就照旧推进那张图的快照（同时把账本里这几个键清掉）
    keys = ("背景扣除模式", "背景窗口 (°)", "空扫归一化", "锚点拟合方式",
            "背景显示原始", "负值截断为 0", "平滑曲线", "平滑窗口 (°)",
            "平滑方法", "平滑阶数", "裁剪区间", "裁剪起点 (°)",
            "裁剪终点 (°)")
    dock = window.plot_docks.get(window.focus_panel)
    if not getattr(window, "_param_replaying", False):
        if dock is None:
            _stash_pending_display(window, keys)
        else:
            _drop_pending_display(window, keys)
    # 回放面板快照期间不许回写快照：那一刻控件值正被程序逐个改写（切焦点
    # 时 _load_params_snapshot 在跑），把"回放了一半"的控件状态当成用户的
    # 设置写进当前面板，就会把上一个面板的显示参数串过来（实测串的是
    # 热图色图等注册在背景组之后的几项），且不可逆。重画本身照做
    if dock is not None and not getattr(window, "_param_replaying", False):
        snap = _display_snapshot(window, dock.params_snapshot)
        # 裁剪清单是窗口级的（不是控件值），这里显式拷进快照——快照才是
        # "这张图用什么画的"的权威来源（批量处理时也要逐面板写一遍）
        if snap.get("裁剪区间"):
            snap["裁剪区间"] = list(getattr(window, "cut_list", []) or [])
        dock.params_snapshot = snap
        # 这条通路也把显示参数控件值写进了快照（_display_snapshot 取控件值）
        # → 图像组的值算是被用掉了（灰字随之灭）
        _note_params_consumed(window, "图像")
        _refresh_pending_labels(window)
    tick_path = _bg_path_of(dock) if dock is not None else None
    if tick_path is not None and not getattr(window, "_param_replaying", False):
        # 手调也算这个文件的配方（来处 = None = 本图手点）：处理页那行
        # "本图配方"跟着显示，[套用] 时也用它。
        #
        # **回放快照期间不许写**（与上面那条同一个旗标）：切焦点时
        # _load_params_snapshot 会把控件逐个改成新面板的值，那些信号同样
        # 走到这里——不挡的话，一打开某文件就把它**批量处理时记下的配方**
        # 覆盖成"本图手点 + 当前默认值"，来处与参数一起丢（2026-09-27 晚
        # 被测试逮住：B 的配方被打开它的动作抹成了 mode=off）。
        note_proc_recipe(window, tick_path,
                         _proc_recipe(
                             _proc_settings(window, dock, tick_path),
                             source=None))
    # 「配方」小节的显示（来处那行 + 下拉）跟着刷一遍：配方是在上面那几行
    # 才写进 window.proc_recipes 的，而填下拉/写来处原先发生在 _sync_bg_rows
    # 里、跑在这之前——刚调完参数时下拉里还只有占位项，用户按 [套用] 只得到
    # "先在下拉里选一份配方"（2026-09-28 探针复现的用户报"套用无效"）
    recipe_hook = getattr(window, "_recipe_rows_sync", None)
    if recipe_hook is not None and tick_path is not None:
        recipe_hook()
    _update_smooth_points(window, dock)
    for key, dock in list(window.plot_docks.items()):
        view = key.split("|", 1)[0]
        try:
            if view == "1D" and getattr(dock, "last_tth", None) is not None:
                _draw_1d(window, dock, dock.last_tth, dock.last_intensity)
            elif view == "对比":
                _redraw_compare(window, key)
            elif view == "瀑布" and getattr(dock, "last_waterfall", None):
                tth, i2d, chi = dock.last_waterfall
                _draw_waterfall(window, dock, tth, i2d, chi)
            elif view == "热图" and getattr(dock, "heat_results", None):
                data = _heat_data(window, dock)
                if data is not None:
                    dock.heat_data = data   # 与画的同源（画热图也从这里取）
                    _draw_heatmap(window, dock, data[0], data[1], data[2])
        except Exception as err:                      # 重画失败不该拖垮整窗
            _log(window, f"背景扣除重画失败：{user_error_text(err)}")

