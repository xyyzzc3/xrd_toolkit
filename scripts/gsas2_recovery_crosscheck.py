#!/usr/bin/env python3
"""已知 wt% 合成混合物的 GSAS-II 对照实验（2026-11-17）。

目的：把「我们的定量误差来自背景模型还是引擎本身」拆开。题目与
`quantitative_recovery.py` 完全相同：从真实原位帧取 2θ 网格、背景形状
和 LaB₆ 仪器峰形，按已知富锂/贫锂重量分数合成两相混合谱。

对照系列：
  ours oracle   本项目，拟合时给真背景（已有，考公式/引擎）
  ours estimated 本项目，生产滑动基线（已有，生产口径）
  ours cheb     本项目，Chebyshev-6 背景（候选改进）
  gsas nobg     GSAS-II，输入已减真背景、不拟合背景（诊断：无背景垫）
  gsas const    GSAS-II，输入已减真背景、只精修一个常数背景
  gsas cheb     GSAS-II，Chebyshev-1 六项自由精修（GSAS 生产口径）

2026-11-17 结果（22 题：11 配比 × 2 种子）：
  ours oracle    bias +0.02  RMS 0.03  max 0.05
  ours estimated bias −1.80  RMS 2.83  max 5.02
  ours cheb-6    bias +0.30  RMS 0.38  max 0.68
  GSAS no-bg     bias −0.62  RMS 2.84  max 4.68
  GSAS const     bias −0.62  RMS 1.17  max 1.90
  GSAS cheb-6    bias −0.46  RMS 0.81  max 1.31
  → 生产误差主要来自滑动背景；Chebyshev-6 把 RMS 从 2.83 降到 0.38，
    与本实验中的 GSAS-II 同档（0.81）。

三步、跨两个环境：

    # 1) 生成合成谱并导出（本项目环境；需要 pymatgen）
    conda run -n XRD_Toolkit_Environment python scripts/gsas2_recovery_crosscheck.py prepare \
        --fractions 10,50,90 --seeds 2 --outdir /tmp/xrd_gsas2_recovery

    # 2) GSAS-II 逐题精修（gsas2 环境）
    conda run -n gsas2 python scripts/gsas2_recovery_crosscheck.py refine \
        --workdir /tmp/xrd_gsas2_recovery

    # 3) 汇总（本项目环境）
    conda run -n XRD_Toolkit_Environment python scripts/gsas2_recovery_crosscheck.py compare \
        --workdir /tmp/xrd_gsas2_recovery \
        --ours-csv outputs/recovery/recovery.csv \
        --cheb-csv outputs/recovery_cheb6/recovery.csv
"""
import argparse
import csv
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

# GSAS-II 只装在 gsas2 环境；refine 子命令才不会导入本项目重依赖。
DEFAULT_OUT = Path("/tmp/xrd_gsas2_recovery")
NPT = 3000
TTH_RANGE = (1.0, 8.0)
PEAK_MAX = 2500.0
NOISE_SCALE = 0.15
FRACTIONS = "10,20,30,40,50,60,70,80,90"
SEEDS = 2


def write_instprm(path, lam, params):
    """与 gsas2_insitu_crosscheck.py 相同的 G2 fmt 单波长仪器文件。"""
    path = Path(path)
    path.write_text(
        "#GSAS-II instrument parameter file; do not add/delete items!\n"
        f"Type:PXC\nLam:{lam}\nZero:{params['zero']}\nPolariz.:0.5\n"
        f"U:{params['u']}\nV:{params['v']}\nW:{params['w']}\n"
        f"X:{params['x']}\nY:{params['y']}\nZ:0.0\nSH/L:0.0\n"
        "Azimuth:0.0\nBank:1.0\n", encoding="utf-8")
    return path


def write_xye(path, tth, intensity, sigma):
    """写 2θ/I/σ 三列；σ 始终取原始观测计数的 Poisson 误差。"""
    arr = np.column_stack([np.asarray(tth, dtype=float),
                           np.asarray(intensity, dtype=float),
                           np.asarray(sigma, dtype=float)])
    np.savetxt(path, arr, fmt="%.8f %.6f %.6f")
    return path


def prepare(args):
    from xrd_toolkit.config import get_config
    from xrd_toolkit.simulation import MixturePhase, load_structure, simulate_mixture
    import quantitative_recovery as qr
    from gsas2_insitu_crosscheck import symmetrize_cif

    outdir = Path(args.outdir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    fractions = [float(x) for x in args.fractions.split(",") if x.strip()]
    seeds = range(args.seeds)

    ns = argparse.Namespace(
        config=args.config, data=args.data, frame=args.frame, mask=args.mask,
        npt=NPT, tth_min=TTH_RANGE[0], tth_max=TTH_RANGE[1],
        lab6_tif=args.lab6_tif, lab6_cif=args.lab6_cif)
    grid, _y_real, bg, instrument, _geom, lam = qr._collect(ns)
    rich = load_structure(str(Path(args.rich).expanduser()))
    poor = load_structure(str(Path(args.poor).expanduser()))
    profile = {
        "polariz": 0.5, "nmax": 10,
        "two_theta_range": (float(grid[0]), float(grid[-1])),
        "zero_deg": instrument["zero"],
        **{key: instrument[key] for key in ("u", "v", "w", "x", "y")},
    }

    write_instprm(outdir / "recovery.instprm", lam, instrument)
    rich_sym, _ = symmetrize_cif(args.rich, outdir / "rich_pnma.cif")
    poor_sym, _ = symmetrize_cif(args.poor, outdir / "poor_pnma.cif")
    print(f"对称化 CIF: rich={rich_sym}, poor={poor_sym}")

    from pymatgen.core import Structure
    cif_cells = {}
    for name in ("rich", "poor"):
        lattice = Structure.from_file(
            str(outdir / f"{name}_pnma.cif")).lattice
        if any(abs(a - 90.0) > 1e-3 for a in lattice.angles):
            raise SystemExit(f"{name} 对称化后不是正交晶胞：{lattice.angles}")
        cif_cells[name] = [float(v) for v in lattice.abc]

    patterns = []
    for target in fractions:
        for seed in seeds:
            phases = [
                MixturePhase(rich, target, "LMFP_rich",
                             b_iso=0.0, size_um=0.093),
                MixturePhase(poor, 100.0 - target, "LMFP_poor",
                             b_iso=0.5, size_um=0.1),
            ]
            _y_true, y_obs = simulate_mixture(
                phases, grid, wavelength=lam, background=bg,
                peak_max=PEAK_MAX, noise_scale=NOISE_SCALE, seed=seed,
                profile=profile)
            y_obs = np.asarray(y_obs, dtype=float)
            sigma = np.sqrt(np.maximum(y_obs, 1.0))
            stem = f"mix{int(target):02d}_s{seed}"
            write_xye(outdir / f"{stem}_raw.xye", grid, y_obs, sigma)
            write_xye(outdir / f"{stem}_nobg.xye", grid, y_obs - bg, sigma)
            patterns.append({"stem": stem, "target": target, "seed": seed})
            print(f"{stem}: I {y_obs.min():.0f}..{y_obs.max():.0f} "
                  f"(bg {bg.min():.0f}..{bg.max():.0f})")

    meta = {"lam": lam, "instrument": instrument, "cif_cells": cif_cells,
            "patterns": patterns, "noise_scale": NOISE_SCALE,
            "peak_max": PEAK_MAX, "fractions": fractions,
            "seeds": args.seeds}
    (outdir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"准备完成：{outdir}")


def _load_gsas():
    gsas_dir = os.environ.get("GSAS2_DIR") or str(Path(sys.prefix) / "GSAS-II")
    if not (Path(gsas_dir) / "GSASII").is_dir():
        raise SystemExit(
            f"找不到 GSAS-II（{gsas_dir}）。请用 gsas2 环境运行，"
            "或设 GSAS2_DIR 指向源码目录。")
    sys.path.insert(0, gsas_dir)
    from GSASII import GSASIIlattice as G2lat
    from GSASII import GSASIIscriptable as G2sc
    return G2sc, G2lat


def _set_cell(phase, abc, G2lat):
    phase.data["General"]["Cell"][1:7] = list(abc) + [90.0, 90.0, 90.0]
    phase.data["General"]["Cell"][7] = G2lat.calc_V(
        G2lat.cell2A(phase.data["General"]["Cell"][1:7]))


def _set_uiso(phase, b_iso):
    u = float(b_iso) / (8.0 * math.pi ** 2)
    for atom in phase.atoms():
        atom.uiso = u


def _background_zero(hist):
    """把 Chebyshev 背景固定为 0（nobg 档）。"""
    hist.set_refinements({"Background": {"type": "chebyschev-1",
                                         "no. coeffs": 1, "refine": False}})
    data = hist.data["Background"]
    # GSAS-II 结构：[type, ?, coeffs, ...]；不同版本略有差异，逐层兜底
    for item in data:
        if isinstance(item, list) and item and isinstance(item[0], (int, float)):
            item[:] = [0.0 for _ in item]
            break
    else:
        try:
            data[0][2] = [0.0]
        except (IndexError, TypeError):
            pass


def refine_pattern(G2sc, G2lat, workdir, stem, mode, meta,
                   refine_cells=True):
    """单题：两相 Rietveld；先 scale/背景，再放开晶胞。"""
    t0 = time.perf_counter()
    workdir = Path(workdir)
    gpx = G2sc.G2Project(
        newgpx=str(workdir / f"gm_{stem}_{mode}.gpx"))
    phases = {}
    for name in ("rich", "poor"):
        phases[name] = gpx.add_phase(
            str(workdir / f"{name}_pnma.cif"), phasename=name,
            fmthint="CIF")
        _set_uiso(phases[name], 0.0 if name == "rich" else 0.5)
        _set_cell(phases[name], tuple(meta["cif_cells"][name]), G2lat)

    fname = f"{stem}_nobg.xye" if mode in ("nobg", "const") else (
        f"{stem}_raw.xye")
    xye = workdir / fname
    hist = gpx.add_powder_histogram(
        str(xye), str(workdir / "recovery.instprm"), phases="all")
    hist.set_refinements({"Limits": TTH_RANGE})
    if mode == "cheb":
        hist.set_refinements({"Background": {"type": "chebyschev-1",
                                             "no. coeffs": 6,
                                             "refine": True}})
    elif mode == "const":
        hist.set_refinements({"Background": {"type": "chebyschev-1",
                                             "no. coeffs": 1,
                                             "refine": True}})
    else:
        _background_zero(hist)
    hist.data["Sample Parameters"]["Scale"][1] = False
    for name in ("rich", "poor"):
        phases[name].set_HAP_refinements({"Scale": True})
        hap = phases[name].getHAPvalues(hist.name)
        hap["Size"][1] = [1e5, 1e5, 1.0]
        hap["Mustrain"][1] = [0.0, 0.0, 1.0]

    gpx.set_Controls("cycles", 4)
    gpx.do_refinements([{}])
    if refine_cells:
        phases = {n: gpx.phase(n) for n in ("rich", "poor")}
        for name in ("rich", "poor"):
            phases[name].set_refinements({"Cell": True})
        gpx.set_Controls("cycles", 8)
        gpx.do_refinements([{}])

    hist = gpx.histogram(0)
    w_r = hist.residuals.get("wR")
    if w_r is None:
        raise RuntimeError(f"{stem} {mode}: GSAS-II 精修失败（wR=None），"
                           f"看 {workdir}/gm_{stem}_{mode}.lst")
    mf = hist.ComputeMassFracs()
    rich_wt = float(mf["rich"][0])
    poor_wt = float(mf["poor"][0])
    denom = rich_wt + poor_wt
    return {"stem": stem, "mode": mode,
            "rich_wt_pct": rich_wt * 100.0,
            "poor_wt_pct": poor_wt * 100.0,
            "f_rich": (rich_wt / denom if denom > 0 else float("nan")),
            "wR": float(w_r), "t_total_s": time.perf_counter() - t0}


def refine(args):
    G2sc, G2lat = _load_gsas()
    workdir = Path(args.workdir).expanduser()
    meta = json.loads((workdir / "meta.json").read_text(encoding="utf-8"))
    rows = []
    for pat in meta["patterns"]:
        for mode in ("nobg", "const", "cheb"):
            print(f"==== {pat['stem']} [{mode}]", flush=True)
            row = refine_pattern(G2sc, G2lat, workdir, pat["stem"], mode,
                                 meta)
            row["target"] = float(pat["target"])
            row["seed"] = int(pat["seed"])
            rows.append(row)
            print(f"   wR={row['wR']:.2f}%  "
                  f"rich={row['rich_wt_pct']:.2f} wt% "
                  f"(target {row['target']:.0f})  {row['t_total_s']:.1f} s",
                  flush=True)
    fields = ["stem", "mode", "target", "seed", "rich_wt_pct",
              "poor_wt_pct", "f_rich", "wR", "t_total_s"]
    with open(workdir / "gsas2_recovery.csv", "w", newline="",
              encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    total = sum(r["t_total_s"] for r in rows)
    print(f"写入 {workdir / 'gsas2_recovery.csv'}（{len(rows)} 题，"
          f"{total:.1f} s）")


def _read_recovery(path, mode):
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["mode"] != mode:
                continue
            rows.append({"target": float(row["target_wt"]),
                         "seed": int(float(row["seed"])),
                         "recovered": float(row["recovered_wt"])})
    return rows


def _stats(rows):
    if not rows:
        return None
    got = np.array([r["recovered"] for r in rows], dtype=float)
    true = np.array([r["target"] for r in rows], dtype=float)
    err = got - true
    return {"n": len(rows), "bias": float(err.mean()),
            "rms": float(np.sqrt(np.mean(err ** 2))),
            "max_abs": float(np.max(np.abs(err))),
            "slope": float(np.polyfit(true, got, 1)[0])}


def _series(args):
    series = {}
    if args.ours_csv:
        series["ours oracle"] = _read_recovery(args.ours_csv, "oracle")
        series["ours estimated"] = _read_recovery(args.ours_csv, "estimated")
    if args.cheb_csv:
        series["ours cheb-6"] = _read_recovery(args.cheb_csv, "estimated")
    with open(args.workdir / "gsas2_recovery.csv", newline="",
              encoding="utf-8") as fh:
        gsas = list(csv.DictReader(fh))
    for mode, label in (("nobg", "GSAS-II no-bg"),
                        ("const", "GSAS-II const"),
                        ("cheb", "GSAS-II cheb-6")):
        series[label] = [
            {"target": float(r["target"]), "seed": int(r["seed"]),
             "recovered": float(r["rich_wt_pct"])}
            for r in gsas if r["mode"] == mode]
    # 只比较 GSAS 实际跑过的题（按 target+seed 对齐），避免 n 不一致
    keys = {(r["target"], r["seed"]) for r in series["GSAS-II cheb-6"]}
    for name in list(series):
        series[name] = [r for r in series[name]
                        if (r["target"], r["seed"]) in keys]
    return series


def compare(args):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    workdir = Path(args.workdir).expanduser()
    series = _series(argparse.Namespace(workdir=workdir,
                                        ours_csv=args.ours_csv,
                                        cheb_csv=args.cheb_csv))
    lines = ["=== known-mixture recovery: ours vs GSAS-II ==="]
    stats = {}
    for name, rows in series.items():
        st = _stats(rows)
        stats[name] = st
        lines.append(
            f"{name:16s} n={st['n']:3d}  bias={st['bias']:+.2f} wt%  "
            f"RMS={st['rms']:.2f}  max={st['max_abs']:.2f}  "
            f"slope={st['slope']:.3f}")
    lines.append("")
    lines.append("per-fraction recovered wt% (mean over seeds):")
    targets = sorted({r["target"] for rows in series.values() for r in rows})
    header = "target  " + "  ".join(f"{name:>14s}" for name in series)
    lines.append(header)
    for target in targets:
        cells = []
        for name, rows in series.items():
            vals = [r["recovered"] for r in rows if r["target"] == target]
            cells.append(f"{np.mean(vals):14.2f}" if vals else f"{'-':>14s}")
        lines.append(f"{target:6.0f}  " + "  ".join(cells))
    text = "\n".join(lines) + "\n"
    (workdir / "comparison.txt").write_text(text, encoding="utf-8")
    print(text)

    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    markers = {"ours oracle": ("o", "#1f4e79"),
               "ours estimated": ("s", "#c55a11"),
               "ours cheb-6": ("^", "#548235"),
               "GSAS-II no-bg": ("D", "#7030a0"),
               "GSAS-II const": ("P", "#8faadc"),
               "GSAS-II cheb-6": ("v", "#c00000")}
    for name, rows in series.items():
        marker, color = markers.get(name, ("o", "gray"))
        ax.plot([r["target"] for r in rows],
                [r["recovered"] for r in rows],
                marker=marker, ls="none", ms=4.5, color=color,
                alpha=0.85, label=name)
    ax.plot([0, 100], [0, 100], ls="--", lw=0.9, color="black")
    ax.fill_between([0, 100], [-2, 98], [2, 102], color="0.85", alpha=0.6,
                    lw=0)
    ax.set_xlabel("known wt% (rich)")
    ax.set_ylabel("recovered wt% (rich)")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.legend(fontsize=7, frameon=False, loc="upper left")
    ax.grid(alpha=0.2, lw=0.4)
    fig.tight_layout(pad=0.3)
    fig.savefig(workdir / "comparison.png", dpi=200)
    plt.close(fig)
    print(f"写入 {workdir / 'comparison.txt'} / comparison.png")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="生成合成谱并导出 xye/CIF/仪器文件")
    p.add_argument("--data", default=str(Path.home() / "Data/XRD/lmfpdata"))
    p.add_argument("--frame", default="LMFP_1_atten0-00029.tif")
    p.add_argument("--mask", default=None)
    p.add_argument("--config", default="lmfp1_lab6")
    p.add_argument("--rich",
                   default=str(ROOT / "data/structures/"
                               "LiMn0.6Fe0.4PO4_olivine.cif"))
    p.add_argument("--poor",
                   default=str(ROOT / "data/structures/"
                               "Mn0.6Fe0.4PO4_heterosite_rich_axis.cif"))
    p.add_argument("--lab6-tif", default=str(ROOT / "data/lab6-00024.tif"))
    p.add_argument("--lab6-cif",
                   default=str(ROOT / "data/structures/LaB6_NIST660c.cif"))
    p.add_argument("--fractions", default=FRACTIONS)
    p.add_argument("--seeds", type=int, default=SEEDS)
    p.add_argument("--outdir", default=str(DEFAULT_OUT))
    p.set_defaults(func=prepare)

    r = sub.add_parser("refine", help="GSAS-II 逐题精修（gsas2 环境）")
    r.add_argument("--workdir", default=str(DEFAULT_OUT))
    r.set_defaults(func=refine)

    c = sub.add_parser("compare", help="汇总五条系列并出图")
    c.add_argument("--workdir", default=str(DEFAULT_OUT))
    c.add_argument("--ours-csv", default=str(ROOT / "outputs/recovery/"
                                             "recovery.csv"))
    c.add_argument("--cheb-csv", default=str(ROOT / "outputs/recovery_cheb6/"
                                            "recovery.csv"))
    c.set_defaults(func=compare)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
