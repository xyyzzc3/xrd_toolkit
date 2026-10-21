"""多文件视图：对比面板（多条 1D 叠图）+ 热图 + 锚点拾取。

从 plot_views.py 拆出来（纯搬迁）。共同点：**要选多个文件才成立**（用户
定的归类规则），将来整体归「对比」页。
依赖方向：本模块 → plot_views（背景叠加/重画）与 plot_panels（面板壳），
反向调用一律函数内延迟导入。
"""
import os
import re
from pathlib import Path

import numpy as np
from matplotlib.colors import LogNorm, Normalize
from matplotlib.patches import Rectangle
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.services.stacking import row_step

from xrd_toolkit.gui.panel_state import (
    DATA_PARAM_DEFAULTS,
    _apply_auto_heatlim, _auto_y_range, _AUX_GID_PREFIX, _proc_curve,
    _compare_shown_curves, _content, _data_snapshot, _DISPLAY_DEFAULTS,
    _drop_pending_display,
    _gradient_color, _display_snapshot, _heat_shown, _log, _panel_param,
    _stash_pending_display,
    _param_box_set, _proc_params, _refresh_pending_labels,
    _set_focus, _widget_value)
from xrd_toolkit.gui.plot_panels import (
    _apply_text_guards, _connect_axis_sync, _data_lines, _open_plot_panel,
    _refresh_home, _restore_line_styles, _settle_scale, _snapshot_canvas)
from xrd_toolkit.gui.plot_views import (
    _apply_plain_view, _bg_path_of, _ready_curve, _refresh_proc)
# 锚点拾取（2026-10-21 从本文件原样搬出；再导出，tests/scripts 照旧经
# gui_plot_compare 取用）
from xrd_toolkit.gui.plot.anchors import (
    _anchor_changed, _anchor_enabled, _anchor_press, _anchor_raw_y,
    _anchor_release, _anchor_right_click, _anchors_visible)
# 对比块（2026-10-21 从本文件原样搬出；再导出，tests/scripts 照旧经
# gui_plot_compare 取用）
from xrd_toolkit.gui.plot.compare import (
    COLOR_SCALE_TAG, LEGEND_MAX_CURVES, MIN_SUFFIX_CHARS, _COLOR_SCALE_STEPS,
    _aggregate_snapshot, _color_rank_by_number, _compare_title,
    _draw_compare_color_scale, _draw_compare_legend, _finish_compare,
    _plot_compare, _preview_names, _raw_not_ready, _redraw_compare,
    _refresh_compare, _refresh_compare_family, _run_compare, _short_labels,
    _sources_mix, _strip_common, _trailing_key)

def _assemble_heatmap(results):
    """把 [(样品名, tth, intensity), ...] 对齐成强度矩阵（纯函数）。

    横轴 = 2θ、纵轴 = 样品（文件列表顺序）、颜色 = 强度。2θ 网格
    以第一个文件的网格为准；别的文件网格不一致（点数/区间不同）
    就 np.interp 重插值到第一网格（视图只求对齐，误差可忽略）——
    是否重插值由返回的 interp 标志报告，调用方记日志。结果空 / 网
    格空（0 点）返回 None。
    """
    if not results:
        return None
    x = np.asarray(results[0][1], dtype=float)
    if x.size < 2:
        return None   # 0/1 个点画不出 extent（imshow 至少要一段区间）
    rows = []
    interp = False
    for name, t, intensity in results:
        ti = np.asarray(t, dtype=float)
        vi = np.asarray(intensity, dtype=float)
        if ti.shape != x.shape or not np.allclose(ti, x, atol=1e-12):
            interp = True
            vi = np.interp(x, ti, vi)   # t 由引擎保证升序
        rows.append(vi)
    return x, np.vstack(rows), [r[0] for r in results], interp

def _heat_row_press(window: QMainWindow, key: str, event) -> None:
    """热图按下：记候选行（没拖动才算"点击"，松手再决定）。

    左键拖 = 平移（通用手势），点按 = 行联动——按下先记账，松手
    时挪动超过阈值就算平移，不算点击。
    """
    if event.inaxes is None or event.button != 1:
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        return
    y = event.ydata
    n = len(getattr(dock, "heat_files", ()))
    row = int(round(y))
    if not (0 <= row < n) or abs(y - row) > 0.5:
        return   # 点在行缝/图外：不算
    dock._heat_press = (event.x, event.y, row)

def _heat_row_release(window: QMainWindow, key: str, event) -> None:
    """热图松手：按下点没怎么挪（阈值 5 px）→ 行联动——该行样品在
    所有含它的对比面板里隐藏/显示切换（再点恢复）；挪多了 = 平移，
    不动。

    隐藏集合挂在对比面板 dock.compare_hidden 上（显示名），画图/
    图例/自动纵轴同口径少掉这条曲线（见 _compare_shown_curves）；
    颜色序号不重排（颜色跟着文件走）。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return
    info = getattr(dock, "_heat_press", None)
    dock._heat_press = None
    if info is None:
        return
    x0, y0, row = info
    if (event.x - x0) ** 2 + (event.y - y0) ** 2 > 5 ** 2:
        return   # 拖过了 = 平移手势，不是点击
    display = dock.heat_files[row].display
    targets = []
    for ckey, cdock in window.plot_docks.items():
        if not ckey.startswith("对比"):
            continue
        if any(s.display == display for s in
               getattr(cdock, "compare_files", ())):
            targets.append((ckey, cdock))
    if not targets:
        _log(window, f"热图点击 {display}：没有含该文件的对比面板")
        return
    for ckey, cdock in targets:
        hidden = set(getattr(cdock, "compare_hidden", None) or ())
        if display in hidden:
            hidden.discard(display)
            _log(window, f"热图点击 {display}：对比面板重新显示该曲线")
        else:
            hidden.add(display)
            _log(window, f"热图点击 {display}：对比面板隐藏该曲线"
                         f"（再点该行恢复）")
        cdock.compare_hidden = hidden
        _redraw_compare(window, ckey)

def _draw_heatmap(window: QMainWindow, dock, tth, matrix, stems) -> None:
    """在热图面板画出强度热图（只允许主线程调用）。

    样式：imshow 行 = 样品（origin=lower，列表第一个文件在最下）、
    列 = 2θ，颜色 = 强度，右侧颜色条。显示参数读该面板自己的快照
    （_panel_param）：热图色图 / 热图归一化（_heat_shown 三模式，
    只动显示数据）/ 热图对数（LogNorm，弱峰抬起来）/ 热图自动范围
    （自动 = 显示矩阵 1%/99.9% 分位，置灰框只读展示，同 2D 对比度
    套路；手动 = 手填上下限）。颜色条同 2D：重画先拆旧的（ax.clear
    不清 colorbar）。
    """
    ax = _content(dock).axes_heat
    keep_title, keep_xlabel, keep_ylabel, keep_scale, old_lines = \
        _snapshot_canvas(ax)
    prev_limits = getattr(window, "_setting_limits", False)   # save/restore（同 _draw_1d）
    window._setting_limits = True
    try:
        ax.clear()
        cmap = _panel_param(window, dock, "热图色图", "magma")
        mode = _panel_param(window, dock, "热图归一化", "off")
        log_c = _panel_param(window, dock, "热图对数", False)
        shown = _heat_shown(matrix, mode)
        auto = _panel_param(window, dock, "热图自动范围", True)
        if auto:
            vmin, vmax = _auto_y_range(shown, log_c)
            if window.plot_docks.get(window.focus_panel) is dock:
                # 回填挂旗标（_refresh_heat 就接在这几个框上）：裸 setValue
                # 会触发即改即画钩子重入本函数（同 _draw_1d 的那条病）
                _param_box_set(window, "热图下限", vmin)
                _param_box_set(window, "热图上限", vmax)
        else:
            vmin = _panel_param(window, dock, "热图下限", 1.0)
            vmax = _panel_param(window, dock, "热图上限", 100000.0)
            if vmax <= vmin:
                vmax = vmin * 10.0   # 手填区间不合法时兜底
        if log_c:
            vmin = max(vmin, 1e-12)   # LogNorm 画不出 ≤0
            vmax = max(vmax, vmin * 10.0)
        norm = LogNorm(vmin=vmin, vmax=vmax) if log_c \
            else Normalize(vmin=vmin, vmax=vmax)
        n = matrix.shape[0]
        im = ax.imshow(shown, aspect="auto", origin="lower", cmap=cmap,
                       norm=norm, extent=[float(tth[0]), float(tth[-1]),
                                          -0.5, n - 0.5])
        if _panel_param(window, dock, "热图显示数据名", False):
            ax.set_yticks(range(n))
            ax.set_yticklabels(_short_labels(stems, ax), fontsize=7)
        else:
            # 行名默认关、由「热图显示」小节自己的 [显示数据名] 管
            # （2026-10-12 "甲，拆开"：原先是「对比显示」小节那把四处
            # 一起管，看热图参数够不着它）；行照样认得出——悬停读数读的
            # 是行名，与刻度无关
            ax.set_yticks([])
        # 视图窗口：与 1D/对比同一套口径（缩放/平移写回快照，重画按它摆回去）
        #
        # 用户 2026-09-28："热图和对比图的画框放大还是有bug"——热图这里原本
        # **从不设坐标范围**，只 imshow 一把；而 imshow 会自己 autoscale，
        # 于是任何一次重画（[应用]、改色图/归一化、重出图）都把缩放顶掉 ✗。
        # 热图 2026-10-08 晚起走**自己的一对键**（用户："对比与热图分开
        # 管理"）：热图的缩放/平移只写「热图视图 2θ」，不再顺带动对比
        xlo = _panel_param(window, dock, "热图视图 2θ 下限 (°)", None)
        xhi = _panel_param(window, dock, "热图视图 2θ 上限 (°)", None)
        zoomed = xlo is not None and xhi is not None and xlo < xhi
        if not zoomed:
            xlo, xhi = float(tth[0]), float(tth[-1])
        ax.set_xlim(xlo, xhi)
        if window.plot_docks.get(window.focus_panel) is dock:
            # 焦点面板 → 「热图显示」小节那对视图窗口框同步显示（同
            # _redraw_compare；程序写控件挂 _param_box_sync 旗标，
            # 不会被即改即画那条路当成用户输入）
            _param_box_set(window, "热图视图 2θ 下限 (°)", xlo)
            _param_box_set(window, "热图视图 2θ 上限 (°)", xhi)
        # y 是**样品行号**，没有强度语义 → 走通用键"视图 y 范围"
        # （见 plot_panels._INTENSITY_Y_VIEWS 的说明）；x 是 2θ，走语义键
        zoomed = _apply_plain_view(window, dock, ax) or zoomed
        # 颜色条同 2D：只建一次、之后 update_normal 复用（remove+
        # 重建会让坐标轴每次再让 20% 宽度，教训 13）
        cb = getattr(dock, "_heat_colorbar", None)
        if cb is None:
            dock._heat_colorbar = ax.figure.colorbar(im, ax=ax)
        else:
            cb.update_normal(im)
        dock._heat_colorbar.ax.tick_params(labelsize=7)
        _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel)
        _content(dock).draw()
    finally:
        window._setting_limits = prev_limits
    # ax.clear() 会把坐标轴的回调注册表整个清空（mpl 3.11 起）→ 画完必须
    # 重连"范围 → 快照"的写回，否则热图上的缩放/平移**永远写不进快照**，
    # 任何一次重画都按数据自动缩放把它顶掉（1D/对比都重连了，热图漏了）
    _connect_axis_sync(window, dock.panel_key, ax)
    if zoomed:
        dock._view_from_gesture = True   # 缩放的窗口不是"家"（同 _draw_1d）
    _refresh_home(dock, ax)
    dock.figure_saved = False

def _heat_data(window: QMainWindow, dock):
    """热图面板的 (tth, matrix, stems, interp)——逐行按各文件自己的参数
    扣背景后再对齐成矩阵。

    顺序对齐：dock.heat_results 与 dock.heat_files 同序（失败项是 None
    占位），所以用**原始下标**取路径，压缩掉 None 之后再取会错位。
    空扫/锚点都按文件走，所以同一张热图里每个样品用的是它自己的基线。
    """
    rows = []
    files = getattr(dock, "heat_files", ()) or ()
    for i, r in enumerate(getattr(dock, "heat_results", ()) or ()):
        if r is None:
            continue
        stem, tth, intensity = r
        src = files[i] if i < len(files) else None
        if src is not None and src.kind != gui_sources.RAW:
            rows.append((stem, tth, intensity))   # 产物本身就是扣完的
            continue
        _, sub, _ = _proc_curve(window, dock, src.path, tth, intensity)
        rows.append((stem, tth, sub))
    return _assemble_heatmap(rows)

def _finish_heatmap(window: QMainWindow, key: str) -> None:
    """热图全部数据到齐（含失败）：对齐成矩阵 → 整图画出 → 成为编辑
    对象 → 记日志。全部失败 = 面板留空。"""
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：静默丢弃
    data = _heat_data(window, dock)
    dock.heat_data = data
    if data is None:
        _log(window, "热图失败：没有一条曲线画得出来，面板留空")
        return
    tth, matrix, stems, interp = data
    n_blank = int((~np.isfinite(np.asarray(matrix, dtype=float))).sum())
    if n_blank:
        _log(window, f"热图提示：{n_blank} 个格子是空的（那几段被裁剪过）"
                     f"——显示为该行的一段空白带")
    if interp:
        _log(window, "热图提示：各文件 2θ 网格不一致，已统一到"
                     "第一个文件的 2θ 网格")
    _draw_heatmap(window, dock, tth, matrix, stems)
    _set_focus(window, key, dock.panel_display)
    _log(window, f"热图完成：{len(stems)} 个文件 × {len(tth)} 点")

def _run_heatmap(window: QMainWindow, key: str) -> None:
    """把面板绑定的每个文件的曲线**读出来**拼成强度矩阵（只读已经算好的）。

    用户 2026-10-16："原始数据不允许直接进行对比，必须先去 1d 积分，日志
    里记得写"——现场积分那条路退休（force 参数随退休，见 plot_views.
    _apply_params 的注释）：按钮那头对没算过的原始数据整批拦下
    （_plot_heatmap），这里对取不到的逐条跳过并写明（数据 [应用] 等直进
    路径的兜底）。全是读盘 / 读面板，完全同步：循环走完即收尾。结果按
    文件列表顺序收进 dock.heat_results（跳过的是 None 占位，见
    _heat_data）。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：迟到点击/重算不落地
    # 数据参数不进快照（同 _run_compare：每个文件按自己上次的范围取数）
    dock.params_snapshot = _aggregate_snapshot(window, dock.params_snapshot)
    # 显示参数刚被用掉，灰字立即刷新（重按 [热图] 时编辑对象没变、
    # _set_focus 直接 return，没人刷新就会留着陈旧的"未应用"）
    _refresh_pending_labels(window)
    results = [None] * len(dock.heat_files)   # 文件顺序占位
    # 总结行永远打：日志一眼看出这次热图拼了几个文件（栅栏后没有"新算"）
    _log(window, f"开始热图：{len(dock.heat_files)} 个文件"
                 "（只拼已经算好的曲线）")
    for i, source in enumerate(dock.heat_files):
        path, display = source.path, source.display
        if source.kind != gui_sources.RAW:
            # 产物条目：直接读盘（不重算、不再扣背景），读不到当失败
            got = gui_sources.load_product(source)
            if got is None:
                _log(window, f"热图：{display} 的产物不存在（可能已被删除）"
                             f"——跳过，请重新计算")
                continue
            results[i] = (Path(path).stem, got[0], got[1])
            continue
        got = _ready_curve(window, str(path), display)
        if got is None:
            _log(window, f"热图：{display} 还没有 1D 曲线（先点 [1D] 出图）"
                         f"——跳过")
            continue
        results[i] = (Path(path).stem, got[0], got[1])
    dock.heat_results = results
    _finish_heatmap(window, key)

def _refresh_heat(window: QMainWindow) -> None:
    """热图显示参数（色图/归一化/对数/范围）改了 → **所有**热图面板就地重画。

    用户 2026-09-27："热图一画出来就改不了颜色什么的，没法调整"——原先
    这几个控件谁都没接：改完只有再点一次 [出热图] 才可能生效，而那个按钮
    走的是"复用已有面板"的路，快照没刷新就还是旧颜色。
    做法与背景扣除的实时预览同源：先把控件里的**显示参数**写进各面板快照
    （_display_snapshot：数据参数沿用各自的旧值），再用已有数据重画。
    热图通常只有一张，直接作用于所有热图面板最符合"改的是一张图的颜色"
    这个直觉；数据不受影响（重画不重算）。
    2026-10-08：热图范围那两个手填框也接上这条路（原先它们在「未应用」
    名单里、应用键在「原图」页，改完够不着）；回放快照与程序回填
    （_apply_auto_heatlim / _param_box_set）都要挡在门外，否则会把
    别的面板的中间态写进所有热图快照（串台）。
    """
    if getattr(window, "_param_replaying", False) \
            or getattr(window, "_param_box_sync", False):
        return   # 回放 / 程序回填不是用户改动
    keys = ("热图色图", "热图归一化", "热图对数", "热图自动范围",
            "热图下限", "热图上限")
    touched = 0
    handled = 0
    for key, dock in list(window.plot_docks.items()):
        if not key.startswith("热图"):
            continue
        dock.params_snapshot = _display_snapshot(window, dock.params_snapshot)
        handled += 1     # 快照已收下改动（含还在算的：算完自己按新值画）
        data = _heat_data(window, dock)
        if data is None:
            continue
        dock.heat_data = data            # 与画的同源（画热图也从这里取）
        _draw_heatmap(window, dock, data[0], data[1], data[2])
        touched += 1
    if handled:
        _drop_pending_display(window, keys)   # 有面板接住：账本里这几个键作废
    else:
        # 一张热图面板都没有：记进"待应用"账本（用户 2026-10-13："在出图
        # 之前调整参数会无效"——此前这几项改了直接丢）
        _stash_pending_display(window, keys)
    if touched:
        _apply_auto_heatlim(window)      # 对数/自动范围改了 → 置灰框跟着更新

def _apply_view_boxes(window: QMainWindow, lo_name: str, hi_name: str,
                      views) -> None:
    """把一对「视图 2θ」框的值写进指定视图面板的快照并就地裁剪重画。

    用户 2026-10-05："热图添加回 2theta 改变视图的功能，就是重看，
    不是重算"——框与缩放/平移写回是同一对键（见
    plot_panels._on_xlim_changed），这里走反方向：换的只是 xlim，
    数据一份都不碰，谁也不用重新积分。超过某个文件自己算过的范围的
    那一段就是空白：画不出来也不重算，正是"重看"的含义。

    2026-10-08 晚拆键（用户："对比与热图分开管理"）：对比用老键
    （_refresh_heat_view）、热图用「热图视图 2θ」新键
    （_refresh_heatmap_view）——改一边不再动两边。

    只动这一对窗口键，**不整包套用别的显示参数**（_refresh_heat 那套
    的写法在这里是错的）：这些控件是所有面板共用的一份，控件值可能
    正显示着别的图（编辑对象）的设置，整包写过去会串台。
    """
    if getattr(window, "_param_replaying", False) \
            or getattr(window, "_param_box_sync", False) \
            or getattr(window, "_setting_limits", False):
        return   # 程序在写控件（回放/缩放同步/[恢复默认]）或正在画图
    lo_w = window.params.get(lo_name)
    hi_w = window.params.get(hi_name)
    if lo_w is None or hi_w is None:
        return   # 裸窗口（测试里没建这对框）：无框可读
    lo, hi = float(lo_w.value()), float(hi_w.value())
    if lo >= hi:
        return   # 还没填好（如先改了上限）：画个空窗口不如不动
    handled = 0
    for key, dock in list(window.plot_docks.items()):
        view = key.split("|", 1)[0]
        if view not in views:
            continue
        snap = getattr(dock, "params_snapshot", None)
        if not snap:
            continue
        # 先写快照再查数据（2026-10-13，同 _refresh_compare）：算图期间改
        # 的窗口算数，算完那一下自己按新窗口画
        snap[lo_name] = lo
        snap[hi_name] = hi
        handled += 1
        if view == "热图":
            data = _heat_data(window, dock)
            if data is None:
                continue         # 还没算完：等它自己画
            dock.heat_data = data   # 与画的同源（同 _refresh_heat）
            _draw_heatmap(window, dock, data[0], data[1], data[2])
        else:
            if not getattr(dock, "compare_data", None):
                continue
            _redraw_compare(window, key)
    if handled:
        _drop_pending_display(window, (lo_name, hi_name))
    else:
        # 一张对得上号的面板都没有：这本窗口记进账本（2026-10-13，
        # "出图前调参数无效"）
        _stash_pending_display(window, (lo_name, hi_name))

def _refresh_heat_view(window: QMainWindow) -> None:
    """「视图 2θ」框（**对比小节那对**，老键）改了 → 各对比面板裁剪重画。"""
    _apply_view_boxes(window, "视图 2θ 下限 (°)", "视图 2θ 上限 (°)",
                      ("对比",))

def _refresh_heatmap_view(window: QMainWindow) -> None:
    """「热图显示」小节那对框（新键）改了 → 各**热图**面板裁剪重画。

    2026-10-08 晚拆键（用户："对比与热图分开管理"）：热图专用键 =
    "热图视图 2θ 下限/上限 (°)"，改热图的窗口不再顺带改对比的。
    """
    _apply_view_boxes(window, "热图视图 2θ 下限 (°)", "热图视图 2θ 上限 (°)",
                      ("热图",))

def _plot_heatmap(window: QMainWindow) -> None:
    """[热图] 按钮：把勾选文件的 1D 曲线拼成一张 2θ×样品 强度热图。

    多文件 → 一张面板（同 [对比] 的流程形态）：横轴 2θ、纵轴样品
    （文件列表顺序，行标签 = 文件名）、颜色 = 强度——原位实验看
    峰位/强度/峰形随样品（时间/充电状态）的变化。勾选 ≥2 个文件；
    只拼已经算好的 1D 曲线（见 _run_heatmap）。

    **栅栏**（同 [对比]，用户 2026-10-16："原始数据不允许直接进行对比，
    必须先去 1d 积分，日志里记得写"）：勾选里有还没出过 1D 的原始数据
    → 整批不出图，日志点名 + 指路（拒绝在开/重绑面板之前）。

    **与 [对比] 一样是"单槽"**：永远只有一张，每次按 [热图] = 按**当前**
    勾选重建（用户 2026-09-28："无论什么时候，都是执行当前勾选，不存在
    以前勾选影响现在的情况。如果别的地方也有这个问题一并改掉"）。
    """
    checked = gui_sources.checked_sources(window)
    if len(checked) < 2:
        _log(window, "热图至少勾选两个文件（多条 1D 曲线拼成一张强度图）")
        return
    files = list(checked)   # 来源条目：原始文件 + 各组产物混着也行
    # 栅栏（见 docstring）：判据与取数同一把尺（_ready_curve），拒绝发生在
    # 开/重绑面板**之前**，旧图才原样留着
    missing = _raw_not_ready(window, files)
    if missing:
        _log(window, f"热图只拼已经算好的曲线：勾选里 {len(missing)} 个原始"
                     f"数据还没有 1D 曲线（{_preview_names(missing)}）"
                     "——先点 [1D] 出图，再来出热图")
        return
    key = "热图"            # 单槽（见 docstring）：不随勾选集合变
    dock = window.plot_docks.get(key)
    if dock is None:
        title = f"热图_{len(files)} 个文件"
        dock = _open_plot_panel(window, "热图", key, title)
        dock.panel_display = title   # 标题/日志/默认存盘名用
        dock.figure_saved = False
        dock.heat_files = files   # 面板绑定这组文件（重算用）
        dock.heat_results = []
        dock.heat_data = None
        # 新面板：显示参数从默认起步；数据参数不进快照（见 _aggregate_snapshot）
        dock.params_snapshot = _aggregate_snapshot(window, None)
        _log(window, f"打开热图面板：{len(files)} 个文件拼一张强度图"
                     f"（{_sources_mix(files)}）")
    else:
        # 复用面板：按当前勾选重绑（旧的 heat_data 由 _run_heatmap 清掉重画）
        dock.heat_files = files
        dock.heat_data = None
        _log(window, f"热图面板按当前勾选重建：{len(files)} 个文件"
                     f"（{_sources_mix(files)}）")
    dock.setVisible(True)
    _run_heatmap(window, key)
