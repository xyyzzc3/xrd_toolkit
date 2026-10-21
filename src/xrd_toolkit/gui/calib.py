"""校准工作台：自动校准 + 手动选点校准（参数坞第 2 页 + 中央校准图面板）。

模块图见 app.py docstring（本模块与 plot_views.py 并列，只依赖下层
panels / panel_state / tasks 与服务层引擎，单向无环）。

两种模式（按数据质量任选一种，也可都跑、结果三列并列对比）：
  - 自动校准 [开始自动校准]：后台线程 fit_center_from_rings 自动定
    环心（失败 find_ring_center 兜底）→ calibrate_geometry pyFAI 迭代
    精修，输出距离/束心/倾斜角/残余误差；
  - 手动选点校准：中央校准图上点击至少 3 个点、覆盖 ≥2 个不同衍射
    环，点自动吸附最近理论环（snap_ring，容差 0.5°），
    [开始手动校准] 交给 refine_geometry_from_points 反推几何。

校准完成后**引擎指标**（services/ring_metrics：环位偏差中位 px、完整
环数、a 离散 ppm）附在结果 dict 上并写进日志后缀。它量的是"几何有没
有把理论环放到图像的真环上"，与 pyFAI 的收敛残差互补——后者只说明
迭代自洽（两种解分支都能报 0.004°），不说明环对不对。指标算不出来时
只记 metrics_error、日志如实说明，不影响校准结果本身。

三条来源（自动定位 / 手动选点 / 二次精修）各出一个结果，**"当前使用"
由"谁好用谁"决定**：新来源的环位偏差比当前的好 ≥ SOURCE_IMPROVE_MIN_PX
才替换（用户手动指定过的则永不自动替换，标成"自定义"）。画图与
[保存为配置] 都取"当前使用"那一份。

界面分工：
  - 参数坞第 0 页 = _build_calib_form（自上而下：配置条目操作区 +
    结果三列区 自动|手动|Δ偏差 + 自动/手动两个动作区）。各来源共用
    同一结果区与保存机制："当前使用"的结果可直接存成命名用户条目
    （config_user.json，不进 git），保存后坞顶"几何配置"下拉框
    立即出现并自动选中（几何填进参数坞）。内置 config.py 注册表
    仍走 CLI 模板人工登记（见 config.py 文件头）。
  - 中央校准图面板 = _CalibSubWindow（MDI 子窗口，imshow + 理论环
    路径 + 控制点/用户点标记 + [看环全貌] 视野开关 + [放大镜]，接
    鼠标点击（选点）与放大镜手势）。理论环由 theoretical_ring_paths
    精确反解（倾斜时是椭圆、圆心是直射束落点），不用"圆心 + 半径"
    的正圆近似——后者会整体偏 8~23 px。视野默认锁在图像那一框
    （图像是唯一不动的参照系，环跑到框外由红字说明）；放大镜点亮后
    可框选放大 / 滚轮缩放（2026-10-13 用户："校准图添加放大功能"），
    [看环全貌] 照旧是复位/看全部环的开关。不进 plot_docks：不掺和
    编辑对象焦点、平铺、总缩放；无抓手（v1 从简）。

状态（都挂在 window 上）：
  calib_dock / calib_key / calib_path / calib_display / calib_canvas
  / calib_ax   面板与画布（None = 没开面板）
  calib_gen    代计数：面板关闭/重开/退出模式时 +1，在飞任务回调
               核对代数，迟到结果静默作废
  calib_state  选点 + 累积结果 + 三个槽（懒创建）——关闭面板即全清
                （关闭即遗忘）；各键说明见 calib_model._calib_state
"""
import re
from datetime import datetime
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import LogNorm
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMdiSubWindow, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

# 常量都住在 calib_model（纯逻辑层）。这里把面板/表格用的几个也一并
# 导入，保持『经 gui_calib 取用』的历史引用可用（同 app.py 的再导出惯例）。
from xrd_toolkit.gui.calib_model import (
    CALIB_PANEL_RESERVE_PX, COMPARE_HINT, COMPARE_ROWS, CP_COLOR,
    KIND_LABELS, MIN_POINTS, MIN_RINGS, PANEL_SCALE, RING_COLOR,
    SLOT_LABELS, SNAP_TOL_DEG,
    SOURCE_IMPROVE_MIN_PX, _add_custom_result, _add_result, _adopt_decision,
    _calib_state, _clear_calib_session, _fill_slot, _geom_to_result_shape,
    _next_name, _result_by_name, _result_dev, _result_to_geom, _slot_label,
    _verdict)
from xrd_toolkit.gui.calib_table import (
    _on_slot_changed, _refresh_table, _sync_slot_combos)
from xrd_toolkit.gui.calib_panel import (
    _CalibSubWindow, _apply_selected_ring, _calib_image,
    _calib_standard_path, _clear_calib_points, _close_calib_panel,
    _geom_px_keys, _load_image, _open_calib_panel, _redraw_calib,
    _redraw_calib_if_open, _undo_calib_point, _warn_rings_off_image)
# 标样辅助（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.calib_calibrant import (
    _adopt_result, _borrow_entry, _current_geom_text, _ensure_current,
    _init_calibrant_for_path, _on_calibrant_changed, _on_ring_count_changed,
    _pixel_ok, _set_pixel_ok, _sync_calibrant_widgets, _update_ring_count,
    _warn_pixel_unchecked)
# 校准表单（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.calib_form import (
    _build_calib_form, _calib_sync, _edit_current, _reset_calib_form)
from xrd_toolkit.gui.config_ops import (
    _delete_config, _import_poni, _save_calib_config, _save_poni,
    _suggest_config_key, _sync_del_config_btn)

from xrd_toolkit import config
from xrd_toolkit.core.processor import find_ring_center, fit_center_from_rings
from xrd_toolkit.gui.panels import _settle
from xrd_toolkit.gui.panel_state import (
    _auto_contrast_values, _collect_geometry, _log, _reload_config_combo)
from xrd_toolkit.gui.tasks import BackgroundTask, user_error_text
from xrd_toolkit.services.calibrants import (
    COMMON_CALIBRANTS, DEFAULT_CALIBRANT, MAX_GUI_RINGS,
    calibrant_label, calibrant_short_label, calibrant_tooltip,
    complete_ring_count, detect_calibrant_from_stem, ring_total)
from xrd_toolkit.services.integrator import (
    calibrate_geometry, refine_geometry_from_points, snap_ring)
from xrd_toolkit.services.ring_metrics import ring_metrics

# 指标 / 尺度扫描 / 后台任务（2026-10-22 从本文件原样搬出；再导出）
from xrd_toolkit.gui.calib_metrics import (
    _attach_metrics, _metrics_note, _metrics_worker, _refresh_current_metrics,
    _stack_custom_metrics)
from xrd_toolkit.gui.calib_scale import (
    _MANUAL_SCALE_PROBE, _SUSPICIOUS_RADIUS_RATIO, _reindex_by_scale,
    _ring_radius, _rings_at_scale, _rings_look_suspicious)
from xrd_toolkit.gui.calib_workers import (
    _auto_calib_worker, _manual_calib_worker, _start_auto_calib,
    _start_manual_calib)

# 结果落地 / 模式进出（2026-10-22 从本文件原样搬出；再导出）
from xrd_toolkit.gui.calib_mode import (
    _default_config_name, _enter_calib, _exit_calib,
    _refresh_config_name_prefill, _restore_dock_width,
    _widen_dock_for_calib)
from xrd_toolkit.gui.calib_result import _on_calib_result
