#!/usr/bin/env python3
"""原位序列的模型无关峰检查（2026-10-28）。

三相 Rietveld 的相分数依赖模型（结构因子/背景/峰形）。这里换一把
不依赖模型的尺子：低角 (200) 双峰——
  rich (200) ≈ 1.35°，poor (200) ≈ 1.43°（λ=0.1223 Å）
两峰只差 ~0.08°，单峰窗口切不开；但对整个双峰做**强度加权质心**，
质心会随 rich→poor 从 ~1.36° 平滑移到 ~1.43°，和批量 f_poor 独立对照。

每帧：
  1. 2D→1D（同一个 mask/几何）；
  2. 自动基线扣除；
  3. 在 1.18–1.55° 窗口内用两侧平带（1.18–1.28、1.50–1.56）做局部
     线性背景，算双峰总面积与强度加权质心；
  4. 和 `insitu_fit.csv` 的 f_poor / 状态对齐。

输出：
  outputs/insitu/model_independent.csv
  outputs/insitu/model_independent.png
  (a) 质心 vs 帧号 + f_poor（右轴）
  (b) 双峰总面积 vs 帧号（应大致守恒）
  (c) 质心 vs f_poor 散点 + 线性拟合 R²

用法：
    python scripts/check_insitu_peaks.py \
        --data ~/Desktop/lmfpdata \
        --mask outputs/masks/lmfp1_lab6_mask.npy \
        --batch-csv outputs/insitu/insitu_fit.csv \
        --outdir outputs/insitu
"""
import argparse
import csv
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.config import get_config  # noqa: E402
from xrd_toolkit.services.background import (  # noqa: E402
    estimate_baseline_sliding)
from xrd_toolkit.services.data_loader import load_diffraction_image  # noqa: E402
from xrd_toolkit.services.integrator import integrate_1d  # noqa: E402
from xrd_toolkit.services.mask import load_mask  # noqa: E402

# (200) 双峰窗口；两侧平带避开 rich/poor (200)
WIN_LO, WIN_HI = 1.18, 1.55
SIDE_LO = (1.18, 1.28)
SIDE_HI = (1.50, 1.56)


def _tag(path):
    import re
    m = re.search(r"(\d+)$", Path(path).stem)
    return int(m.group(1)) if m else 0


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


def _batch_rows(path):
    if not path or not Path(path).exists():
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = {}
    for r in rows:
        try:
            out[int(float(r["frame"]))] = {
                "f_poor": float(r.get("f_poor", "nan")),
                "status": r.get("status", ""),
                "usable": str(r.get("usable", "")).lower() == "true",
            }
        except (KeyError, ValueError):
            continue
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=str(Path.home() / "Desktop" / "lmfpdata"))
    ap.add_argument("--mask",
                    default="outputs/masks/lmfp1_lab6_mask.npy")
    ap.add_argument("--config", default="lmfp1_lab6")
    ap.add_argument("--batch-csv",
                    default="outputs/insitu/insitu_fit.csv")
    ap.add_argument("--outdir", default="outputs/insitu")
    ap.add_argument("--npt", type=int, default=3000)
    args = ap.parse_args()

    data_dir = Path(args.data).expanduser()
    files = sorted(data_dir.glob("*.tif"), key=_tag)
    if not files:
        ap.error(f"没有 .tif：{data_dir}")
    geom = dict(get_config(args.config)["geometry"])
    mask = None
    mp = Path(args.mask).expanduser()
    if not mp.is_absolute():
        mp = ROOT / mp
    if mp.exists():
        mask = load_mask(mp)
    batch = _batch_rows(args.batch_csv)

    outdir = Path(args.outdir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, path in enumerate(files, start=1):
        img = load_diffraction_image(str(path))
        t, y = integrate_1d(img, npt=args.npt, tth_min_deg=1.0,
                           tth_max_deg=8.0, mask=mask, **geom)
        bg = estimate_baseline_sliding(t, y, 0.2)
        area, cen = _doublet_stats(t, y - bg)
        meta = batch.get(i, {})
        rows.append({
            "frame": i, "file": path.name,
            "status": meta.get("status", ""),
            "usable": meta.get("usable", False),
            "f_poor": meta.get("f_poor", float("nan")),
            "doublet_area": area, "centroid": cen,
        })
        print(f"[{i}/{len(files)}] {path.name}  "
              f"centroid={cen:.4f}  area={area:.3f}", flush=True)

    csv_path = outdir / "model_independent.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {csv_path}")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    frames = [r["frame"] for r in rows]
    cen = np.array([r["centroid"] for r in rows], dtype=float)
    area = np.array([r["doublet_area"] for r in rows], dtype=float)
    fpoor = np.array([r["f_poor"] for r in rows], dtype=float)
    finite = [r for r in rows if math.isfinite(r["f_poor"])]
    trans = (max(finite, key=lambda r: r["f_poor"])["frame"]
             if finite else frames[0])
    lo, hi = frames[0] - 0.5, frames[-1] + 0.5
    ok = np.array([r["status"] == "ok" for r in rows])
    bad = ~ok

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))
    ax = axes[0]
    ax.axvspan(lo, trans + 0.5, color="tab:blue", alpha=0.06, lw=0)
    ax.axvspan(trans + 0.5, hi, color="tab:orange", alpha=0.06, lw=0)
    ax.plot(frames, cen, "-", lw=1.0, color="tab:green", label="(200) centroid")
    ax.plot(np.array(frames)[bad], cen[bad], "o", mfc="none",
            mec="tab:orange", ms=4, label="not_converged/cell_jump")
    ax.set_xlim(lo, hi); ax.set_xlabel("frame")
    ax.set_ylabel("centroid 2θ (°)")
    ax.set_title("(a) (200) doublet centroid", fontsize=10)
    ax.grid(alpha=0.25, linestyle=":")
    ax2 = ax.twinx()
    ax2.plot(frames, fpoor, ":", lw=1.0, color="0.35", label="f_poor (batch)")
    ax2.set_ylabel("f_poor (batch)")
    ax2.set_ylim(-0.02, 1.02)
    lines = ax.get_lines() + ax2.get_lines()
    ax.legend(lines, [ln.get_label() for ln in lines],
              fontsize=7, loc="center right")

    ax = axes[1]
    ax.axvspan(lo, trans + 0.5, color="tab:blue", alpha=0.06, lw=0)
    ax.axvspan(trans + 0.5, hi, color="tab:orange", alpha=0.06, lw=0)
    ax.plot(frames, area / np.nanmedian(area), "-", lw=1.0, color="tab:purple")
    ax.plot(np.array(frames)[bad],
            (area / np.nanmedian(area))[bad], "o", mfc="none",
            mec="tab:orange", ms=4)
    ax.set_xlim(lo, hi); ax.set_xlabel("frame")
    ax.set_ylabel("area / median")
    ax.set_title("(b) (200) doublet total area (phase-dependent)",
                 fontsize=10)
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
                va="top", fontsize=9)
    ax.set_xlabel("f_poor (batch)")
    ax.set_ylabel("(200) centroid 2θ (°)")
    ax.set_title("(c) centroid vs batch f_poor", fontsize=10)
    ax.grid(alpha=0.25, linestyle=":")

    fig.suptitle("Model-independent check: (200) doublet centroid follows "
                 "the batch phase fraction", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    png = outdir / "model_independent.png"
    fig.savefig(png, dpi=160)
    plt.close(fig)
    print(f"wrote {png}")


if __name__ == "__main__":
    main()
