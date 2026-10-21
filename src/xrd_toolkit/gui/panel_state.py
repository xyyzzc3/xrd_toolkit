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
# 未应用参数账本（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.state.pending import (
    _PENDING_GROUPS, _capture_pending, _group_of, _note_params_consumed,
    _note_user_edit, _overlay_pending, _pending_diff, _pending_names,
    _refresh_pending_labels)
# 焦点/参数回放（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.state.focus import (
    FOCUS_EMPTY_TEXT, _load_params_snapshot, _load_params_snapshot_body,
    _set_focus)

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

# （_AUTO_OWNS / _auto_owner 随「图像」组于 2026-10-08 退休：它们只用来把
# "自动开关算完填进上下限框的展示值"排除出 pending 比对，那个组没了就
# 没有比对对象了。程序回填现在挂 _param_box_sync 旗标——见
# _apply_auto_contrast / _apply_auto_heatlim——即改即画那一路靠它区分
# 程序同步与用户输入。）

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
