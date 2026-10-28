"""多文件对比：图例、色标、叠图重画、对比流程。

2026-10-21 从 plot_compare.py 原样搬出（只改结构，不动逻辑）。
plot_compare 再导出，tests/scripts 照旧经 gui_plot_compare 取用。
"""
import os
import re
from pathlib import Path

import numpy as np
from matplotlib.patches import Rectangle
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.panel_state import (
    DATA_PARAM_DEFAULTS, _DISPLAY_DEFAULTS, _auto_y_range,
    _compare_shown_curves, _content, _data_snapshot, _display_snapshot,
    _drop_pending_display, _gradient_color, _log, _panel_param, _param_box_set,
    _proc_params, _refresh_pending_labels, _set_focus, _stash_pending_display,
    _widget_value)
from xrd_toolkit.gui.plot_panels import (
    _apply_text_guards, _connect_axis_sync, _data_lines, _open_plot_panel,
    _refresh_home, _restore_line_styles, _settle_scale, _snapshot_canvas)
from xrd_toolkit.gui.plot_views import _ready_curve
from xrd_toolkit.services.stacking import row_step
# 对比辅助（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.plot.compare_helpers import (
    COLOR_SCALE_TAG, LEGEND_MAX_CURVES, MIN_SUFFIX_CHARS, _COLOR_SCALE_STEPS,
    _color_rank_by_number, _compare_title, _draw_compare_color_scale,
    _draw_compare_legend, _preview_names, _raw_not_ready, _short_labels,
    _sources_mix, _strip_common, _trailing_key)

def _redraw_compare(window: QMainWindow, key: str) -> None:
    """用面板已有的曲线数据按该面板自己的 1D 显示参数重画整张对比图。

    与 _draw_1d 同套路：坐标范围随参数（2θ 上下限 / 对数纵轴 /
    纵轴范围），只是画多条曲线 + 图例。显示参数同样只读本面板快照
    （_panel_param），不读参数坞控件——各算完到齐收尾时焦点可能还
    在别的面板上，读控件会把别的图的设置画到这张图上。对比归一化
    只动显示数据（原始结果原样保留在 compare_data，见
    _compare_shown_curves 的四模式）。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：静默丢弃
    ax = _content(dock).axes_1d
    # Customize 保护：同 _draw_1d，先拍下现状再 clear
    keep_title, keep_xlabel, keep_ylabel, keep_scale, old_lines = \
        _snapshot_canvas(ax)
    prev_limits = getattr(window, "_setting_limits", False)   # save/restore（同 _draw_1d）
    window._setting_limits = True   # 同 _draw_1d：程序设范围不算用户改动
    try:
        ax.clear()
        curves = _compare_shown_curves(window, dock)
        # 画图顺序 = 文件列表顺序（不随各文件算完的先后变）→ 图例顺序、
        # 颜色序号稳定（i 由 _compare_shown_curves 携带）。**颜色序号按
        # 文件名尾号排**（用户 2026-10-15 晚："识别的不对，应该是文件名
        # 最后几位数的顺序"——编号才是样品身份的锚；文件栏怎么排只动
        # 行/图例的位置，不动谁最深）。n 用**全部**勾选文件数——热图联动
        # 隐藏的行不重排色号，重新显示回来颜色不变。
        family = _panel_param(window, dock, "渐变色系", "蓝")
        files = getattr(dock, "compare_files", None) or []
        rank = _color_rank_by_number(files)   # {files 下标 → 颜色序号}
        n_color = len(files) or (max((c[3] for c in curves), default=0) + 1)
        stack = _panel_param(window, dock, "对比堆叠", False)
        if stack:
            # 瀑布式错开叠放，行距统一：所有行同一个行高（= 各行峰值的
            # 第二高的行峰 × 0.7，口径见 services/stacking），y 刻度 = 各条基线
            # （显示名）。**不按各自峰值定行高**——那等于把每条曲线缩到
            # 各自的高度，样品之间的强弱没法横向比（用户 2026-09-26：
            # "不要按照各自的最高峰归一化，所有的图"）；也不按全场最大
            # ——一条特别强的曲线会把其余压成平线（2026-10-02）。
            # 归一化先做（每条显示数据再叠），堆叠下纵轴范围/
            # 对数不适用（行偏移由数据决定，同瀑布）
            peaks = [float(np.nanmax(c[1])) for c in curves
                     if len(c[1]) and np.isfinite(c[1]).any()]
            step = row_step(peaks)   # 行距口径见 services/stacking（第二高的行峰）
            if _panel_param(window, dock, "手动行距", False):
                # 用户自己定行距（2026-10-05："让用户自己改 y offset"）：
                # 按倍数缩放自动行距——只动显示，1.00 = 与自动相同
                step *= float(_panel_param(window, dock, "行距倍数", 1.0))
            offsets = [i * step for i in range(len(curves))]
            for (tth, shown, display, i, _ref), off in zip(curves, offsets):
                color = _gradient_color(family, rank.get(i, i), n_color)
                ax.plot(tth, shown + off, color=color, lw=0.8,
                        label=display)
            ax.set_yticks(offsets)
            # 纵轴样品名：「显示数据名」默认关（81 条全名实测占图宽 29%、
            # 糊成黑带）；勾上按条件显示 = 短名 + 按高度抽稀（热图同款，
            # _short_labels）。不显示名字时行序 = 文件栏顺序，认具体某行
            # 靠悬停读数（全名 + 2θ）
            if _panel_param(window, dock, "显示数据名", False):
                ax.set_yticklabels(
                    _short_labels([c[2] for c in curves], ax), fontsize=6)
            else:
                ax.set_yticks([])
            # "家"的自然量程 = 全堆叠刚好装下；在套用缩放窗口**之前**量
            # （同瀑布 2026-10-13 的教训：窗口不许被封成"家"）
            natural_y = tuple(ax.get_ylim())
            # 堆叠自己的缩放窗口（视图键，见 plot_panels._on_ylim_changed）：
            # 在堆叠里滚轮/平移过的位置记在这，重画（如切"显示数据名"）
            # 套回去——此前不套，堆叠里放大的位置一动任何显示参数就丢。
            # 它**不碰平铺的设置**（纵轴自动/上下限），所以取消堆叠时视图
            # 回到堆叠前的样子（用户 2026-10-15："堆叠后取消堆叠会导致
            # 视图不能回来"）
            pinned = _panel_param(window, dock, "堆叠视图 y 范围", None)
            if (isinstance(pinned, (list, tuple)) and len(pinned) == 2
                    and float(pinned[0]) < float(pinned[1])):
                ax.set_ylim(float(pinned[0]), float(pinned[1]))
        else:
            for tth, shown, display, i, _ref in curves:
                color = _gradient_color(family, rank.get(i, i), n_color)
                ax.plot(tth, shown, color=color, lw=0.8, label=display)
        # 颜色不套旧快照（渐变色系参数在画图时已定），其余样式
        # 照旧保护（图例在下面读标签，先套回样式）
        _restore_line_styles(ax, old_lines, restore_color=False)
        xlo = _panel_param(window, dock, "视图 2θ 下限 (°)", None)
        xhi = _panel_param(window, dock, "视图 2θ 上限 (°)", None)
        # 同 _draw_1d：先分清这次画的是"用户缩放的窗口"还是"按参数算的窗口"
        zoomed = xlo is not None and xhi is not None and xlo < xhi
        if not zoomed:
            xlo = _panel_param(window, dock, "2θ 下限 (°)", 1.0)
            xhi = _panel_param(window, dock, "2θ 上限 (°)", 8.0)
        if xlo < xhi:
            ax.set_xlim(xlo, xhi)
        if window.plot_docks.get(window.focus_panel) is dock:
            _param_box_set(window, "视图 2θ 下限 (°)", xlo)
            _param_box_set(window, "视图 2θ 上限 (°)", xhi)
        if not stack:
            # 堆叠模式下纵轴由行偏移决定，对数/范围参数不适用（同瀑布）
            log_y = _panel_param(window, dock, "对数纵轴", False)
            scale = _settle_scale(dock, keep_scale, log_y)
            eff_log = (scale == "log")
            if scale != "linear":
                ax.set_yscale(scale)
            auto_y = _panel_param(window, dock, "纵轴自动", True)
            ylo = yhi = None
            if auto_y:
                if curves:
                    # 纵轴范围看 ref（不跑背景/平滑的那份，见
                    # _compare_shown_curves）：调背景时框不动、只有曲线
                    # 在框里往下走
                    ylo, yhi = _auto_y_range(
                        np.concatenate([c[4] for c in curves]), eff_log)
                    # 但又得给"扣完的那份"留地方：扣了背景/平滑/裁剪时
                    # 下界兜到 0 附近（留 2% 框高），否则曲线的基线贴在
                    # 框底外面（同 1D 面板的兜法）；对数轴画不出 ≤0，不兜
                    ch = _proc_params(window, dock, None) or {}
                    active = (str(ch.get("mode", "off")) != "off"
                              or float(ch.get("smooth_deg") or 0.0) > 0
                              or bool(ch.get("cut_ranges")))
                    if active and not eff_log:
                        ylo = min(ylo, -0.02 * abs(yhi))
                    if ylo < yhi:
                        ax.set_ylim(ylo, yhi)
                else:
                    pass   # 一条曲线都没算成：空图，纵轴交给 matplotlib 默认
            else:
                ylo = _panel_param(window, dock, "纵轴下限", 1.0)
                yhi = _panel_param(window, dock, "纵轴上限", 100000.0)
                if eff_log:
                    ylo = max(ylo, 1e-6)
                if ylo < yhi:
                    ax.set_ylim(ylo, yhi)
            # 自动模式把实际用的区间填进置灰输入框（同 _draw_1d，只填焦点；
            # 回填挂旗标——裸 setValue 会触发即改即画钩子重入本函数，见
            # _draw_1d 那处的长注释）
            if auto_y and ylo is not None and window.plot_docks.get(window.focus_panel) is dock:
                _param_box_set(window, "纵轴下限", ylo)
                _param_box_set(window, "纵轴上限", yhi)
        _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel)
        if dock.compare_data and not stack:
            # 堆叠下 y 刻度为样品名（曲线就躺在自己名字那行上），
            # 图例冗余（同瀑布）；平铺时按条数决定画不画（多了挡图）
            _draw_compare_legend(
                window, dock, ax,
                [ln.get_label() for ln in _data_lines(ax)])
        # 色标（颜色 ↔ 数据顺序的读数条）：朴素开关（用户 2026-10-15 晚：
        # "把这个改为显示图例的选项，默认不显示"），参数坞「显示色标」
        # 勾选；说明文字与位置（放哪个角）在「外观…」里改。端点顺序 =
        # 颜色序号（文件名尾号）——与颜色实际怎么分同一个口径
        if (len(curves) >= 2
                and bool(_panel_param(window, dock, "色标", False))):
            ordered = sorted(curves, key=lambda c: rank.get(c[3], c[3]))
            _draw_compare_color_scale(
                ax, family, [c[2] for c in ordered],
                str(_panel_param(window, dock, "色标说明",
                                 _DISPLAY_DEFAULTS["色标说明"]) or ""),
                str(_panel_param(window, dock, "色标位置",
                                 _DISPLAY_DEFAULTS["色标位置"])))
        ax.grid(alpha=0.3)
        _content(dock).draw()
    finally:
        window._setting_limits = prev_limits
    _connect_axis_sync(window, dock.panel_key)   # ax.clear() 清掉了回调（见 helper 注释）
    if zoomed:
        dock._view_from_gesture = True   # 缩放的窗口不是"家"（同 _draw_1d）
    # 程序重画 = 新"家"（见 helper 注释）。家的 y 分两份记（2026-10-15）：
    # 平铺那份（view_home）原样留着——取消堆叠后按 Home 回到没堆叠时的
    # 最初视图；堆叠这份记进 view_home_stack（y 取 natural_y，钉住的
    # 缩放窗口不算"家"：用户 2026-10-09 实测"先放大、再开堆叠后按 Home"
    # 恢复的必须是整个堆叠，不许裁掉上半截）。此前共用一份、由 mode_y
    # 覆写，取消堆叠时家留在堆叠坐标上（用户 2026-10-15："堆叠后取消
    # 堆叠会导致视图不能回来"的另一半）。
    if stack:
        home = getattr(dock, "view_home", None)
        x = home[0] if home is not None else tuple(ax.get_xlim())
        dock.view_home_stack = (x, natural_y, ax.get_yscale())
        dock._view_from_gesture = False   # 堆叠的 y 与手势无关，消费掉（同 mode_y 分支）
    else:
        _refresh_home(dock, ax)   # 程序重画 = 新"家"（见 helper 注释）
    dock.figure_saved = False   # 重画 = 新内容还没存盘

def _refresh_compare_family(window: QMainWindow) -> None:
    """渐变色系下拉拨了（对比页那把）→ 写进快照再重画。

    色系不参与 `_refresh_compare` 的"控件值优先"重建（理由见
    `_display_snapshot` 里那段），所以它要有这条**专用写回**：只有"用户
    真的拨了这把下拉"才把控件值写进快照——别的显示参数改动的重建一律
    沿用快照里的旧值，不会再把「外观」里选好的色系打回默认
    （用户 2026-10-14："对比图颜色在改参数后会恢复默认"，实测紫 → 蓝）。
    """
    if getattr(window, "_param_replaying", False):
        return   # 回放快照期间控件值正被程序改写，不是用户改动（同 _refresh_proc）
    box = window.params.get("渐变色系")
    if box is None:
        return   # 裸窗口/测试没这把下拉：没得拨
    fam = _widget_value(box)
    handled = False
    for key, dock in list(window.plot_docks.items()):
        if not key.startswith("对比"):
            continue
        snap = getattr(dock, "params_snapshot", None)
        if snap is None:
            continue     # 还没算完（快照都没拍）：跟 _refresh_compare 同款跳过
        snap["渐变色系"] = fam
        handled = True
    if handled:
        _drop_pending_display(window, ("渐变色系",))   # 有面板接住：账本作废
    else:
        # 一张对比面板都没有：记进"待应用"账本，出图时当新面板起点
        _stash_pending_display(window, ("渐变色系",))
    _refresh_compare(window)   # 用已有数据重画（它那几个键的账本收尾照旧）

def _refresh_compare(window: QMainWindow) -> None:
    """对比显示参数（归一化 / 归一化目标 / 堆叠）改了 → 就地重画。

    用户 2026-10-02 第 1 条："堆叠显示无效"。实测画图那侧是好的（勾上
    [堆叠显示]、按 [应用显示设置]，y 刻度确实变成样品名、曲线按全场峰值
    ×0.7 抬起来）——卡在**够不着**：那个勾选框住在「对比」页，而
    [应用显示设置] 与"未应用"灰字只在「原图」页，在对比页勾上它，那一页
    没有任何东西能把改动用出去（也没有灰字提示），看起来就是"勾了没反应"。

    做法照抄热图的 `_refresh_heat`（用户 2026-09-27 那条）：显示参数改了
    就立刻按新值重画，不需要 [应用]。显示参数不触发重算，本来就是
    "改了就该看见"的东西——`_display_snapshot` 把控件值写进各面板快照
    （数据参数沿用旧值），再用已有数据重画；对比是单槽面板，通常就一张。
    """
    if getattr(window, "_param_replaying", False):
        return   # 回放快照期间控件值正被程序改写，不是用户改动（同 _refresh_proc）
    # handler 管的这几个键（app.py 里接上 _refresh_compare 的那几件控件）。
    # 渐变色系 2026-10-14 起不在这——它有自己的 _refresh_compare_family
    # （从控件采集会被下拉旧值打回默认，见那里的说明）。
    keys = ("对比归一化", "归一化目标", "对比堆叠",
            "手动行距", "行距倍数", "显示数据名", "色标")
    handled = 0
    for key, dock in list(window.plot_docks.items()):
        if not key.startswith("对比"):
            continue
        # 先写快照再查数据（2026-10-13 对齐 _refresh_heat 的顺序）：算图
        # 期间改的参数落进快照、算完那一下按新值画；否则"还在算"的
        # 面板会被当成没人接，改动丢在账本里、这张图白算一趟
        dock.params_snapshot = _display_snapshot(window, dock.params_snapshot)
        handled += 1
        if not getattr(dock, "compare_data", None):
            continue     # 还没算完：等它自己画
        _redraw_compare(window, key)
    if handled:
        _drop_pending_display(window, keys)   # 有面板接住：账本里这几个键作废
    else:
        # 一张对比面板都没有：改动记进"待应用"账本，出图时当新面板起点
        # （用户 2026-10-13："在出图之前调整参数会无效"）
        _stash_pending_display(window, keys)
    # 这几项从此没有"未应用"这一态：灰字量的是"控件 vs 快照"的差，
    # 而这里刚把控件值写进了快照（同 _refresh_heat 的收尾）
    _refresh_pending_labels(window)

def _finish_compare(window: QMainWindow, key: str) -> None:
    """对比面板全部曲线到齐（含失败）：整图重画 → 成为编辑对象 → 记日志。"""
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：静默丢弃
    _redraw_compare(window, key)
    _set_focus(window, key, dock.panel_display)
    if dock.compare_data:
        srcs = getattr(dock, "compare_sources", {}) or {}
        tally = {}
        for src in srcs.values():
            tally[src] = tally.get(src, 0) + 1
        # 来处的说法要对得上"我勾的是什么"：勾**原始条目**、曲线从 1D 缓存
        # 读出来的，写成"复用 1D 缓存"而不是"1D 产物"——后者会让人以为
        # "我没勾 1D 产物啊，怎么冒出来的"（用户 2026-09-27 的原话）。
        # "新算"没了（2026-10-16 栅栏）：对比不再现场积分，每条曲线都有来处。
        label = {"1D 产物": "复用 1D 缓存", "处理产物": "复用处理产物",
                 "1D 面板": "复用 1D 面板"}
        detail = "、".join(f"{label.get(k, k)} {v} 条"
                          for k, v in sorted(tally.items()))
        _log(window, f"对比完成：{len(dock.compare_data)} 条曲线"
                     + (f"（{detail}）" if detail else ""))
    else:
        _log(window, "对比失败：没有一条曲线画得出来，面板留空")

def _aggregate_snapshot(window: QMainWindow, base: dict) -> dict:
    """对比 / 热图面板的快照：显示参数照旧，**数据参数（2θ / 点数）不要**。

    用户 2026-10-03："对对比图改 2θ 会影响所有参与对比的图，这不合适"——
    对比/热图是"看已经算好的东西"，每条曲线有**自己**的范围（取数见
    plot_views._curve_for），面板不该声称"我按某个全局范围算过"。数据键
    不进快照正好办两件事：切到对比面板**不回放也不写**坞顶那行（切回
    1D 页时盒子还是那边自己的值，不串），坞顶那行的「未应用」也不会为
    对比面板亮（_pending_diff 跳过快照里没有的键）。
    """
    snap = _data_snapshot(window, base)
    for name in DATA_PARAM_DEFAULTS:
        snap.pop(name, None)
    return snap

def _run_compare(window: QMainWindow, key: str) -> None:
    """把面板绑定的每个文件的曲线**读出来**叠到同一张图（只读已经算好的）。

    用户 2026-10-16："原始数据不允许直接进行对比，必须先去 1d 积分，日志
    里记得写"——现场积分那条路退休：按钮那头已经整批拦下没算过的原始
    数据（_plot_compare），这里对取不到的再逐条跳过并写明（数据 [应用]
    等直进路径的兜底）。全是读盘 / 读面板，这一趟完全同步：循环走完即
    收尾（compare_pending 一路减到 0——`panels._PANEL_ATTRS` 与两个
    测试还读它，别删）。快照开工前拍下（与单文件面板一致）；代次（gen）
    与面板代数（epoch）的检查保留为安全网——同步之后它们已不可达
    （迟到回调那类场景随现场积分一起退休），留着防将来又引入异步。

    **每个文件按它自己上次用过的数据参数取数**（甲，2026-10-03）：对比
    读的是每个文件**自己**的产物/范围，不看坞顶那行（那行在对比页也藏
    起来了）——"对对比图改 2θ 会影响所有参与对比的图，这不合适"。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：迟到点击/重算不落地
    # 显示参数沿用面板自己的旧快照（重按 [对比] 刷新不改这张图的长相）；
    # 数据参数不进快照（见 _aggregate_snapshot）
    dock.params_snapshot = _aggregate_snapshot(window, dock.params_snapshot)
    # 显示参数的「未应用」灰字该灭（重按 [对比] 时编辑对象没变，
    # _set_focus 会直接 return、不再刷新——用户 2026-10-03："灰字还在"）
    _refresh_pending_labels(window)
    dock.compare_gen += 1
    gen = dock.compare_gen
    epoch = window._panel_epoch.get(key, 0)   # 面板代数：关过重开旧代全作废
    dock.compare_pending = len(dock.compare_files)
    dock.compare_data = {}
    prev_limits = getattr(window, "_setting_limits", False)   # save/restore（同 _draw_1d）
    window._setting_limits = True   # 程序自己清轴：不触发范围同步写回
    try:
        _content(dock).axes_1d.clear()
    finally:
        window._setting_limits = prev_limits

    def finish_one(path, display, result):
        """单文件结果到位：存原始数据、画一条曲线；全齐后收尾。"""
        # 现查面板（不捕获对象：面板可能已关、已换容器）：
        # 面板没了 / 代数变了（关过重开）/ 代次旧了 → 丢弃
        panel = window.plot_docks.get(key)
        if (panel is None
                or window._panel_epoch.get(key, 0) != epoch
                or panel.compare_gen != gen):
            return   # 旧结果静默丢弃（整图已由新代次重画）
        tth, intensity = result
        panel.compare_data[display] = (tth, intensity)
        panel.compare_pending -= 1
        if panel.compare_pending == 0:
            _finish_compare(window, key)

    def fail_one(path, display, msg):
        panel = window.plot_docks.get(key)
        if (panel is None
                or window._panel_epoch.get(key, 0) != epoch
                or panel.compare_gen != gen):
            return
        panel.compare_pending -= 1
        _log(window, f"对比：{display} 跳过 —— {msg}")
        if panel.compare_pending == 0:
            _finish_compare(window, key)

    # 取数分两种来源（用户 2026-09-25 定的"阶段文件夹"语义）：
    #   - 产物条目（"1D"/"扣背景"）说的是**那一份产物**：直接读盘，
    #     不比对设置、不重算（读不到就跳过并说清楚）；
    #   - 原始数据条目：**只认已经算好的**（处理产物 / 1D 产物 / 本会话
    #     的 1D 面板，见 plot_views._ready_curve）——没算过的不再现场
    #     积分（2026-10-16 栅栏），逐条跳过并指明"先点 [1D] 出图"。
    # 用了哪一份记进 compare_sources，收尾时写进日志（"看得见"）
    dock.compare_sources = {}
    _log(window, f"开始对比 {len(dock.compare_files)} 个文件"
                 "（只叠已经算好的曲线）")
    for source in dock.compare_files:
        path, display = source.path, source.display
        if not gui_sources.is_rawlike(source.kind):
            got = gui_sources.load_product(source)
            if got is None:
                fail_one(path, display, "产物不存在（可能已被删除），请重新计算")
                continue
            dock.compare_sources[display] = gui_sources.describe_source(source)
            finish_one(path, display, got)
            continue
        got = _ready_curve(window, path, display)
        if got is not None:
            dock.compare_sources[display] = got[2]
            finish_one(path, display, (got[0], got[1]))
            continue
        fail_one(path, display, "还没有 1D 曲线（先点 [1D] 出图）")

def _plot_compare(window: QMainWindow) -> None:
    """[对比] 按钮：把勾选文件的 1D 曲线叠到同一张面板上。

    只做 1D。勾选 ≥2 个文件，每个文件**读已经算好的**那条曲线
    （处理产物 / 1D 产物 / 本会话的 1D 面板），画进同一张图（一个
    文件一条曲线，自动分色 + 图例显示名），完成后该面板成为编辑对象。

    **栅栏**（用户 2026-10-16："原始数据不允许直接进行对比，必须先去
    1d 积分，日志里记得写"）：勾选里有还没出过 1D 的原始数据 → 整批
    不出图，日志点名 + 指路——不现场积分、不开空面板、也不动已开着的
    那张（拒绝在开/重绑之前，见下面 checked 之后的判定）。

    **面板是"单槽"：永远只有一张，每次按 [对比] = 按**当前**勾选重建。**
    （用户 2026-09-28："对比图就是实现文件区当前实际勾选的图的对比图…
    不要留以前勾选过的这个记忆"。旧版把键做成"对比｜排序后的路径串"，
    于是改了勾选再按一次 = 另开一张新面板、**旧那张还赖在屏幕上**——
    用户看到的是旧画面，还以为新勾选没生效。）
    """
    checked = gui_sources.checked_sources(window)
    if len(checked) < 2:
        _log(window, "对比至少勾选两个文件（勾上的文件叠到一张图）")
        return
    files = list(checked)   # 来源条目：原始文件 + 各组产物混着也能对比
    # 栅栏（见 docstring）：判据与取数同一把尺（_ready_curve）——拒绝要
    # 发生在开/重绑面板**之前**，旧图才原样留着
    missing = _raw_not_ready(window, files)
    if missing:
        _log(window, f"对比只叠已经算好的曲线：勾选里 {len(missing)} 个原始"
                     f"数据还没有 1D 曲线（{_preview_names(missing)}）"
                     "——先点 [1D] 出图，再来对比")
        return
    key = "对比"            # 单槽（见 docstring）：不随勾选集合变
    dock = window.plot_docks.get(key)
    if dock is None:
        title = _compare_title(files)
        # 新面板级联摆放，现有面板原地不动（同 _plot_view）
        dock = _open_plot_panel(window, "1D", key, title)
        dock.panel_display = title   # 标题/日志/默认存盘名用
        dock.figure_saved = False
        dock.compare_files = files   # 面板绑定这组来源（重算用）
        dock.compare_gen = 0
        # 新面板：显示参数从默认起步；数据参数不进快照（见 _aggregate_snapshot）
        dock.params_snapshot = _aggregate_snapshot(window, None)
        _log(window, f"打开对比面板：{len(files)} 条曲线叠一张图"
                     f"（{_sources_mix(files)}）")
    else:
        # 复用面板：按当前勾选重绑（旧曲线由 _run_compare 清掉重画），
        # 标题跟着新集合与新名字走
        dock.compare_files = files
        title = _compare_title(files)
        if title != dock.panel_display:
            dock.setWindowTitle(title)
            dock.panel_display = title
        _log(window, f"对比面板按当前勾选重建：{len(files)} 条"
                     f"（{_sources_mix(files)}）")
    dock.setVisible(True)
    _run_compare(window, key)

