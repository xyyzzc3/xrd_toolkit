#!/usr/bin/env python3
"""已知 wt% 混合物的回收实验（定量验证 A，2026-11-06）。

做法：从真实原位帧取 2θ 网格、背景形状和仪器峰形，按已知富锂/贫锂
重量分数合成混合谱 → 用原位管线的拟合口径回收 wt% → 量化偏差。

两档背景：
  * oracle     拟合时直接给"真背景"（真实帧的滑动基线）——验证公式本身；
  * estimated  拟合时像生产一样重新估背景——量化背景估计带来的偏差。

产物（默认 outputs/recovery/）：
  recovery.csv          每档/每个配比/每个噪声种子的回收表
  recovery_summary.txt  偏差/RMS/esd 对照
  recovery.png          回收值 vs 真值（1:1 线 + ±2 wt% 带）

用法：
    python scripts/quantitative_recovery.py
    python scripts/quantitative_recovery.py --fractions 10,30,50,70,90 \
        --seeds 3 --noise-scale 0.15
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
from xrd_toolkit.simulation import (  # noqa: E402
    MixturePhase, load_structure, recovery_fit, simulate_mixture,
    summarize_recovery, weight_percent_esd)
from xrd_toolkit.simulation.insitu import (  # noqa: E402
    CELL_BOUNDS, DEFAULT_POOR, DEFAULT_RICH, _instrument)


def _fractions(text):
    return [float(x) for x in str(text).split(",") if x.strip()]


def _collect(args):
    """读一帧真实数据，返回 (grid, y, bg, instrument, geom, lam)。"""
    geom = dict(get_config(args.config)["geometry"])
    lam = geom["wavelength_m"] * 1e10
    path = Path(args.data).expanduser() / args.frame
    if not path.exists():
        raise SystemExit(f"找不到帧：{path}")
    mask = None
    if args.mask:
        mask = load_mask(Path(args.mask).expanduser())
    img = load_diffraction_image(str(path))
    grid, y = integrate_1d(
        img, npt=args.npt, tth_min_deg=args.tth_min,
        tth_max_deg=args.tth_max, mask=mask, **geom)
    bg = estimate_baseline_sliding(grid, y, 0.2)
    inst = _instrument(geom, lam, lab6_tif=args.lab6_tif,
                       lab6_cif=args.lab6_cif)
    if inst is None:
        raise SystemExit("没有 LaB₆ 标样图，无法固定仪器峰形")
    instrument = {key: float(inst.params[key])
                  for key in ("zero", "u", "v", "w", "x", "y")}
    return grid, y, bg, instrument, geom, lam


def _run(args):
    grid, _y_real, bg, instrument, _geom, lam = _collect(args)
    rich = load_structure(str(Path(args.rich).expanduser()))
    poor = load_structure(str(Path(args.poor).expanduser()))
    profile = {
        "polariz": 0.5, "nmax": 10,
        "two_theta_range": (float(grid[0]), float(grid[-1])),
        "zero_deg": instrument["zero"],
        **{key: instrument[key] for key in ("u", "v", "w", "x", "y")},
    }
    targets = _fractions(args.fractions)
    modes = (["oracle", "estimated"] if args.mode == "both"
             else [args.mode])
    rows = []
    total = len(targets) * len(modes) * max(1, args.seeds)
    done = 0
    for mode in modes:
        for target in targets:
            for seed in range(max(1, args.seeds)):
                phases = [
                    MixturePhase(rich, target, "LMFP_rich",
                                 b_iso=0.0, size_um=0.093),
                    MixturePhase(poor, 100.0 - target, "LMFP_poor",
                                 b_iso=0.5, size_um=0.1),
                ]
                y_true, y_obs = simulate_mixture(
                    phases, grid, wavelength=lam, background=bg,
                    peak_max=args.peak_max,
                    noise_scale=args.noise_scale, seed=seed,
                    profile=profile)
                res = recovery_fit(
                    rich, poor, grid, y_obs, wavelength=lam,
                    instrument=instrument,
                    main_cell=rich.lattice.abc,
                    extra_cell=poor.lattice.abc,
                    background=(bg if mode == "oracle" else "estimated"),
                    background_window=args.fit_background_window,
                    cell_bounds=CELL_BOUNDS, max_nfev=args.max_nfev)
                weights = {row.label: row for row in res.phase_weights}
                main_row = weights.get("LMFP_rich")
                esd = weight_percent_esd(res).get("LMFP_rich", float("nan"))
                got = (float(main_row.weight_percent)
                       if main_row is not None else float("nan"))
                rows.append({
                    "mode": mode,
                    "target_wt": float(target),
                    "seed": int(seed),
                    "recovered_wt": got,
                    "bias_wt": got - float(target),
                    "esd_wt": float(esd),
                    "rwp": float(res.rwp),
                    "success": bool(res.success),
                })
                done += 1
                print(f"[{done}/{total}] {mode:9s} target {target:5.1f} "
                      f"-> {got:6.2f} wt%  (Rwp {res.rwp:4.1f}%, "
                      f"esd {esd:4.2f})", flush=True)
    return rows


def _write_csv(outdir, rows):
    path = outdir / "recovery.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_summary(outdir, rows, args):
    lines = ["=== known-mixture wt% recovery ===",
             f"frame: {args.frame}",
             f"noise: {args.noise_scale} x sqrt(N) "
             "(1.0 = Poisson; this batch's real data is ~0.15)",
             f"peak max: {args.peak_max} counts",
             f"fractions: {args.fractions}",
             f"seeds: {args.seeds}", ""]
    for mode in ("oracle", "estimated"):
        sub = [r for r in rows if r["mode"] == mode]
        if not sub:
            continue
        stat = summarize_recovery(sub)
        lines.append(
            f"{mode:9s}: n={stat['n']:3d}  bias={stat['bias']:+.2f} wt%  "
            f"RMS={stat['rms']:.2f}  max|err|={stat['max_abs']:.2f}  "
            f"slope={stat['slope']:.3f}")
        esds = [r["esd_wt"] for r in sub if math.isfinite(r["esd_wt"])]
        errs = [abs(r["bias_wt"]) for r in sub if math.isfinite(r["bias_wt"])]
        if esds and errs:
            ratio = (float(np.mean(esds)) / float(np.mean(errs))
                     if np.mean(errs) > 0 else float("inf"))
            lines.append(
                f"{'':9s}  mean linear esd {np.mean(esds):.2f} wt% vs "
                f"mean |error| {np.mean(errs):.2f} wt% "
                f"(esd/|error| = {ratio:.2f}; <1 means the esd is optimistic)")
        lines.append("")
    lines.append("per-fraction bias (wt%):")
    for mode in ("oracle", "estimated"):
        sub = [r for r in rows if r["mode"] == mode]
        if not sub:
            continue
        targets = sorted({r["target_wt"] for r in sub})
        parts = []
        for target in targets:
            vals = [r["bias_wt"] for r in sub
                    if r["target_wt"] == target
                    and math.isfinite(r["bias_wt"])]
            if vals:
                parts.append(f"{target:g}:{np.mean(vals):+.2f}")
        lines.append(f"  {mode:9s} " + "  ".join(parts))
    lines.append("")
    lines.extend([
        "reading: oracle isolates the wt% formula; estimated is the "
        "production background treatment.",
        "GSAS-II cross-checks are development references only and are "
        "never shown in the GUI.",
    ])
    path = outdir / "recovery_summary.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_figure(outdir, rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    styles = {"oracle": ("#0072B2", "o",
                         "known background (formula check)"),
              "estimated": ("#D55E00", "s",
                            "estimated background (production)")}
    lo, hi = 0.0, 100.0
    ax.plot([lo, hi], [lo, hi], color="0.35", lw=1.0, ls="--",
            label="1:1")
    ax.fill_between([lo, hi], [lo - 2, hi - 2], [lo + 2, hi + 2],
                    color="0.85", alpha=0.6, label="±2 wt%")
    for mode, (color, marker, label) in styles.items():
        sub = [r for r in rows if r["mode"] == mode]
        if not sub:
            continue
        xs = np.array([r["target_wt"] for r in sub])
        ys = np.array([r["recovered_wt"] for r in sub])
        es = np.array([r["esd_wt"] for r in sub])
        ax.errorbar(xs, ys, yerr=es, fmt=marker, color=color, ms=4,
                    lw=1.0, capsize=2, label=label)
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel("known wt% (rich)")
    ax.set_ylabel("recovered wt% (rich)")
    ax.set_title("Known-mixture recovery (LMFP rich / poor)")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    path = outdir / "recovery.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=str(Path.home() / "Data/XRD/lmfpdata"))
    ap.add_argument("--frame", default="LMFP_1_atten0-00029.tif")
    ap.add_argument("--mask", default=None,
                    help="可选 mask（.npy/.edf）；缺省不扣坏点")
    ap.add_argument("--config", default="lmfp1_lab6")
    ap.add_argument("--rich", default=str(DEFAULT_RICH))
    ap.add_argument("--poor", default=str(DEFAULT_POOR))
    ap.add_argument("--lab6-tif", default=str(ROOT / "data/lab6-00024.tif"))
    ap.add_argument("--lab6-cif",
                    default=str(ROOT / "data/structures/LaB6_NIST660c.cif"))
    ap.add_argument("--outdir", default=str(ROOT / "outputs/recovery"))
    ap.add_argument("--fractions", default="5,10,20,30,40,50,60,70,80,90,95")
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--noise-scale", type=float, default=0.15)
    ap.add_argument("--fit-background-window", type=float, default=0.2,
                    help="拟合时重新估背景的滑动窗宽（度）；只影响 "
                         "estimated 档")
    ap.add_argument("--peak-max", type=float, default=2500.0)
    ap.add_argument("--mode", choices=("both", "oracle", "estimated"),
                    default="both")
    ap.add_argument("--npt", type=int, default=3000)
    ap.add_argument("--tth-min", type=float, default=1.0)
    ap.add_argument("--tth-max", type=float, default=8.0)
    ap.add_argument("--max-nfev", type=int, default=120)
    args = ap.parse_args(argv)
    outdir = Path(args.outdir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    rows = _run(args)
    if not rows:
        raise SystemExit("没有结果")
    print("\n--- summary ---")
    for mode in ("oracle", "estimated"):
        sub = [r for r in rows if r["mode"] == mode]
        if sub:
            stat = summarize_recovery(sub)
            print(f"{mode:9s}: bias {stat['bias']:+.2f} wt%  "
                  f"RMS {stat['rms']:.2f}  max {stat['max_abs']:.2f}  "
                  f"slope {stat['slope']:.3f}")
    print(f"csv:     {_write_csv(outdir, rows)}")
    print(f"summary: {_write_summary(outdir, rows, args)}")
    print(f"figure:  {_write_figure(outdir, rows)}")


if __name__ == "__main__":
    main()
