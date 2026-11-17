"""通用 N 相原位视图（2026-11-17）。

从 `insitu_plots` 的 LMFP 专用四视图抽出来的通用版：

  * draw_multi_trends   —— 样品相分数堆叠 + 每相 a/c 相对变化 + Rwp；
  * draw_multi_summary  —— 分数、每相 a/c、Rwp/状态汇总；
  * draw_window_tracker —— 用户配置的 2θ 峰窗（质心/面积/峰高），
                           可与某个样品相的分数做相关（模型无关检查）。

行数据来自 `GenericBatch.rows`：`f_<label>`、`<label>_a`、`<label>_c`、
`win_<k>_centroid/area/height`、`rwp`、`status`。LMFP 预设只是把
PhaseSpec 的 display_name/颜色/分数对和 (200) 峰窗填好，不写死物理。
"""
import math

import numpy as np

from xrd_toolkit.simulation.insitu_plots import (
    MINORITY_FRACTION, _finite_pairs, _num, _placeholder, _status_markers)

_PALETTE = ("#1f5fa8", "#e08a1e", "#2a9d8f", "#8e5aa8",
            "#c0392b", "#16a085", "#7f8c8d", "#d35400")


def _color(phase, index):
    return getattr(phase, "color", None) or _PALETTE[index % len(_PALETTE)]


def _rows(rows):
    out = []
    for row in rows or []:
        try:
            int(row.get("frame"))
        except (TypeError, ValueError):
            continue
        item = dict(row)
        status = str(item.get("status") or "ok")
        item.setdefault("group", status.split("(", 1)[0] or "ok")
        out.append(item)
    return sorted(out, key=lambda r: int(r["frame"]))


def _shade(ax, annotations, lo, hi):
    for item in annotations or []:
        try:
            start, end, label = item
        except (TypeError, ValueError):
            continue
        ax.axvspan(float(start), float(end), color="0.92", alpha=0.6,
                   lw=0, zorder=0, label=label)


def draw_multi_trends(fig, rows, phase_set, *, title=None):
    """样品相分数堆叠 / 每相 a,c 相对变化 / Rwp。"""
    rows = _rows(rows)
    if not rows:
        return _placeholder(fig, "In-situ multi-phase batch",
                            "Choose a frame folder and press [Start]")
    samples = phase_set.sample_phases()
    frames = np.array([int(r["frame"]) for r in rows])
    lo, hi = frames.min() - 0.5, frames.max() + 0.5
    fig.clear()
    axes = fig.subplots(2, 2)
    ax = axes[0, 0]
    _shade(ax, phase_set.views.annotations, lo, hi)
    base = np.zeros(len(rows))
    for i, phase in enumerate(samples):
        vals = np.array([_num(r.get(f"f_{phase.label}"), 0.0)
                         for r in rows])
        vals = np.nan_to_num(vals, nan=0.0)
        ax.fill_between(frames, base, base + vals, alpha=0.35,
                        color=_color(phase, i),
                        label=f"{phase.plot_label()} (f)")
        base = base + vals
    pair = phase_set.views.crossover_pair
    if pair:
        ax.axhline(0.5, color="0.45", lw=0.8, ls="--")
    ax.set_ylim(0.0, 1.0)
    ax.set_xlim(lo, hi)
    ax.set_ylabel("relative fraction", fontsize=8)
    ax.set_title("(a) Sample phase fractions", fontsize=9)
    ax.grid(alpha=0.25, linestyle=":")
    if samples:
        ax.legend(fontsize=7, loc="best")

    for idx, axis in enumerate(("a", "c")):
        ax = axes[0, 1] if idx == 0 else axes[1, 0]
        _shade(ax, phase_set.views.annotations, lo, hi)
        for i, phase in enumerate(samples):
            raw = np.array([_num(r.get(f"{phase.label}_{axis}"))
                            for r in rows], dtype=float)
            good = np.isfinite(raw) & (raw > 0)
            base_v = float(raw[good][0]) if np.any(good) else None
            if base_v is None:
                continue
            rel = np.where(np.isfinite(raw),
                           100.0 * (raw / base_v - 1.0), np.nan)
            ax.plot(frames, rel, "-", lw=0.9, color=_color(phase, i),
                    alpha=0.35,
                    label=f"{phase.plot_label()} ({axis}₀ {base_v:.3f} Å)")
            # 实心点 = 该相分数 >= 50%，淡色 = 少数相估计
            solid = np.array([_num(r.get(f"f_{phase.label}"), 0.0)
                              >= MINORITY_FRACTION for r in rows])
            if np.any(solid & np.isfinite(rel)):
                ax.plot(frames[solid & np.isfinite(rel)],
                        rel[solid & np.isfinite(rel)], "o", ms=2.2,
                        color=_color(phase, i), label="_nolegend_")
        ax.axhline(0.0, color="0.7", lw=0.6)
        ax.set_xlim(lo, hi)
        ax.set_ylabel(f"Δ{axis}/{axis}₀ (%)", fontsize=8)
        ax.set_title(f"({'b' if idx == 0 else 'c'}) Cell {axis} change",
                     fontsize=9)
        ax.grid(alpha=0.25, linestyle=":")
        if ax.get_legend_handles_labels()[0]:
            ax.legend(fontsize=7, loc="best")

    ax = axes[1, 1]
    _shade(ax, phase_set.views.annotations, lo, hi)
    rwp = np.array([_num(r.get("rwp")) for r in rows], dtype=float)
    ax.plot(frames, rwp, "o-", ms=2.5, color="#c0392b")
    _status_markers(ax, rows, "rwp")
    ax.set_xlim(lo, hi)
    ax.set_xlabel("frame", fontsize=8)
    ax.set_ylabel("Rwp (%)", fontsize=8)
    ax.set_title("(d) Rwp / anomaly status", fontsize=9)
    ax.grid(alpha=0.25, linestyle=":")
    for ax in axes.ravel():
        ax.tick_params(labelsize=7)
    fig.suptitle(title or f"In-situ multi-phase batch — {len(rows)} frames",
                 fontsize=11)
    fig.text(0.5, 0.01,
             "phase fractions are relative scattering intensity, not wt%; "
             "faded cell markers = minority-phase estimate (not reliable).",
             ha="center", fontsize=6.5, color="0.4")
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    return fig


def draw_multi_summary(fig, rows, phase_set, *, title=None):
    """汇总：分数 / 每相 a / 每相 c / Rwp+状态（无 esd 时只画点线）。"""
    rows = _rows(rows)
    if not rows:
        return _placeholder(fig, "In-situ multi-phase batch",
                            "Choose a frame folder and press [Start]")
    samples = phase_set.sample_phases()
    frames = np.array([int(r["frame"]) for r in rows])
    lo, hi = frames.min() - 0.5, frames.max() + 0.5
    fig.clear()
    axes = fig.subplots(2, 2)
    panels = [(axes[0, 0], "f"), (axes[0, 1], "a"),
              (axes[1, 0], "c"), (axes[1, 1], "rwp")]
    for ax, kind in panels:
        _shade(ax, phase_set.views.annotations, lo, hi)
        ax.set_xlim(lo, hi)
        ax.grid(alpha=0.25, linestyle=":")
        ax.tick_params(labelsize=7)
    ax = axes[0, 0]
    for i, phase in enumerate(samples):
        vals = [_num(r.get(f"f_{phase.label}")) for r in rows]
        ax.plot(frames, vals, "o-", ms=2.2, lw=0.8,
                color=_color(phase, i), label=phase.plot_label())
    ax.set_ylim(-0.02, 1.02)
    ax.set_ylabel("relative fraction", fontsize=8)
    ax.set_title("(a) Sample phase fractions", fontsize=9)
    if samples:
        ax.legend(fontsize=7, loc="best")
    for ax, axis in ((axes[0, 1], "a"), (axes[1, 0], "c")):
        for i, phase in enumerate(samples):
            vals = [_num(r.get(f"{phase.label}_{axis}")) for r in rows]
            ax.plot(frames, vals, "o-", ms=2.2, lw=0.8,
                    color=_color(phase, i), label=phase.plot_label())
        ax.set_ylabel(f"{axis} (Å)", fontsize=8)
        ax.set_title(f"({'b' if axis == 'a' else 'c'}) Cell {axis}",
                     fontsize=9)
        if samples:
            ax.legend(fontsize=7, loc="best")
    ax = axes[1, 1]
    ax.plot(frames, [_num(r.get("rwp")) for r in rows], "o-",
            ms=2.2, lw=0.8, color="#c0392b", label="Rwp")
    _status_markers(ax, rows, "rwp")
    ax.set_xlabel("frame", fontsize=8)
    ax.set_ylabel("%", fontsize=8)
    ax.set_title("(d) Rwp / anomaly status", fontsize=9)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=7, loc="lower left")
    n_ok = sum(1 for r in rows if str(r.get("status")) == "ok")
    fig.suptitle(title or (f"In-situ multi-phase summary — {len(rows)} "
                           f"frames, ok {n_ok}/{len(rows)}"), fontsize=11)
    fig.tight_layout(rect=(0, 0.02, 1, 0.95))
    return fig


def draw_window_tracker(fig, rows, phase_set, *, title=None):
    """峰窗跟踪：质心/面积/峰高 vs 帧，质心 vs 某样品相分数。"""
    rows = _rows(rows)
    if not rows or phase_set is None:
        return _placeholder(
            fig, "Peak-window tracker",
            "Add a 2θ window in the preset to track a peak model-independently")
    windows = list(getattr(phase_set.views, "peak_windows", []) or [])
    if not windows:
        return _placeholder(
            fig, "Peak-window tracker",
            "Add a 2θ window in the preset to track a peak model-independently")
    frames = np.array([int(r["frame"]) for r in rows])
    fig.clear()
    axes = fig.subplots(2, 2)
    ax = axes[0, 0]
    for k, window in enumerate(windows):
        vals = [_num(r.get(f"win_{k}_centroid")) for r in rows]
        ax.plot(frames, vals, "o-", ms=2.0, lw=0.8,
                color=_PALETTE[k % len(_PALETTE)], label=window.name)
    ax.set_xlabel("frame", fontsize=8)
    ax.set_ylabel("centroid 2θ (°)", fontsize=8)
    ax.set_title("(a) Window centroid", fontsize=9)
    ax.grid(alpha=0.25, linestyle=":")
    if len(windows):
        ax.legend(fontsize=7, loc="best")
    ax = axes[0, 1]
    for k, window in enumerate(windows):
        vals = [_num(r.get(f"win_{k}_area")) for r in rows]
        ax.plot(frames, vals, "o-", ms=2.0, lw=0.8,
                color=_PALETTE[k % len(_PALETTE)], label=window.name)
    ax.set_xlabel("frame", fontsize=8)
    ax.set_ylabel("area", fontsize=8)
    ax.set_title("(b) Window area", fontsize=9)
    ax.grid(alpha=0.25, linestyle=":")
    if len(windows):
        ax.legend(fontsize=7, loc="best")
    ax = axes[1, 0]
    for k, window in enumerate(windows):
        vals = [_num(r.get(f"win_{k}_height")) for r in rows]
        ax.plot(frames, vals, "o-", ms=2.0, lw=0.8,
                color=_PALETTE[k % len(_PALETTE)], label=window.name)
    ax.set_xlabel("frame", fontsize=8)
    ax.set_ylabel("peak height", fontsize=8)
    ax.set_title("(c) Window height", fontsize=9)
    ax.grid(alpha=0.25, linestyle=":")
    ax = axes[1, 1]
    drawn = False
    for k, window in enumerate(windows):
        if not window.vs_phase:
            continue
        xs = np.array([_num(r.get(f"f_{window.vs_phase}"))
                       for r in rows], dtype=float)
        ys = np.array([_num(r.get(f"win_{k}_centroid"))
                       for r in rows], dtype=float)
        ok = np.isfinite(xs) & np.isfinite(ys)
        if np.count_nonzero(ok) < 2:
            continue
        color = _PALETTE[k % len(_PALETTE)]
        ax.plot(xs[ok], ys[ok], "o", ms=2.5, color=color,
                label=f"{window.name} vs f_{window.vs_phase}")
        if np.count_nonzero(ok) >= 3:
            try:
                coef = np.polyfit(xs[ok], ys[ok], 1)
                yy = np.polyval(coef, xs[ok])
                ss_res = float(np.sum((ys[ok] - yy) ** 2))
                ss_tot = float(np.sum((ys[ok] - np.mean(ys[ok])) ** 2))
                r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
                if math.isfinite(r2):
                    ax.text(0.04, 0.94, f"R² = {r2:.3f}",
                            transform=ax.transAxes, fontsize=7, va="top",
                            color="0.3")
            except (ValueError, TypeError):
                pass
        drawn = True
    ax.set_xlabel("sample phase fraction", fontsize=8)
    ax.set_ylabel("centroid 2θ (°)", fontsize=8)
    ax.set_title("(d) Centroid vs fraction (model-independent)",
                 fontsize=9)
    ax.grid(alpha=0.25, linestyle=":")
    if drawn:
        ax.legend(fontsize=7, loc="best")
    for ax in axes.ravel():
        ax.tick_params(labelsize=7)
    fig.suptitle(title or f"Peak-window tracker — {len(rows)} frames",
                 fontsize=11)
    fig.tight_layout(rect=(0, 0.02, 1, 0.95))
    return fig
