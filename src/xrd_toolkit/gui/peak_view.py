"""1D 面板的寻峰标记与峰表（2026-10-23）。

界面里的"寻峰"只做**显示**：按该面板快照里的参数调用
`services.peak_finding.find_peaks_1d`，在曲线上画峰位短线和 2θ 标签，
并把结果填进「1D」页下方的峰表。不写产物、不改曲线、不重算积分。

口径：
  * 用**画出来的那条曲线**（扣背景/平滑/裁剪之后），和眼睛看到的一致；
  * 参数按面板各记各的（快照键 `寻峰*` / `显示峰位`）；
  * 裁剪留下的断点（NaN）按连续段分别寻峰，不会在断点处造出假峰；
  * 峰表双击一行 = 把视图缩放到那根峰（写回该面板的"视图 2θ"）。
"""
from math import ceil

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QTableWidgetItem

from xrd_toolkit.gui.state.base import _panel_param, _param_box_set
from xrd_toolkit.gui.state.config import _AUX_GID_PREFIX
from xrd_toolkit.gui.state.processing import _proc_curve
from xrd_toolkit.services.peak_finding import find_peaks_1d

PEAK_COLOR = "#E69F00"          # Okabe–Ito 橙：与曲线蓝、背景绿都分得开
PEAK_KEYS = ("寻峰显著度", "寻峰最小峰距 (°)", "寻峰最小 FWHM (°)",
             "寻峰最多根数", "显示峰位")
TABLE_COLUMNS = ("2θ (°)", "FWHM (°)", "高度")


def _finite_segments(tth, intensity):
    """把裁剪造成的 NaN 断点拆成连续段（每段 ≥10 点才寻峰）。"""
    t = np.asarray(tth, dtype=float)
    y = np.asarray(intensity, dtype=float)
    ok = np.isfinite(y)
    if ok.all():
        if y.size >= 10:
            yield t, y
        return
    start = None
    for i, good in enumerate(ok):
        if good and start is None:
            start = i
        elif not good and start is not None:
            if i - start >= 10:
                yield t[start:i], y[start:i]
            start = None
    if start is not None and y.size - start >= 10:
        yield t[start:], y[start:]


def _settings(window, dock):
    """该面板的寻峰参数（快照优先，见 _panel_param）。"""
    factor = float(_panel_param(window, dock, "寻峰显著度", 5.0))
    distance = float(_panel_param(window, dock, "寻峰最小峰距 (°)", 0.008))
    min_fwhm = float(_panel_param(window, dock, "寻峰最小 FWHM (°)", 0.0))
    max_peaks = int(_panel_param(window, dock, "寻峰最多根数", 20))
    return (factor, distance, None if min_fwhm <= 0 else min_fwhm,
            max(1, max_peaks))


def detect_peaks_for_dock(window, dock, tth=None, intensity=None):
    """在面板曲线上找峰；`显示峰位` 关着时返回空列表。

    tth/intensity 省略时用面板缓存的数据并跑一遍处理链（扣背景/
    平滑/裁剪）；绘图路径已经把处理后的曲线递进来，避免重复计算。
    """
    if not _panel_param(window, dock, "显示峰位", False):
        return []
    if tth is None or intensity is None:
        tth = getattr(dock, "last_tth", None)
        intensity = getattr(dock, "last_intensity", None)
        if tth is None or intensity is None:
            return []
        tth, intensity, _base = _proc_curve(
            window, dock, getattr(dock, "panel_file", None), tth, intensity)
    factor, distance, min_fwhm, max_peaks = _settings(window, dock)
    found = []
    for seg_t, seg_y in _finite_segments(tth, intensity):
        found.extend(find_peaks_1d(
            seg_t, seg_y, prominence_factor=factor,
            min_distance_deg=distance, min_fwhm_deg=min_fwhm,
            max_peaks=max_peaks))
    if len(found) > max_peaks:
        found = sorted(found, key=lambda p: -p.height)[:max_peaks]
    found.sort(key=lambda p: p.center)
    return found


def draw_peak_markers(window, dock, ax, tth, intensity):
    """在 1D 轴上画峰位竖线与 2θ 标签，返回检出的峰。

    在坐标范围（含纵轴自动）定好之后调用；`ax.clear()` 会清掉旧标记，
    所以每次重画都重建。峰太多时只标前 ~15 个标签，短线仍全部画。
    """
    peaks = detect_peaks_for_dock(window, dock, tth, intensity)
    dock.detected_peaks = peaks
    if not peaks:
        return peaks
    y = np.asarray(intensity, dtype=float)
    t = np.asarray(tth, dtype=float)
    lo, hi = ax.get_ylim()
    log = ax.get_yscale() == "log"
    span = hi - lo if hi > lo else 1.0
    stride = max(1, ceil(len(peaks) / 15))
    for k, peak in enumerate(peaks):
        i = int(np.clip(np.searchsorted(t, peak.center),
                        0, t.size - 1))
        yy = float(y[i]) if np.isfinite(y[i]) else float(peak.height)
        if not np.isfinite(yy) or (log and yy <= 0):
            continue
        top = yy * 1.25 if log else yy + 0.04 * span
        line, = ax.plot([peak.center, peak.center], [yy, top],
                        color=PEAK_COLOR, lw=1.0, zorder=5,
                        label="_peak_marker")
        # 标成辅助线：重画的 Customize 保护按"数据线"顺序回填样式，
        # 把标记线算进去会让曲线线型错位（同背景辅助线，见 _is_aux_line）
        line.set_gid(_AUX_GID_PREFIX + "peak_marker")
        if k % stride:
            continue
        ax.text(peak.center, top, f"{peak.center:.3f}", rotation=90,
                fontsize=7, color=PEAK_COLOR, ha="center", va="bottom",
                clip_on=True, zorder=6)
    return peaks


def fill_peak_table(window, dock, peaks):
    """把峰填进「1D」页的峰表；没有峰时整块隐藏。"""
    table = getattr(window, "peak_table", None)
    box = getattr(window, "peak_box", None)
    if table is None or box is None:
        return
    table.setRowCount(0)
    if not peaks:
        box.setVisible(False)
        return
    table.setRowCount(len(peaks))
    for row, peak in enumerate(peaks):
        for col, text in enumerate((
                f"{peak.center:.4f}", f"{peak.fwhm:.4f}",
                f"{peak.height:.0f}")):
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if col == 0:
                item.setData(Qt.UserRole, (peak.center, peak.fwhm))
            table.setItem(row, col, item)
    box.setVisible(True)


def refresh_peak_table(window):
    """焦点切换/显隐变化后刷新峰表（只认 1D 面板）。"""
    dock = window.plot_docks.get(window.focus_panel)
    if dock is None or not str(window.focus_panel).startswith("1D|"):
        fill_peak_table(window, None, [])
        return
    peaks = detect_peaks_for_dock(window, dock)
    dock.detected_peaks = peaks
    fill_peak_table(window, dock, peaks)


def zoom_to_peak(window, row):
    """双击峰表某行：把该面板的视图缩到这个峰附近。"""
    table = getattr(window, "peak_table", None)
    dock = window.plot_docks.get(window.focus_panel)
    if table is None or dock is None:
        return
    item = table.item(row, 0)
    if item is None:
        return
    data = item.data(Qt.UserRole)
    if not data:
        return
    center, fwhm = (float(v) for v in data)
    half = max(10.0 * fwhm, 0.05)
    lo, hi = center - half, center + half
    snap = dict(getattr(dock, "params_snapshot", None) or {})
    snap["视图 2θ 下限 (°)"] = lo
    snap["视图 2θ 上限 (°)"] = hi
    dock.params_snapshot = snap
    _param_box_set(window, "视图 2θ 下限 (°)", lo)
    _param_box_set(window, "视图 2θ 上限 (°)", hi)
    from xrd_toolkit.gui.plot.refresh import _redraw_panel   # 破循环
    _redraw_panel(window, window.focus_panel)


def install_peak_view(window):
    """把峰表接到焦点切换上（在参数坞建好之后调用一次）。"""
    prev = getattr(window, "_on_focus_changed", None)

    def hook(previous, current):
        if prev is not None:
            prev(previous, current)
        refresh_peak_table(window)

    window._on_focus_changed = hook
