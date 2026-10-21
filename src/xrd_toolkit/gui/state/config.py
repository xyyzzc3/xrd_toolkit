"""配置条目应用：几何配置注册表同步、条目应用到参数坞。

2026-10-21 从 panel_state.py 原样搬出（只改结构，不动逻辑）。
panel_state 再导出，app/calib/builder 照旧经 gui_panel_state 取用。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.config import CONFIGS, entry_display
from xrd_toolkit.gui.state.base import _log


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

# 背景扣除的辅助线（原始曲线/基线/锚点标记）统一带这个 gid 前缀。
# 它们不是"曲线"，凡按线号/线列表做事的逻辑都要排除，否则会错位：
# _snapshot_canvas 的快照、_restore_line_styles 的按序回填、
# _hover_motion 的最近线选择、_draw_waterfall 的扇区名回贴。
# 统一走 _data_lines(ax)，别直接迭代 ax.lines。
_AUX_GID_PREFIX = "bg:"

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

