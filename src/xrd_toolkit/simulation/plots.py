"""XRD 模拟图谱的绘图工具（与计算分离，matplotlib 属主依赖）。

三个绘图入口：

- `plot_pattern`：竖线（棒状）谱图。理论峰是无限窄的，模拟谱的标准画法
  就是一排竖线；
- `plot_annotated`：在每根峰上方竖排标注 (hkl)（六方默认四位 hkil，可切三位卡片式），
  相邻标签过近时自动抬高一档，避免叠字；
- `plot_profile`（2026-10-17 第七周加）：连续模拟谱——细网格曲线为主体，
  可选把理想竖线以浅色垫在下面（一眼看出"理论棒 → 模拟谱"的关系），
  峰顶同样竖排 (hkl) 标注（沿用同一套避叠字规矩）。
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from xrd_toolkit.simulation.core import format_hkls

FOOTPRINT_DEG = 1.35   # 竖排标签的横向"占地"（度）：小于这个距离就躲到上一档
TIER_GAP = 15.0        # 每档抬高多少（强度单位）


def plot_pattern(two_thetas, intensities, title, out_path):
    """竖线谱图：横轴 2θ，纵轴相对强度（最强峰 100）。返回输出路径。"""
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.vlines(two_thetas, 0, intensities, color="#2b6cb0", lw=1.8)
    ax.plot(two_thetas, intensities, "o", color="#2b6cb0", ms=3)
    ax.set_xlabel(r"2$\theta$ (degrees)")
    ax.set_ylabel("Intensity (a.u.)")
    ax.set_title(title)
    ax.set_xlim(float(two_thetas.min()) - 2, float(two_thetas.max()) + 2)
    ax.set_ylim(0, 110)
    ax.grid(alpha=0.25, linestyle=":")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_annotated(two_thetas, intensities, peaks, is_hex, title, out_path,
                   four_index=True):
    """带 (hkl) 标注的谱图（第六周最终交付图的画法）。

    peaks 用 core.pattern_from_structure 返回的峰表（含代表 hkl 与多重性）；
    four_index=False 时六方晶系改用三位指数（如 (104)），与 PDF 卡片行号对照方便。
    返回输出路径。
    """
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.vlines(two_thetas, 0, intensities, color="#2b6cb0", lw=1.8)
    ax.plot(two_thetas, intensities, "o", color="#2b6cb0", ms=3.2)

    last_x = []   # 每一档最后放过的 x（度）
    for tt, ii, refs in peaks:
        tier = 0
        while tier < len(last_x) and tt - last_x[tier] < FOOTPRINT_DEG:
            tier += 1
        if tier == len(last_x):
            last_x.append(tt)
        else:
            last_x[tier] = tt
        ax.text(tt, ii + 3 + tier * TIER_GAP, format_hkls(refs, is_hex, four_index),
                rotation=90, ha="center", va="bottom",
                fontsize=8.5, color="#333333")

    ax.set_xlim(8, 82)
    ax.set_ylim(0, 140)
    ax.set_xlabel(r"2$\theta$ (degrees)")
    ax.set_ylabel("Intensity (a.u.)")
    ax.set_title(title)
    ax.text(0.012, 0.02, "peak labels: (hkl) — in-house simulation from CIF",
            transform=ax.transAxes, ha="left", fontsize=7.5, color="0.45")
    ax.grid(alpha=0.25, linestyle=":")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def _annotate_peaks(ax, tth, curve, peaks, is_hex, zero_deg, four_index):
    """峰顶竖排 (hkl) 标注（避叠字规矩同 plot_annotated）。"""
    last_x = []   # 每一档最后放过的 x（度）
    for tt, _ii, refs in peaks:
        x = tt + zero_deg
        y = float(np.interp(x, tth, curve))
        tier = 0
        while tier < len(last_x) and x - last_x[tier] < FOOTPRINT_DEG:
            tier += 1
        if tier == len(last_x):
            last_x.append(x)
        else:
            last_x[tier] = x
        ax.text(x, y + 3 + tier * TIER_GAP, format_hkls(refs, is_hex, four_index),
                rotation=90, ha="center", va="bottom",
                fontsize=8.5, color="#333333")


def _fwhm_of_peak(tth, curve, center, half_span=0.8):
    """测 center 附近那个峰的半高宽（线性插值两侧半高点）。"""
    m = (tth > center - half_span) & (tth < center + half_span)
    xs, ys = tth[m], curve[m]
    i0 = int(np.argmax(ys))
    half = ys[i0] / 2.0
    left = float(np.interp(half, ys[:i0 + 1], xs[:i0 + 1]))
    right = float(np.interp(half, ys[i0:][::-1], xs[i0:][::-1]))
    return left, right, float(ys[i0])


def plot_profile(tth, curve, peaks, is_hex, title, out_path, *,
                 sticks=None, zero_deg=0.0, four_index=True, zoom=None):
    """连续模拟谱（第七周画法）：细网格曲线 + 峰顶 (hkl) 标注。

    tth/curve = profile.simulate_pattern 的细网格输出；peaks = 它的峰表
    （理想位置/强度%，标注用）；sticks = 可选 (理想 2θ, 强度%)——浅灰竖线
    叠在底层，展示"理论棒→模拟谱"的关系；zero_deg：曲线相对理想峰位
    平移过的角度（标注跟着移）。

    zoom = (中心 2θ, ±半宽°) 时下方加一块放大面板：把那个峰放大、标出
    实测 FWHM——0.1° 量级的峰在整幅图上就是一根竖线，放大才看得见"峰形"。
    返回输出路径。
    """
    tth = np.asarray(tth, dtype=float)
    curve = np.asarray(curve, dtype=float)
    if zoom is None:
        fig, ax = plt.subplots(figsize=(10, 5.5))
        axz = None
    else:
        fig, (ax, axz) = plt.subplots(2, 1, figsize=(10, 7.2),
                                      height_ratios=[2.6, 1.0])
    if sticks is not None:
        st_tt, st_ii = sticks
        # 理想竖线按曲线自己的峰顶归一（峰表是 %，曲线可能另设了 scale）
        st_ii = np.asarray(st_ii, dtype=float)
        if st_ii.max() > 0 and curve.max() > 0:
            st_ii = st_ii / st_ii.max() * curve.max()
        ax.vlines(np.asarray(st_tt, dtype=float) + zero_deg, 0, st_ii,
                  color="0.82", lw=1.2, zorder=1)
    ax.plot(tth, curve, color="#2b6cb0", lw=1.6, zorder=2)
    _annotate_peaks(ax, tth, curve, peaks, is_hex, zero_deg, four_index)
    ax.set_xlim(float(tth.min()) - 2, float(tth.max()) + 2)
    ax.set_ylim(0, max(140.0, float(curve.max()) * 1.4))
    ax.set_xlabel(r"2$\theta$ (degrees)")
    ax.set_ylabel("Intensity (a.u.)")
    ax.set_title(title)
    if sticks is not None:
        ax.text(0.012, 0.02,
                "grey sticks = ideal crystal;  blue = simulated profile",
                transform=ax.transAxes, ha="left", fontsize=7.5, color="0.45")
    ax.grid(alpha=0.25, linestyle=":")

    if axz is not None:
        center, half = zoom
        left, right, top = _fwhm_of_peak(tth, curve, center)
        axz.plot(tth, curve, color="#2b6cb0", lw=1.6)
        axz.set_xlim(center - half, center + half)
        axz.set_ylim(0, top * 1.22)
        axz.hlines(top / 2.0, left, right, color="#d94b2b", lw=1.2)
        axz.annotate("", xy=(right, top / 2.0), xytext=(left, top / 2.0),
                     arrowprops=dict(arrowstyle="<->", color="#d94b2b", lw=1.2))
        axz.text((left + right) / 2.0, top / 2.0 * 1.06,
                 f"FWHM ≈ {right - left:.2f}°",
                 ha="center", va="bottom", fontsize=9, color="#d94b2b")
        axz.set_xlabel(r"2$\theta$ (degrees) — zoom on the strongest peak")
        axz.grid(alpha=0.25, linestyle=":")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path
