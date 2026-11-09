"""一键分析报告：把当前会话的结果汇成一份自包含 HTML。

纯生成器：不碰 Qt、不重新积分、不重跑拟合。输入一份 `ReportSnapshot`
（由 GUI 从当前面板/工作台读出来），输出 HTML 字符串或写文件；图用
Matplotlib 的 Agg 画布按脚本尺寸重画（不抓 Qt 屏幕），base64 内嵌，
浏览器打开即可阅读、可直接打印成 PDF。

为什么单独放 services（2026-11-08）：生成器要能被单测直接调用，不依赖
窗口；GUI 只负责"收集快照 + 选路径"。v1.1（2026-11-09）加了精修与
原位两节：精修读 `refine_editor.result`，原位读 `insitu_editor.batch`，
仍然只汇总已经算好的数组，不重新积分、不重跑拟合。

口径（与 docs/ROADMAP.zh-CN.md 的计划 1 一致）：
  * 缺的节跳过，并在"本次报告范围"里写清楚为什么没做；
  * 图内文字英文（沿用界面图的中英分工），正文中文；
  * 报告是"当前会话快照"——精修/物相/原位的中间结果不落盘，关程序后
    补不回来（见报告末尾的"口径与边界"）。
"""
from __future__ import annotations

import base64
import html
import io
import math
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from xrd_toolkit.simulation.peakfit import (
    gaussian, lorentzian, pseudo_voigt, split_pearson7)

# 图的统一配色（与界面上的观感一致：观测蓝、拟合红、峰位橙）
_OBS_COLOR = "#1f5fa8"
_RAW_COLOR = "#b8bdc4"
_PEAK_COLOR = "#d97706"
_FIT_COLOR = "#d0544e"
_PHASE_COLORS = ("#d62728", "#2ca02c", "#9467bd", "#8c564b",
                 "#e377c2", "#bcbd22", "#17becf", "#1f77b4")

# 报告里可勾选的节：键 → 中文标题（顺序 = 正文顺序）
SECTIONS = (
    ("curve", "一维衍射曲线"),
    ("peaks", "峰表"),
    ("fits", "单峰拟合"),
    ("wh", "W-H / Scherrer"),
    ("phases", "物相筛选"),
    ("refine", "全谱精修"),
    ("insitu", "原位三相批量"),
)
_ALL_KEYS = tuple(key for key, _title in SECTIONS)


# ══ 快照数据（GUI 收集，生成器只读）════════════════════════
@dataclass
class ReportPeak:
    """峰表的一行（对应 services.peak_finding.FoundPeak）。"""

    center: float
    height: float
    fwhm: float
    area: float
    prominence: float = float("nan")


@dataclass
class ReportFit:
    """一根单峰拟合；window_tth/background 是画叠加曲线要用的窗口数据。"""

    center0: float
    half_window: float
    shape: str
    center: float
    fwhm: float
    fwhm_left: float
    fwhm_right: float
    eta: float
    m: float
    height: float
    area: float
    rwp: float
    window_tth: np.ndarray
    background: np.ndarray
    wh: bool = True
    background_mode: str = "linear"   # none / linear / quadratic（方法表用）
    # 同一窗口里的观测强度：残差图用（obs − model）；旧快照没有就留空
    window_intensity: np.ndarray = field(
        default_factory=lambda: np.array([]))


@dataclass
class ReportWH:
    """Williamson–Hall 结果（对应 simulation.microstructure 的结果）。"""

    size_nm: float
    strain: float
    intercept: float
    slope: float
    x: np.ndarray
    y: np.ndarray
    r2: float = float("nan")
    instrument_note: str = ""
    warning: str = ""
    n_used: int = 0


@dataclass
class ReportPhase:
    """物相筛选的一行（只带画图需要的理论峰位）。

    `selected` = 这张候选相在物相工作台里被选中做叠加（报告的表里会标
    “是/否”，图里只画选中的；没选任何行时按工作台的规则取当前行/最佳）。
    """

    name: str
    score: float
    coverage: float
    purity: float
    scale: float
    tth: np.ndarray
    intensity: np.ndarray
    selected: bool = False


@dataclass
class ReportProfileFit:
    """一条全谱拟合的绘图快照（精修或原位单帧）。

    字段名与 `simulation.fit.FitResult` 对齐，可以直接交给
    `simulation.plots.draw_fit`（只读，不重跑拟合）。
    """

    tth: np.ndarray
    y_obs: np.ndarray
    y_calc: np.ndarray
    residual: np.ndarray
    rwp: float
    rp: float
    title: str = ""
    background: Optional[np.ndarray] = None
    bragg: list[tuple[str, np.ndarray]] = field(default_factory=list)


@dataclass
class ReportPhaseWeight:
    """Rietveld 定量的一行（simulation.quantitative.PhaseWeight 快照）。"""

    label: str
    weight_percent: float
    scale: float
    z: float
    molar_mass: float
    volume: float
    source: str = "structure"
    warning: str = ""


@dataclass
class ReportRefine:
    """全谱精修的一次结果（`refine_editor.result` 的报告快照）。"""

    phase_label: str
    cif: str
    rwp: float = float("nan")
    rp: float = float("nan")
    reduced_chi2: float = float("nan")
    n_obs: int = 0
    n_params: int = 0
    success: bool = True
    message: str = ""
    lebail: bool = False
    profile: Optional[ReportProfileFit] = None
    # (参数名, 初值, 拟合值, esd)，已按工作台参数表的过滤规则筛过
    params: list[tuple[str, float, float, float]] = field(
        default_factory=list)
    weights: list[ReportPhaseWeight] = field(default_factory=list)
    weight_note: str = ""
    # (位点标签, Wyckoff, x, y, z)
    site_coords: list[tuple[str, str, float, float, float]] = field(
        default_factory=list)
    methods: list[tuple[str, str]] = field(default_factory=list)
    note: str = ""


@dataclass
class ReportInSitu:
    """原位三相批量的报告快照（`insitu_editor.batch` 的只读副本）。"""

    rows: list[dict] = field(default_factory=list)
    methods: list[tuple[str, str]] = field(default_factory=list)
    # 代表性单帧拟合（当前查看的帧，或 Rwp 最低的可用帧）
    frame_fit: Optional[ReportProfileFit] = None
    # 模型无关检查（(200) 双峰质心）；由 GUI 从已算好的拟合曲线导出
    mi_rows: list[dict] = field(default_factory=list)


@dataclass
class ReportCurve:
    """一条 1D 曲线的完整快照（处理后的曲线 + 峰/拟合/W-H）。"""

    name: str
    tth: np.ndarray
    intensity: np.ndarray
    path: str = ""
    raw_tth: Optional[np.ndarray] = None
    raw_intensity: Optional[np.ndarray] = None
    chain: str = "none"
    config: str = ""
    wavelength: Optional[float] = None
    peaks: list[ReportPeak] = field(default_factory=list)
    fits: list[ReportFit] = field(default_factory=list)
    wh: Optional[ReportWH] = None
    notes: list[str] = field(default_factory=list)
    # 方法表：(分组, 参数名, 值)，例如 ("寻峰", "显著度", "5 σ")
    methods: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class ReportSnapshot:
    """一份报告的输入：一条主曲线 + 物相工作台的结果 + 元数据。"""

    title: str
    sample: str = ""
    generated: str = ""
    version: str = ""
    curve: Optional[ReportCurve] = None
    phases: list[ReportPhase] = field(default_factory=list)
    phase_tth: Optional[np.ndarray] = None
    phase_intensity: Optional[np.ndarray] = None
    phase_source: str = ""
    refine: Optional[ReportRefine] = None
    insitu: Optional[ReportInSitu] = None
    sections: tuple[str, ...] = _ALL_KEYS
    notes: list[str] = field(default_factory=list)
    # 物相筛选的方法参数（相库/元素/容差/寻峰），同样用于方法表
    phase_methods: list[tuple[str, str]] = field(default_factory=list)
    report_id: str = ""


# ══ 小工具 ════════════════════════════════════════════════
def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _num(value, fmt="{:.4f}", dash="—") -> str:
    """数字格式化：NaN / 无穷 / 非数字一律显示破折号，别把 nan 印出去。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return dash
    if not math.isfinite(number):
        return dash
    return fmt.format(number)


def _as_float(value, default=float("nan")) -> float:
    """宽松转 float：读 CSV/行字典时字符串也能用；非有限值给默认。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _truthy(value) -> bool:
    """行字典里的 usable 可能是 bool，也可能是 CSV 读回的字符串。"""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _insitu_stats(rows) -> dict:
    """原位批量行的摘要数字（范围表 / 摘要 / KPI / 正文共用）。"""
    rows = list(rows or [])
    status = {}
    for row in rows:
        key = str(row.get("status", "")).split("(", 1)[0] or "unknown"
        if key.startswith("error"):
            key = "error"
        status[key] = status.get(key, 0) + 1
    usable = sum(1 for row in rows if _truthy(row.get("usable")))
    rwp = np.asarray([_as_float(row.get("rwp")) for row in rows],
                     dtype=float)
    rwp = rwp[np.isfinite(rwp)]
    f_values = [(row.get("frame"), _as_float(row.get("f_rich")))
                for row in rows]
    f_values = [(frame, value) for frame, value in f_values
                if math.isfinite(value)]
    if f_values:
        f_min = min(f_values, key=lambda pair: pair[1])
    else:
        f_min = (None, float("nan"))
    return {
        "total": len(rows),
        "usable": usable,
        "status": status,
        "rwp_min": float(rwp.min()) if rwp.size else float("nan"),
        "rwp_max": float(rwp.max()) if rwp.size else float("nan"),
        "rwp_med": float(np.median(rwp)) if rwp.size else float("nan"),
        "f_start": f_values[0][1] if f_values else float("nan"),
        "f_end": f_values[-1][1] if f_values else float("nan"),
        "f_min": f_min[1],
        "f_min_frame": f_min[0],
        "has_f": bool(f_values),
    }


def _range_text(values, fmt="{:.2f}") -> str:
    """一串数字的 min–max 文本；没有有限值时给破折号。"""
    finite = [float(value) for value in values
              if math.isfinite(_as_float(value))]
    if not finite:
        return "—"
    return f"{fmt.format(min(finite))}–{fmt.format(max(finite))}"


def _rwp_quality(rwp):
    """单峰拟合质量分级：返回 (文字, CSS 类)。

    阈值按单峰窗口内的局部 Rwp 定：≤2% 良好、2–5% 可接受、>5% 偏差大。
    这是报告层的质量提示，不改变拟合本身。
    """
    try:
        value = float(rwp)
    except (TypeError, ValueError):
        return "—", "q-na"
    if not math.isfinite(value):
        return "—", "q-na"
    if value <= 2.0:
        return "良好", "q-ok"
    if value <= 5.0:
        return "可接受", "q-warn"
    return "偏差大", "q-bad"


def _wh_r2(result) -> float:
    """W-H 直线的 R²（散点 → 拟合线）；点太少返回 NaN。"""
    x = np.asarray(result.x, dtype=float)
    y = np.asarray(result.y, dtype=float)
    if x.size < 2:
        return float("nan")
    line = result.intercept + result.slope * x
    ss_res = float(np.sum((y - line) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    if ss_tot <= 0:
        return float("nan")
    return 1.0 - ss_res / ss_tot


def _fit_shape_values(fit: ReportFit, x) -> np.ndarray:
    """按拟合线型算峰形（与 gui/fit_view._shape_values 同式）。"""
    if fit.shape == "gaussian":
        return gaussian(x, fit.center, fit.fwhm)
    if fit.shape == "lorentzian":
        return lorentzian(x, fit.center, fit.fwhm)
    if fit.shape == "pseudovoigt":
        return pseudo_voigt(x, fit.center, fit.fwhm, fit.eta)
    if fit.shape == "split_pearson7":
        return split_pearson7(x, fit.center, fit.fwhm_left,
                              fit.fwhm_right, fit.m)
    return np.zeros_like(x, dtype=float)


def _data_uri(fig: Figure) -> str:
    """Matplotlib 图 → data:image/png;base64,…（自包含 HTML 的关键）。"""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor="white")
    payload = base64.b64encode(buf.getvalue()).decode("ascii")
    fig.clear()
    return f"data:image/png;base64,{payload}"


def _new_figure(width=7.2, height=3.5) -> Figure:
    fig = Figure(figsize=(width, height), dpi=150)
    FigureCanvasAgg(fig)          # 固定用 Agg：不依赖 Qt/Tk 画布
    return fig


def _style_axes(ax) -> None:
    ax.grid(alpha=0.22, linestyle=":")
    ax.tick_params(labelsize=8)
    ax.set_xlabel("2θ (°)", fontsize=9)
    ax.set_ylabel("intensity (counts)", fontsize=9)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)


# ══ 图 ════════════════════════════════════════════════════
def _fig_curve(curve: ReportCurve) -> str:
    """1D 曲线：处理后曲线 + 可选原始曲线 + 检出峰短线。"""
    tth = np.asarray(curve.tth, dtype=float)
    y = np.asarray(curve.intensity, dtype=float)
    if tth.size == 0 or y.size == 0:
        return ""
    fig = _new_figure(7.2, 3.4)
    ax = fig.add_subplot(111)
    if (curve.raw_tth is not None and curve.raw_intensity is not None
            and curve.chain != "none"):
        ax.plot(curve.raw_tth, curve.raw_intensity, "-", lw=0.6,
                color=_RAW_COLOR, label="raw", zorder=1)
        label = "processed"
    else:
        label = "observed"
    ax.plot(tth, y, "-", lw=0.9, color=_OBS_COLOR, label=label, zorder=2)
    if curve.peaks:
        finite = y[np.isfinite(y)]
        y0 = float(finite.min()) if finite.size else 0.0
        for peak in curve.peaks:
            idx = int(np.clip(np.searchsorted(tth, peak.center),
                              0, tth.size - 1))
            top = float(y[idx]) if math.isfinite(y[idx]) else y0
            ax.plot([peak.center, peak.center], [y0, top], "-",
                    color=_PEAK_COLOR, lw=0.8, alpha=0.55, zorder=3)
        ax.plot([], [], "-", color=_PEAK_COLOR, lw=0.8, alpha=0.55,
                label=f"peaks ({len(curve.peaks)})")
    ax.set_title(curve.name, fontsize=10)
    _style_axes(ax)
    ax.legend(fontsize=8, loc="best")
    fig.tight_layout()
    return _data_uri(fig)


def _fits_have_residual(fits) -> bool:
    return any(
        np.size(getattr(fit, "window_intensity", []))
        == np.size(fit.window_tth) and np.size(fit.window_tth) > 0
        for fit in fits)


def _fig_fits(curve: ReportCurve) -> str:
    """单峰拟合叠加图 + 残差；只画拟合窗口覆盖的 2θ 段。

    上图 = 观测 + 每根峰的拟合线，下图 = obs − model。残差图是"拟合好
    不好"最直接的证据：曲线贴合时残差在 0 附近随机抖动；峰形/窗口/背景
    选错时残差会出现系统性的鼓包或斜坡。
    """
    fits = list(curve.fits)
    if not fits:
        return ""
    has_residual = _fits_have_residual(fits)
    if has_residual:
        fig = _new_figure(7.2, 4.7)
        grid = fig.add_gridspec(2, 1, height_ratios=(3.0, 1.1),
                                hspace=0.08)
        ax = fig.add_subplot(grid[0])
        axr = fig.add_subplot(grid[1], sharex=ax)
    else:
        fig = _new_figure(7.2, 3.6)
        ax = fig.add_subplot(111)
        axr = None
    tth = np.asarray(curve.tth, dtype=float)
    y = np.asarray(curve.intensity, dtype=float)
    ax.plot(tth, y, "-", lw=0.7, color="#9aa0a6", label="observed",
            zorder=1)
    for i, fit in enumerate(fits):
        x = np.asarray(fit.window_tth, dtype=float)
        bg = np.asarray(fit.background, dtype=float)
        model = bg + fit.height * _fit_shape_values(fit, x)
        color = _PHASE_COLORS[i % len(_PHASE_COLORS)]
        ax.plot(x, model, "-", lw=1.2, color=color,
                label=f"{fit.center:.3f}°", zorder=3)
        observed = np.asarray(
            getattr(fit, "window_intensity", np.array([])), dtype=float)
        if axr is not None and observed.size == x.size:
            axr.plot(x, observed - model, "-", lw=0.7, color=color,
                     alpha=0.85, zorder=3)
    lo = min(f.center0 - f.half_window for f in fits)
    hi = max(f.center0 + f.half_window for f in fits)
    pad = 0.03 * (hi - lo) if hi > lo else 0.1
    ax.set_xlim(lo - pad, hi + pad)
    rwp_values = [float(fit.rwp) for fit in fits
                  if math.isfinite(float(fit.rwp))]
    title = f"single-peak fits ({len(fits)})"
    if rwp_values:
        title += (f"; Rwp {min(rwp_values):.2f}–{max(rwp_values):.2f}%"
                  if len(rwp_values) > 1 else f"; Rwp {rwp_values[0]:.2f}%")
    ax.set_title(title, fontsize=10)
    _style_axes(ax)
    if axr is not None:
        ax.set_xlabel("")
        ax.tick_params(labelbottom=False)
        axr.axhline(0.0, color="#667085", lw=0.8, ls="--", zorder=2)
        axr.set_xlabel("2θ (°)", fontsize=9)
        axr.set_ylabel("residual (counts)", fontsize=9)
        axr.grid(alpha=0.22, linestyle=":")
        axr.tick_params(labelsize=8)
        for spine in ("top", "right"):
            axr.spines[spine].set_visible(False)
    if len(fits) <= 8:
        ax.legend(fontsize=7, ncol=2, loc="best")
    if axr is not None:
        # sharex + tight_layout 会报 "axes not compatible"；手工留边距
        fig.subplots_adjust(left=0.10, right=0.98, top=0.92,
                            bottom=0.12, hspace=0.08)
    else:
        fig.tight_layout()
    return _data_uri(fig)


def _fig_wh(wh: ReportWH) -> str:
    """W-H 散点 + 拟合线。"""
    x = np.asarray(wh.x, dtype=float)
    y = np.asarray(wh.y, dtype=float)
    if x.size == 0:
        return ""
    fig = _new_figure(6.2, 3.9)
    ax = fig.add_subplot(111)
    ax.plot(x, y, "o", ms=5, color=_OBS_COLOR,
            label=f"peaks ({x.size})", zorder=3)
    if x.size >= 2:
        xs = np.linspace(float(x.min()), float(x.max()), 100)
        ax.plot(xs, wh.intercept + wh.slope * xs, "-",
                color=_FIT_COLOR, lw=1.3, label="linear fit", zorder=2)
    ax.set_xlabel(r"sin$^2\theta$", fontsize=9)
    ax.set_ylabel(r"$(\beta\cos\theta)^2$  (rad$^2$)", fontsize=9)
    title = (f"D = {_num(wh.size_nm, '{:.1f}')} nm, "
             f"ε = {_num(wh.strain, '{:.2e}')}")
    if math.isfinite(float(wh.r2)):
        title += f", R² = {wh.r2:.3f}"
    ax.set_title(title, fontsize=10)
    ax.grid(alpha=0.22, linestyle=":")
    ax.tick_params(labelsize=8)
    ax.legend(fontsize=8, loc="best")
    fig.tight_layout()
    return _data_uri(fig)


def _fig_phases(snapshot: ReportSnapshot) -> str:
    """物相叠加图：观测曲线 + 各候选相的理论峰位短线。"""
    if snapshot.phase_tth is None or snapshot.phase_intensity is None:
        return ""
    tth = np.asarray(snapshot.phase_tth, dtype=float)
    y = np.asarray(snapshot.phase_intensity, dtype=float)
    if tth.size == 0:
        return ""
    fig = _new_figure(7.2, 3.6)
    ax = fig.add_subplot(111)
    ax.plot(tth, y, "-", lw=0.9, color=_OBS_COLOR, label="observed",
            zorder=2)
    finite = y[np.isfinite(y)]
    if finite.size:
        span = float(finite.max() - finite.min())
        y0 = float(finite.min())
    else:
        span, y0 = 1.0, 0.0
    amp = 0.55 * span if span > 0 else 1.0
    selected = [row for row in snapshot.phases if row.selected]
    if not selected:
        selected = snapshot.phases[:1]
    for i, row in enumerate(selected):
        sticks = np.asarray(row.intensity, dtype=float)
        pos = np.asarray(row.tth, dtype=float)
        keep = sticks >= 0.02
        pos, sticks = pos[keep], sticks[keep]
        inside = (pos >= tth.min()) & (pos <= tth.max())
        pos, sticks = pos[inside], sticks[inside]
        if pos.size == 0:
            continue
        ax.vlines(pos, y0, y0 + sticks * amp,
                  color=_PHASE_COLORS[i % len(_PHASE_COLORS)], lw=1.0,
                  alpha=0.9, label=f"{row.name} (score {row.score:.3f})")
    ax.set_title(f"phase candidates ({len(snapshot.phases)}; "
                 f"{len(selected)} overlaid)", fontsize=10)
    _style_axes(ax)
    if selected:
        ax.legend(fontsize=7, loc="best")
    fig.tight_layout()
    return _data_uri(fig)


def _thin_positions(values, tol=0.05) -> np.ndarray:
    """把显示上挨得太近的布拉格峰位合并成一个刻度。"""
    values = np.asarray(values, dtype=float)
    if values.size < 2:
        return values
    keep = [values[0]]
    for value in values[1:]:
        if value - keep[-1] > tol:
            keep.append(value)
    return np.asarray(keep)


def _fig_profile(profile: Optional[ReportProfileFit]) -> str:
    """全谱拟合图（精修或原位单帧）：obs/calc/背景 + 差值 + 布拉格刻度。

    不直接复用 `simulation.plots.draw_fit`：它的 "Bragg positions" 竖排
    轴标签在报告这种较矮的画布里会压到残差轴（2026-11-09 实拍），这里
    用相名作刻度标签、去掉竖排轴名，布局按报告宽度固定。
    """
    if profile is None:
        return ""
    tth = np.asarray(profile.tth, dtype=float)
    if tth.size == 0:
        return ""
    y_obs = np.asarray(profile.y_obs, dtype=float)
    y_calc = np.asarray(profile.y_calc, dtype=float)
    residual = np.asarray(profile.residual, dtype=float)
    bragg = [(str(label), np.asarray(pos, dtype=float))
             for label, pos in profile.bragg
             if np.asarray(pos, dtype=float).size]
    fig = _new_figure(7.4, 5.2)
    gs = fig.add_gridspec(3, 1, height_ratios=[3.1, 0.9, 0.55],
                          hspace=0.08)
    ax = fig.add_subplot(gs[0])
    axr = fig.add_subplot(gs[1], sharex=ax)
    axb = fig.add_subplot(gs[2], sharex=ax)
    ax.tick_params(labelbottom=False, labelsize=8)
    axr.tick_params(labelbottom=False, labelsize=8)
    ax.plot(tth, y_obs, ".", ms=1.7, color=_OBS_COLOR, alpha=0.85,
            label="observed")
    ax.plot(tth, y_calc, "-", lw=1.1, color=_FIT_COLOR,
            label="calculated")
    background = profile.background
    if background is not None and np.size(background):
        ax.plot(tth, background, "--", lw=0.9, color="#8a8f98",
                label="background")
    ax.set_ylabel("intensity (counts)", fontsize=9)
    ax.grid(alpha=0.22, linestyle=":")
    ax.legend(fontsize=7.5, loc="upper right", frameon=True,
              framealpha=0.9, edgecolor="0.85")
    ax.set_title(
        f"{profile.title}   Rwp = {_num(profile.rwp, '{:.2f}')}%"
        f"   Rp = {_num(profile.rp, '{:.2f}')}%", fontsize=10)
    ax.set_xlim(float(tth.min()), float(tth.max()))

    axr.plot(tth, residual, "-", lw=0.8, color="#555555")
    axr.axhline(0.0, color="#999999", lw=0.7)
    axr.set_ylabel("obs − calc", fontsize=8.5)
    axr.grid(alpha=0.22, linestyle=":")
    finite = residual[np.isfinite(residual)]
    if finite.size:
        lim = float(np.percentile(np.abs(finite), 99.8))
        if lim > 0:
            axr.set_ylim(-1.15 * lim, 1.15 * lim)

    if bragg:
        axb.set_yticks(range(len(bragg)))
        axb.set_yticklabels([label for label, _pos in bragg], fontsize=7.5)
        axb.set_ylim(-0.6, len(bragg) - 0.4)
        for i, (_label, pos) in enumerate(bragg):
            inside = pos[(pos >= tth.min()) & (pos <= tth.max())]
            inside = _thin_positions(inside)
            if inside.size:
                axb.plot(inside, np.full(inside.size, float(i)),
                         "|", ms=9, mew=1.0,
                         color=_PHASE_COLORS[i % len(_PHASE_COLORS)])
        axb.tick_params(axis="y", length=0, pad=3)
    else:
        axb.set_yticks([])
        axb.tick_params(axis="y", length=0)
    axb.tick_params(axis="x", labelsize=8)
    axb.set_xlabel("2θ (°)", fontsize=9)
    fig.subplots_adjust(left=0.13, right=0.98, top=0.91, bottom=0.10)
    return _data_uri(fig)


def _fig_insitu_summary(insitu: ReportInSitu) -> str:
    """原位批量汇总图（四栏：相分数 / 两相晶胞 / Rwp）。"""
    if not insitu.rows:
        return ""
    try:
        from xrd_toolkit.simulation.insitu_plots import draw_summary
    except ImportError:                            # pragma: no cover
        return ""
    fig = _new_figure(8.6, 5.8)
    draw_summary(fig, insitu.rows)
    return _data_uri(fig)


def _fig_insitu_model_independent(insitu: ReportInSitu) -> str:
    """模型无关检查图（(200) 双峰质心）；没有缓存行就跳过。"""
    if not insitu.mi_rows:
        return ""
    try:
        from xrd_toolkit.simulation.insitu_plots import draw_model_independent
    except ImportError:                            # pragma: no cover
        return ""
    fig = _new_figure(9.6, 3.3)
    draw_model_independent(fig, insitu.mi_rows)
    return _data_uri(fig)


# ══ HTML ═════════════════════════════════════════════════
_CSS = """
:root {
  --ink: #1f2933;
  --muted: #667085;
  --faint: #98a2b3;
  --line: #d0d5dd;
  --accent: #0f4c81;
  --accent-soft: #eaf1f8;
  --bg: #f2f4f7;
  --paper: #ffffff;
}
* { box-sizing: border-box; }
html { background: var(--bg); }
body {
  margin: 0;
  color: var(--ink);
  font: 15px/1.7 -apple-system, BlinkMacSystemFont, "PingFang SC",
        "Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans SC",
        sans-serif;
  -webkit-font-smoothing: antialiased;
}
main {
  max-width: 900px;
  margin: 0 auto;
  padding: 0 56px 56px;
  background: var(--paper);
  box-shadow: 0 1px 3px rgba(16, 24, 40, .08);
}
/* 封面 */
.cover {
  position: relative;
  min-height: 540px;
  padding: 64px 0 64px;
  border-bottom: 1px solid var(--line);
}
.cover-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
}
.brand {
  color: var(--accent);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .16em;
  text-transform: uppercase;
}
.cover-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.report-id {
  color: var(--faint);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.cover h1 {
  margin: 54px 0 8px;
  font-size: 34px;
  line-height: 1.25;
  letter-spacing: -.01em;
}
.cover-sub {
  margin: 0 0 30px;
  color: var(--muted);
  font-size: 16px;
}
.cover-rule {
  width: 64px;
  height: 4px;
  background: var(--accent);
  margin: 0 0 30px;
}
.meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px 44px;
  font-size: 13px;
}
.meta-grid .label {
  display: block;
  margin-bottom: 2px;
  color: var(--muted);
  font-size: 10.5px;
  letter-spacing: .08em;
  text-transform: uppercase;
}
.meta-grid .value {
  font-weight: 600;
  overflow-wrap: anywhere;
}
.cover-foot {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 26px;
  display: flex;
  justify-content: space-between;
  gap: 20px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
  color: var(--faint);
  font-size: 11px;
}
.print-btn {
  border: 1px solid var(--accent);
  border-radius: 4px;
  background: #fff;
  color: var(--accent);
  padding: 5px 12px;
  font-size: 12px;
  cursor: pointer;
}
.print-btn:hover { background: var(--accent-soft); }
/* 目录 */
.toc {
  padding: 30px 0 26px;
  border-bottom: 1px solid var(--line);
}
.toc h2 {
  margin: 0 0 14px;
  padding: 0;
  border: none;
  color: var(--muted);
  font-size: 13px;
  letter-spacing: .12em;
  text-transform: uppercase;
}
.toc-list {
  margin: 0;
  padding: 0;
  list-style: none;
  columns: 1;
  font-size: 13.5px;
}
.toc-list li { margin: 4px 0; break-inside: avoid; }
.toc-list li.sub { padding-left: 18px; }
.toc-num {
  display: inline-block;
  width: 2.6em;
  color: var(--muted);
}
.toc a { color: var(--ink); text-decoration: none; }
.toc a:hover { color: var(--accent); }
/* 标题与正文 */
h2 {
  margin: 40px 0 12px;
  padding-bottom: 7px;
  border-bottom: 1px solid var(--line);
  font-size: 21px;
  line-height: 1.3;
  break-after: avoid;
  page-break-after: avoid;
}
h3 {
  margin: 26px 0 8px;
  color: #344054;
  font-size: 16px;
  line-height: 1.4;
  break-after: avoid;
  page-break-after: avoid;
}
h2 .num, h3 .num {
  margin-right: 8px;
  color: var(--accent);
  font-variant-numeric: tabular-nums;
}
p { margin: 8px 0; }
.lead { color: var(--muted); font-size: 14px; }
/* 摘要卡片 */
.kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 16px 0 14px;
}
.kpi {
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-top: 3px solid var(--accent);
  background: #fff;
  break-inside: avoid;
  page-break-inside: avoid;
}
.kpi .k {
  color: var(--muted);
  font-size: 10.5px;
  letter-spacing: .06em;
  text-transform: uppercase;
}
.kpi .v {
  margin: 4px 0 2px;
  font-size: 20px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.kpi .s { color: var(--faint); font-size: 11px; line-height: 1.45; }
/* 表格 */
.table-block { margin: 14px 0 18px; }
.table-block.keep {
  break-inside: avoid;
  page-break-inside: avoid;
}
.table-caption {
  margin: 0 0 5px;
  color: var(--muted);
  font-size: 12px;
}
.table-caption b { color: var(--ink); font-weight: 600; }
table.data, table.scope {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
table.data th, table.scope th {
  padding: 7px 8px;
  border-top: 1.5px solid var(--ink);
  border-bottom: 1px solid var(--line);
  background: #fff;
  color: #344054;
  font-weight: 600;
  text-align: right;
}
table.data td, table.scope td {
  padding: 6px 8px;
  border-bottom: 1px solid #eaecf0;
  text-align: right;
}
table.data th:first-child, table.data td:first-child,
table.data th:nth-child(2), table.data td:nth-child(2),
table.scope th, table.scope td {
  text-align: left;
}
table.data td.left, table.data th.left { text-align: left; }
table.data tbody tr:nth-child(even) td { background: #fafbfc; }
table.data tbody tr:last-child td,
table.scope tbody tr:last-child td { border-bottom: 1.5px solid var(--ink); }
table.data tr.group th {
  padding: 7px 8px;
  border-bottom: 1px solid var(--line);
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
  letter-spacing: .04em;
  text-align: left;
}
.status-done, .q-ok { color: #047857; font-weight: 600; }
.status-skip, .q-warn { color: #b45309; font-weight: 600; }
.q-bad { color: #b42318; font-weight: 600; }
.q-na { color: var(--faint); }
/* 图 */
figure {
  margin: 16px 0 20px;
  break-inside: avoid;
  page-break-inside: avoid;
}
figure img {
  display: block;
  width: 100%;
  height: auto;
  border: 1px solid #eaecf0;
}
figcaption {
  margin-top: 6px;
  color: var(--muted);
  font-size: 12px;
  text-align: center;
}
/* 提示与限制 */
.note {
  margin: 12px 0;
  padding: 10px 12px;
  border-left: 3px solid #f0b429;
  background: #fffbeb;
  color: #7c5e10;
  font-size: 13px;
  break-inside: avoid;
}
.muted { color: var(--muted); }
.limits h3 { margin-top: 18px; }
.limits ul { margin: 6px 0 10px; padding-left: 20px; }
.limits li { margin: 4px 0; }
/* 页脚 */
.footer {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  margin-top: 44px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
  color: var(--faint);
  font-size: 11px;
}
@media (max-width: 720px) {
  main { padding: 0 22px 32px; }
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .meta-grid { grid-template-columns: 1fr; }
  .toc-list { columns: 1; }
  .cover h1 { font-size: 28px; }
}
@media print {
  html, body { background: #fff; }
  main { max-width: none; margin: 0; padding: 0; box-shadow: none; }
  .cover {
    min-height: 247mm;
    padding-top: 48mm;
    page-break-after: always;
    break-after: page;
  }
  .toc { page-break-after: always; break-after: page; }
  .print-btn { display: none; }
  body { font-size: 11pt; }
  h2 { font-size: 15pt; }
  h3 { font-size: 12pt; }
  table.data, table.scope { font-size: 9.5pt; }
  figure, .table-block.keep, .kpi, .note {
    break-inside: avoid;
    page-break-inside: avoid;
  }
  a { color: inherit; text-decoration: none; }
  @page {
    size: A4;
    margin: 18mm 15mm 16mm;
    @top-right {
      content: "XRD Toolkit · 分析报告";
      color: #98a2b3;
      font-size: 8.5pt;
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC",
                   "Microsoft YaHei", sans-serif;
    }
    @bottom-center {
      content: "第 " counter(page) " 页 / 共 " counter(pages) " 页";
      color: #98a2b3;
      font-size: 8.5pt;
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC",
                   "Microsoft YaHei", sans-serif;
    }
  }
  @page :first {
    @top-right { content: none; }
    @bottom-center { content: none; }
  }
}
"""


def _report_id(snapshot: ReportSnapshot) -> str:
    if snapshot.report_id:
        return snapshot.report_id
    stamp = snapshot.generated or datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S")
    digits = "".join(ch for ch in stamp if ch.isdigit())[:14]
    return f"XRD-{digits or 'REPORT'}"


def _curve_stats(curve: Optional[ReportCurve]):
    """曲线统计：(点数, 2θ 范围文本, 平均步长)。"""
    if curve is None:
        return 0, "—", float("nan")
    tth = np.asarray(curve.tth, dtype=float)
    finite = tth[np.isfinite(tth)]
    if finite.size >= 2:
        step = float(np.median(np.diff(finite)))
        rng = f"{finite.min():.3f}–{finite.max():.3f}°"
    else:
        step, rng = float("nan"), "—"
    return int(tth.size), rng, step


def _subtitle(snapshot: ReportSnapshot) -> str:
    parts = ["一维衍射"]
    curve = snapshot.curve
    if curve is not None and (curve.fits or curve.wh is not None):
        parts.append("峰形与微结构")
    if snapshot.phases:
        parts.append("物相筛选")
    if snapshot.refine is not None:
        parts.append("全谱精修")
    if snapshot.insitu is not None and snapshot.insitu.rows:
        parts.append("原位三相")
    return " · ".join(parts)


def _cover(snapshot: ReportSnapshot) -> str:
    curve = snapshot.curve
    stats = _curve_stats(curve)
    wavelength = (f"{curve.wavelength:.4f} Å"
                  if curve is not None and curve.wavelength is not None
                  else "—")
    generated = snapshot.generated or datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S")
    title = snapshot.title or "XRD 分析报告"
    sample = snapshot.sample or "—"
    data_file = (curve.path if curve is not None and curve.path else "—")
    config = (curve.config if curve is not None and curve.config else "—")
    version = (f"XRD Toolkit {snapshot.version}"
               if snapshot.version else "XRD Toolkit")
    meta = [
        ("样品", sample),
        ("数据文件", data_file),
        ("几何配置", config),
        ("波长", wavelength),
        ("生成时间", generated),
        ("软件版本", version),
    ]
    meta_html = "".join(
        f"<div><span class=\"label\">{_esc(label)}</span>"
        f"<span class=\"value\">{_esc(value)}</span></div>"
        for label, value in meta)
    return f"""
<header class="cover" id="cover">
  <div class="cover-top">
    <div class="brand">XRD Toolkit · Materials Diffraction Analysis</div>
    <div class="cover-actions">
      <span class="report-id">报告编号　{_esc(_report_id(snapshot))}</span>
      <button class="print-btn" onclick="window.print()">打印 / 保存为 PDF</button>
    </div>
  </div>
  <h1>{_esc(title)}</h1>
  <p class="cover-sub">{_esc(sample)} · {_esc(_subtitle(snapshot))}</p>
  <div class="cover-rule"></div>
  <div class="meta-grid">{meta_html}</div>
  <div class="cover-foot">
    <span>本报告由 XRD Toolkit 自动生成</span>
    <span>{_esc(generated)}</span>
  </div>
</header>
"""


def _result_sections(snapshot: ReportSnapshot) -> list:
    """有数据、且用户勾选的结果节：[(key, 标题), …]。"""
    curve = snapshot.curve
    out = []
    if "curve" in snapshot.sections and curve is not None:
        out.append(("curve", "一维衍射曲线"))
    if "peaks" in snapshot.sections and curve is not None and curve.peaks:
        out.append(("peaks", "峰表"))
    if "fits" in snapshot.sections and curve is not None and curve.fits:
        out.append(("fits", "单峰拟合"))
    if ("wh" in snapshot.sections and curve is not None
            and curve.wh is not None):
        out.append(("wh", "Williamson–Hall / Scherrer"))
    if "phases" in snapshot.sections and snapshot.phases:
        out.append(("phases", "物相筛选"))
    if "refine" in snapshot.sections and snapshot.refine is not None:
        out.append(("refine", "全谱精修"))
    if ("insitu" in snapshot.sections and snapshot.insitu is not None
            and snapshot.insitu.rows):
        out.append(("insitu", "原位三相批量"))
    return out


def _toc(snapshot: ReportSnapshot, result_sections: list) -> str:
    def item(anchor, number, title, cls=""):
        extra = f" class=\"{cls}\"" if cls else ""
        return (f"<li{extra}><a href=\"#{anchor}\">"
                f"<span class=\"toc-num\">{_esc(number)}</span>"
                f"{_esc(title)}</a></li>")

    items = [item("s1", "1", "报告摘要"),
             item("s2", "2", "数据与方法")]
    if result_sections:
        items.append(item("s3", "3", "结果"))
        for i, (_key, title) in enumerate(result_sections, start=1):
            items.append(item(f"s3-{i}", f"3.{i}", title, cls="sub"))
    items.append(item("s4", "4", "结果说明与限制"))
    return f"""
<nav class="toc" id="toc">
  <h2>目录</h2>
  <ul class="toc-list">{''.join(items)}</ul>
</nav>
"""


def _scope_rows(snapshot: ReportSnapshot):
    curve = snapshot.curve
    stats = _curve_stats(curve)
    rows = []
    for key, title in SECTIONS:
        if key not in snapshot.sections:
            continue
        if key == "curve":
            done = curve is not None
            detail = (f"{stats[0]} 点，{stats[1]}" if done else "—")
        elif key == "peaks":
            done = bool(curve is not None and curve.peaks)
            detail = (f"{len(curve.peaks)} 根" if done
                      else "没有检出峰（或寻峰参数不合适）")
        elif key == "fits":
            done = bool(curve is not None and curve.fits)
            detail = (f"{len(curve.fits)} 根" if done
                      else "本次未做单峰拟合")
        elif key == "wh":
            done = bool(curve is not None and curve.wh is not None)
            detail = (f"{curve.wh.n_used} 根参与"
                      if done else "至少需要 3 根参与 W-H 的拟合峰")
        elif key == "phases":
            done = bool(snapshot.phases)
            if done:
                picked = sum(1 for row in snapshot.phases if row.selected)
                detail = f"{len(snapshot.phases)} 个候选，{picked} 个叠加"
            else:
                detail = "本次未筛相"
        elif key == "refine":
            refine = snapshot.refine
            done = refine is not None
            if done:
                phase = (refine.phase_label
                         or Path(refine.cif).stem or "主相")
                detail = (f"{phase} · Rwp "
                          f"{_num(refine.rwp, '{:.2f}')}%")
            else:
                detail = "本次未做全谱精修"
        elif key == "insitu":
            insitu = snapshot.insitu
            done = bool(insitu is not None and insitu.rows)
            if done:
                stats = _insitu_stats(insitu.rows)
                detail = (f"{stats['total']} 帧，可用 "
                          f"{stats['usable']}/{stats['total']}")
            else:
                detail = "本次未跑原位批量"
        else:
            done, detail = False, "—"
        rows.append((title, done, detail))
    rows.append(("结果说明与限制", True, "固定说明"))
    return rows


def _scope_table(snapshot: ReportSnapshot) -> str:
    rows = []
    for title, done, detail in _scope_rows(snapshot):
        status = ("<span class=\"status-done\">已包含</span>" if done
                  else "<span class=\"status-skip\">本次未做</span>")
        rows.append(
            f"<tr><td>{_esc(title)}</td><td>{status}</td>"
            f"<td class=\"left\">{_esc(detail)}</td></tr>")
    return ("<table class=\"scope\"><thead><tr><th>分析项</th><th>状态</th>"
            "<th>说明</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def _kpi(label: str, value: str, sub: str) -> str:
    return (f"<div class=\"kpi\"><div class=\"k\">{_esc(label)}</div>"
            f"<div class=\"v\">{_esc(value)}</div>"
            f"<div class=\"s\">{_esc(sub)}</div></div>")


def _kpis(snapshot: ReportSnapshot) -> str:
    curve = snapshot.curve
    n_points, rng, step = _curve_stats(curve)
    cards = [
        _kpi("2θ 范围", rng,
             f"{n_points} 点 · 步长 {_num(step, '{:.5f}')}°")
    ]
    if curve is not None and curve.peaks:
        strongest = max(curve.peaks, key=lambda p: p.height)
        cards.append(_kpi(
            "检出峰", f"{len(curve.peaks)} 根",
            f"最强 2θ {_num(strongest.center)}° · "
            f"FWHM {_num(strongest.fwhm)}°"))
    else:
        cards.append(_kpi("检出峰", "—", "本次未做"))
    if curve is not None and curve.wh is not None:
        wh = curve.wh
        cards.append(_kpi(
            "W-H / 微结构",
            f"D {_num(wh.size_nm, '{:.1f}')} nm",
            f"ε {_num(wh.strain, '{:.2e}')} · "
            f"R² {_num(wh.r2, '{:.3f}')}"))
    elif curve is not None and curve.fits:
        cards.append(_kpi(
            "单峰拟合", f"{len(curve.fits)} 根",
            "W-H 需至少 3 根参与峰"))
    else:
        cards.append(_kpi("W-H / 微结构", "—", "本次未做"))
    if snapshot.phases:
        best = max(snapshot.phases, key=lambda row: row.score)
        cards.append(_kpi(
            "物相候选", f"{len(snapshot.phases)} 个",
            f"最佳 {best.name} · score {_num(best.score, '{:.3f}')}"))
    else:
        cards.append(_kpi("物相候选", "—", "本次未做"))
    if "refine" in snapshot.sections:
        refine = snapshot.refine
        if refine is not None:
            cards.append(_kpi(
                "全谱精修", f"Rwp {_num(refine.rwp, '{:.2f}')}%",
                f"{refine.phase_label or Path(refine.cif).stem or '主相'}"
                f" · Rp {_num(refine.rp, '{:.2f}')}%"))
        else:
            cards.append(_kpi("全谱精修", "—", "本次未做"))
    if "insitu" in snapshot.sections:
        insitu = snapshot.insitu
        if insitu is not None and insitu.rows:
            stats = _insitu_stats(insitu.rows)
            cards.append(_kpi(
                "原位三相批量",
                f"{stats['usable']}/{stats['total']} 帧",
                f"f_rich {_num(stats['f_start'], '{:.2f}')} → "
                f"{_num(stats['f_end'], '{:.2f}')}"))
        else:
            cards.append(_kpi("原位三相批量", "—", "本次未做"))
    return "<div class=\"kpis\">" + "".join(cards) + "</div>"


def _summary_text(snapshot: ReportSnapshot) -> str:
    curve = snapshot.curve
    if curve is None:
        return ("<p class=\"lead\">本次没有可用的 1D 曲线，报告只包含"
                "方法与说明。</p>")
    n_points, rng, _step = _curve_stats(curve)
    wavelength = (f"{curve.wavelength:.4f} Å"
                  if curve.wavelength is not None else "—")
    parts = [
        f"本次分析基于 <b>{_esc(curve.path or '—')}</b> 的 1D 积分曲线"
        f"（{_esc(curve.config or '—')}，λ = {_esc(wavelength)}），"
        f"2θ 范围 {_esc(rng)}，共 {n_points} 点。"
    ]
    if curve.chain and curve.chain != "none":
        parts.append(f"处理链：{_esc(curve.chain)}。")
    else:
        parts.append("未做背景扣除、平滑或裁剪处理。")
    if curve.peaks:
        strongest = max(curve.peaks, key=lambda p: p.height)
        parts.append(
            f"共检出 <b>{len(curve.peaks)}</b> 根峰，最强峰位于 "
            f"2θ = {_num(strongest.center)}°"
            f"（FWHM {_num(strongest.fwhm)}°）。")
    else:
        parts.append("本次未检出峰。")
    if curve.fits:
        parts.append(f"对 <b>{len(curve.fits)}</b> 根峰做单峰拟合")
        if curve.wh is not None:
            wh = curve.wh
            parts.append(
                f"；W-H 给出 D = <b>{_num(wh.size_nm, '{:.1f}')} nm</b>、"
                f"ε = <b>{_num(wh.strain, '{:.2e}')}</b>"
                f"（R² = {_num(wh.r2, '{:.3f}')}）。")
        else:
            parts.append("。")
        poor = [fit for fit in curve.fits
                if math.isfinite(float(fit.rwp)) and float(fit.rwp) > 5.0]
        if poor:
            parts.append(
                f"其中 <b>{len(poor)}</b> 根 Rwp 超过 5%，"
                "建议检查峰形、半窗或局部背景。")
    if snapshot.phases:
        best = max(snapshot.phases, key=lambda row: row.score)
        picked = sum(1 for row in snapshot.phases if row.selected)
        parts.append(
            f"物相筛选中得到 <b>{len(snapshot.phases)}</b> 个候选，最高分为 "
            f"<b>{_esc(best.name)}</b>（score "
            f"{_num(best.score, '{:.3f}')}）；叠加图展示 {picked} 个选中的相。")
    if snapshot.refine is not None:
        refine = snapshot.refine
        phase = refine.phase_label or Path(refine.cif).stem or "主相"
        parts.append(
            f"全谱精修（<b>{_esc(phase)}</b>）得到 Rwp = "
            f"<b>{_num(refine.rwp, '{:.2f}')}%</b>、Rp = "
            f"{_num(refine.rp, '{:.2f}')}%；定量相分数表含 "
            f"<b>{len(refine.weights)}</b> 个晶态相。")
    if snapshot.insitu is not None and snapshot.insitu.rows:
        stats = _insitu_stats(snapshot.insitu.rows)
        parts.append(
            f"原位三相批量共 <b>{stats['total']}</b> 帧，可用 "
            f"<b>{stats['usable']}</b> 帧；f_rich 从 "
            f"{_num(stats['f_start'], '{:.2f}')} 变到 "
            f"{_num(stats['f_end'], '{:.2f}')}（相对散射强度，不是 wt%）。")
    available = {"curve": curve is not None,
                 "peaks": bool(curve.peaks), "fits": bool(curve.fits),
                 "wh": curve.wh is not None,
                 "phases": bool(snapshot.phases),
                 "refine": snapshot.refine is not None,
                 "insitu": bool(snapshot.insitu is not None
                                and snapshot.insitu.rows)}
    missing = [title for key, title in SECTIONS
               if key in snapshot.sections and not available[key]]
    if missing:
        parts.append("本次未做：" + "、".join(missing) + "。")
    return "<p class=\"lead\">" + "".join(parts) + "</p>"


def _table_caption(number: int, title: str) -> str:
    return (f"<div class=\"table-caption\"><b>表 {number}</b>"
            f"　{_esc(title)}</div>")


def _methods_table(snapshot: ReportSnapshot, number: int):
    """表 1：数据与分析方法；返回 (HTML, 下一个表号)。"""
    curve = snapshot.curve
    rows = []
    if curve is not None:
        n_points, rng, step = _curve_stats(curve)
        wavelength = (f"{curve.wavelength:.4f} Å"
                      if curve.wavelength is not None else "—")
        chain = (curve.chain if curve.chain and curve.chain != "none"
                 else "未做处理（原始曲线）")
        rows.append("<tr class=\"group\"><th colspan=\"2\">数据与处理</th></tr>")
        for label, value in (
                ("数据文件", curve.path or "—"),
                ("几何配置", curve.config or "—"),
                ("波长", wavelength),
                ("2θ 范围", rng),
                ("点数 / 平均步长",
                 f"{n_points} / {_num(step, '{:.5f}')}°"),
                ("处理链", chain)):
            rows.append(f"<tr><td class=\"left\">{_esc(label)}</td>"
                        f"<td class=\"left\">{_esc(value)}</td></tr>")
    current_group = None
    for group, label, value in curve.methods if curve is not None else []:
        if group != current_group:
            rows.append(f"<tr class=\"group\"><th colspan=\"2\">"
                        f"{_esc(group)}</th></tr>")
            current_group = group
        rows.append(f"<tr><td class=\"left\">{_esc(label)}</td>"
                    f"<td class=\"left\">{_esc(value)}</td></tr>")
    if snapshot.phase_methods:
        rows.append("<tr class=\"group\"><th colspan=\"2\">物相筛选</th></tr>")
        for label, value in snapshot.phase_methods:
            rows.append(f"<tr><td class=\"left\">{_esc(label)}</td>"
                        f"<td class=\"left\">{_esc(value)}</td></tr>")
    if snapshot.refine is not None and snapshot.refine.methods:
        rows.append("<tr class=\"group\"><th colspan=\"2\">全谱精修</th></tr>")
        for label, value in snapshot.refine.methods:
            rows.append(f"<tr><td class=\"left\">{_esc(label)}</td>"
                        f"<td class=\"left\">{_esc(value)}</td></tr>")
    if snapshot.insitu is not None and snapshot.insitu.methods:
        rows.append("<tr class=\"group\"><th colspan=\"2\">原位三相批量</th></tr>")
        for label, value in snapshot.insitu.methods:
            rows.append(f"<tr><td class=\"left\">{_esc(label)}</td>"
                        f"<td class=\"left\">{_esc(value)}</td></tr>")
    if not rows:
        return "", number
    html = (f"<div class=\"table-block long\">"
            f"{_table_caption(number, '数据与分析方法')}"
            "<table class=\"data\"><thead><tr>"
            "<th class=\"left\">项目</th><th class=\"left\">参数 / 值</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")
    return html, number + 1


def _section_curve(snapshot: ReportSnapshot, fig_no: int):
    curve = snapshot.curve
    if curve is None:
        return "", fig_no
    n_points, rng, _step = _curve_stats(curve)
    lead = (f"<p class=\"lead\">曲线为 2θ {_esc(rng)} 范围内的 1D 积分结果，"
            f"共 {n_points} 点。")
    if curve.peaks:
        lead += "橙色短线标出检出峰。"
    if curve.chain and curve.chain != "none" and curve.raw_intensity is not None:
        lead += "灰色细线为处理前曲线。"
    lead += "</p>"
    img = _fig_curve(curve)
    if not img:
        return lead, fig_no
    caption = f"图 {fig_no}　一维衍射曲线"
    if curve.peaks:
        caption += "（橙色短线 = 检出峰）"
    if curve.chain != "none" and curve.raw_intensity is not None:
        caption += "；灰色细线 = 处理前"
    body = (lead + f"<figure><img src=\"{img}\" alt=\"1D curve\">"
            f"<figcaption>{_esc(caption)}</figcaption></figure>")
    return body, fig_no + 1


def _section_peaks(snapshot: ReportSnapshot, number: int):
    curve = snapshot.curve
    if curve is None or not curve.peaks:
        return "", number
    head = ("<table class=\"data\"><thead><tr>"
            "<th>#</th><th>2θ (°)</th><th>FWHM (°)</th>"
            "<th>峰高</th><th>面积</th>"
            "</tr></thead><tbody>")
    rows = []
    for i, peak in enumerate(curve.peaks, start=1):
        rows.append(
            f"<tr><td>{i}</td><td>{_num(peak.center)}</td>"
            f"<td>{_num(peak.fwhm)}</td>"
            f"<td>{_num(peak.height, '{:.1f}')}</td>"
            f"<td>{_num(peak.area, '{:.1f}')}</td></tr>")
    note = ("<p class=\"muted\">峰高 = 显著度（本次寻峰未单独扣基线）；"
            "面积是基区内信号减去两端连线的积分。寻峰参数取自这条面板"
            "的当前设置。</p>")
    body = (f"<p class=\"lead\">按当前面板的寻峰参数检出 "
            f"<b>{len(curve.peaks)}</b> 根峰。</p>"
            f"<div class=\"table-block keep\">{_table_caption(number, '峰表')}"
            + head + "".join(rows) + "</tbody></table></div>" + note)
    return body, number + 1


def _section_fits(snapshot: ReportSnapshot, fig_no: int, number: int):
    curve = snapshot.curve
    if curve is None or not curve.fits:
        return "", fig_no, number
    head = ("<table class=\"data\"><thead><tr>"
            "<th>#</th><th>初始 2θ (°)</th><th>拟合 2θ (°)</th>"
            "<th>FWHM (°)</th><th>峰高</th><th>面积</th>"
            "<th>Rwp (%)</th><th>质量</th><th>线型</th><th>局部背景</th>"
            "<th>W-H</th></tr></thead><tbody>")
    shape_names = {"gaussian": "Gaussian", "lorentzian": "Lorentzian",
                   "pseudovoigt": "pseudo-Voigt",
                   "split_pearson7": "split Pearson VII"}
    bg_names = {"none": "无", "linear": "线性", "quadratic": "二次"}
    rows = []
    for i, fit in enumerate(curve.fits, start=1):
        quality, qcls = _rwp_quality(fit.rwp)
        rows.append(
            f"<tr><td>{i}</td><td>{_num(fit.center0)}</td>"
            f"<td>{_num(fit.center)}</td>"
            f"<td>{_num(fit.fwhm)}</td>"
            f"<td>{_num(fit.height, '{:.1f}')}</td>"
            f"<td>{_num(fit.area, '{:.1f}')}</td>"
            f"<td>{_num(fit.rwp, '{:.2f}')}</td>"
            f"<td class=\"{qcls}\">{_esc(quality)}</td>"
            f"<td>{_esc(shape_names.get(fit.shape, fit.shape))}</td>"
            f"<td>{_esc(bg_names.get(fit.background_mode, fit.background_mode))}</td>"
            f"<td>{'是' if fit.wh else '否'}</td></tr>")
    has_residual = _fits_have_residual(curve.fits)
    lead = (f"<p class=\"lead\">对 <b>{len(curve.fits)}</b> 根峰做单峰拟合；"
            "上图显示拟合叠加"
            + ("，下图显示 obs − model 残差。</p>" if has_residual
               else "。</p>"))
    note = ("<p class=\"muted\">Rwp 是单峰窗口内的局部残差：≤2% 良好、"
            "2–5% 可接受、&gt;5% 偏差大（检查峰形、半窗或局部背景）。</p>")
    table = (f"<div class=\"table-block keep\">{_table_caption(number, '单峰拟合结果')}"
             + head + "".join(rows) + "</tbody></table></div>" + note)
    figure = ""
    img = _fig_fits(curve)
    if img:
        caption = ("单峰拟合叠加与残差" if has_residual
                   else "单峰拟合叠加")
        figure = (f"<figure><img src=\"{img}\" alt=\"peak fits and residuals\">"
                  f"<figcaption>图 {fig_no}　{_esc(caption)}</figcaption>"
                  f"</figure>")
        fig_no += 1
    return lead + table + figure, fig_no, number + 1


def _section_wh(snapshot: ReportSnapshot, fig_no: int, number: int):
    curve = snapshot.curve
    if curve is None or curve.wh is None:
        return "", fig_no, number
    wh = curve.wh
    rows = [
        ("晶粒尺寸 D", f"{_num(wh.size_nm, '{:.1f}')} nm"),
        ("微应变 ε", _num(wh.strain, "{:.2e}")),
        ("R²", _num(wh.r2, "{:.3f}")),
        ("参与峰", f"{wh.n_used} 根"),
        ("截距", _num(wh.intercept, "{:.4g}")),
        ("斜率", _num(wh.slope, "{:.4g}")),
        ("仪器展宽", wh.instrument_note or "未扣仪器"),
    ]
    table = ("<table class=\"data\"><tbody>" + "".join(
        f"<tr><td class=\"left\">{_esc(label)}</td>"
        f"<td class=\"left\">{_esc(value)}</td></tr>"
        for label, value in rows) + "</tbody></table>")
    notes = []
    if wh.instrument_note:
        notes.append(f"仪器展宽：{wh.instrument_note}")
    if wh.warning:
        notes.append(f"提示：{wh.warning}")
    note_html = "".join(f"<p class=\"muted\">{_esc(text)}</p>"
                        for text in notes)
    lead = (f"<p class=\"lead\">Williamson–Hall 用 <b>{wh.n_used}</b> 根"
            "参与峰做线性拟合。</p>")
    block = (f"<div class=\"table-block keep\">"
             f"{_table_caption(number, 'Williamson–Hall 结果')}"
             + table + "</div>" + note_html)
    figure = ""
    img = _fig_wh(wh)
    if img:
        figure = (f"<figure><img src=\"{img}\" alt=\"Williamson-Hall\">"
                  f"<figcaption>图 {fig_no}　"
                  f"Williamson–Hall 线性拟合</figcaption></figure>")
        fig_no += 1
    return lead + block + figure, fig_no, number + 1


def _section_phases(snapshot: ReportSnapshot, fig_no: int, number: int):
    if not snapshot.phases:
        return "", fig_no, number
    head = ("<table class=\"data\"><thead><tr>"
            "<th>候选相</th><th>score</th><th>coverage</th>"
            "<th>purity</th><th>scale</th><th>叠加</th>"
            "</tr></thead><tbody>")
    rows = []
    for row in snapshot.phases:
        rows.append(
            f"<tr><td>{_esc(row.name)}</td>"
            f"<td>{_num(row.score, '{:.3f}')}</td>"
            f"<td>{_num(row.coverage, '{:.3f}')}</td>"
            f"<td>{_num(row.purity, '{:.3f}')}</td>"
            f"<td>{_num(row.scale, '{:.4g}')}</td>"
            f"<td>{'是' if row.selected else '否'}</td></tr>")
    picked = sum(1 for row in snapshot.phases if row.selected)
    src = (f"<p class=\"muted\">观测曲线：{_esc(snapshot.phase_source)}</p>"
           if snapshot.phase_source else "")
    lead = (f"<p class=\"lead\">共 <b>{len(snapshot.phases)}</b> 个候选相，"
            f"叠加图展示 {picked} 个选中的相。</p>")
    block = (f"<div class=\"table-block keep\">"
             f"{_table_caption(number, '物相筛选候选')}"
             + src + head + "".join(rows) + "</tbody></table></div>")
    figure = ""
    img = _fig_phases(snapshot)
    if img:
        figure = (f"<figure><img src=\"{img}\" alt=\"phase candidates\">"
                  f"<figcaption>图 {fig_no}　候选相理论峰位（短线）"
                  f"与观测曲线</figcaption></figure>")
        fig_no += 1
    return lead + block + figure, fig_no, number + 1


def _section_refine(snapshot: ReportSnapshot, fig_no: int, number: int):
    refine = snapshot.refine
    if refine is None:
        return "", fig_no, number
    phase = refine.phase_label or Path(refine.cif).stem or "主相"
    mode = "Le Bail " if refine.lebail else "结构"
    lead = (f"<p class=\"lead\">对 <b>{refine.n_obs}</b> 个 2θ 点、"
            f"<b>{refine.n_params}</b> 个参数做{mode}全谱精修"
            f"（{_esc(phase)}，{_esc(Path(refine.cif).name or '—')}），"
            f"Rwp = <b>{_num(refine.rwp, '{:.2f}')}%</b>、"
            f"Rp = {_num(refine.rp, '{:.2f}')}%、"
            f"reduced χ² = {_num(refine.reduced_chi2, '{:.3g}')}。</p>")
    body = [lead]
    if not refine.success:
        body.append(
            f"<p class=\"note\">优化器退出标志：{_esc(refine.message)}；"
            "结果仍按当前解报告，请结合拟合图与参数 esd 判断。</p>")
    img = _fig_profile(refine.profile)
    if img:
        body.append(
            f"<figure><img src=\"{img}\" alt=\"whole-pattern refinement\">"
            f"<figcaption>图 {fig_no}　全谱精修：观测 / 计算 / 背景与 "
            f"obs − calc 差值（底部为布拉格峰位）</figcaption></figure>")
        fig_no += 1
    if refine.params:
        head = ("<table class=\"data\"><thead><tr>"
                "<th class=\"left\">参数</th><th>初值</th><th>拟合值</th>"
                "<th>esd</th></tr></thead><tbody>")
        rows = []
        for name, initial, value, esd in refine.params:
            rows.append(
                f"<tr><td class=\"left\">{_esc(name)}</td>"
                f"<td>{_num(initial, '{:.6g}')}</td>"
                f"<td>{_num(value, '{:.6g}')}</td>"
                f"<td>{_num(esd, '{:.3g}')}</td></tr>")
        body.append(
            f"<div class=\"table-block keep\">"
            f"{_table_caption(number, '精修参数')}"
            + head + "".join(rows) + "</tbody></table></div>")
        number += 1
    if refine.weights:
        head = ("<table class=\"data\"><thead><tr>"
                "<th class=\"left\">相</th><th>wt%</th><th>scale</th>"
                "<th>Z</th><th>M (g/mol)</th><th>V (Å³)</th>"
                "<th class=\"left\">来源</th>"
                "<th class=\"left\">备注</th></tr></thead><tbody>")
        source_names = {"structure": "结构模式", "le_bail": "Le Bail"}
        rows = []
        for weight in refine.weights:
            rows.append(
                f"<tr><td class=\"left\">{_esc(weight.label)}</td>"
                f"<td>{_num(weight.weight_percent, '{:.2f}')}</td>"
                f"<td>{_num(weight.scale, '{:.4g}')}</td>"
                f"<td>{_num(weight.z, '{:.3g}')}</td>"
                f"<td>{_num(weight.molar_mass, '{:.3f}')}</td>"
                f"<td>{_num(weight.volume, '{:.3f}')}</td>"
                f"<td class=\"left\">"
                f"{_esc(source_names.get(weight.source, weight.source))}</td>"
                f"<td class=\"left\">"
                f"{_esc(weight.warning or '—')}</td></tr>")
        body.append(
            f"<div class=\"table-block keep\">"
            f"{_table_caption(number, '定量相分数（wt%）')}"
            + head + "".join(rows) + "</tbody></table></div>")
        number += 1
    if refine.site_coords:
        head = ("<table class=\"data\"><thead><tr>"
                "<th class=\"left\">位点</th><th>Wyckoff</th>"
                "<th>x</th><th>y</th><th>z</th>"
                "</tr></thead><tbody>")
        rows = []
        for label, wyckoff, x, y, z in refine.site_coords:
            rows.append(
                f"<tr><td class=\"left\">{_esc(label)}</td>"
                f"<td>{_esc(wyckoff)}</td>"
                f"<td>{_num(x, '{:.5f}')}</td>"
                f"<td>{_num(y, '{:.5f}')}</td>"
                f"<td>{_num(z, '{:.5f}')}</td></tr>")
        body.append(
            f"<div class=\"table-block keep\">"
            f"{_table_caption(number, '精修后的位点坐标（分数坐标）')}"
            + head + "".join(rows) + "</tbody></table></div>")
        number += 1
    if refine.note:
        body.append(f"<p class=\"muted\">{_esc(refine.note)}</p>")
    if refine.weight_note:
        body.append(f"<p class=\"muted\">{_esc(refine.weight_note)}</p>")
    return "".join(body), fig_no, number


def _section_insitu(snapshot: ReportSnapshot, fig_no: int, number: int):
    insitu = snapshot.insitu
    if insitu is None or not insitu.rows:
        return "", fig_no, number
    rows = list(insitu.rows)
    stats = _insitu_stats(rows)
    body = []
    rwp_text = ("—" if not math.isfinite(stats["rwp_min"])
                else f"{stats['rwp_min']:.2f}–{stats['rwp_max']:.2f}"
                     f"（中位 {stats['rwp_med']:.2f}）")
    rwp_phrase = ("Rwp —" if rwp_text == "—" else f"Rwp {rwp_text}%")
    lead = (f"<p class=\"lead\">本次原位三相批量共 <b>{stats['total']}</b> 帧，"
            f"可用 <b>{stats['usable']}</b> 帧；{rwp_phrase}。")
    if stats["has_f"]:
        lead += (f"f_rich 从 {_num(stats['f_start'], '{:.3f}')} 变到 "
                 f"{_num(stats['f_end'], '{:.3f}')}，最低 "
                 f"{_num(stats['f_min'], '{:.3f}')}（第 "
                 f"{_esc(stats['f_min_frame'])} 帧）。")
    lead += "</p>"
    body.append(lead)

    status_text = "、".join(
        f"{key}={value}" for key, value in sorted(stats["status"].items()))
    wt_rich = [row.get("wt_rich") for row in rows]
    wt_poor = [row.get("wt_poor") for row in rows]
    summary_rows = [
        ("帧数", str(stats["total"])),
        ("可用帧", f"{stats['usable']}/{stats['total']}"),
        ("状态统计", status_text or "—"),
        ("Rwp (%)", rwp_text),
        ("f_rich", (f"{_num(stats['f_start'], '{:.3f}')} → "
                    f"{_num(stats['f_end'], '{:.3f}')}"
                    if stats["has_f"] else "—")),
        ("wt_rich (%)", _range_text(wt_rich)),
        ("wt_poor (%)", _range_text(wt_poor)),
    ]
    table = ("<table class=\"data\"><tbody>" + "".join(
        f"<tr><td class=\"left\">{_esc(label)}</td>"
        f"<td class=\"left\">{_esc(value)}</td></tr>"
        for label, value in summary_rows) + "</tbody></table>")
    body.append(
        f"<div class=\"table-block keep\">"
        f"{_table_caption(number, '原位批量摘要')}" + table + "</div>")
    number += 1

    img = _fig_insitu_summary(insitu)
    if img:
        body.append(
            f"<figure><img src=\"{img}\" alt=\"in-situ batch summary\">"
            f"<figcaption>图 {fig_no}　原位三相批量汇总：相分数、两相晶胞、"
            f"Rwp / Rp 与异常状态（底色按 f_rich 极小值划分充/放电段）"
            f"</figcaption></figure>")
        fig_no += 1

    img = _fig_profile(insitu.frame_fit)
    if img:
        title = insitu.frame_fit.title if insitu.frame_fit else ""
        body.append(
            f"<figure><img src=\"{img}\" alt=\"in-situ single frame fit\">"
            f"<figcaption>图 {fig_no}　代表性单帧拟合"
            f"{('：' + _esc(title)) if title else ''}</figcaption></figure>")
        fig_no += 1

    img = _fig_insitu_model_independent(insitu)
    if img:
        body.append(
            f"<figure><img src=\"{img}\" alt=\"model-independent check\">"
            f"<figcaption>图 {fig_no}　模型无关检查：(200) 双峰质心 / "
            f"面积与批量相分数的一致性</figcaption></figure>")
        fig_no += 1

    head = ("<table class=\"data\"><thead><tr>"
            "<th>帧</th><th class=\"left\">状态</th><th>Rwp (%)</th>"
            "<th>f_rich</th><th>wt_rich (%)</th><th>wt_poor (%)</th>"
            "<th>rich a</th><th>rich c</th>"
            "<th>poor a</th><th>poor c</th>"
            "</tr></thead><tbody>")
    frame_rows = []
    for row in rows:
        status = str(row.get("status", ""))
        if status.startswith(("error", "high_rwp")):
            qcls = "q-bad"
        elif status != "ok":
            qcls = "q-warn"
        else:
            qcls = "q-ok"
        # 异常状态可能带整段异常信息：表里截短，悬停看全，别把表撑爆
        short_status = (status if len(status) <= 32
                        else status[:29] + "…")
        frame_rows.append(
            f"<tr><td>{_esc(row.get('frame', '—'))}</td>"
            f"<td class=\"left {qcls}\" title=\"{_esc(status)}\">"
            f"{_esc(short_status or '—')}</td>"
            f"<td>{_num(row.get('rwp'), '{:.2f}')}</td>"
            f"<td>{_num(row.get('f_rich'), '{:.3f}')}</td>"
            f"<td>{_num(row.get('wt_rich'), '{:.2f}')}</td>"
            f"<td>{_num(row.get('wt_poor'), '{:.2f}')}</td>"
            f"<td>{_num(row.get('rich_a'))}</td>"
            f"<td>{_num(row.get('rich_c'))}</td>"
            f"<td>{_num(row.get('poor_a'))}</td>"
            f"<td>{_num(row.get('poor_c'))}</td></tr>")
    body.append(
        f"<div class=\"table-block long\">"
        f"{_table_caption(number, '逐帧结果（完整）')}"
        + head + "".join(frame_rows) + "</tbody></table></div>")
    number += 1

    note = ("<p class=\"muted\">可用 = Rwp 未超过阈值、晶胞有限且状态不是 "
            "error / high_rwp；ok_soft = 优化器退出标志为 False，但 "
            "Rwp 与相分数仍合格。f_rich 是 Le Bail 相对散射强度分数；"
            "wt_rich / wt_poor 只在两个 LMFP 晶态相之间归一化，Al 参考相"
            "单列、不参与分母。</p>")
    body.append(note)
    return "".join(body), fig_no, number


def _limitations(snapshot: ReportSnapshot) -> str:
    notes = list(snapshot.notes)
    if snapshot.curve is not None:
        notes += list(snapshot.curve.notes)
    extra = ""
    if notes:
        items = "".join(f"<li>{_esc(text)}</li>" for text in notes)
        extra = ("<h3><span class=\"num\">4.4</span>本次报告的特殊说明</h3>"
                 f"<ul>{items}</ul>")
    return f"""
<div class="limits">
  <h3><span class="num">4.1</span>定量说明</h3>
  <ul>
    <li>精修的 wt% 只对拟合模型里的晶态相有效；非晶或未计入的相不在
        分母里。Le Bail 相用逐反射强度的加权平均，精度低于结构模式。</li>
    <li>原位的 f_rich 是 Le Bail 相对散射强度分数，不是 wt%；
        wt_rich / wt_poor 只在两个 LMFP 晶态相之间归一化，Al 参考相
        单列、不参与分母。</li>
    <li>背景用 estimated 模型时有系统偏差：合成已知配比回收的 bias
        −1.8 wt%、RMS 2.8 wt%；真实 LMFP 数据（ipoly0）对 GSAS-II 的
        平均绝对偏差约 4.2 wt%。</li>
  </ul>
  <h3><span class="num">4.2</span>模型与数据边界</h3>
  <ul>
    <li>W-H 的 D 与 ε 来自单峰拟合的峰宽；esd 只是线性拟合的下限，
        低角峰重叠时只作趋势看。</li>
    <li>单峰拟合的 Rwp 是窗口内局部残差，不等同于全谱 Rwp；报告按
        ≤2% / 2–5% / &gt;5% 标“良好 / 可接受 / 偏差大”。</li>
    <li>物相筛选只做峰位匹配，不做自动指标化；score / coverage / purity
        是候选排序，不是相含量。</li>
    <li>少数相（&lt;50%）的晶胞在弱峰帧不可独立辨识；界面上以淡色显示，
        报告不把它当独立结论。</li>
    <li>原位每帧从上一帧种子与 CIF 端元双起点拟合，Rwp 打平时取晶胞
        变化小的解；not_converged / cell_jump 等状态照实收录，不中断
        批次、也不把异常帧删掉。</li>
  </ul>
  <h3><span class="num">4.3</span>报告范围</h3>
  <ul>
    <li>本报告是当前会话的快照：精修/物相/原位的中间结果只保留在当前
        会话里，关程序后无法补做；1D 与批处理产物另有文件可追溯。</li>
    <li>报告只汇总已经算好的结果，不重新积分、不重跑拟合；精修 / 原位
        两节也遵循同一口径。</li>
    <li>报告里的 Rwp 有三种口径：单峰窗口局部 Rwp、全谱精修 Rwp、原位
        逐帧 Rwp，数值不能互相比较。</li>
  </ul>
  {extra}
</div>
"""


def build_report_html(snapshot: ReportSnapshot) -> str:
    """快照 → 自包含 HTML 字符串（不写文件，纯函数）。"""
    sections = tuple(key for key in snapshot.sections if key in _ALL_KEYS)
    snapshot = replace(snapshot, sections=sections)
    result_sections = _result_sections(snapshot)
    parts = [
        "<!DOCTYPE html>",
        "<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, "
        "initial-scale=1\">",
        f"<title>{_esc(snapshot.title or 'XRD 分析报告')}</title>",
        f"<style>{_CSS}</style></head><body><main>",
        _cover(snapshot),
        _toc(snapshot, result_sections),
        "<section id=\"s1\">",
        "<h2><span class=\"num\">1</span>报告摘要</h2>",
        _kpis(snapshot),
        _summary_text(snapshot),
        "<h3><span class=\"num\">1.1</span>本次报告范围</h3>",
        _scope_table(snapshot),
        "</section>",
    ]

    table_no = 1
    methods_html, table_no = _methods_table(snapshot, table_no)
    parts += [
        "<section id=\"s2\">",
        "<h2><span class=\"num\">2</span>数据与方法</h2>",
        "<p class=\"lead\">本节列出报告用到的数据与参数；所有结果均由"
        "当前会话的已算好数据汇总，未在本报告中重新积分或重跑拟合。</p>",
    ]
    if methods_html:
        parts.append(methods_html)
    else:
        parts.append("<p class=\"note\">本次没有可用的数据，方法表为空。</p>")
    parts.append("</section>")

    parts += ["<section id=\"s3\">",
              "<h2><span class=\"num\">3</span>结果</h2>"]
    if result_sections:
        fig_no = 1
        builders = {
            "curve": _section_curve,
            "peaks": _section_peaks,
            "fits": _section_fits,
            "wh": _section_wh,
            "phases": _section_phases,
            "refine": _section_refine,
            "insitu": _section_insitu,
        }
        for i, (key, title) in enumerate(result_sections, start=1):
            parts.append(
                f"<h3 id=\"s3-{i}\"><span class=\"num\">3.{i}</span>"
                f"{_esc(title)}</h3>")
            builder = builders[key]
            if key == "curve":
                body, fig_no = builder(snapshot, fig_no)
            elif key == "peaks":
                body, table_no = builder(snapshot, table_no)
            else:
                body, fig_no, table_no = builder(snapshot, fig_no, table_no)
            parts.append(body)
    else:
        parts.append("<p class=\"note\">本次没有可展示的结果；"
                     "报告范围表列出的“本次未做”项说明了原因。</p>")
    parts.append("</section>")

    parts += [
        "<section id=\"s4\">",
        "<h2><span class=\"num\">4</span>结果说明与限制</h2>",
        _limitations(snapshot),
        "</section>",
    ]
    generated = snapshot.generated or datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S")
    parts.append(
        f"<div class=\"footer\"><span>XRD Toolkit "
        f"{_esc(snapshot.version or '')} · {_esc(_report_id(snapshot))}"
        f"</span><span>{_esc(generated)} · 本报告为当前会话快照</span></div>")
    parts.append("</main></body></html>")
    return "\n".join(parts)


def write_report(snapshot: ReportSnapshot, path) -> Path:
    """生成并写盘（UTF-8）；返回实际路径（调用方负责建目录）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_report_html(snapshot), encoding="utf-8")
    return target
