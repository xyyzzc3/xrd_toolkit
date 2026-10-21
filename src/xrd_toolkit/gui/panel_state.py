"""面板状态共享层：面板登记访问、参数快照、焦点回放、自动显示区间。

模块图（调用方向永远从上往下；2026-09-18 与 09-23 分两轮拆模块，
2026-09-24 再拆出多文件视图 plot_compare 与面板壳 plot_panels）：
    app.py（窗口组装）
      ├── plot_compare.py（多文件视图：对比 / 热图 / 锚点拾取）
      │     └── plot_views.py（单文件视图：runner + 出图 + 任务回调）
      │           └── plot_panels.py（面板壳：画布 / 手势 / 工具栏）
      ├── file_dock.py（文件列表坞，共用 plot_export 存图）
      └── calib.py（校准工作台）→ calib_model / calib_panel /
            calib_table / config_ops
    共同下层：plot_export.py（存图对话框）→ panels.py（面板容器生命
      周期）→ customize.py（Customize 对话框）→ panel_state.py（本
      模块：最底层共享层，不 import 任何兄弟模块；tasks.py 同样零
      兄弟依赖，只管后台线程）
    反向调用（plot_views / plot_panels / customize → plot_compare，
    calib_* → calib）一律函数内延迟导入——全包没有模块级环。

内容：
  - _log / _content：日志与"面板容器 → 面板内容"的统一入口；
  - 参数快照：_snapshot_params（拍控件）/ _data_snapshot（数据参数
    用当前值、显示参数沿用旧快照）/ _display_snapshot（反过来）/
    _panel_param（读某面板自己的参数值，画图一律走它）——每张图
    各记各的，新开面板从默认起步、重算保留自己的旧设置；
  - 焦点与回放：_set_focus（点面板/计算完成时切编辑对象）/
    _load_params_snapshot（把快照填回参数坞，只展示不计算）；
  - _apply_config：几何配置条目应用到参数坞（PONI/倾斜角等完整
    条目挂在 window.config 供后续接线）；
  - _reload_config_combo：配置下拉框与 CONFIGS 注册表同步
    （校准工作台保存新条目后调用，保存即选中生效）；
  - _collect_geometry：从参数坞收集积分几何（像素/波长/距离 +
    配置条目的 PONI/倾斜角）；
  - 自动显示区间：_auto_y_range / _auto_contrast_values（1%/99.9%
    分位）/ _apply_auto_contrast / _apply_auto_ylim（按焦点面板
    重算填回置灰输入框）/ _compare_shown_curves（对比面板实际画
    上去的归一化后曲线——画图与自动纵轴共用同一份口径）。
"""
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QMainWindow, QMdiSubWindow, QWidget)

from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG, entry_display
from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.services import process
from xrd_toolkit.services.background import (
    compute_baseline, subtract_background)
from xrd_toolkit.services.data_loader import load_diffraction_image
# 对比/热图配色（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.plot.colors import (
    _CLASSIC_COLORS, _CLASSIC_DARK_BRIGHT_FLOOR, _CLASSIC_DARK_FLOOR,
    _CLASSIC_DARK_PER_ROUND, _CLASSIC_FAMILY, _CLASSIC_LIGHT_CAP,
    _GRADIENT_DARK_MIX, _GRADIENT_FAMILIES, _GRADIENT_FAMILY_ORDER,
    _GRADIENT_LIGHT_MIX, _RAINBOW_FAMILY, _RAINBOW_SINGLE_POS,
    _family_label, _gradient_color, _mix_hex)
# 基础层（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.state.base import (
    _content, _log, _panel_param, _param_box_set, _set_widget_value,
    _widget_value)
# 处理/背景参数（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.state.processing import (
    _BG_GEOM_KEYS, _bg_geom_sig, _bg_params, _bg_settings, _collect_geometry,
    _compare_shown_curves, _cut_list_of, _heat_shown, _proc_curve,
    _proc_params, _proc_settings, _smooth_cut_params)
# 自动区间（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.state.ranges import (
    _CONTRAST_FALLBACK, _YLIM_FALLBACK, _apply_auto_contrast,
    _apply_auto_heatlim, _apply_auto_ylim, _auto_contrast_values,
    _auto_y_range)
# 参数快照（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.state.snapshots import (
    _DISPLAY_DEFAULTS, _DISPLAY_PARAMS, _VIEW_WINDOW_KEYS, _data_snapshot,
    _display_snapshot, _snapshot_params)

# 图像参数组的全部条目（"只看图不参与计算"的那一组）。快照里这两类
# 参数按职责分开更新（见 _data_snapshot / _display_snapshot）：两个
# [应用] 各管各的，互不串改。
#
# 数据参数组的出厂默认（坞顶第三行那三个控件的初值，缺一不可）：
# 控件的初始值从这里取（app._build_param_dock），"从没算过的文件开新图
# 时回到默认"（plot_views._apply_range_memory，甲方案）也用它——同一份
# 数字两处读，别再各写一个字面量。
DATA_PARAM_DEFAULTS = {
    "2θ 下限 (°)": 1.0,
    "2θ 上限 (°)": 8.0,
    "输出点数": 3000,
}

# 校准图面板占 window._hover_panel_key 的哨兵值（Home 键的"认领"标记）：
# 校准图不进 plot_docks（见 calib_panel 模块说明），鼠标停在它上面时把
# "最后停留"标成这个值——plot_panels._home_key_reset 见到它就把 Home
# 转给校准视野的复位（用户 2026-10-14："校准图添加 home 键，还是回到
# 最初的视图"）。
CALIB_HOVER_KEY = "校准图"

# 渐变色系：同一种颜色的深浅渐变（用户 2026-10-13："对比图的颜色改为

def _stash_pending_display(window: QMainWindow, keys) -> None:
    """即改即画时**没有面板能接住**的改动：把控件现值记进窗口级"待应用"
    小账本（window.pending_display）。

    用户 2026-10-13："对比热图页面如果在出图之前调整参数会无效，出图瞬间
    所有的参数都会恢复默认，怀疑所有实时改变的参数都会有这种情况"——属实：
    没有对应面板时各家 handler 都早退/空转（改动只留在控件上），出图时
    新面板显示参数统一从 _DISPLAY_DEFAULTS 起步，焦点一回写就"瞬间恢复
    默认"。账本让"先调好、再出图"成立：出图（_data_snapshot base=None）
    时叠在默认值上；**不主动清**——出图后焦点回放若把该键刷成与账本不同
    的值，说明控件已改属别的图，那时才逐键作废（见 _load_params_snapshot_
    body 的收尾）；handler 有面板可接时也顺手清掉自己那几个键
    （_drop_pending_display）——同一个键不能同时"已生效"又"待应用"。

    keys = 键名可迭代（各家 handler 自己知道自己管哪几个键）；控件不在
    （裸窗口/测试）就跳过该键。
    """
    store = getattr(window, "pending_display", None)
    if store is None:
        store = window.pending_display = {}
    for name in keys:
        w = window.params.get(name)
        if w is not None:
            store[name] = _widget_value(w)

def _drop_pending_display(window: QMainWindow, keys) -> None:
    """把某几个键从"待应用"账本里清掉（改动已有面板接住 / 用户重拨）。

    清空后连空的容器也收掉（window.pending_display 没了 = 没账可记，
    _load_params_snapshot_body 的收尾据此短路）。
    """
    store = getattr(window, "pending_display", None)
    if not store:
        return
    for name in keys:
        store.pop(name, None)
    if not store:
        window.pending_display = {}

# ── 「未应用」：改了要按一下才生效的控件（用户 2026-09-30 的"丙"方案） ──
# 用户原话："参数页显示本图的参数，就是上方图片名称的本图参数。如果用户改了，
# 添加一个灰字未应用来区分，切图再切回来保持"。三件事：
#   ① 灰字：当前编辑对象有"改了还没生效"的改动时，坞顶那个灰字亮着；
#   ② 记住：改动**按图**记（`dock.pending_params`），切走再切回来原样还在
#      （改之前的行为是：切图时回放直接把用户填的值冲掉，什么提示都没有）；
#   ③ 生效即清：快照一旦追上控件（[重算这张图] 会写快照），差集自然为空、
#      灰字自己灭——不需要谁专门去"清 pending"。
# 表里只放**要按一下才生效**的控件。即改即生效的不放——显示参数
# 2026-10-08 起**全部**即改即画（对比四参数 10-02、热图三项与 1D 显示
# 10-07、2D 三项 / 剖面角度 / 热图范围 10-08，见 app 与 plot_views 的
# _refresh_* 接线），本表只剩「数据」一组：2θ 范围 / 点数要等**下一次
# 计算**才被用掉，"改了还没算"是真实且必须提示的状态。
_PENDING_GROUPS = {
    "数据": ("2θ 下限 (°)", "2θ 上限 (°)", "输出点数"),
}

def _pending_names() -> tuple:
    """表里所有控件的名字并成一条（按表里的顺序）。"""
    return tuple(name for names in _PENDING_GROUPS.values() for name in names)

# （_AUTO_OWNS / _auto_owner 随「图像」组于 2026-10-08 退休：它们只用来把
# "自动开关算完填进上下限框的展示值"排除出 pending 比对，那个组没了就
# 没有比对对象了。程序回填现在挂 _param_box_sync 旗标——见
# _apply_auto_contrast / _apply_auto_heatlim——即改即画那一路靠它区分
# 程序同步与用户输入。）

def _group_of(name: str):
    """这个键属于哪一组（数据 / 图像；不在名单里返回 None）。"""
    for group, names in _PENDING_GROUPS.items():
        if name in names:
            return group
    return None

def _note_user_edit(window: QMainWindow, name: str) -> None:
    """用户动了某个"要按一下才生效"的控件 → 这组的值又变回"没被用掉"。

    配套 _note_params_consumed：两个标记一起决定"这次改动算不算未应用"
    （见 _capture_pending 的说明——"填数 → 出图"必须不算）。
    """
    consumed = dict(getattr(window, "_pending_consumed", None) or {})
    group = _group_of(name)
    if group is not None:
        consumed[group] = False
    window._pending_consumed = consumed
    _refresh_pending_labels(window)

def _note_params_consumed(window: QMainWindow, group: str) -> None:
    """这一组控件的当前值刚被**用掉**（算了/重算了一张图、应用了显示设置）。

    用掉之后它们就不再是"未应用"：用户填 2.5 出一张新图，那是**指令**，
    不是"上一张图的未生效改动"——不区分的话，切回上一张图时它会把刚填的值
    当成那张图的待办冒出来（TestParamSnapshot 那两条就是这么挂的）。
    """
    consumed = dict(getattr(window, "_pending_consumed", None) or {})
    consumed[group] = True
    window._pending_consumed = consumed

def _pending_diff(window: QMainWindow, dock) -> dict:
    """当前控件值 vs 该面板**已生效**快照的差（只算上面那张表里的键）。

    "程序自己填的值不算用户改动"的例外（探针实测踩到的，不排掉的话"未
    应用"会永远亮着）：带空占位项的下拉（`currentData()` 是 None）与快照
    里的 ""（默认表的写法）是同一个意思——直接比会永远不等。快照里没有
    这个键（老快照）同样不算改动：宁可少报，也别误报。
    （"自动开关算完填进上下限"那条例外随「图像」组 2026-10-08 退休。）
    """
    snap = getattr(dock, "params_snapshot", None) or {}
    out = {}
    for name in _pending_names():
        if name not in snap:
            continue
        w = window.params.get(name)
        if w is None:
            continue
        value = _widget_value(w)
        same = (value == snap[name]) or (value is None and snap[name] == "")
        if not same:
            out[name] = value
    return out

def _refresh_pending_labels(window: QMainWindow) -> None:
    """按"当前编辑对象有没有未应用的改动"开关那个灰字（没控件就跳过）。"""
    labels = getattr(window, "pending_labels", None)
    if not labels:
        return
    dock = window.plot_docks.get(window.focus_panel)
    diff = _pending_diff(window, dock) if dock is not None else {}
    for group, label in labels.items():
        label.setVisible(any(name in diff for name in _PENDING_GROUPS[group]))

def _capture_pending(window: QMainWindow, key) -> None:
    """切走之前：把"改了还没生效"的控件值记给**正要离开**的那张图。

    用户 2026-09-30："切图再切回来保持"——不记的话回放会把它们冲掉。
    记在面板对象上（不是全局）：面板关了它就跟着一块儿忘掉，与
    "关闭即遗忘"的既有口径一致。

    **刚被用掉的那组不记**（`_pending_consumed`）：用户填 2θ=2.5 按 [出图]
    出一张新图，2.5 是**指令**、不是"上一张图的未生效改动"——记下来的话，
    切回上一张图会凭空冒出一个"未应用"（TestParamSnapshot 两条实测）。
    """
    dock = window.plot_docks.get(key) if key else None
    if dock is None:
        return
    consumed = getattr(window, "_pending_consumed", None) or {}
    dock.pending_params = {
        name: value
        for name, value in _pending_diff(window, dock).items()
        if not consumed.get(_group_of(name))
    } or None

def _overlay_pending(window: QMainWindow, dock) -> None:
    """切进来之后：把该面板自己的未应用改动铺回控件（在快照回放**之后**调）。

    挂 _param_replaying 旗标：写控件会触发那些"改了即重画"的信号，
    不挡的话这一铺会被当成用户改动。
    """
    pend = getattr(dock, "pending_params", None)
    if not pend:
        return
    prev = getattr(window, "_param_replaying", False)
    window._param_replaying = True
    try:
        for name, value in pend.items():
            w = window.params.get(name)
            if w is not None:
                _set_widget_value(w, value)
    finally:
        window._param_replaying = prev

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

# 未选中编辑对象时坞顶灰字的文案（2026-10-07 用户："最上方灰字在未选中时
# 添加双击某某产物的提示，在同一行不要分段"）。**四处复位点共用这一份**：
# app 建标签、panels 关面板、plot_views 两处清焦点——各写一份必然漂移。
# 单行显示（_ElideLabel：坞窄时尾部省略、悬停看全），绝不撑宽参数坞。
FOCUS_EMPTY_TEXT = "编辑对象：未选中（双击文件栏「1D 产物」或点开一张图即可选定）"

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

def _apply_config(window: QMainWindow, index: int, silent: bool = False) -> None:
    """把下拉框选中的配置条目应用到参数面板。

    几何值来自 config.py 注册表（标定值，不是占位默认值）：完整条目
    （label / geometry / beam_center）挂在 window.config 上，_collect_
    geometry、2D/剖面视图、校准页的借用起点都从它取。
    面板这边只两件事：记住选中的 key，并把这条几何的读数写进"几何配置"
    那一行的悬停提示（像素/波长/距离——原先它们各占一行灰色只读字段，
    2026-09-26 用户定：只留下拉框这一行，读数改悬停）。
    silent = 快照回放时的静默切换（用户没动手，不记日志）。
    """
    key = window.config_combo.itemData(index)
    cfg = CONFIGS[key]
    window.config_name = key
    window.config = cfg   # 完整条目（label / geometry / beam_center）
    # 坞顶那一行的悬停读数（像素/波长/距离）与置灰状态都由 app 的
    # _sync_geom_row 一处写：这里是换了条目，叫它重画一遍
    sync = getattr(window, "_geom_row_sync", None)
    if sync is not None:
        sync()
    if not silent:
        _log(window, f"已加载几何配置 {entry_display(key, cfg)}")

def _reload_config_combo(window: QMainWindow,
                         select_key: str | None = None) -> None:
    """配置下拉框与 CONFIGS 注册表同步（GUI 保存新条目后调用）。

    重建全部条目（内置在前、用户条目在后，按 CONFIGS 顺序），悬停
    提示保留（用户条目附加残差），最后选中 select_key：索引变化触发
    _apply_config 把几何填进参数坞（保存即生效）。select_key 为 None
    时保持当前选择不变。
    """
    combo = window.config_combo
    if select_key is None:
        select_key = combo.itemData(combo.currentIndex())
    combo.blockSignals(True)
    combo.clear()
    for name, entry in CONFIGS.items():
        combo.addItem(entry_display(name, entry), name)   # 一条 key_备注，同 app 建库处
        tip = entry["label"]
        if "residual_deg" in entry:
            tip += f"（残差 {entry['residual_deg']:.4f}°）"
        combo.setItemData(combo.count() - 1, tip, Qt.ToolTipRole)
    combo.blockSignals(False)
    idx = combo.findData(select_key)
    if idx < 0:
        idx = 0
    if idx != combo.currentIndex():
        combo.setCurrentIndex(idx)   # 索引变化 → currentIndexChanged → _apply_config
    else:
        _apply_config(window, idx)   # 索引没变信号不触发：手动应用

# 背景扣除的辅助线（原始曲线/基线/锚点标记）统一带这个 gid 前缀。
# 它们不是"曲线"，凡按线号/线列表做事的逻辑都要排除，否则会错位：
# _snapshot_canvas 的快照、_restore_line_styles 的按序回填、
# _hover_motion 的最近线选择、_draw_waterfall 的扇区名回贴。
# 统一走 _data_lines(ax)，别直接迭代 ax.lines。
_AUX_GID_PREFIX = "bg:"
