#!/usr/bin/env python3
"""把原位三相批量 CSV 画成一张 4-panel 展示图（2026-10-28）。

读 `fit_insitu.py` 输出的 CSV，不重拟合：
  (a) 富锂/贫锂相对相分数 vs 帧号（f_rich 带线性参数 esd 阴影），
      标出 ok / ok_soft / cell_jump；
  (b) rich 相 a/b/c（带 esd 误差棒）；
  (c) poor 相 a/b/c（带 esd 误差棒）；
  (d) Rwp / Rp vs 帧号，标异常帧。

横轴是帧号（没有电压/时间列，不假装有）。相分数是相对散射强度分数，
不是 wt%——标题和脚注都写清楚。充电/放电底色由 f_rich 最小值自动推断
（本批第 45 帧），只作视觉分区。

用法：
    python scripts/plot_insitu_summary.py \
        --csv outputs/insitu/insitu_fit.csv \
        --out outputs/insitu/insitu_summary.png
"""
import argparse
import csv
import math
from pathlib import Path

import numpy as np

MARKERS = {
    "ok": dict(marker="o", mfc="tab:blue", mec="tab:blue", ms=3.2),
    "ok_soft": dict(marker="o", mfc="none", mec="tab:blue", ms=3.6),
    "not_converged": dict(marker="o", mfc="none", mec="tab:orange", ms=4.2),
    "cell_jump": dict(marker="x", mfc="none", mec="tab:red", ms=4.2),
}
OTHER = dict(marker="s", mfc="none", mec="0.4", ms=4.0)


def _read_rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        def f(key):
            try:
                v = float(r.get(key, "nan"))
            except (TypeError, ValueError):
                return float("nan")
            return v
        status = str(r.get("status", ""))
        group = status.split("(", 1)[0]
        out.append({
            "frame": int(float(r["frame"])),
            "status": status,
            "group": group,
            "usable": str(r.get("usable", "")).lower() == "true",
            "f_rich": f("f_rich"), "f_poor": f("f_poor"),
            "f_rich_esd": f("f_rich_esd"),
            "rwp": f("rwp"), "rp": f("rp"),
            "rich_a": f("rich_a"), "rich_b": f("rich_b"),
            "rich_c": f("rich_c"),
            "rich_a_esd": f("rich_a_esd"),
            "rich_b_esd": f("rich_b_esd"),
            "rich_c_esd": f("rich_c_esd"),
            "poor_a": f("poor_a"), "poor_b": f("poor_b"),
            "poor_c": f("poor_c"),
            "poor_a_esd": f("poor_a_esd"),
            "poor_b_esd": f("poor_b_esd"),
            "poor_c_esd": f("poor_c_esd"),
        })
    return out


def _finite_pairs(rows, key, esd=None):
    x, y, e = [], [], []
    for r in rows:
        v = r.get(key, float("nan"))
        if not math.isfinite(v):
            continue
        x.append(r["frame"])
        y.append(v)
        if esd is not None:
            ev = r.get(esd, float("nan"))
            e.append(ev if math.isfinite(ev) else 0.0)
    return np.asarray(x), np.asarray(y), np.asarray(e)


def _status_markers(ax, rows, key, color=None):
    for group, style in list(MARKERS.items()) + [("other", OTHER)]:
        sel = [r for r in rows
               if (r["group"] == group if group != "other"
                   else r["group"] not in MARKERS)
               and math.isfinite(r.get(key, float("nan")))]
        if not sel:
            continue
        kw = dict(style)
        if color is not None:
            kw["mec"] = color
            if kw.get("mfc") != "none":
                kw["mfc"] = color
        ax.plot([r["frame"] for r in sel], [r[key] for r in sel],
                linestyle="none", label=group, **kw)


def _shade(ax, lo, hi, trans):
    ax.axvspan(lo, trans + 0.5, color="tab:blue", alpha=0.06, lw=0)
    ax.axvspan(trans + 0.5, hi, color="tab:orange", alpha=0.06, lw=0)
    ax.axvline(trans + 0.5, color="0.55", lw=0.8, ls="--")


def plot_summary(csv_path, out_path, *, title=None, dpi=160):
    """读 CSV 画 4-panel；返回 (figure, axes)。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = _read_rows(csv_path)
    if not rows:
        raise ValueError(f"CSV 没有数据：{csv_path}")
    frames = [r["frame"] for r in rows]
    lo, hi = min(frames) - 0.5, max(frames) + 0.5
    finite_f = [r for r in rows if math.isfinite(r["f_rich"])]
    trans = (min(finite_f, key=lambda r: r["f_rich"])["frame"]
             if finite_f else frames[0])
    n_ok = sum(1 for r in rows if r["group"] == "ok")
    n_use = sum(1 for r in rows if r["usable"])

    fig, axes = plt.subplots(2, 2, figsize=(12.5, 8.0))
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
    ax.set_ylabel("relative fraction")
    ax.set_title("(a) Phase fractions (relative scattering, not wt%)",
                 fontsize=10)
    ax.grid(alpha=0.25, linestyle=":")
    ax.legend(fontsize=7.5, loc="center right", ncol=1)
    ax.text(trans + 2.0, 0.20,
            f"f_rich min {min(r['f_rich'] for r in finite_f):.3f}\n"
            f"@ frame {trans}", fontsize=7.5, color="0.4")

    for i, (phase, title_i) in enumerate(
            (("rich", "(b) rich cell (Li-rich LMFP)"),
             ("poor", "(c) poor cell (heterosite)"))):
        ax = axes[0, 1] if i == 0 else axes[1, 0]
        _shade(ax, lo, hi, trans)
        for axis, color in (("a", "tab:blue"), ("b", "tab:orange"),
                            ("c", "tab:green")):
            x, y, e = _finite_pairs(rows, f"{phase}_{axis}",
                                    f"{phase}_{axis}_esd")
            if len(x):
                ax.errorbar(x, y, yerr=e, fmt="o-", ms=2.2, lw=0.8,
                            capsize=1.3, elinewidth=0.7, color=color,
                            label=axis)
        ax.set_xlim(lo, hi)
        ax.set_ylabel(f"{phase} cell (Å)")
        ax.set_title(title_i, fontsize=10)
        ax.grid(alpha=0.25, linestyle=":")
        ax.legend(fontsize=8, loc="best")

    ax = axes[1, 1]
    _shade(ax, lo, hi, trans)
    ax.plot([r["frame"] for r in rows], [r["rwp"] for r in rows],
            "-", lw=1.0, color="tab:red", label="Rwp")
    ax.plot([r["frame"] for r in rows], [r["rp"] for r in rows],
            "-", lw=0.9, color="0.4", label="Rp")
    _status_markers(ax, rows, "rwp")
    ax.set_xlim(lo, hi)
    ax.set_xlabel("frame")
    ax.set_ylabel("%")
    ax.set_title("(d) Rwp / Rp and anomaly status", fontsize=10)
    ax.grid(alpha=0.25, linestyle=":")
    ax.legend(fontsize=7.5, loc="lower left")
    rwp_vals = [r["rwp"] for r in rows if math.isfinite(r["rwp"])]
    if rwp_vals:
        ax.annotate(f"max Rwp {max(rwp_vals):.2f}%",
                    xy=(trans, max(rwp_vals)),
                    xytext=(trans + 3, max(rwp_vals) + 0.03),
                    fontsize=7.5, color="0.35",
                    arrowprops=dict(arrowstyle="-", lw=0.6,
                                    color="0.55"))

    fig.suptitle(
        title or (f"LMFP in-situ three-phase batch — "
                  f"{len(rows)} frames, strict ok {n_ok}/{len(rows)}, "
                  f"usable {n_use}/{len(rows)}"),
        fontsize=12)
    fig.text(0.5, 0.005,
             "charge/discharge shading inferred from the f_rich minimum; "
             "phase fractions are relative scattering intensity, not wt%",
             ha="center", fontsize=7.5, color="0.4")
    fig.tight_layout(rect=(0, 0.02, 1, 0.96))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=dpi)
    return fig, axes


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", default="outputs/insitu/insitu_fit.csv")
    ap.add_argument("--out", default="outputs/insitu/insitu_summary.png")
    ap.add_argument("--title", default=None)
    ap.add_argument("--dpi", type=int, default=160)
    args = ap.parse_args()
    fig, _axes = plot_summary(args.csv, args.out,
                              title=args.title, dpi=args.dpi)
    import matplotlib.pyplot as plt
    plt.close(fig)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
