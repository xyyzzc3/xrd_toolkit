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
        --data ~/Data/XRD/lmfpdata \
        --mask outputs/masks/lmfp1_lab6_mask.npy \
        --batch-csv outputs/insitu/insitu_fit.csv \
        --outdir outputs/insitu
"""
import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.config import get_config  # noqa: E402
from xrd_toolkit.services.background import (  # noqa: E402
    estimate_baseline_sliding)
from xrd_toolkit.services.data_loader import load_diffraction_image  # noqa: E402
from xrd_toolkit.services.integrator import integrate_1d  # noqa: E402
from xrd_toolkit.services.mask import load_mask  # noqa: E402
from xrd_toolkit.simulation.insitu_plots import (  # noqa: E402
    _doublet_stats, draw_model_independent)


def _tag(path):
    import re
    m = re.search(r"(\d+)$", Path(path).stem)
    return int(m.group(1)) if m else 0


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
    ap.add_argument("--data", default=str(Path.home() / "Data" / "XRD" / "lmfpdata"))
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
    fig = plt.figure(figsize=(15, 4.6))
    draw_model_independent(fig, rows)
    png = outdir / "model_independent.png"
    fig.savefig(png, dpi=160)
    plt.close(fig)
    print(f"wrote {png}")


if __name__ == "__main__":
    main()
