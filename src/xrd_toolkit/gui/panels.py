"""面板容器生命周期：MDI 子窗口 / 弹出窗口、关闭即遗忘、平铺缩放与抓手。

模块图见 panel_state 模块 docstring（本模块处于 plot_views 之下）。

内容：
  - 容器：_PlotSubWindow（MDI 子窗口）/ _FloatedWindow（弹出顶层
    窗口），× 关闭都走 _close_panel（登记表移除 + 焦点移交 + 销毁，
    关闭即遗忘）；
  - 中央绘图区：_build_center（QMdiArea + 底部模式条 + 横排/竖排
    + 总缩放控件）；
  - 抓手：_PanelGripFilter（四边 8px 抓取带 + 四角 24px 抓取区 +
    右下角可见把手；应用级覆盖光标——macOS 部件光标会被带过期
    坐标的合成事件打回箭头）/ _install_resize_grip；
  - 比例记忆：_PanelResizeFilter / _on_canvas_resized（画布尺寸
    一变就记"用户拖过"：每拖一次 = 记住当前画布比例；程序自己的
    布局变化不算）/ _panel_extra（面板壳尺寸）；
  - 摆位缩放：_tile_panels（按类型分层摆位置，绝不缩放）/
    _arrange / _apply_area_zoom（总缩放 50%–200%）/ _AreaZoomFilter
    （Ctrl+滚轮）/ _settle（消化排队中的 Qt 布局事件）；
  - 弹出收回：_toggle_pop_out（状态经 _PANEL_ATTRS 白名单搬家）。
"""
from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMdiArea,
    QMdiSubWindow, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from xrd_toolkit.gui.panel_state import (
    FOCUS_EMPTY_TEXT, _content, _log, _set_focus)
# 面板小部件（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.panel_widgets import (
    _COORD_MIN_W, _COORD_SLOT_W, _CoordReadout, _ElideLabel, _FocusElideLabel)
# 面板容器（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.panel_container import (
    _PANEL_ATTRS, _FloatedWindow, _PanelBarFilter, _PlotSubWindow,
    _apply_panel_chrome, _close_all_panels, _close_panel, _copy_panel_attrs,
    _sync_bar_tooltip)
# 抓手/尺寸记忆（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.panel_grip import (
    _PanelGripFilter, _PanelResizeFilter, _dock_geo, _install_resize_grip,
    _on_canvas_resized, _panel_extra)
# 布局（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.panel_layout import (
    _AreaZoomFilter, _apply_area_zoom, _arrange, _build_center, _settle,
    _tile_panels, _toggle_pop_out)

