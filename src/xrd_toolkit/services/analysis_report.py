"""一键分析报告：把当前会话的结果汇成一份自包含 HTML。

纯生成器：不碰 Qt、不重新积分、不重跑拟合。输入一份 `ReportSnapshot`
（由 GUI 从当前面板/工作台读出来），输出 HTML 字符串或写文件；图用
Matplotlib 的 Agg 画布按脚本尺寸重画（不抓 Qt 屏幕），base64 内嵌，
浏览器打开即可阅读、可直接打印成 PDF。

为什么单独放 services（2026-11-08）：生成器要能被单测直接调用，不依赖
窗口；GUI 只负责"收集快照 + 选路径"。以后要加精修/原位节，也只在
`ReportSnapshot` 上加字段，不动生成器的主干。

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
        else:
            done, detail = False, "—"
        rows.append((title, done, detail))
    rows.append(("结果说明与限制", True, "固定说明"))
    rows.append(("精修 / 原位", False, "报告 v1 范围外，后续版本补"))
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
    available = {"curve": curve is not None,
                 "peaks": bool(curve.peaks), "fits": bool(curve.fits),
                 "wh": curve.wh is not None,
                 "phases": bool(snapshot.phases)}
    missing = [title for key, title in SECTIONS
               if key in snapshot.sections and not available[key]]
    missing.append("精修 / 原位（v1.1）")
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
    <li>精修 wt% 只对拟合模型里的晶态相有效；非晶或未计入的相不在分母里。</li>
    <li>原位/物相的 f_rich 是 Le Bail 相对散射强度分数，不是 wt%。</li>
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
  </ul>
  <h3><span class="num">4.3</span>报告范围</h3>
  <ul>
    <li>本报告是当前会话的快照：精修/物相/原位的中间结果只保留在当前
        会话里，关程序后无法补做；1D 与批处理产物另有文件可追溯。</li>
    <li>精修 / 原位两节留到后续版本（v1.1）；本次报告的范围表已注明。</li>
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
