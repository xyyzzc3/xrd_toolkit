"""焦点与参数回放：切换编辑对象、把快照填回参数坞。

2026-10-21 从 panel_state.py 原样搬出（只改结构，不动逻辑）。
panel_state 再导出；对 _apply_config 用函数内延迟导入破环。
"""
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui.state.base import (
    _content, _log, _param_box_set, _panel_param, _set_widget_value,
    _widget_value)
from xrd_toolkit.gui.state.pending import (
    _capture_pending, _overlay_pending, _refresh_pending_labels)
from xrd_toolkit.gui.state.ranges import (
    _apply_auto_contrast, _apply_auto_heatlim, _apply_auto_ylim)


# 未选中编辑对象时坞顶灰字的文案（2026-10-07 用户："最上方灰字在未选中时
# 添加双击某某产物的提示，在同一行不要分段"）。**四处复位点共用这一份**：
# app 建标签、panels 关面板、plot_views 两处清焦点——各写一份必然漂移。
# 单行显示（_ElideLabel：坞窄时尾部省略、悬停看全），绝不撑宽参数坞。
FOCUS_EMPTY_TEXT = "编辑对象：未选中（双击文件栏「1D 产物」或点开一张图即可选定）"

def _load_params_snapshot(window: QMainWindow, snap: dict) -> None:
    """把一份面板快照回放进参数坞控件，期间挂 _param_replaying 旗标。

    旗标的作用：回放是**程序**在写控件，而每个背景扣除控件都连着
    _refresh_proc（实时预览）。不挂旗标的话回放途中的 setValue 会一路触发
    _refresh_proc → _display_snapshot，把"正回放到一半的控件状态"当成用户
    的设置写进当前面板的快照——切一次焦点就把别的面板的显示参数（实测是
    注册在背景组之后的 热图色图/热图归一化/热图对数/热图范围 这几项）
    串到本面板上，而且不可逆。详见 _load_params_snapshot_body。
    """
    window._param_replaying = True
    try:
        _load_params_snapshot_body(window, snap)
    finally:
        window._param_replaying = False

def _load_params_snapshot_body(window: QMainWindow, snap: dict) -> None:
    """把参数快照填回参数面板（只展示不计算）。

    顺序讲究：
      - 先切几何配置：触发 _apply_config，同步 window.config（PONI/
        倾斜角取该条目注册表值，[应用] 依赖它）；
      - 再填各输入框：用"当时真用的"值覆盖注册表几何值；
      - 最后处理自动对比度开关（blockSignals 防日志刷屏）：勾着 →
        静默刷新展示值（焦点是 2D/剖面 才读图，其余占位）；手动
        模式 → 回放快照里的值。

    显示参数（对数纵轴/纵轴范围/对比归一化）也一起回放：每张图的
    显示设置各记各的，点哪张图参数坞就显示哪张图作图时的设置。
    纵轴自动勾着时，最后按焦点图的实际数据重算并填回置灰输入框
    （同自动对比度的套路，见 _apply_auto_ylim）。
    """
    idx = window.config_combo.findData(snap.get("config"))
    if idx >= 0 and idx != window.config_combo.currentIndex():
        # 静默切换：拦下 currentIndexChanged，手动应用（不记日志）
        window.config_combo.blockSignals(True)
        window.config_combo.setCurrentIndex(idx)
        window.config_combo.blockSignals(False)
        from xrd_toolkit.gui.panel_state import _apply_config  # 破循环
        _apply_config(window, idx, silent=True)
    auto = window.params.get("自动对比度")
    for name, value in snap.items():
        if name == "config" or name == "自动对比度":
            continue
        if value is None:
            continue   # 视图 2θ 范围没被用户动过 = 跟随积分范围（下方补填）
        w = window.params.get(name)
        if w is None:
            continue
        _set_widget_value(w, value)   # data 不在下拉列表里就保持原样（下方兜底）
    # 视图 2θ 范围跟随积分范围时：输入框显示"正在用的视图" =
    # 该面板快照里的积分范围（无快照值退回控件当前值，防御后路）。
    # 热图那对同款（2026-10-08 晚拆键；焦点不是热图时填的是焦点面板的
    # 范围——框不在眼前，切到热图面板时回放会再填一次）
    for name, fallback in (("视图 2θ 下限 (°)", "2θ 下限 (°)"),
                           ("视图 2θ 上限 (°)", "2θ 上限 (°)"),
                           ("热图视图 2θ 下限 (°)", "2θ 下限 (°)"),
                           ("热图视图 2θ 上限 (°)", "2θ 上限 (°)")):
        box = window.params.get(name)
        if box is None or snap.get(name) is not None:
            continue
        other = window.params.get(fallback)
        if other is not None:
            box.setValue(snap.get(fallback, other.value()))
    if auto is not None:
        auto.blockSignals(True)
        auto.setChecked(snap.get("自动对比度", True))
        auto.blockSignals(False)
        window.params["对比度下限"].setEnabled(not auto.isChecked())
        window.params["对比度上限"].setEnabled(not auto.isChecked())
        if auto.isChecked():
            _apply_auto_contrast(window, silent=True)   # 焦点图的自动值
        else:
            window.params["对比度下限"].setValue(snap.get("对比度下限", 1.0))
            window.params["对比度上限"].setValue(
                snap.get("对比度上限", 100000.0))
    # 纵轴自动开关的联动状态同步：自动开 → 上下限输入框置灰（只读
    # 展示）。回放 setChecked 时 toggled 信号一般已联动，这里再兜底
    # 一次（同值 setChecked 不触发信号，联动状态可能停在旧面板的）；
    # 勾着自动 → 按焦点图数据重算填回（toggled 时填过一次，但随后
    # 的回放循环可能又覆盖了上下限框的值，收尾再算一次才是准的）
    auto_y = window.params.get("纵轴自动")
    if auto_y is not None:
        window.params["纵轴下限"].setEnabled(not auto_y.isChecked())
        window.params["纵轴上限"].setEnabled(not auto_y.isChecked())
        if auto_y.isChecked():
            _apply_auto_ylim(window, silent=True)
    # 热图自动范围联动（同纵轴自动套路）：自动开 → 上下限框置灰
    # 只读展示，并按焦点热图重算填回
    auto_heat = window.params.get("热图自动范围")
    if auto_heat is not None:
        window.params["热图下限"].setEnabled(not auto_heat.isChecked())
        window.params["热图上限"].setEnabled(not auto_heat.isChecked())
        if auto_heat.isChecked():
            _apply_auto_heatlim(window, silent=True)
    # 归一化目标下拉框按焦点对比面板的文件列表重建（面板里有几个
    # 文件列表就是什么样；焦点不是对比面板 = 保持原样），建完再按
    # 快照值回选——列表重建会丢掉旧选中。只在"指定数据"模式可用
    norm_target = window.params.get("归一化目标")
    if norm_target is not None:
        dock = window.plot_docks.get(window.focus_panel)
        files = getattr(dock, "compare_files", None)
        if files:
            norm_target.blockSignals(True)
            norm_target.clear()
            for src in files:
                path, display = src.path, src.display
                # data 存字符串路径：findData 对 Path 不按 Python 相等
                # 比较（Path 不是 Qt 认识的类型），字符串才找得回
                norm_target.addItem(display, str(path))
            idx = norm_target.findData(str(snap.get("归一化目标", "")))
            norm_target.setCurrentIndex(idx if idx >= 0 else 0)
            norm_target.blockSignals(False)
        mode = window.params.get("对比归一化")
        if mode is not None:
            norm_target.setEnabled(mode.currentData() == "file")
    # 背景扣除的专用行按回放后的模式收起/放出（模式是每张图各记各的：
    # 切面板时控件值跟着变，行也得跟着变）。app 侧的函数经窗级回调
    # 调用，避免 panel_state 反向 import app
    bg_sync = getattr(window, "_bg_rows_sync", None)
    if bg_sync is not None:
        bg_sync(window)
    # 「待应用」账本收尾（2026-10-13）：回放把某个键刷成了与账本**不同**
    # 的值 = 控件从此显示这张图的设置 ≠ 用户先调好的那套 → 那个键作废
    # （"切走 = 放弃"）。值相同的不动：刚出的图采纳的就是账本值，回放
    # 出来当然相等——批量出图时账本要留着给同批的下几张用。
    pending = getattr(window, "pending_display", None)
    if pending:
        for name in list(pending):
            w = window.params.get(name)
            if w is None:
                continue
            if _widget_value(w) != pending[name]:
                pending.pop(name, None)
        if not pending:
            window.pending_display = {}

def _set_focus(window: QMainWindow, key: str, title: str) -> None:
    """把参数面板的编辑对象切到某面板（点面板 / 计算完成时调用）。

    切过去的同时回放该面板的参数快照——点哪张图，旁边就显示哪张
    图的参数。重复点同一面板直接返回：不冲掉用户正在改的值。

    "未应用"的两步也在这里（用户 2026-09-30）：走之前把没生效的改动**记给
    离开的那张图**，进来之后再把它**铺回控件**——所以切走切回来，你填的值
    还在（改之前是回放直接冲掉、一声不响）。
    """
    if window.focus_panel == key:
        return
    prev = window.focus_panel
    _capture_pending(window, prev)
    window.focus_panel = key
    window.focus_label.setText(f"编辑对象：{title}")
    dock = window.plot_docks.get(key)
    snap = getattr(dock, "params_snapshot", None)
    if snap is not None:
        _load_params_snapshot(window, snap)
    _overlay_pending(window, dock)      # 这张图自己的未应用改动：切回来要还在
    _refresh_pending_labels(window)
    # 焦点变了通知外面（面板壳用它把"活动"标题栏挪到新面板；回调挂在
    # window 上，本模块是最底层、不 import 兄弟模块——同 _bg_rows_sync）
    hook = getattr(window, "_on_focus_changed", None)
    if hook is not None:
        hook(prev, key)

