"""视图刷新：参数应用、2D/剖面/1D/瀑布显示刷新、重画面板。

2026-10-21 从 plot_views.py 原样搬出（只改结构，不动逻辑）。
plot_views 再导出；对 _run_view / _run_profile 用函数内延迟导入破环。
"""
from pathlib import Path

from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.panel_state import (
    FOCUS_EMPTY_TEXT, _collect_geometry, _display_snapshot,
    _drop_pending_display, _log, _note_params_consumed, _panel_param,
    _refresh_pending_labels, _stash_pending_display)
from xrd_toolkit.gui.peak_view import PEAK_KEYS
from xrd_toolkit.gui.plot.curves import _retarget_product_panel_to_source
from xrd_toolkit.gui.plot.drawing import (
    _draw_1d, _draw_2d, _draw_profile, _draw_waterfall)


def _apply_params(window: QMainWindow) -> None:
    """[应用] / [重算这张图]：用当前参数重算焦点面板。

    焦点面板 = 参数坞顶部"编辑对象"（点图面板或计算完成时设定）；
    没选焦点时提示用户先点图。

    编辑对象若是**产物面板**，先摘掉它的"产物身份"再重算（见下面的说明）——
    产物是冻结点，重算出来的是另一条曲线，不能顶着旧身份继续走。
    """
    # 多文件视图（对比/热图）的计算在 plot_compare：模块级导入会成环
    # （见文件头"依赖方向"），只取本函数用得到的两个 runner
    from xrd_toolkit.gui.plot_compare import _run_compare, _run_heatmap
    from xrd_toolkit.gui.plot_views import _run_view  # 破循环
    key = window.focus_panel
    if key is None:
        _log(window, "先点击要更新的图面板（如 1D），再点 [应用]")
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        _log(window, "编辑对象的面板已不存在")
        window.focus_panel = None
        window.focus_label.setText(FOCUS_EMPTY_TEXT)
        return
    view = key.split("|", 1)[0]
    # 产物面板重算前先摘掉"产物身份"（用户 2026-09-30："本功能的范围只影响
    # 本功能的产物"）：产物是**冻结点**，按新范围重算出来的这条已经不是那一份
    # 了。不摘有两个后果（① 探针实测，② 同一类）：
    #   ① 处理产物：原始文件会被按新范围重积分、**画进这张"处理后"面板**，
    #      而面板仍按产物身份参与后续 [套用]/[采用这份结果]/[批量处理]；
    #   ② 1D 产物：面板画着新范围的曲线，[采用这份结果] 却回头去读**旧范围**
    #      那一份（屏幕上看到的与存下来的不是同一条，这里最忌讳的一类错）。
    # 处理产物走 [套用] 那套"就地换成它底下的 1D 曲线"（血缘查不到会明确拒绝）。
    # 只有 1D 视图可能有产物面板（产物都是曲线条目），别的视图这里天然是空转。
    src = getattr(dock, "panel_source", None)
    if src is not None:
        if src.kind == gui_sources.BG:
            if not _retarget_product_panel_to_source(window, dock):
                return
        else:
            dock.panel_source = None
            dock.panel_display = f"1D_{Path(src.path).name}"
            dock.setWindowTitle(dock.panel_display)
            _log(window, f"这一条是 1D 产物（读的是 {src.display} 那一份）："
                         "按当前设置重算后它不再是那一份了，"
                         "面板改为这个文件的一条 1D 曲线")
    # 这行要在**所有**分支之前（对比/热图也要它：用户点 [应用] 时日志必须
    # 说清重算的是谁）；摘身份之后再写，标题才是摘完的那个
    _log(window, f"[应用] 重算编辑对象：{dock.windowTitle()}")
    if view == "对比":
        _run_compare(window, key)   # 一组文件全部重读+重画（栅栏：不现场积分）
    elif view == "热图":
        _run_heatmap(window, key)   # 同上（force 已于 2026-10-16 栅栏随退休）
    else:
        _run_view(window, view, dock.panel_file, key)
    # 上面这些都会把控件当前值写进那张图的快照 → 数据组的「未应用」灰字
    # 该灭了（它量的是"控件 vs 快照"的差，见 panel_state._refresh_pending_labels）
    _note_params_consumed(window, "数据")
    _refresh_pending_labels(window)

def _refresh_image_display(window: QMainWindow) -> None:
    """2D 显示参数（自动对比度 / 显示范围 / 显示束心）改了 → 就地重画编辑对象。

    2026-10-08：这三项从"要按 [应用显示设置]"改成即改即画——那个按钮
    随这次清扫删了（同 10-02 对比四参数、10-07 1D 显示与瀑布行距的老
    做法）。原先的 _apply_image_params 就是干这件事（只写快照 + 用已有
    数据重画、不重新积分），这里把触发从按钮换成值变。

    作用域 = **编辑对象这一个面板**（同 _refresh_1d_display 的判例）：
    2D 面板能同时开很多张、显示参数每张各记各的（色图/对比度/束心都按
    面板记），改这张不该顺带改别的张。焦点不是 2D/剖面 时同样只写快照
    ——与旧 [应用] 的口径一致（那几个键在别的视图里不参与画图）。
    """
    if getattr(window, "_param_replaying", False) \
            or getattr(window, "_param_box_sync", False):
        return   # 回放快照 / 程序回填（自动对比度算值进框）不是用户改动
    keys = ("自动对比度", "对比度下限", "对比度上限", "显示束心")
    key = window.focus_panel
    dock = window.plot_docks.get(key) if key is not None else None
    if dock is None:
        # 没有编辑对象：控件值先记进"待应用"账本——别的地方改法见
        # _stash_pending_display（用户 2026-10-13："出图之前调整参数会无效"）
        _stash_pending_display(window, keys)
        return
    _drop_pending_display(window, keys)   # 有面板接住：账本里这几个键作废
    dock.params_snapshot = _display_snapshot(window, dock.params_snapshot)
    _redraw_panel(window, key)   # 还没算完时它自己知道，这里不打扰

def _refresh_profile_angle(window: QMainWindow) -> None:
    """剖面角度改了 → 编辑对象是剖面面板就就地重算（后台），否则只写快照。

    剖面角度是这批显示参数里唯一的"要重算"（沿新角度重新取一条线），
    其余都是"用已有数据重画"。焦点是剖面面板 → 走 _run_profile（后台
    任务按面板 key 一场一任务，拨快了由新任务说了算）；焦点不是剖面 →
    只写快照，等将来算这条线时用它（与旧 [应用] 的口径一致）。
    """
    if getattr(window, "_param_replaying", False) \
            or getattr(window, "_param_box_sync", False):
        return   # 回放快照 / 程序同步写控件不是用户改动
    keys = ("剖面角度 (°)",)
    key = window.focus_panel
    dock = window.plot_docks.get(key) if key is not None else None
    if dock is None:
        _stash_pending_display(window, keys)   # 无编辑对象：记进待应用账本
        return
    _drop_pending_display(window, keys)        # 有面板接住：账本里的角度作废
    dock.params_snapshot = _display_snapshot(window, dock.params_snapshot)
    if key.split("|", 1)[0] != "剖面":
        return   # 焦点不是剖面：快照已带上新角度，将来算它时用
    angle = _panel_param(window, dock, "剖面角度 (°)", 0.0)
    if angle == getattr(dock, "profile_angle", None):
        return   # 角度没变（别的参数顺手带出来的刷新）：别白重算
    from xrd_toolkit.gui.plot_views import _run_profile  # 破循环
    _run_profile(window, dock.panel_file, key,
                 _collect_geometry(window),
                 int(window.params["输出点数"].value()))

def _redraw_panel(window: QMainWindow, key: str) -> str:
    """按该面板自己的参数快照重画一张图（**用已有数据，不重算**）。

    两个调用方共用：图像参数的 [应用]（改显示参数后重画编辑对象）与
    面板的 [Home]（回到"参数定义的样子"）。Home 以前走 mpl 的历史栈，
    而程序重画会清空那个栈 → 按下去常常"没反应"或只回到"上次画的
    位置"（用户 2026-09-24 反馈）；现在 Home 与 [应用] 同源 = 确定性：
    参数是什么样，Home 就是什么样（缩放/平移不写进参数，所以它确实
    是"最初的样子"）。

    返回 "" = 重画了；否则返回"还没算完"的说明（调用方拼进日志）。
    视图类型没接线/面板已关也返回说明文本。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return "面板已关闭"
    view = key.split("|", 1)[0]
    if view == "1D":
        if getattr(dock, "last_tth", None) is None:
            return "积分完成后再试"
        _draw_1d(window, dock, dock.last_tth, dock.last_intensity)
    elif view == "对比":
        if not getattr(dock, "compare_data", None):
            return "积分完成后再试"
        from xrd_toolkit.gui.plot_compare import _redraw_compare
        _redraw_compare(window, key)
    elif view == "2D":
        if getattr(dock, "last_image", None) is None:
            return "读取完成后再试"
        _draw_2d(window, dock, dock.last_image)
    elif view == "剖面":
        if getattr(dock, "last_profile_t", None) is None:
            return "剖面算完后再试"
        _draw_profile(window, dock, dock.last_profile_t,
                      dock.last_profile_intensity)
    elif view == "瀑布":
        if getattr(dock, "last_waterfall", None) is None:
            return "扇形积分完成后再试"
        tth, i2d, chi = dock.last_waterfall
        _draw_waterfall(window, dock, tth, i2d, chi)
    elif view == "热图":
        if getattr(dock, "heat_data", None) is None:
            return "热图完成后再试"
        from xrd_toolkit.gui.plot_compare import _draw_heatmap, _heat_data
        data = _heat_data(window, dock)
        if data is None:
            return "热图数据不完整"
        dock.heat_data = data   # 与画的保持同一份：_apply_auto_heatlim 读它
        _draw_heatmap(window, dock, data[0], data[1], data[2])
    else:
        return f"{view} 视图暂不可用"
    return ""

def _refresh_1d_display(window: QMainWindow) -> None:
    """「1D 显示」参数（对数纵轴 / 纵轴自动 / 纵轴范围）改了 → 就地重画。

    2026-10-07 这一小节从「原图」页搬回「1D」页时从"要按 [应用显示设置]"
    改成**即改即画**：那个按钮住在「原图」页，搬过来就够不着了（这个应用
    反复在修的"够不着"病；对照 10-02 对比四参数、10-07 瀑布行距的同款处理）。

    作用域 = **编辑对象这一个面板**（显示参数每张图各记各的，10-02 起的
    规矩）：不学 _refresh_compare 的"所有同类面板"——1D 面板可以同时开
    很多张，每张各有各的缩放/刻度，改了这张不该顺带改其它张。对比面板
    非堆叠时也读这几个键 → 焦点是它时同样就地重画。
    """
    if getattr(window, "_param_replaying", False) \
            or getattr(window, "_param_box_sync", False):
        return   # 回放快照 / 程序同步写控件（缩放写回等）都不是用户改动
    keys = ("对数纵轴", "纵轴自动", "纵轴下限", "纵轴上限") + PEAK_KEYS
    dock = window.plot_docks.get(window.focus_panel)
    if dock is None:
        # 没有编辑对象：记进"待应用"账本，出图时当新面板的起点
        # （用户 2026-10-13："出图之前调整参数会无效"）
        _stash_pending_display(window, keys)
        return
    _drop_pending_display(window, keys)   # 有面板接住：账本里这几个键作废
    snap = _display_snapshot(window, dock.params_snapshot)
    if snap.get("裁剪区间"):
        snap["裁剪区间"] = list(getattr(window, "cut_list", []) or [])
    dock.params_snapshot = snap
    view = window.focus_panel.split("|", 1)[0]
    if view == "1D" and getattr(dock, "last_tth", None) is not None:
        _draw_1d(window, dock, dock.last_tth, dock.last_intensity)
    elif view == "对比" and getattr(dock, "compare_data", None):
        from xrd_toolkit.gui.plot_compare import (   # 破循环：延迟导入
            _redraw_compare)
        _redraw_compare(window, window.focus_panel)

def _refresh_waterfalls(window: QMainWindow) -> None:
    """瀑布的行距控件（[手动行距] + 倍数）改了 → 各瀑布面板就地重画。

    与 plot_compare._refresh_compare 同套路（那是用户 2026-10-02 报
    "堆叠显示无效"时定的：显示参数改了就该立刻看见，不能"够不着"）：
    把控件值写进各瀑布面板的快照，再用已有数据重画——纯显示、不重算。

    只写这两个键，**不整包套用别的显示参数**：控件是所有面板共用的
    一份，控件值可能正显示着别的图（编辑对象）的设置，整包写过去会把
    那张图的配色等串到所有瀑布上。
    """
    if getattr(window, "_param_replaying", False):
        return   # 回放快照期间控件值正被程序改写，不是用户改动
    keys = ("瀑布手动行距", "瀑布行距倍数")
    chk = window.params.get("瀑布手动行距")
    mult = window.params.get("瀑布行距倍数")
    if chk is None or mult is None:
        return   # 裸窗口（测试里没建这对控件）
    handled = 0
    for key, dock in list(window.plot_docks.items()):
        if not key.startswith("瀑布"):
            continue
        snap = getattr(dock, "params_snapshot", None)
        if snap is not None:
            snap["瀑布手动行距"] = bool(chk.isChecked())
            snap["瀑布行距倍数"] = float(mult.value())
        handled += 1     # 快照已收下（含还在算的：算完自己按新值画）
        data = getattr(dock, "last_waterfall", None)
        if data is None:
            continue     # 还没算完：等它自己画
        tth, i2d, chi = data
        _draw_waterfall(window, dock, tth, i2d, chi)
    if handled:
        _drop_pending_display(window, keys)
    else:
        _stash_pending_display(window, keys)   # 没有瀑布面板：记进待应用账本

def _refresh_heatmap_names(window: QMainWindow) -> None:
    """「热图显示」小节的 [显示数据名] 改了 → 各热图面板就地重画（不重算）。

    2026-10-12 用户拍板"甲，拆开"（原话："对比热图现在共用一个显示数据
    名，但是是在对比的板块"）——热图行名从「对比显示」小节那把开关里分
    出来，勾选框住在「热图显示」小节，键 =「热图显示数据名」，改哪边只
    动自己那一类图。做法同前身：只写这一个键、再按已有数据重画——
    **不整包套用别的显示参数**（控件是所有面板共用的一份，整包写会把
    别的图的设置串过来，同 _refresh_waterfalls 的判例）。
    """
    if getattr(window, "_param_replaying", False):
        return   # 回放快照期间控件值正被程序改写，不是用户改动
    chk = window.params.get("热图显示数据名")
    if chk is None:
        return   # 裸窗口（测试里没建这个控件）
    on = bool(chk.isChecked())
    from xrd_toolkit.gui.plot_compare import (   # 破循环：只取这一个
        _draw_heatmap)
    handled = 0
    for key, dock in list(window.plot_docks.items()):
        if not key.startswith("热图"):
            continue
        snap = getattr(dock, "params_snapshot", None)
        if snap is not None:
            snap["热图显示数据名"] = on
        handled += 1     # 快照已收下（含还在算的）
        data = getattr(dock, "heat_data", None)
        if data is not None:
            _draw_heatmap(window, dock, data[0], data[1], data[2])
    if handled:
        _drop_pending_display(window, ("热图显示数据名",))
    else:
        _stash_pending_display(window, ("热图显示数据名",))

