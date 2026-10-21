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

# ══ 结果 / Δ / 保存为配置 ════════════════════════════════════
def _on_calib_result(window: QMainWindow, kind: str, result: dict) -> None:
    """一次校准结果落地（主线程）：进累积列表 → 判要不要换当前配置 → 填槽。

    顺序有讲究：先把结果挂进列表（这样日志与槽都有名字可用），再判
    "拟合得好不好"（_adopt_decision），采纳就换当前配置并重画青线，最后
    按 A→B 轮换填没被钉住的槽。
    """
    state = _calib_state(window)
    got_cal = result.get("calibrant")
    if got_cal and got_cal != state.get("calibrant"):
        # 任务在飞时换过标样：这是一份旧标样的结果，进列表只会误导
        _log(window, f"{KIND_LABELS.get(kind, kind)}结果作废：标样已从 "
                     f"{calibrant_short_label(got_cal)} 切到 "
                     f"{calibrant_short_label(state.get('calibrant'))}")
        return
    name = _add_result(state, kind, result)
    label = KIND_LABELS.get(kind, kind)
    if result.get("reindex_note"):
        # 判环换过一套（尺度扫描赢的）：先写这句，下面的结果才读得懂
        _log(window, result["reindex_note"])
    cal_txt = calibrant_short_label(got_cal or state.get("calibrant")
                                    or DEFAULT_CALIBRANT)
    rings_txt = (f"{result['n_rings']} 环"
                 if result.get("n_rings") else "")
    _log(window, f"{label}完成（{name}，{cal_txt}{'，' + rings_txt if rings_txt else ''}）："
                 f"距离 {result['dist_m'] * 1000:.2f} mm，"
                 f"PONI ({result['poni1_px']:.2f}, {result['poni2_px']:.2f}) px，"
                 f"残差 {result['residual_deg']:.4f}°"
                 f"{_metrics_note(result)}")
    take, note = _adopt_decision(state, name)
    if take:
        # 一律采纳（用户 2026-09-30："只要是用户操作的……都填入当前，
        # 让用户看到变化"）——青环立刻跟着动，说明里写明好了/差了多少
        _adopt_result(window, name, why="自动采纳")
    _log(window, note)
    _log(window, _fill_slot(state, name))
    _calib_sync(window)
    if kind == "manual" and state.get("manual_n", 99) < 6:
        # 点数少时最小二乘对单点点击误差敏感，残差小也不代表可信
        _log(window, "提示：点数较少（<6）时手动结果可能不稳，建议多点"
                     "几个环上的点再跑一次，以环位偏差判断可信度")

# ══ 几何配置条目：加载 .poni / 保存 .poni / 删除 ═══════════════
# （从 app.py 搬来：几何配置的增删改查归校准页——分析页只读，下拉框
# 选条目；按钮的工作对象仍是"分析页当前选中的那一条"。）

# ══ 模式进出（app._on_mode 调用）══════════════════════════════
def _default_config_name(window: QMainWindow) -> str:
    """「条目名称」的预填值：标样文件名去掉末尾的「-数字」段。

    用户 2026-10-07："预填文件名的不含-后的长数字部分"——lab6-00024.tif
    → lab6；LMFP_1_atten0-00029.tif → LMFP_1_atten0。没有标样（只在
    校准页里借条目 / 手输几何）时退回按当前条目递推（lmfp1_lab6 →
    lmfp2_lab6，见 config_ops._suggest_config_key）。
    """
    path = getattr(window, "calib_path", None)
    if path:
        stem = Path(path).stem
        cleaned = re.sub(r"-\d+$", "", stem)
        return cleaned or stem
    return _suggest_config_key(config.DEFAULT_CONFIG)

def _refresh_config_name_prefill(window: QMainWindow) -> None:
    """进入校准时把「条目名称」按当前标样文件名重填。

    只覆盖"还挂着上一次预填值"的框（或空框）——用户自己改过的名字不动。
    """
    box = getattr(window, "calib_name_edit", None)
    if box is None:
        return
    if box.text().strip() not in ("", getattr(window, "_calib_name_auto", None)):
        return   # 用户改过：不动
    box.setText(_default_config_name(window))
    window._calib_name_auto = box.text()

def _enter_calib(window: QMainWindow) -> None:
    """进入校准模式（[校准] 按下）：开校准面板 + 按校准页内容拉宽参数坞。

    没勾文件只记日志提示（不崩）——用户勾好文件后点 [定位束心并精修]
    也能开面板。宽度只在够得着时拉：给绘图区留 CALIB_PANEL_RESERVE_PX
    （点环选点是在图上做的，坞太宽就没法点了）。
    """
    # 先让参数坞露出来再量宽：开局它是收起的（_clear_entrance），
    # 隐藏时量到的是旧值/默认值 → 拉宽和"退出还原"都会拿假数字
    # （[校准] 的 toggled 先于 clicked 触发，所以不能指望入口那边先
    # 露坞——2026-09-26 实测：以前量出来 327 px，远没到该有的宽度）
    window.param_dock.setVisible(True)
    # 进校准露坞同理记意图（见 app._switch_entrance 的同款记账）：不记的话
    # 意图还停在开局的 False，窗口状态一变（全屏/最小化恢复）就会被
    # _resync_docks 收回去
    if hasattr(window, "_dock_intent"):
        window._dock_intent["参数"] = True
    # 出口按钮（坞顶那一行）：本模式的显式退出口，进来就亮出来。
    # 放在"没勾文件"的提前返回之前——没文件也要能退出去
    window.calib_exit_btn.setVisible(True)
    path = _calib_standard_path(window)
    if path is None:
        _log(window, "请先在文件列表勾选标样文件")
        return
    _open_calib_panel(window, path)
    _refresh_config_name_prefill(window)   # 「条目名称」跟着这个标样重填
    _widen_dock_for_calib(window)

def _exit_calib(window: QMainWindow) -> None:
    """退出校准模式：关校准面板（关闭即遗忘）+ 参数坞宽度还原。"""
    window.calib_exit_btn.setVisible(False)
    _close_calib_panel(window)
    _restore_dock_width(window)

def _widen_dock_for_calib(window: QMainWindow) -> None:
    """校准模式下把参数坞拉宽到"放下校准页所有内容"（不超过窗口上限）。"""
    dock = getattr(window, "param_dock", None)
    page = getattr(window, "calib_scroll", None)
    if dock is None or page is None:
        return
    if getattr(window, "_dock_w_before", None) is None:
        # 记住分析模式的宽度。参数坞开局是收起的（app._clear_entrance，
        # 用户 2026-09-25 定），点 [校准] 时才刚由隐藏转可见、还没走
        # 布局，此刻 width() 是旧值/默认值 → 先手动走一遍布局再量，
        # 否则"退出校准还原宽度"会还原成一个假数字
        lay = window.layout()
        if lay is not None:
            lay.activate()
        window._dock_w_before = dock.width()
    need = page.widget().sizeHint().width() + 24  # 滚动区边框/条余量
    limit = max(360, window.width() - CALIB_PANEL_RESERVE_PX)
    target = int(min(need, limit))
    if dock.isFloating():
        dock.resize(target, dock.height())
    else:
        window.resizeDocks([dock], [target], Qt.Horizontal)
    if need > limit:
        _log(window, f"校准页需要 {need} px，但为保住绘图区（点环用）只给到 "
                     f"{target} px：窄窗口下校准页里可以横向滚动")

def _restore_dock_width(window: QMainWindow) -> None:
    """出校准模式：参数坞宽度还原成进之前那一条。"""
    was = getattr(window, "_dock_w_before", None)
    dock = getattr(window, "param_dock", None)
    if was is None or dock is None:
        return
    if dock.isFloating():
        dock.resize(was, dock.height())
    else:
        window.resizeDocks([dock], [was], Qt.Horizontal)
    window._dock_w_before = None
