"""1D 页的 Williamson–Hall 数字块（2026-10-30）。

单峰拟合给出每根峰的 2θ 与 FWHM 后，W-H 用

    (β cosθ)² = (Kλ/D)² + (4ε)² sin²θ

线性拟合截距/斜率，分离晶粒尺寸 D 与微应变 ε。引擎在
`simulation.microstructure.williamson_hall`，命令行 `scripts/fit_peaks.py`
同一份。

这里的数字块跟着单峰拟合结果自动更新：≥3 根峰就重算，<3 根提示再拟合。
仪器展宽用一个**常数 FWHM**（°）近似扣除；要按角度变化的仪器曲线，
用命令行的 LaB₆ 标样那条路。
"""
import time
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QPushButton, QVBoxLayout)

from xrd_toolkit import paths
from xrd_toolkit.gui.panel_state import _log, _panel_param
from xrd_toolkit.gui.peak_view import _wavelength_for
from xrd_toolkit.gui.tasks import user_error_text

WH_INSTRUMENT_KEY = "W-H 仪器 FWHM (°)"


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


def _set_labels(window, result, note="", total=None):
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
        if result.warning:
            text += f"；{result.warning}"
        note_label.setText(text)
        note_label.setStyleSheet(
            "color: #b7791f;" if result.warning else "color: gray;")


def compute_wh(window, dock):
    """用 dock 的单峰拟合结果算 W-H；结果存 dock.wh_result。"""
    if dock is None:
        _set_labels(window, None, "先点开一张 1D 图")
        return None
    centers, fwhms = _fit_points(dock)
    if centers.size < 3:
        total = len(getattr(dock, "fit_results", None) or [])
        msg = ("没有勾选参与 W-H 的峰" if total and centers.size == 0
               else "再拟合几根峰（至少 3 根）")
        dock.wh_result = None
        _set_labels(window, None, msg)
        return None
    wavelength = _wavelength_for(dock)
    if wavelength is None:
        dock.wh_result = None
        _set_labels(window, None, "这张图没有几何配置，取不到波长")
        return None
    instrument = float(_panel_param(window, dock, WH_INSTRUMENT_KEY, 0.0) or 0.0)
    instrument_fwhm = (np.full(centers.size, instrument)
                       if instrument > 0 else None)
    try:
        from xrd_toolkit.simulation.microstructure import williamson_hall
        result = williamson_hall(
            centers, fwhms, wavelength=wavelength,
            instrument_fwhm_deg=instrument_fwhm)
    except Exception as err:                       # noqa: BLE001
        dock.wh_result = None
        _set_labels(window, None, user_error_text(err))
        return None
    dock.wh_result = result
    dock.wh_instrument = instrument
    _set_labels(window, result, total=len(dock.fit_results or []))
    return result


def refresh_wh(window, dock):
    """拟合结果/焦点/仪器值变了就重算；<3 根勾选峰时清空数字。"""
    save_btn = getattr(window, "wh_save_btn", None)
    view_btn = getattr(window, "wh_view_btn", None)
    if dock is None or not getattr(dock, "fit_results", None):
        if dock is not None:
            dock.wh_result = None
        _set_labels(window, None, "再拟合几根峰（至少 3 根）")
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


def _excluded_points(dock, instrument):
    """未勾选参与 W-H 的峰 → (sin²θ, (βcosθ)²)，画成灰色空心点。"""
    xs, ys = [], []
    for entry in (getattr(dock, "fit_results", None) or []):
        if getattr(entry, "wh", True):
            continue
        fit = getattr(entry, "fit", None)
        if fit is None:
            continue
        theta = np.radians(float(fit.center) / 2.0)
        beta = np.radians(float(fit.fwhm))
        if instrument > 0:
            beta = np.sqrt(max(beta * beta - float(instrument) ** 2, 0.0))
        xs.append(float(np.sin(theta) ** 2))
        ys.append(float((beta * np.cos(theta)) ** 2))
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)


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
    ex, ey = _excluded_points(dock, getattr(dock, "wh_instrument", 0.0))
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
