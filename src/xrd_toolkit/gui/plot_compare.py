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
# 热图块（2026-10-21 从本文件原样搬出；再导出，tests/scripts 照旧经
# gui_plot_compare 取用）
from xrd_toolkit.gui.plot.heatmap import (
    _apply_view_boxes, _assemble_heatmap, _draw_heatmap, _finish_heatmap,
    _heat_data, _heat_row_press, _heat_row_release, _plot_heatmap,
    _refresh_heat, _refresh_heat_view, _refresh_heatmap_view, _run_heatmap)

