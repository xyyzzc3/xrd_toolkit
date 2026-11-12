"""配置条目应用：几何配置注册表同步、条目应用到参数坞。

2026-10-21 从 panel_state.py 原样搬出（只改结构，不动逻辑）。
panel_state 再导出，app/calib/builder 照旧经 gui_panel_state 取用。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG, entry_display
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

def set_geometry(window: QMainWindow, key, silent: bool = False) -> None:
    """切换当前几何（唯一入口，2026-11-12 方案重构）。

    状态只写 window.config / window.config_name，**不读下拉框**。启动、
    预设应用、焦点快照回放、校准保存、.poni 导入全部走这里；未知 key
    退回默认条目并记日志。

    B1 阶段坞顶的几何下拉还在，这里顺手把它同步到当前条目（屏蔽信号，
    控件只做显示）。B2 把下拉搬进方案管理页后，这段自然变成空操作。
    """
    key = str(key or "")
    cfg = CONFIGS.get(key)
    if cfg is None:
        fallback = DEFAULT_CONFIG
        if key and key != fallback:
            _log(window, f"几何配置 {key!r} 不存在，已改用 {fallback}")
        key = fallback
        cfg = CONFIGS[key]
    window.config_name = key
    window.config = cfg   # 完整条目（label / geometry / beam_center）
    # B1 遗留：坞顶下拉仍在，保持它指向当前条目（状态由这里写）。
    combo = getattr(window, "config_combo", None)
    if combo is not None:
        idx = combo.findData(key)
        if idx >= 0 and idx != combo.currentIndex():
            blocked = combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(blocked)
    # 坞顶那一行的悬停读数：_sync_geom_row 一处写（B2 后改成灰字刷新）
    sync = getattr(window, "_geom_row_sync", None)
    if sync is not None:
        sync()
    # 方案管理页等界面订阅几何变化（B2 用；没订阅就是空操作）
    notify = getattr(window, "_geometry_changed", None)
    if callable(notify):
        notify(key)
    if not silent:
        _log(window, f"已加载几何配置 {entry_display(key, cfg)}")


def _apply_config(window: QMainWindow, index: int, silent: bool = False) -> None:
    """兼容旧调用：把几何下拉的第 index 项交给 set_geometry。

    B1 之后状态不再从控件采集。这个包装只为「下拉框信号」和旧测试/
    脚本保留；新代码一律直接调 set_geometry(key)。
    """
    combo = getattr(window, "config_combo", None)
    if combo is None:
        return
    set_geometry(window, combo.itemData(index), silent=silent)

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
    # 状态统一由 set_geometry 写；重建后的控件只做显示（索引变没变
    # 都不再依赖 currentIndexChanged）。
    set_geometry(window, combo.itemData(idx))

