"""面板壳：画布容器、自绘标题栏、手势、轴同步、悬停、开面板。

从 plot_views.py 拆出来（纯搬迁）：这一层只关心"一个画布长什么样、
怎么拖怎么缩放、鼠标放在上面显示什么"，不知道数据从哪来。
依赖方向：plot_views / plot_compare 都用本模块，本模块不反向依赖它们。

壳的形状（2026-09-24，用户要求"边框、小工具栏做小一点"）：面板 =
一行 26 px 自绘标题栏（[复位视图][放大镜][外观…][保存图片…] + [弹出][关闭]，
_build_slim_bar；不显示名字——名字在参数坞"编辑对象"与图上标题里
已经有两处，悬停提示才给）+ 画布；容器是 frameless 子窗口
（panels._apply_panel_chrome），原先"原生标题栏 36 + 工具栏 47 =
83 px"的壳瘦到 26 px——同样高度的面板里画布多拿 57 px。matplotlib
的工具栏对象仍然存在（_SlimToolbar）但 hide()，只当 action 仓库：
测试与 _magnifier_on 都按 content.toolbar 取 action。
"""
from pathlib import Path

import time
from contextlib import contextmanager

import numpy as np
from matplotlib import get_data_path
from matplotlib.backend_tools import Cursors
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.patches import Rectangle
from matplotlib.figure import Figure
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMainWindow,
                               QMdiSubWindow, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

from xrd_toolkit import paths
from xrd_toolkit.gui.customize import _default_texts, _open_customize_dialog
from xrd_toolkit.gui.panel_state import (_AUX_GID_PREFIX, _content,
                                            _log, _param_box_set,
                                            _panel_param, CALIB_HOVER_KEY)
from xrd_toolkit.gui.panels import (_apply_area_zoom, _apply_panel_chrome,
                                    _close_panel, _install_resize_grip,
                                    _PanelBarFilter, _PanelResizeFilter,
                                    _panel_extra, _PlotSubWindow, _settle,
                                    _toggle_pop_out)
from xrd_toolkit.gui.plot_export import _ask_save_options
# 画布辅助与交互层（2026-10-21 从本文件原样搬出；再导出，plot_views 与
# 测试照旧经 gui_plot_panels 取用）
from xrd_toolkit.gui.plot.canvas import (
    _apply_text_guards, _blit_box, _blit_drop, _blit_take, _data_lines,
    _is_aux_line, _note_hover, _panel_axes, _restore_line_styles,
    _settle_scale, _snapshot_canvas)
from xrd_toolkit.gui.plot.interaction import (
    BOX_MIN_PX, _INTENSITY_Y_VIEWS, _TTH_X_VIEWS, _connect_axis_sync,
    _draw_box, _home_key_reset, _hover_label, _hover_leave, _hover_motion,
    _magnifier_on, _on_xlim_changed, _on_ylim_changed, _pan_motion,
    _pan_press, _pan_release, _refresh_home, _wheel_zoom)
# 面板构建（2026-10-21 从本文件原样搬出；再导出，plot_views 与测试照旧）
from xrd_toolkit.gui.plot.panel_build import (
    PANEL_BAR_ACTIVE, PANEL_BAR_BG, PANEL_BAR_CLOSE_HOVER, PANEL_BAR_H,
    PANEL_BAR_HOVER, PANEL_BAR_LINE, PANEL_BAR_PRESS, PANEL_BAR_TEXT,
    PLOT_OPEN_H, PLOT_OPEN_W, _SlimToolbar, _VIEW_BUILDERS,
    _build_1d_widget, _build_2d_widget, _build_canvas_panel,
    _build_heat_widget, _build_profile_widget, _build_slim_bar,
    _build_view_widget, _build_waterfall_widget, _open_plot_panel,
    _save_panel, _sync_bar_active)

    # 平移**不**贴位图：拖动期视图每帧都在变，要贴就得靠
    # restore_region(xy=) 平移缓存块，而实测（mpl 3.11 + Qt 后端，
    # /tmp/dbg_blit_api3.py 那个最小实验）它并不做干净的平移——点没挪
    # 位、还贴出多份。宁可 30 ms/帧的整帧重画，也不要一个方向错的快速
    # 预览。框选不同：拖动期视图不变，只多一个选框，贴图与真画法等价
    # （见 _blit_box）。

# 视图注册表：视图名 → 内容 builder（签名 window/key → QWidget）。
# 新视图接线 = 加条目，分发骨架不动（热图不占 VIEW_NAMES 工具栏
# 按钮：它是多文件 → 一张面板，走 _plot_heatmap，同 [对比]）

