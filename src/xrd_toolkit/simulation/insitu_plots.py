"""原位三相批量的展示图（GUI 与命令行共用，2026-11-02）。

这里只负责"把已经算好的 rows / FitResult 画到一张 Figure 上"：
- `draw_trends`：四栏趋势（相分数与交叉、两相 a 轴相对变化、
  两相 c 轴相对变化、Rwp）；
- `draw_summary`：四栏汇总（带状态标记/误差棒/充放电底色）；
- `draw_model_independent`：模型无关的 (200) 双峰质心检查；
- `model_independent_rows`：从 `InSituBatch.fitted` 里算质心/面积。

不导入 pyplot、不建 figure、不保存——Qt 画布和命令行各自决定怎么用。
"""
import math
from pathlib import Path

import numpy as np

_RICH_COLOR = "#1f5fa8"
_POOR_COLOR = "#e08a1e"
_RWP_COLOR = "#c0392b"
# 相分数低于这个值：该相的晶胞主要靠“猜”，图上淡色显示。
# 用 0.5（多数相）最稳：GSAS-II 对照显示 <50% 的端点晶胞仍会被
# majority 相拉偏。
MINORITY_FRACTION = 0.5

# 状态标记（与 scripts/plot_insitu_summary.py 同一套）
MARKERS = {
    "ok": dict(marker="o", mfc="tab:blue", mec="tab:blue", ms=3.2),
    "ok_soft": dict(marker="o", mfc="none", mec="tab:blue", ms=3.6),
    "not_converged": dict(marker="o", mfc="none", mec="tab:orange", ms=4.2),
    "cell_jump": dict(marker="x", mfc="none", mec="tab:red", ms=4.2),
}
OTHER = dict(marker="s", mfc="none", mec="0.4", ms=4.0)

# (200) 双峰窗口；两侧平带避开 rich/poor (200)
WIN_LO, WIN_HI = 1.18, 1.55
SIDE_LO = (1.18, 1.28)
SIDE_HI = (1.50, 1.56)


def _num(value, default=float("nan")):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _placeholder(fig, title, hint):
    fig.clear()
    ax = fig.add_subplot(111)
    ax.set_axis_off()
    ax.text(0.5, 0.55, title, ha="center", va="center",
            fontsize=15, color="#666666", transform=ax.transAxes)
    ax.text(0.5, 0.44, hint, ha="center", va="center",
            fontsize=10, color="#888888", transform=ax.transAxes)
    return fig


def _finite_pairs(rows, key, esd=None):
    x, y, e = [], [], []
    for row in rows:
        value = _num(row.get(key))
        if not math.isfinite(value):
            continue
        x.append(row["frame"])
        y.append(value)
        if esd is not None:
            ev = _num(row.get(esd), 0.0)
            e.append(ev if math.isfinite(ev) else 0.0)
    return np.asarray(x), np.asarray(y), np.asarray(e)


def _status_markers(ax, rows, key, color=None):
    for group, style in list(MARKERS.items()) + [("other", OTHER)]:
        sel = [r for r in rows
               if (r["group"] == group if group != "other"
                   else r["group"] not in MARKERS)
               and math.isfinite(_num(r.get(key)))]
        if not sel:
            continue
        kw = dict(style)
        if color is not None:
            kw["mec"] = color
            if kw.get("mfc") != "none":
                kw["mfc"] = color
        ax.plot([r["frame"] for r in sel], [_num(r[key]) for r in sel],
                linestyle="none", label=group, **kw)


def _shade(ax, lo, hi, trans):
    ax.axvspan(lo, trans + 0.5, color="tab:blue", alpha=0.06, lw=0)
    ax.axvspan(trans + 0.5, hi, color="tab:orange", alpha=0.06, lw=0)
    ax.axvline(trans + 0.5, color="0.55", lw=0.8, ls="--")


def draw_trends(fig, rows, *, title="LMFP in-situ three-phase fit trends"):
    """四栏趋势：相分数 / rich 晶胞 / poor 晶胞 / Rwp。

    与 `scripts/fit_insitu.py` 的 `insitu_trends.png` 同布局；横轴是帧号。
    """
    rows = list(rows or [])
    good = [r for r in rows
            if math.isfinite(_num(r.get("f_rich")))
            and math.isfinite(_num(r.get("rwp")))]
    if not good:
        return _placeholder(fig, "原位三相批量", "选帧目录，点 [开始]")

    fig.clear()
    # CSV rows carry frame as str; GUI rows carry int. Coerce so matplotlib
    # plots a numeric axis instead of one categorical tick per frame.
    x = [int(r["frame"]) for r in good]
    rich_y = [_num(r["f_rich"]) for r in good]
    poor_y = [_num(r.get("f_poor", 1.0 - _num(r["f_rich"])))
              for r in good]
    ax1 = fig.add_subplot(2, 2, 1)
    # 堆叠面积把"两相此消彼长"直接画出来；曲线仍保留，便于逐帧读数
    ax1.fill_between(x, 0.0, rich_y, color=_RICH_COLOR, alpha=0.10, lw=0)
    ax1.fill_between(x, rich_y, 1.0, color=_POOR_COLOR, alpha=0.10, lw=0)
    ax1.plot(x, rich_y, "o-", ms=2.5, color=_RICH_COLOR, label="LMFP rich")
    ax1.plot(x, poor_y, "s-", ms=2.5, color=_POOR_COLOR, label="LMFP poor")
    ax1.axhline(MINORITY_FRACTION, color="0.55", lw=0.8, ls=":")
    # 交叉帧 = 两相比例换手的位置，大致框出反应区间
    for (x0, y0), (x1, y1) in zip(zip(x, rich_y), zip(x[1:], rich_y[1:])):
        if (y0 - MINORITY_FRACTION) * (y1 - MINORITY_FRACTION) < 0:
            ax1.axvline(x1, color="0.55", lw=0.6, ls="--", alpha=0.7)
    yerr = [_num(r.get("f_rich_esd"), 0.0) for r in good]
    if any(e > 0 for e in yerr):
        ax1.errorbar(x, rich_y, yerr=yerr, fmt="none", ecolor=_RICH_COLOR,
                     capsize=2, alpha=0.6)
    ax1.set_ylim(0.0, 1.0)
    ax1.set_ylabel("relative fraction", fontsize=8)
    ax1.set_title("(a) Phase fractions", fontsize=9)
    ax1.legend(fontsize=8, loc="best")
    ax1.grid(alpha=0.25, linestyle=":")

    # (b)(c) 两相的同一根晶胞轴放在一起：Δa/a0 与 Δc/c0（%）。
    # 一条线一个相，看的是"谁在胀、谁在缩"；实心点 = 该相占多数
    # （>=50%），淡色 = 少数相估计，只作趋势参考。
    for idx, axis in enumerate(("a", "c")):
        ax = fig.add_subplot(2, 2, 2 + idx)
        for key, color, label in (("rich", _RICH_COLOR, "rich"),
                                  ("poor", _POOR_COLOR, "poor")):
            raw = [_num(r.get(f"{key}_{axis}")) for r in good]
            base = next((v for v in raw if math.isfinite(v) and v > 0), None)
            if base is None:
                continue
            rel = [100.0 * (v / base - 1.0) if math.isfinite(v)
                   else float("nan") for v in raw]
            f_key = "f_rich" if key == "rich" else "f_poor"
            ax.plot(x, rel, "-", lw=0.9, color=color, alpha=0.3,
                    label=f"{label} ({axis}₀ {base:.3f} Å)")
            pts = [(xi, yi) for xi, yi, r in zip(x, rel, good)
                   if math.isfinite(yi)
                   and _num(r.get(f_key, 1.0 - _num(r["f_rich"])))
                   >= MINORITY_FRACTION]
            if pts:
                ax.plot([p[0] for p in pts], [p[1] for p in pts],
                        "o", ms=2.2, color=color, label="_nolegend_")
        ax.axhline(0.0, color="0.7", lw=0.6)
        ax.set_ylabel(f"Δ{axis}/{axis}₀ (%)", fontsize=8)
        ax.set_title(f"({'b' if idx == 0 else 'c'}) cell {axis} change",
                     fontsize=9)
        if ax.get_legend_handles_labels()[0]:
            ax.legend(fontsize=7, loc="best")
        ax.grid(alpha=0.25, linestyle=":")
        if idx == 1:
            ax.set_xlabel("frame", fontsize=8)

    ax4 = fig.add_subplot(2, 2, 4)
    # 状态不是 ok 的帧：淡红竖带，提醒这一帧的读数不可信
    for row in good:
        status = str(row.get("status", "ok"))
        if status and status not in ("ok", "usable"):
            ax4.axvspan(int(row["frame"]) - 0.5, int(row["frame"]) + 0.5,
                        color="#d0544e", alpha=0.08, lw=0)
    ax4.plot(x, [_num(r["rwp"]) for r in good], "o-", ms=2.5,
             color=_RWP_COLOR)
    ax4.set_ylabel("Rwp (%)", fontsize=8)
    ax4.set_xlabel("frame", fontsize=8)
    ax4.set_title("(d) Rwp", fontsize=9)
    ax4.grid(alpha=0.25, linestyle=":")

    for ax in fig.axes:
        ax.tick_params(labelsize=7)
    fig.suptitle(title, fontsize=11)
    fig.text(0.5, 0.012,
             "cell tracks: solid markers only when that phase is the majority (≥50%); "
             "faded = minority-phase estimate (not reliable). Dotted line in (a) = "
             "50/50 crossover; dashed verticals mark crossings.",
             ha="center", fontsize=7, color="0.4")
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    return fig


def _summary_rows(rows):
    """把 GUI row / CSV row 归一化成汇总图要的字段。"""
    out = []
    for row in rows or []:
        status = str(row.get("status", ""))
        usable = row.get("usable", False)
        if isinstance(usable, str):
            usable = usable.strip().lower() == "true"
        f_rich = _num(row.get("f_rich"))
        out.append({
            "frame": int(row["frame"]),
            "status": status,
            "group": status.split("(", 1)[0],
            "usable": bool(usable),
            "f_rich": f_rich,
            "f_poor": _num(row.get("f_poor",
                                   1.0 - f_rich if math.isfinite(f_rich)
                                   else float("nan"))),
            "f_rich_esd": _num(row.get("f_rich_esd")),
            "rwp": _num(row.get("rwp")),
            "rp": _num(row.get("rp")),
            "rich_a": _num(row.get("rich_a")),
            "rich_b": _num(row.get("rich_b")),
            "rich_c": _num(row.get("rich_c")),
            "rich_a_esd": _num(row.get("rich_a_esd")),
            "rich_b_esd": _num(row.get("rich_b_esd")),
            "rich_c_esd": _num(row.get("rich_c_esd")),
            "poor_a": _num(row.get("poor_a")),
            "poor_b": _num(row.get("poor_b")),
            "poor_c": _num(row.get("poor_c")),
            "poor_a_esd": _num(row.get("poor_a_esd")),
            "poor_b_esd": _num(row.get("poor_b_esd")),
            "poor_c_esd": _num(row.get("poor_c_esd")),
        })
    return out


def draw_summary(fig, rows, *, title=None):
    """四栏汇总图：状态标记 + 误差棒 + 充放电底色（与
    `scripts/plot_insitu_summary.py` 同布局）。
    """
    rows = _summary_rows(rows)
    if not rows:
        return _placeholder(fig, "原位三相批量", "选帧目录，点 [开始]")

    frames = [r["frame"] for r in rows]
    lo, hi = min(frames) - 0.5, max(frames) + 0.5
    finite_f = [r for r in rows if math.isfinite(r["f_rich"])]
    trans = (min(finite_f, key=lambda r: r["f_rich"])["frame"]
             if finite_f else frames[0])
    n_ok = sum(1 for r in rows if r["group"] == "ok")
    n_use = sum(1 for r in rows if r["usable"])

    def _inside(x):
        """把文字锚点夹在 x 范围里；只有 2 帧时 trans+3 会跑到框外，
        tight_layout 会因此把坐标轴压成一条缝（2026-11-02 实测）。"""
        pad = 0.06 * (hi - lo)
        return min(max(float(x), lo + pad), hi - pad)

    fig.clear()
    axes = fig.subplots(2, 2)
    ax = axes[0, 0]
    _shade(ax, lo, hi, trans)
    ax.plot([r["frame"] for r in rows], [r["f_rich"] for r in rows],
            "-", lw=1.0, color="tab:blue", label="f_rich")
    ax.plot([r["frame"] for r in rows], [r["f_poor"] for r in rows],
            "-", lw=1.0, color="tab:orange", label="f_poor")
    fx, fy, fe = _finite_pairs(rows, "f_rich", esd="f_rich_esd")
    if len(fx) and np.any(fe > 0):
        ax.fill_between(fx, fy - fe, fy + fe, color="tab:blue",
                        alpha=0.18, lw=0)
    _status_markers(ax, rows, "f_rich")
    ax.set_xlim(lo, hi)
    ax.set_ylim(-0.02, 1.02)
    ax.set_ylabel("relative fraction", fontsize=8)
    ax.set_title("(a) Phase fractions (relative scattering, not wt%)",
                 fontsize=9)
    ax.grid(alpha=0.25, linestyle=":")
    ax.legend(fontsize=7, loc="center right", ncol=1)
    if finite_f:
        ax.text(_inside(trans + 2.0), 0.20,
                f"f_rich min {min(r['f_rich'] for r in finite_f):.3f}\n"
                f"@ frame {trans}", fontsize=7, color="0.4")

    for i, (phase, title_i) in enumerate((
            ("rich", "(b) rich cell (Li-rich LMFP)"),
            ("poor", "(c) poor cell (heterosite)"))):
        ax = axes[0, 1] if i == 0 else axes[1, 0]
        _shade(ax, lo, hi, trans)
        f_key = "f_rich" if phase == "rich" else "f_poor"
        rel_rows = [r for r in rows
                    if _num(r.get(f_key, 1.0 - _num(r["f_rich"])))
                    >= MINORITY_FRACTION]
        for axis, color in (("a", "tab:blue"), ("b", "tab:orange"),
                            ("c", "tab:green")):
            x, y, e = _finite_pairs(rows, f"{phase}_{axis}",
                                    f"{phase}_{axis}_esd")
            if len(x):
                ax.errorbar(x, y, yerr=e, fmt="o-", ms=2.0, lw=0.7,
                            capsize=1.2, elinewidth=0.6, color=color,
                            alpha=0.22, label="_nolegend_")
            x, y, e = _finite_pairs(rel_rows, f"{phase}_{axis}",
                                    f"{phase}_{axis}_esd")
            if len(x):
                ax.errorbar(x, y, yerr=e, fmt="o", ms=2.3,
                            capsize=1.3, elinewidth=0.7, color=color,
                            label=axis)
        ax.set_xlim(lo, hi)
        ax.set_ylabel(f"{phase} cell (Å)", fontsize=8)
        ax.set_title(title_i, fontsize=9)
        ax.grid(alpha=0.25, linestyle=":")
        if ax.get_legend_handles_labels()[0]:
            ax.legend(fontsize=7, loc="best")

    ax = axes[1, 1]
    _shade(ax, lo, hi, trans)
    ax.plot([r["frame"] for r in rows], [r["rwp"] for r in rows],
            "-", lw=1.0, color="tab:red", label="Rwp")
    ax.plot([r["frame"] for r in rows], [r["rp"] for r in rows],
            "-", lw=0.9, color="0.4", label="Rp")
    _status_markers(ax, rows, "rwp")
    ax.set_xlim(lo, hi)
    ax.set_xlabel("frame", fontsize=8)
    ax.set_ylabel("%", fontsize=8)
    ax.set_title("(d) Rwp / Rp and anomaly status", fontsize=9)
    ax.grid(alpha=0.25, linestyle=":")
    ax.legend(fontsize=7, loc="lower left")
    rwp_vals = [r["rwp"] for r in rows if math.isfinite(r["rwp"])]
    if rwp_vals:
        ax.annotate(f"max Rwp {max(rwp_vals):.2f}%",
                    xy=(trans, max(rwp_vals)),
                    xytext=(0.55, 0.92), textcoords="axes fraction",
                    ha="left", va="top", fontsize=7, color="0.35",
                    arrowprops=dict(arrowstyle="-", lw=0.6,
                                    color="0.55"))

    for ax in axes.ravel():
        ax.tick_params(labelsize=7)
    fig.suptitle(
        title or (f"LMFP in-situ three-phase batch — "
                  f"{len(rows)} frames, strict ok {n_ok}/{len(rows)}, "
                  f"usable {n_use}/{len(rows)}"),
        fontsize=11)
    fig.text(0.5, 0.005,
             "charge/discharge shading inferred from the f_rich minimum; "
             "phase fractions are relative scattering intensity, not wt%; "
             "cell markers are solid only when that phase is the majority (≥50%).",
             ha="center", fontsize=6.5, color="0.4")
    fig.tight_layout(rect=(0, 0.02, 1, 0.95))
    return fig


def _doublet_stats(tth, y, lo=WIN_LO, hi=WIN_HI):
    """局部线性背景 + 双峰面积/强度加权质心。"""
    t = np.asarray(tth, dtype=float)
    y = np.asarray(y, dtype=float)
    m = (t >= lo) & (t <= hi)
    if np.count_nonzero(m) < 8:
        return float("nan"), float("nan")
    side = (((t >= SIDE_LO[0]) & (t <= SIDE_LO[1]))
            | ((t >= SIDE_HI[0]) & (t <= SIDE_HI[1])))
    if np.count_nonzero(side) >= 4:
        coef = np.polyfit(t[side], y[side], 1)
        yy = y[m] - np.polyval(coef, t[m])
    else:
        yy = y[m] - float(np.median(y[side])) if np.any(side) else y[m]
    area = float(np.trapezoid(yy, t[m]))
    if not math.isfinite(area) or area <= 0:
        return area, float("nan")
    centroid = float(np.trapezoid(t[m] * yy, t[m]) / area)
    return area, centroid


def model_independent_rows(fitted):
    """从 `InSituBatch.fitted` 算每帧的 (200) 双峰面积/质心。

    `fitted` 的元素是 `(frame, path, res, f_rich, status)`；曲线直接取
    `res.tth / res.y_obs`（已经按同一 mask/几何积分过），不重新读图。
    """
    from xrd_toolkit.services.background import estimate_baseline_sliding

    rows = []
    # 快照：GUI 的 worker 可能正往 fitted 追加当前帧
    for entry in list(fitted or []):
        try:
            frame, path, res, f_rich, status = entry
        except (TypeError, ValueError):
            continue
        if res is None or getattr(res, "tth", None) is None:
            continue
        t = np.asarray(res.tth, dtype=float)
        y = np.asarray(res.y_obs, dtype=float)
        bg = estimate_baseline_sliding(t, y, 0.2)
        area, centroid = _doublet_stats(t, y - bg)
        f_poor = (1.0 - float(f_rich)
                  if math.isfinite(_num(f_rich)) else float("nan"))
        rows.append({
            "frame": int(frame),
            "file": Path(path).name,
            "status": str(status),
            "usable": str(status) in ("ok", "ok_soft"),
            "f_poor": f_poor,
            "doublet_area": area,
            "centroid": centroid,
        })
    return rows


def draw_model_independent(
        fig, rows,
        *, title=("Model-independent check: (200) doublet centroid "
                  "follows the batch phase fraction")):
    """三栏模型无关检查：质心 vs 帧号 / 双峰面积 / 质心 vs f_poor。"""
    rows = list(rows or [])
    cen = np.asarray([_num(r.get("centroid")) for r in rows], dtype=float)
    area = np.asarray([_num(r.get("doublet_area")) for r in rows],
                      dtype=float)
    fpoor = np.asarray([_num(r.get("f_poor")) for r in rows], dtype=float)
    if not rows or not np.any(np.isfinite(cen)):
        return _placeholder(fig, "模型无关检查", "先跑几帧，再回来看 (200) 双峰")

    frames = np.asarray([r["frame"] for r in rows], dtype=int)
    finite_f = [r for r in rows if math.isfinite(_num(r.get("f_poor")))]
    trans = (max(finite_f, key=lambda r: _num(r["f_poor"]))["frame"]
             if finite_f else frames[0])
    lo, hi = frames[0] - 0.5, frames[-1] + 0.5
    bad = np.asarray([str(r.get("status", "")) != "ok" for r in rows])

    fig.clear()
    axes = fig.subplots(1, 3)
    ax = axes[0]
    ax.axvspan(lo, trans + 0.5, color="tab:blue", alpha=0.06, lw=0)
    ax.axvspan(trans + 0.5, hi, color="tab:orange", alpha=0.06, lw=0)
    ax.plot(frames, cen, "-", lw=1.0, color="tab:green",
            label="(200) centroid")
    ax.plot(frames[bad], cen[bad], "o", mfc="none", mec="tab:orange",
            ms=4, label="not_converged/cell_jump")
    ax.set_xlim(lo, hi)
    ax.set_xlabel("frame", fontsize=8)
    ax.set_ylabel("centroid 2θ (°)", fontsize=8)
    ax.set_title("(a) (200) doublet centroid", fontsize=9)
    ax.grid(alpha=0.25, linestyle=":")
    ax2 = ax.twinx()
    ax2.plot(frames, fpoor, ":", lw=1.0, color="0.35",
             label="f_poor (batch)")
    ax2.set_ylabel("f_poor (batch)", fontsize=8)
    ax2.set_ylim(-0.02, 1.02)
    lines = ax.get_lines() + ax2.get_lines()
    ax.legend(lines, [ln.get_label() for ln in lines],
              fontsize=7, loc="center right")

    ax = axes[1]
    ax.axvspan(lo, trans + 0.5, color="tab:blue", alpha=0.06, lw=0)
    ax.axvspan(trans + 0.5, hi, color="tab:orange", alpha=0.06, lw=0)
    median = float(np.nanmedian(area)) if np.any(np.isfinite(area)) else 1.0
    scale = median if math.isfinite(median) and median != 0 else 1.0
    ax.plot(frames, area / scale, "-", lw=1.0, color="tab:purple")
    ax.plot(frames[bad], (area / scale)[bad], "o", mfc="none",
            mec="tab:orange", ms=4)
    ax.set_xlim(lo, hi)
    ax.set_xlabel("frame", fontsize=8)
    ax.set_ylabel("area / median", fontsize=8)
    ax.set_title("(b) (200) doublet total area (phase-dependent)",
                 fontsize=9)
    ax.grid(alpha=0.25, linestyle=":")

    ax = axes[2]
    good = np.isfinite(cen) & np.isfinite(fpoor)
    ax.plot(fpoor[good], cen[good], "o", ms=3, color="tab:green")
    if np.count_nonzero(good) >= 3:
        p = np.polyfit(fpoor[good], cen[good], 1)
        xs = np.linspace(np.nanmin(fpoor[good]), np.nanmax(fpoor[good]), 20)
        ax.plot(xs, np.polyval(p, xs), "--", lw=0.9, color="0.4")
        pred = np.polyval(p, fpoor[good])
        ss = np.sum((cen[good] - np.mean(cen[good])) ** 2)
        r2 = 1.0 - np.sum((cen[good] - pred) ** 2) / ss if ss > 0 else 0.0
        ax.text(0.04, 0.94, f"R² = {r2:.3f}", transform=ax.transAxes,
                va="top", fontsize=8)
    ax.set_xlabel("f_poor (batch)", fontsize=8)
    ax.set_ylabel("(200) centroid 2θ (°)", fontsize=8)
    ax.set_title("(c) centroid vs batch f_poor", fontsize=9)
    ax.grid(alpha=0.25, linestyle=":")

    for ax in axes.ravel():
        ax.tick_params(labelsize=7)
    fig.suptitle(title, fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    return fig
