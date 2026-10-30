"""1D 页的 Williamson–Hall 数字块（2026-10-30）。

单峰拟合给出每根峰的 2θ 与 FWHM 后，W-H 用

    (β cosθ)² = (Kλ/D)² + (4ε)² sin²θ

线性拟合截距/斜率，分离晶粒尺寸 D 与微应变 ε。引擎在
`simulation.microstructure.williamson_hall`，命令行 `scripts/fit_peaks.py`
同一份。

这里的数字块跟着单峰拟合结果自动更新：≥3 根峰就重算，<3 根提示再拟合。
仪器展宽有两种扣法：**常数 FWHM**（°）近似，或直接用精修工作台
[存为仪器基线] 的 LaB₆ zero/U/V/W/X/Y（按角度逐峰算 FWHM，与命令行
`fit_peaks.py --instrument-from` 同口径）。基线波长和当前几何不符时
自动回退常数并写明原因。
"""
import time
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QPushButton, QVBoxLayout)

from xrd_toolkit import paths
from xrd_toolkit.gui.panel_state import _log, _panel_param
from xrd_toolkit.gui.peak_view import _wavelength_for
from xrd_toolkit.gui.tasks import user_error_text

WH_INSTRUMENT_KEY = "W-H 仪器 FWHM (°)"
WH_SOURCE_KEY = "W-H 仪器来源"
WH_SOURCE_CONSTANT = "constant"
WH_SOURCE_LAB6 = "lab6"


def _fit_points(dock):
    centers, fwhms = [], []
    for entry in (getattr(dock, "fit_results", None) or []):
        if not getattr(entry, "wh", True):
            continue
        fit = getattr(entry, "fit", None)
        if fit is None:
            continue
        centers.append(float(fit.center))
        fwhms.append(float(fit.fwhm))
    return np.asarray(centers, dtype=float), np.asarray(fwhms, dtype=float)


def _set_labels(window, result, note="", total=None, inst_note=""):
    d_label = getattr(window, "wh_d_label", None)
    e_label = getattr(window, "wh_e_label", None)
    note_label = getattr(window, "wh_note_label", None)
    if result is None:
        if d_label is not None:
            d_label.setText("D = —")
        if e_label is not None:
            e_label.setText("ε = —")
        if note_label is not None:
            note_label.setText(note or "—")
            note_label.setStyleSheet("color: gray;")
        return
    size = float(result.size_nm)
    strain = float(result.strain)
    if d_label is not None:
        d_label.setText("D = " + ("不可辨识" if not np.isfinite(size)
                                  else f"{size:.1f} nm"))
    if e_label is not None:
        e_label.setText("ε = " + ("不可辨识" if not np.isfinite(strain)
                                  else f"{strain:.2e}"))
    if note_label is not None:
        count = len(result.beta_sample_deg)
        text = (f"{count}/{total} 根参与" if total else f"{count} 根峰")
        if inst_note:
            text += f"；{inst_note}"
        if result.warning:
            text += f"；{result.warning}"
        note_label.setText(text)
        note_label.setStyleSheet(
            "color: #b7791f;" if result.warning else "color: gray;")


def _set_scherrer_label(window, text):
    label = getattr(window, "wh_scherrer_label", None)
    if label is not None:
        label.setText(text)
        label.setStyleSheet("color: gray;")


def _instrument_baseline(window):
    """精修工作台存的 LaB₆ 仪器基线；没开过工作台则看主窗口的副本。"""
    workbench = getattr(window, "refine_editor", None)
    inst = (getattr(workbench, "_instrument", None)
            if workbench is not None else None)
    if inst is None:
        inst = getattr(window, "instrument_baseline", None)
    return inst


def _baseline_state(window, dock):
    """（可用?, 给用户看的说明）：基线是否存在、波长是否和这张图一致。"""
    inst = _instrument_baseline(window)
    if not inst:
        return False, ("未保存 LaB₆ 仪器基线：在 [精修] 工作台精修标样后"
                       "点 [存为仪器基线]")
    wavelength = _wavelength_for(dock) if dock is not None else None
    if wavelength is None:
        return False, "这张图没有几何配置，取不到波长"
    stored = float(inst.get("wavelength", 0.0) or 0.0)
    if abs(stored - float(wavelength)) > 0.01:
        return False, (f"基线来自 λ {stored:.4f} Å，与当前 λ "
                       f"{float(wavelength):.4f} Å 不符")
    return True, "用 LaB₆ 基线按角度算每根峰的仪器 FWHM（U/V/W/X/Y）"


def _baseline_fwhm(window, centers, wavelength):
    """用保存的 LaB₆ 参数算逐峰仪器 FWHM（与 fit_peaks.py 同口径）。"""
    inst = _instrument_baseline(window)
    p = (inst or {}).get("params") or {}
    try:
        from xrd_toolkit.simulation import (
            reflection_sig_gam, sig_gam_to_fwhm_eta)
        sig, gam = reflection_sig_gam(
            np.asarray(centers, dtype=float), float(wavelength),
            u=float(p.get("u", 0.0) or 0.0),
            v=float(p.get("v", 0.0) or 0.0),
            w=float(p.get("w", 0.0) or 0.0),
            x=float(p.get("x", 0.0) or 0.0),
            y=float(p.get("y", 0.0) or 0.0),
            size_um=1e4, mustrain=0.0)
        fwhm, _eta = sig_gam_to_fwhm_eta(sig, gam)
        return np.asarray(fwhm, dtype=float), "仪器 LaB₆ 基线"
    except Exception as err:                       # noqa: BLE001
        return None, f"LaB₆ 基线算不出仪器宽度：{user_error_text(err)}"


def _instrument_fwhm(window, dock, centers, wavelength):
    """逐峰仪器 FWHM（度）与说明；None = 不扣仪器。"""
    const = float(_panel_param(window, dock, WH_INSTRUMENT_KEY, 0.0) or 0.0)
    const_arr = np.full(np.size(centers), const) if const > 0 else None
    source = str(_panel_param(window, dock, WH_SOURCE_KEY, WH_SOURCE_CONSTANT))
    if source == WH_SOURCE_LAB6:
        ok, why = _baseline_state(window, dock)
        if ok:
            fwhm, note = _baseline_fwhm(window, centers, wavelength)
            if fwhm is not None:
                return fwhm, note
            why = note
        if const_arr is not None:
            return const_arr, f"{why}，改用常数 {const:g}°"
        return None, f"{why}，未扣仪器"
    if const_arr is not None:
        return const_arr, f"仪器 {const:g}°"
    return None, ""


def _sync_source_combo(window, dock):
    """LaB₆ 项只在基线存在且波长对得上时可选；否则自动退回常数。"""
    combo = getattr(window, "wh_source_combo", None)
    if combo is None:
        return
    ok, tip = _baseline_state(window, dock)
    model = combo.model()
    item = model.item(1) if hasattr(model, "item") else None
    if item is not None:
        item.setEnabled(ok)
    combo.setItemData(1, tip, Qt.ToolTipRole)
    if not ok and combo.currentData() == WH_SOURCE_LAB6:
        combo.blockSignals(True)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)
        if dock is not None and getattr(dock, "params_snapshot", None) is not None:
            dock.params_snapshot[WH_SOURCE_KEY] = WH_SOURCE_CONSTANT
    spin = (getattr(window, "params", {}) or {}).get(WH_INSTRUMENT_KEY)
    if spin is not None:
        spin.setEnabled(combo.currentData() == WH_SOURCE_CONSTANT)


def _scherrer_text(centers, fwhms, wavelength, instrument):
    """Scherrer 数字块：范围 + 中位 + 参与根数（只统计有限值）。"""
    if np.size(centers) == 0:
        return "Scherrer：—"
    from xrd_toolkit.simulation.microstructure import scherrer_sizes
    sizes = scherrer_sizes(centers, fwhms, wavelength=wavelength, K=0.9,
                           instrument_fwhm_deg=instrument)
    finite = sizes[np.isfinite(sizes)]
    if finite.size == 0:
        return "Scherrer：不可辨识（峰宽不大于仪器宽度）"
    if np.isclose(float(finite.min()), float(finite.max())):
        span = f"{finite[0]:.1f} nm"
    else:
        span = f"{finite.min():.1f}–{finite.max():.1f} nm"
    text = (f"Scherrer（K=0.9）：{span}，"
            f"中位 {np.median(finite):.1f} nm（{finite.size} 根）")
    if instrument is None:
        text += "；未扣仪器，D 为下限"
    missing = int(sizes.size - finite.size)
    if missing:
        text += f"；{missing} 根比仪器还窄"
    return text


def compute_wh(window, dock):
    """用 dock 的单峰拟合结果算 W-H；结果存 dock.wh_result。"""
    if dock is None:
        _set_labels(window, None, "先点开一张 1D 图")
        _set_scherrer_label(window, "Scherrer：—")
        return None
    centers, fwhms = _fit_points(dock)
    if centers.size == 0:
        total = len(getattr(dock, "fit_results", None) or [])
        msg = ("没有勾选参与 W-H 的峰" if total
               else "再拟合几根峰（至少 3 根）")
        dock.wh_result = None
        _set_labels(window, None, msg)
        _set_scherrer_label(window, "Scherrer：—")
        return None
    wavelength = _wavelength_for(dock)
    if wavelength is None:
        dock.wh_result = None
        _set_labels(window, None, "这张图没有几何配置，取不到波长")
        _set_scherrer_label(window, "Scherrer：—（取不到波长）")
        return None
    instrument, inst_note = _instrument_fwhm(window, dock, centers, wavelength)
    _set_scherrer_label(
        window, _scherrer_text(centers, fwhms, wavelength, instrument))
    if centers.size < 3:
        total = len(getattr(dock, "fit_results", None) or [])
        dock.wh_result = None
        dock.wh_instrument = instrument
        _set_labels(window, None, "再拟合几根峰（至少 3 根）")
        return None
    try:
        from xrd_toolkit.simulation.microstructure import williamson_hall
        result = williamson_hall(
            centers, fwhms, wavelength=wavelength,
            instrument_fwhm_deg=instrument)
    except Exception as err:                       # noqa: BLE001
        dock.wh_result = None
        _set_labels(window, None, user_error_text(err))
        return None
    dock.wh_result = result
    dock.wh_instrument = instrument
    _set_labels(window, result, total=len(dock.fit_results or []),
                inst_note=inst_note)
    return result


def refresh_wh(window, dock):
    """拟合结果/焦点/仪器值变了就重算；<3 根勾选峰时清空数字。"""
    _sync_source_combo(window, dock)
    save_btn = getattr(window, "wh_save_btn", None)
    view_btn = getattr(window, "wh_view_btn", None)
    if dock is None or not getattr(dock, "fit_results", None):
        if dock is not None:
            dock.wh_result = None
        _set_labels(window, None, "再拟合几根峰（至少 3 根）")
        _set_scherrer_label(window, "Scherrer：—")
        if save_btn is not None:
            save_btn.setEnabled(False)
        if view_btn is not None:
            view_btn.setEnabled(False)
        return
    result = compute_wh(window, dock)
    if save_btn is not None:
        save_btn.setEnabled(result is not None)
    if view_btn is not None:
        view_btn.setEnabled(result is not None)


def _r2(result):
    """W-H 直线的 R²（散点 → 拟合线的线性度）。"""
    x = np.asarray(result.x, dtype=float)
    y = np.asarray(result.y, dtype=float)
    if x.size < 2:
        return float("nan")
    fit = result.intercept + result.slope * x
    ss_res = float(np.sum((y - fit) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    if ss_tot <= 0:
        return float("nan")
    return 1.0 - ss_res / ss_tot


def _excluded_points(window, dock):
    """未勾选参与 W-H 的峰 → (sin²θ, (βcosθ)²)，画成灰色空心点。"""
    entries = [entry for entry in (getattr(dock, "fit_results", None) or [])
               if not getattr(entry, "wh", True)
               and getattr(entry, "fit", None) is not None]
    if not entries:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)
    centers = np.asarray([float(e.fit.center) for e in entries], dtype=float)
    fwhms = np.asarray([float(e.fit.fwhm) for e in entries], dtype=float)
    wavelength = _wavelength_for(dock)
    instrument = None
    if wavelength is not None:
        instrument, _note = _instrument_fwhm(window, dock, centers, wavelength)
    theta = np.radians(centers / 2.0)
    beta = np.radians(fwhms)
    if instrument is not None:
        beta = np.sqrt(np.maximum(beta * beta - instrument ** 2, 0.0))
    return (np.sin(theta) ** 2, (beta * np.cos(theta)) ** 2)


def show_wh_dialog(window, dock):
    """非模态预览 W-H 图：参与点 / 排除点 / 拟合线 / D、ε、R²。"""
    result = getattr(dock, "wh_result", None)
    if result is None:
        _log(window, "W-H：先拟合至少 3 根峰，并勾选参与 W-H 的峰")
        return None
    old = getattr(window, "_wh_dialog", None)
    if old is not None:
        try:
            old.close()
        except Exception:                          # noqa: BLE001
            pass
    dlg = QDialog(window)
    dlg.setWindowTitle("Williamson–Hall")
    dlg.resize(660, 520)
    lay = QVBoxLayout(dlg)
    fig = Figure(figsize=(5.8, 4.2))
    canvas = FigureCanvasQTAgg(fig)
    lay.addWidget(canvas, 1)
    ax = fig.add_subplot(111)
    x = np.asarray(result.x, dtype=float)
    y = np.asarray(result.y, dtype=float)
    ax.plot(x, y, "o", ms=5, color="#1f5fa8",
            label=f"参与 {x.size} 根")
    ex, ey = _excluded_points(window, dock)
    if ex.size:
        ax.plot(ex, ey, "o", ms=5, mfc="none", color="#999999",
                label=f"排除 {ex.size} 根")
    if x.size:
        xs = np.linspace(float(x.min()), float(x.max()), 100)
        ax.plot(xs, result.intercept + result.slope * xs, "-",
                color="#d0544e", lw=1.3, label="线性拟合")
    ax.set_xlabel(r"$\sin^2\theta$")
    ax.set_ylabel(r"$(\beta\cos\theta)^2$  (rad$^2$)")
    ax.grid(alpha=0.2, linestyle=":")
    ax.legend(fontsize=8)
    r2 = _r2(result)
    size = ("nan" if not np.isfinite(result.size_nm)
            else f"{result.size_nm:.1f}")
    strain = ("nan" if not np.isfinite(result.strain)
              else f"{result.strain:.2e}")
    title = f"D = {size} nm, ε = {strain}"
    if np.isfinite(r2):
        title += f", R² = {r2:.3f}"
    ax.set_title(title, fontsize=10)
    fig.tight_layout()

    row = QHBoxLayout()
    row.addStretch(1)
    save = QPushButton("保存图片…")
    save.setToolTip("把这个 W-H 图存成 PNG/PDF")

    def _save():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        default = paths.default_outputs_dir() / f"williamson_hall_{stamp}.png"
        path, _ = QFileDialog.getSaveFileName(
            dlg, "保存 W-H 图", str(default), "PNG (*.png);;PDF (*.pdf)")
        if path:
            fig.savefig(path, dpi=200)

    save.clicked.connect(_save)
    row.addWidget(save)
    close = QPushButton("关闭")
    close.clicked.connect(dlg.close)
    row.addWidget(close)
    lay.addLayout(row)
    window._wh_dialog = dlg
    dlg.show()
    return dlg


def dock_for_wh(window):
    """当前焦点若是 1D 面板就返回它（给仪器 spin 变更用）。"""
    dock = window.plot_docks.get(window.focus_panel)
    if dock is None or not str(window.focus_panel or "").startswith("1D|"):
        return None
    return dock


def save_wh_plot(window, dock, path=None):
    """把 W-H 图存成 PNG/PDF；path 给定时不弹框（测试/脚本用）。"""
    result = getattr(dock, "wh_result", None)
    if result is None:
        _log(window, "W-H：先拟合至少 3 根峰")
        return None
    if path is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        default = paths.default_outputs_dir() / f"williamson_hall_{stamp}.png"
        path, _ = QFileDialog.getSaveFileName(
            window, "保存 W-H 图", str(default),
            "PNG (*.png);;PDF (*.pdf)")
        if not path:
            return None
    try:
        from xrd_toolkit.simulation import plot_williamson_hall
        title = dock.windowTitle() or "Williamson–Hall"
        plot_williamson_hall(result, str(path), title=title)
    except Exception as err:                       # noqa: BLE001
        _log(window, f"W-H 图保存失败：{user_error_text(err)}")
        return None
    _log(window, f"W-H 图已保存：{path}")
    return Path(path)
