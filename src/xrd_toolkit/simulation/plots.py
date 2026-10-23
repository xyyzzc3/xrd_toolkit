"""XRD 模拟图谱的绘图工具（与计算分离，matplotlib 属主依赖）。

三个绘图入口：

- `plot_pattern`：竖线（棒状）谱图。理论峰是无限窄的，模拟谱的标准画法
  就是一排竖线；
- `plot_annotated`：在每根峰上方竖排标注 (hkl)（六方默认四位 hkil，可切三位卡片式），
  相邻标签过近时自动抬高一档，避免叠字；
- `plot_profile`（2026-10-17 第七周加）：连续模拟谱——细网格曲线为主体，
  可选把理想竖线以浅色垫在下面（一眼看出"理论棒 → 模拟谱"的关系），
  峰顶同样竖排 (hkl) 标注（沿用同一套避叠字规矩）。
- `plot_fit`（2026-10-22 加，2026-10-23 起默认论文式布局）：观测点 /
  计算线 / 背景 + 残差 + 每相一行的布拉格峰位刻度（`peak_tth` /
  `extra_peak_tth` 自动生成），可选一个放大窗（看峰形与峰间底部）。
- `plot_fit_bottom`（2026-10-22 加）：把残差 y 轴按峰间噪声的 ±8σ 收窄，
  专看"峰间底部平不平"（主图残差被峰顶几千 counts 撑满，看不见 ±10）。
- `plot_intensity_ratios`（2026-10-22 加）：逐峰 obs/fit 面积比 + 两条
  背景的差，回答"实验扣背景后为什么比拟合高"。
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from xrd_toolkit.simulation.core import format_hkls

FOOTPRINT_DEG = 1.35   # 竖排标签的横向"占地"（度）：小于这个距离就躲到上一档
TIER_GAP = 15.0        # 每档抬高多少（强度单位）


def plot_intensity_ratios(result, auto_background, peak_tt, labels, out_path,
                          *, intensities=None, window=0.06):
    """逐峰强度诊断（2026-10-22）：obs/fit 面积比 + 两条背景的差。

    左图回答"为什么实验扣背景后比拟合高"：红点 >1 是实验高、蓝点 <1 是
    拟合高；强峰通常贴着 1，高角弱峰因重叠/hkl 效应散得最大。右图把
    自动基线与拟合背景画在一起，说明两者的系统差。
    """
    import matplotlib.pyplot as plt
    import numpy as np
    tth = np.asarray(result.tth, dtype=float)
    y_obs = np.asarray(result.y_obs, dtype=float) - np.asarray(
        result.background, dtype=float)
    y_fit = np.asarray(result.y_calc, dtype=float) - np.asarray(
        result.background, dtype=float)
    ratios, used_tt, used_lab, used_int = [], [], [], []
    for i, (tt, lab) in enumerate(zip(peak_tt, labels)):
        m = np.abs(tth - tt) <= window
        if not np.any(m):
            continue
        ao = float(np.trapezoid(y_obs[m], tth[m]))
        af = float(np.trapezoid(y_fit[m], tth[m]))
        if af <= 0:
            continue
        ratios.append(ao / af)
        used_tt.append(float(tt))
        used_lab.append(lab)
        used_int.append(float(intensities[i]) if intensities is not None else 1.0)
    ratios = np.asarray(ratios)
    used_int = np.asarray(used_int)
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(13.5, 4.8))
    ax.axhline(1.0, color="0.5", lw=0.9, ls="--")
    for tt, r, ii, lab in zip(used_tt, ratios, used_int, used_lab):
        ax.plot(tt, r, "o", color=("#d94b2b" if r > 1 else "#2b6cb0"),
                ms=4.5 + 3.0 * np.sqrt(max(ii, 0.0) / max(used_int.max(), 1e-9)))
        ax.text(tt, r + 0.008, lab.split(" x")[0], rotation=60, fontsize=7,
                ha="left", va="bottom", color="0.25")
    pad = max(0.02, 0.15 * (ratios.max() - ratios.min()))
    ax.set_ylim(max(0.0, ratios.min() - pad), ratios.max() + pad)
    ax.set_xlabel(r"2$\theta$ (degrees)")
    ax.set_ylabel("observed area / fit area")
    ax.set_title("(a) per-peak integrated area ratio", fontsize=10.5)
    ax.grid(alpha=0.25, linestyle=":")

    auto_bg = np.asarray(auto_background, dtype=float)
    ax2.plot(tth, auto_bg, color="#888888", lw=1.4,
             label="auto baseline (0.2°)")
    ax2.plot(tth, result.background, color="#2b6cb0", lw=1.4,
             label="fit background")
    ax2.set_xlabel(r"2$\theta$ (degrees)")
    ax2.set_ylabel("Background (counts)")
    ax2.set_title(f"(b) backgrounds differ by "
                  f"{np.mean(result.background - auto_bg):+.1f} counts "
                  f"on average", fontsize=10.5)
    ax2.legend(loc="center right", fontsize=8.5)
    ax2.grid(alpha=0.25, linestyle=":")
    ax3 = ax2.twinx()
    ax3.plot(tth, result.background - auto_bg, color="#d94b2b", lw=1.0,
             ls=":")
    ax3.set_ylabel("fit − auto (counts)", color="#d94b2b")
    ax3.tick_params(axis="y", labelcolor="#d94b2b")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_fit_bottom(result, title, out_path, *, inter_mask=None, zoom=None):
    """拟合的"底部"诊断图（2026-10-22）：峰间残差 vs ±3σ 噪声带。

    为什么单独画：主拟合图的残差坐标被峰顶的几千 counts 撑满，峰间
    ±10 counts 的系统差根本看不见。这里把 y 轴按底部噪声的 ±8σ 收窄，
    再给一个峰间窗口的放大，专门看"底部平不平"。
    """
    import matplotlib.pyplot as plt
    import numpy as np
    r = np.asarray(result.residual, dtype=float)
    tth = np.asarray(result.tth, dtype=float)
    if inter_mask is None:
        bg = np.asarray(result.background, dtype=float)
        inter_mask = result.y_obs < 1.1 * np.median(bg) if bg.size else \
            result.y_obs < 0.05 * result.y_obs.max()
    inter_mask = np.asarray(inter_mask, dtype=bool)
    if not np.any(inter_mask):          # 兜底：没有底部点时用全部点
        inter_mask = np.ones_like(r, dtype=bool)
    rr = r[inter_mask]
    mu = float(np.median(rr))
    sigma = float(1.4826 * np.median(np.abs(rr - mu)))
    sigma = max(sigma, 1e-6)
    lim = 8.0 * sigma

    fig, (ax, axz) = plt.subplots(1, 2, figsize=(13.5, 4.4))
    ax.axhspan(-3 * sigma, 3 * sigma, color="#2b6cb0", alpha=0.10,
               label="±3σ noise")
    ax.axhline(mu, color="0.4", lw=0.9, ls="--")
    ax.plot(tth, r, ".", ms=2.2, color="0.25")
    ax.set_ylim(-lim, lim)
    ax.set_xlabel(r"2$\theta$ (degrees)")
    ax.set_ylabel("obs − calc (counts)")
    ax.set_title("(a) residual, y-axis clipped to the bottom scale",
                 fontsize=10.5)
    ax.text(0.015, 0.96,
            f"inter-peak: mean {mu:+.1f}   RMS "
            f"{np.sqrt(np.mean((rr - mu) ** 2)):.1f}   σ≈{sigma:.1f} counts",
            transform=ax.transAxes, va="top", fontsize=9,
            bbox=dict(boxstyle="round,pad=0.25", fc="white", alpha=0.85))
    ax.legend(loc="lower right", fontsize=8.5)
    ax.grid(alpha=0.25, linestyle=":")

    if zoom is None:
        zoom = (float(tth.min()), float(tth.max()))
    lo, hi = zoom
    m = (tth >= lo) & (tth <= hi)
    axz.axhspan(-3 * sigma, 3 * sigma, color="#2b6cb0", alpha=0.10)
    axz.axhline(mu, color="0.4", lw=0.9, ls="--")
    axz.plot(tth[m], r[m], "o-", ms=3.2, lw=0.8, color="#2b6cb0")
    axz.set_xlim(lo, hi)
    axz.set_ylim(-lim, lim)
    axz.set_xlabel(r"2$\theta$ (degrees)")
    axz.set_title(f"(b) bottom zoom {lo:.2f}–{hi:.2f}°", fontsize=10.5)
    axz.grid(alpha=0.25, linestyle=":")

    fig.suptitle(title, fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


_MAIN_COLOR = "#1f5fa8"
_EXTRA_COLORS = ("#e08a1e", "#2a9d8f", "#8e5aa8")
_OBS_COLOR = "#333333"
_BG_COLOR = "#d0544e"
_BRAGG_MERGE_DEG = 0.02   # 显示上合并更近的反射（拟合本身仍保留逐反射）


def _thin_bragg(pos, tol=_BRAGG_MERGE_DEG):
    """把显示上挨得太近的反射位置合并成一个刻度。"""
    if len(pos) < 2:
        return pos
    keep = [pos[0]]
    for x in pos[1:]:
        if x - keep[-1] > tol:
            keep.append(x)
    return np.asarray(keep)


def plot_fit(result, title, out_path, zoom=None, exclude_ranges=None,
             bragg="auto"):
    """拟合结果图（论文式布局，2026-10-23 起为默认画法）。

    三栏：观测点 / 计算线 / 背景虚线 → obs−calc 差值 → 布拉格峰位刻度
    （每相一行）。`result` 是 `FitResult`（鸭子类型即可：tth / y_obs /
    y_calc / background / residual / rwp / rp / peak_tth）。
    zoom 给 (lo, hi) 时在上图里嵌放大窗；exclude_ranges 把排除区画灰带。

    bragg：
        "auto"（默认）= 用 `result.peak_tth`（主相，`phase_label` 命名）
            和 `result.extra_peak_tth`（第二相）自动生成刻度行；
        None = 不画刻度，回到旧的两栏布局；
        [(label, positions, color), …] = 手工指定刻度行。
    """
    import matplotlib.pyplot as plt
    rows = []
    if bragg == "auto":
        if result.peak_tth is not None and len(result.peak_tth):
            rows.append((result.phase_label or "main phase",
                         np.asarray(result.peak_tth, dtype=float),
                         _MAIN_COLOR))
        for i, (label, pos) in enumerate((result.extra_peak_tth or {}).items()):
            rows.append((str(label), np.asarray(pos, dtype=float),
                         _EXTRA_COLORS[i % len(_EXTRA_COLORS)]))
    elif bragg:
        rows = [(str(label), np.asarray(pos, dtype=float), color)
                for label, pos, color in bragg]

    if rows:
        fig = plt.figure(figsize=(11.5, 7.6))
        gs = fig.add_gridspec(3, 1, height_ratios=[3.2, 0.95, 0.55],
                              hspace=0.07)
        ax = fig.add_subplot(gs[0])
        axr = fig.add_subplot(gs[1], sharex=ax)
        axb = fig.add_subplot(gs[2], sharex=ax)
        ax.tick_params(labelbottom=False)
        axr.tick_params(labelbottom=False)
    else:
        fig = plt.figure(figsize=(11.5, 6.5))
        gs = fig.add_gridspec(2, 1, height_ratios=[3.2, 0.95],
                              hspace=0.07)
        ax = fig.add_subplot(gs[0])
        axr = fig.add_subplot(gs[1], sharex=ax)
        axb = None
        ax.tick_params(labelbottom=False)

    tth = np.asarray(result.tth, dtype=float)
    xlo, xhi = float(tth.min()), float(tth.max())

    ax.plot(tth, result.y_obs, ".", ms=1.7, color=_OBS_COLOR, alpha=0.85,
            label="observed")
    ax.plot(tth, result.y_calc, color=_MAIN_COLOR, lw=1.3,
            label="calculated")
    if result.background is not None:
        ax.plot(tth, result.background, "--", color=_BG_COLOR, lw=0.9,
                label="background")
    first = True
    for lo, hi in exclude_ranges or []:
        ax.axvspan(lo, hi, color="0.88", alpha=0.6, zorder=0,
                   label="excluded" if first else None)
        axr.axvspan(lo, hi, color="0.88", alpha=0.6, zorder=0)
        if axb is not None:
            axb.axvspan(lo, hi, color="0.88", alpha=0.6, zorder=0)
        first = False
    ax.set_ylabel("Intensity")
    ax.set_title(f"{title}\nRwp = {result.rwp:.2f}%   Rp = {result.rp:.2f}%",
                 fontsize=10.5, pad=9)
    ax.legend(loc="upper right", fontsize=8.5, frameon=True,
              framealpha=0.9, edgecolor="0.85")
    ax.grid(alpha=0.18, linestyle=":")
    ax.tick_params(labelsize=9)
    ax.set_xlim(xlo, xhi)

    resid = np.asarray(result.residual, dtype=float)
    axr.axhline(0.0, color="0.55", lw=0.8)
    axr.plot(tth, resid, color=_MAIN_COLOR, lw=0.8)
    axr.set_ylabel("obs − calc")
    axr.grid(alpha=0.18, linestyle=":")
    axr.tick_params(labelsize=8.5)
    if resid.size:
        lim = float(np.percentile(np.abs(resid), 99.8))
        if lim > 0:
            axr.set_ylim(-1.15 * lim, 1.15 * lim)

    xlbl = r"2$\theta$ (degrees)"
    if axb is not None:
        n = len(rows)
        axb.set_ylim(0.0, float(n))
        axb.set_yticks([n - 0.5 - i for i in range(n)])
        axb.set_yticklabels([label for label, _pos, _c in rows],
                            fontsize=7.5)
        axb.tick_params(axis="y", length=0, pad=4)
        for i, (_label, pos, color) in enumerate(rows):
            inside = _thin_bragg(pos[(pos >= xlo) & (pos <= xhi)])
            if inside.size:
                y = float(n - 1 - i)
                axb.vlines(inside, y + 0.30, y + 0.70, color=color, lw=0.85)
        for side in ("left", "right", "top"):
            axb.spines[side].set_visible(False)
        axb.tick_params(axis="x", labelsize=9)
        axb.set_xlabel(xlbl)
    else:
        axr.set_xlabel(xlbl)

    if zoom is not None:
        lo, hi = zoom
        ins = ax.inset_axes([0.53, 0.50, 0.43, 0.40])
        ins.plot(tth, result.y_obs, ".", ms=2.3, color=_OBS_COLOR,
                 alpha=0.85)
        ins.plot(tth, result.y_calc, color=_MAIN_COLOR, lw=1.2)
        ins.set_xlim(lo, hi)
        ins.set_ylim(bottom=0)
        ins.tick_params(labelsize=6.5)
        ins.grid(alpha=0.18, linestyle=":")
        ins.set_title("zoom", fontsize=7.5, pad=3)
        for side in ins.spines.values():
            side.set_color("0.7")

    fig.savefig(out_path, dpi=160)
    plt.close(fig)
    return out_path


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
                   four_index=False):
    """带 (hkl) 标注的谱图（第六周最终交付图的画法）。

    peaks 用 core.pattern_from_structure 返回的峰表（含代表 hkl 与多重性）；
    **默认三位卡片式**（如 (116)，与 PDF 卡片行号对照方便——用户
    2026-10-17："hkl 就显示三位，图上不要写四位"）；four_index=True 才写
    四位的 (h k i l)。返回输出路径。
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
                 sticks=None, zero_deg=0.0, four_index=False, zoom=None):
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
