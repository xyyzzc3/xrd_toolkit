"""1D 面板的峰形拟合与结果表（2026-10-30）。

和 `peak_view` 分工：寻峰只找峰位/峰高（显示），这里对峰表里的峰做
**单峰峰形拟合**（局部背景 + 一个峰），结果填进 1D 页的「拟合结果」表，
图上叠拟合曲线（默认只叠选中峰，可勾「叠加全部拟合」）。

口径：
  * 用**画出来的那条曲线**（扣背景/平滑/裁剪之后），与寻峰同源；
  * 参数（线型/半窗/背景/叠加全部）按面板快照各记各的；
  * 不写产物、不改曲线、不重算积分；拟合结果挂在 `dock.fit_results`；
  * 曲线变了（重新积分/处理参数变）→ 旧结果标「曲线已变，请重新拟合」，
    不把旧拟合叠到新曲线上。
"""
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication

from xrd_toolkit.gui.state.base import _panel_param
from xrd_toolkit.gui.state.config import _AUX_GID_PREFIX
from xrd_toolkit.gui.state.processing import _proc_curve
from xrd_toolkit.gui.panel_state import _log
from xrd_toolkit.gui.wh_view import refresh_wh
from xrd_toolkit.simulation.peakfit import (
    fit_single_peak, gaussian, lorentzian, pseudo_voigt, split_pearson7)

FIT_COLOR = "#CC79A7"          # Okabe–Ito 紫红：与曲线蓝、峰标橙都分得开
FIT_KEYS = ("拟合线型", "拟合半窗 (°)", "拟合背景", "拟合全部叠加")
# 5 列挤在 320 px 里，带单位的表头会被裁（实测 "FWHM (°)" 只剩 "WHM"）；
# 单位放进表头 tooltip，列名只留短名。
FIT_COLUMNS = ("W-H", "2θ", "FWHM", "面积", "Rwp")

SHAPE_LABELS = {
    "gaussian": "高斯",
    "lorentzian": "洛伦兹",
    "pseudovoigt": "伪 Voigt",
    "split_pearson7": "分裂 Pearson VII",
}
BACKGROUND_LABELS = {"none": "无", "linear": "线性", "quadratic": "二次"}


@dataclass
class FitEntry:
    """一根峰的拟合结果。

    center0/half_window 是**拟合窗口**的定义（拟合完成后峰心可能略微
    移动，但窗口和背景数组仍按初始中心算），画叠加曲线时要用它们。
    """

    center0: float
    half_window: float
    fit: object                    # SinglePeakFit
    wh: bool = True                # 勾选参与 Williamson–Hall（默认参与）
    # 拟合时用的局部背景（none/linear/quadratic）；报告的方法表要写清楚，
    # 不能拿"当前面板设置"顶替（拟合完改过设置就串了）
    background: str = "linear"


def _settings(window, dock):
    shape = str(_panel_param(window, dock, "拟合线型", "pseudovoigt"))
    half = float(_panel_param(window, dock, "拟合半窗 (°)", 0.12))
    bg = str(_panel_param(window, dock, "拟合背景", "linear"))
    return shape, half, bg


def _curve_sig(tth, intensity):
    """曲线指纹：长度 + 强度和（处理链一改，和就变）。"""
    y = np.asarray(intensity, dtype=float)
    return (int(np.size(tth)),
            float(np.nansum(np.nan_to_num(y, nan=0.0))))


def curve_sig_for(dock):
    """面板当前（处理后）曲线的指纹；没有数据返回 None。"""
    tth = getattr(dock, "last_tth", None)
    y = getattr(dock, "last_intensity", None)
    if tth is None or y is None:
        return None
    return _curve_sig(tth, y)


def fit_series(tth, intensity, centers, *, shape="pseudovoigt",
               half_window=0.12, background="linear"):
    """纯拟合：对一组峰位做单峰拟合，返回 (entries, failures)。"""
    entries, failures = [], []
    for center in centers:
        try:
            fit = fit_single_peak(
                tth, intensity, float(center), shape=shape,
                half_window=float(half_window), background=background)
        except Exception as err:                   # noqa: BLE001
            failures.append((float(center), str(err)))
            continue
        entries.append(FitEntry(float(center), float(half_window), fit,
                                background=background))
    return entries, failures


def selected_peak_rows(window):
    """峰表当前选中的行号（Ctrl/Cmd 多选；没有选中时退回当前行）。"""
    table = getattr(window, "peak_table", None)
    if table is None:
        return []
    model = table.selectionModel()
    rows = (sorted({i.row() for i in model.selectedRows()})
            if model is not None else [])
    if not rows:
        row = table.currentRow()
        if row >= 0:
            rows = [row]
    return rows


def select_peak_centers(window, centers):
    """拟合后重新选中这些峰（峰表重填会清掉选择，按钮会变灰）。"""
    table = getattr(window, "peak_table", None)
    if table is None:
        return
    model = table.selectionModel()
    if model is None:
        return
    wanted = {round(float(c), 6) for c in centers}
    table.clearSelection()
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        data = item.data(Qt.UserRole) if item is not None else None
        if data and round(float(data[0]), 6) in wanted:
            model.select(table.model().index(row, 0),
                         QItemSelectionModel.Select | QItemSelectionModel.Rows)


def _current_curve(window, dock):
    tth = getattr(dock, "last_tth", None)
    y = getattr(dock, "last_intensity", None)
    if tth is None or y is None:
        return None, None
    tth, y, _base = _proc_curve(
        window, dock, getattr(dock, "panel_file", None), tth, y)
    return tth, y


def _dock_for_fit(window):
    dock = window.plot_docks.get(window.focus_panel)
    if dock is None or not str(window.focus_panel or "").startswith("1D|"):
        _log(window, "峰形拟合：先在文件栏双击/点 [出图] 开一张 1D 图")
        return None
    if getattr(dock, "last_tth", None) is None:
        _log(window, "峰形拟合：这张 1D 图还没算完")
        return None
    return dock


def _refresh(window):
    # 延迟导入：plot.refresh 在模块层 import 本文件的 FIT_KEYS，别绕成环
    from xrd_toolkit.gui.plot.refresh import _refresh_1d_display
    _refresh_1d_display(window)


def fit_all_peaks(window):
    """兼容入口：全选峰表后走“拟合选中”（按钮已合并）。"""
    table = getattr(window, "peak_table", None)
    if table is not None and table.rowCount():
        table.selectAll()
    return fit_selected_peaks(window)


def fit_selected_peaks(window):
    """[拟合选中]：对峰表里选中的每一根做单峰拟合（Ctrl/Cmd 多选）。"""
    dock = _dock_for_fit(window)
    if dock is None:
        return 0
    rows = selected_peak_rows(window)
    if not rows:
        _log(window, "峰形拟合：先在峰表里点选一行（Ctrl/Cmd 可多选）")
        return 0
    table = window.peak_table
    centers = []
    for row in rows:
        item = table.item(row, 0)
        data = item.data(Qt.UserRole) if item is not None else None
        if data:
            centers.append(float(data[0]))
        elif item is not None:
            try:
                centers.append(float(item.text()))
            except ValueError:
                pass
    if not centers:
        return 0
    tth, y = _current_curve(window, dock)
    if tth is None:
        return 0
    shape, half, bg = _settings(window, dock)
    entries, failures = fit_series(
        tth, y, centers, shape=shape, half_window=half, background=bg)
    # 按峰心合并：同一根重拟覆盖旧结果，其它保留
    old = {round(e.center0, 6): e
           for e in (getattr(dock, "fit_results", None) or [])}
    for entry in entries:
        old[round(entry.center0, 6)] = entry
    dock.fit_results = sorted(old.values(), key=lambda e: e.center0)
    dock.fit_sig = _curve_sig(tth, y)
    fill_fit_table(
        window, dock,
        select_center=(entries[0].center0 if entries else None), switch=True)
    _refresh(window)
    select_peak_centers(window, centers)
    _log(window, f"峰形拟合：选中 {len(centers)} 根，"
                 f"成功 {len(entries)} / 失败 {len(failures)}"
                 f"（{SHAPE_LABELS.get(shape, shape)}，半窗 {half:g}°）")
    for center, err in failures:
        _log(window, f"  峰 {center:.4f}° 拟合失败：{err}")
    return len(entries)


# 兼容旧名字：现在“选中”可以是多行
fit_selected_peak = fit_selected_peaks


def delete_fit_rows(window):
    """删除拟合结果表里选中的行（右键菜单）。"""
    dock = window.plot_docks.get(window.focus_panel)
    if dock is None or not str(window.focus_panel or "").startswith("1D|"):
        return 0
    table = getattr(window, "fit_table", None)
    entries = list(getattr(dock, "fit_results", None) or [])
    if table is None or not entries:
        return 0
    model = table.selectionModel()
    rows = (sorted({i.row() for i in model.selectedRows()})
            if model is not None else [])
    if not rows:
        row = table.currentRow()
        if row >= 0:
            rows = [row]
    if not rows:
        return 0
    drop = set(rows)
    keep = [e for i, e in enumerate(entries) if i not in drop]
    removed = len(entries) - len(keep)
    dock.fit_results = keep or None
    if not keep:
        dock.fit_sig = None
        dock.wh_result = None
    fill_fit_table(window, dock)
    _refresh(window)
    _log(window, f"峰形拟合：已删除 {removed} 根结果")
    return removed


def clear_fit_results(window):
    """参数一改，旧的拟合结果就不作数了（表清空、叠加消失）。"""
    dock = window.plot_docks.get(window.focus_panel)
    if dock is None:
        return
    dock.fit_results = None
    dock.fit_sig = None
    dock.wh_result = None
    fill_fit_table(window, dock)


def _eta_text(fit):
    if fit.shape == "pseudovoigt":
        return f"{fit.eta:.2f}"
    if fit.shape == "split_pearson7":
        return f"{fit.m:.2f}"
    return "—"


def fill_fit_table(window, dock, *, select_center=None, switch=False):
    """把 dock.fit_results 填进「拟合结果」标签页；没有结果时该页置灰。

    switch=True 只给"用户刚点了拟合"的两条路：切到拟合结果页，让人
    立刻看到表。焦点切换时的刷新不切页，否则每点一张图就被拽走。
    """
    table = getattr(window, "fit_table", None)
    tabs = getattr(window, "peak_tabs", None)
    if table is None:
        return
    entries = (list(getattr(dock, "fit_results", None) or [])
               if dock is not None else [])
    table.blockSignals(True)
    table.setRowCount(0)
    if not entries:
        table.setVisible(False)
        if tabs is not None:
            tabs.setTabEnabled(1, False)
            tabs.setTabText(1, "拟合结果")
            if tabs.currentIndex() == 1:
                tabs.setCurrentIndex(0)
        table.blockSignals(False)
        refresh_wh(window, dock)
        return
    table.setVisible(True)
    if tabs is not None:
        tabs.setTabEnabled(1, True)
        tabs.setTabText(1, f"拟合结果 · {len(entries)} 根")
        if switch:
            tabs.setCurrentIndex(1)
    table.setRowCount(len(entries))
    from PySide6.QtWidgets import QTableWidgetItem
    pick = 0
    for row, entry in enumerate(entries):
        fit = entry.fit
        check = QTableWidgetItem("")
        check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        check.setCheckState(Qt.Checked if entry.wh else Qt.Unchecked)
        check.setData(Qt.UserRole, entry.center0)       # _selected_center 用
        check.setData(Qt.UserRole + 1, entry)           # 勾选回写用
        check.setToolTip(
            f"勾选：参与 W-H\n初始 2θ {entry.center0:.4f}°\n"
            f"半窗 {entry.half_window:g}°\n"
            f"线型 {SHAPE_LABELS.get(fit.shape, fit.shape)}"
            f"（{_eta_text(fit)}）")
        table.setItem(row, 0, check)
        values = ((f"{fit.center:.4f}", fit.center),
                  (f"{fit.fwhm:.4f}", fit.fwhm),
                  (f"{fit.area:.1f}", fit.area),
                  (f"{fit.rwp:.2f}", fit.rwp))
        for col, (text, _value) in enumerate(values, start=1):
            item = table.item(row, col)
            if item is None:
                item = QTableWidgetItem()
                table.setItem(row, col, item)
            item.setText(text)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if select_center is not None and abs(entry.center0 - select_center) < 1e-6:
            pick = row
    table.selectRow(pick)
    table.blockSignals(False)
    refresh_wh(window, dock)


def _selected_center(window):
    """当前要叠哪根拟合：拟合表选中行优先，其次峰表选中行。"""
    fit_table = getattr(window, "fit_table", None)
    if fit_table is not None and fit_table.currentRow() >= 0:
        item = fit_table.item(fit_table.currentRow(), 0)
        if item is not None:
            data = item.data(Qt.UserRole)
            if data is not None:
                return float(data)
    peak_table = getattr(window, "peak_table", None)
    if peak_table is not None and peak_table.currentRow() >= 0:
        item = peak_table.item(peak_table.currentRow(), 0)
        if item is not None:
            data = item.data(Qt.UserRole)
            if data:
                return float(data[0])
    return None


def _shape_values(fit, x):
    p = fit.params
    if fit.shape == "gaussian":
        return gaussian(x, p["center"], p["fwhm"])
    if fit.shape == "lorentzian":
        return lorentzian(x, p["center"], p["fwhm"])
    if fit.shape == "pseudovoigt":
        return pseudo_voigt(x, p["center"], p["fwhm"], p["eta"])
    if fit.shape == "split_pearson7":
        return split_pearson7(x, p["center"], p["fwhm_left"],
                              p["fwhm_right"], p["m"])
    return np.zeros_like(x)


def draw_fit_overlays(window, dock, ax, tth, intensity):
    """在 1D 轴上叠拟合曲线（默认选中峰；勾「叠加全部拟合」= 全部）。"""
    entries = list(getattr(dock, "fit_results", None) or [])
    if not entries:
        return 0
    sig = _curve_sig(tth, intensity)
    if sig != getattr(dock, "fit_sig", None):
        # 曲线变了（重新积分/换处理参数/换裁剪）：旧结果直接作废，
        # 别在表里留着一组对不上当前曲线的数字（W-H 也跟着清）。
        had = bool(entries)
        dock.fit_results = None
        dock.fit_sig = None
        dock.wh_result = None
        if window.plot_docks.get(window.focus_panel) is dock:
            fill_fit_table(window, dock)
        if had:
            _log(window, "曲线已变：旧的峰形拟合结果已清空")
        return 0
    show_all = bool(_panel_param(window, dock, "拟合全部叠加", False))
    selected = None if show_all else _selected_center(window)
    if not show_all and selected is None:
        return 0
    drawn = 0
    tth = np.asarray(tth, dtype=float)
    for entry in entries:
        if not show_all and abs(entry.center0 - selected) > 1e-6:
            continue
        m = ((tth > entry.center0 - entry.half_window)
             & (tth < entry.center0 + entry.half_window))
        x = tth[m]
        bg = np.asarray(entry.fit.background, dtype=float)
        if x.size != bg.size:
            continue
        model = bg + entry.fit.height * _shape_values(entry.fit, x)
        line, = ax.plot(x, model, color=FIT_COLOR, lw=1.2, ls="--",
                        zorder=5)
        line.set_gid(_AUX_GID_PREFIX + "fit_curve")
        drawn += 1
    return drawn


def fit_table_tsv(window, selected_only=False):
    """拟合结果表 → TSV（表头 + 数据）；没有行返回空串。"""
    table = getattr(window, "fit_table", None)
    if table is None or table.rowCount() == 0:
        return ""
    if selected_only:
        rows = sorted({i for rng in table.selectedRanges()
                       for i in range(rng.topRow(), rng.bottomRow() + 1)})
    else:
        rows = list(range(table.rowCount()))
    if not rows:
        return ""
    lines = ["\t".join(table.horizontalHeaderItem(c).text()
                       for c in range(table.columnCount()))]
    for row in rows:
        values = []
        for c in range(table.columnCount()):
            item = table.item(row, c)
            if c == 0:
                values.append("✓" if item is not None
                              and item.checkState() == Qt.Checked else "")
            else:
                values.append(item.text() if item is not None else "")
        lines.append("\t".join(values))
    return "\n".join(lines)


def copy_fit_table(window, selected_only=False):
    """复制拟合结果表；返回复制的数据行数（0 = 没复制）。"""
    text = fit_table_tsv(window, selected_only)
    if not text:
        return 0
    QApplication.clipboard().setText(text)
    return max(0, len(text.splitlines()) - 1)


def install_fit_view(window):
    """焦点切换时刷新拟合结果表（接在峰表钩子之后）。"""
    prev = getattr(window, "_on_focus_changed", None)

    def hook(previous, current):
        if prev is not None:
            prev(previous, current)
        dock = window.plot_docks.get(window.focus_panel)
        if dock is None or not str(window.focus_panel or "").startswith("1D|"):
            fill_fit_table(window, None)
        else:
            fill_fit_table(window, dock)

    window._on_focus_changed = hook
