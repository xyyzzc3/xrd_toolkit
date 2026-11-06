#!/usr/bin/env python3
"""少数相两段拟合实验（A 定量验证，2026-11-06）。

第一段 = 生产管线（双起点、两相晶胞全放开）；第二段固定**多数相**晶胞，
只放开少数相晶胞，看 minority 晶胞能不能从"被多数相拉偏"里救回来。

判定"多数"沿用图上的口径：`f_rich`（相对散射分数）≥ 0.5 时 rich 为
多数相，否则 poor 为多数相。这就是趋势图上实心/淡色点的同一条线。

GSAS-II 只作为开发对照：`--gsas-csv` 可选，读
`gsas2_insitu_crosscheck.py refine` 产出的 `gsas2_insitu.csv`。
**本脚本的产物不接 GUI，也不出现在界面文案里。**

用法：
    python scripts/minority_stage_check.py \
        --frames 1,15,25,27,35,45,55,65,75,81 \
        --gsas-csv outputs/gsas2_insitu10/gsas2_insitu.csv
"""
import argparse
import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.simulation import fit_pattern  # noqa: E402
from xrd_toolkit.simulation.insitu import (  # noqa: E402
    BG_SMOOTH, CELL_BOUNDS, FRAME_NFEV, _cell, _extra_phases,
    _fit_initial, _fit_second_stage, frames_in_dir, load_frame,
    prepare_setup)


def _value(res, key):
    """优先取自由参数；第二段里被固定的晶胞在 initial 里。"""
    if key in (getattr(res, "params", None) or {}):
        return float(res.params[key])
    return float((getattr(res, "initial", None) or {}).get(
        key, float("nan")))


def _cells(res):
    return {**{f"rich_{axis}": _value(res, axis) for axis in "abc"},
            **{f"poor_{axis}": _value(res, f"LMFP_poor:{axis}")
               for axis in "abc"}}


def _load_seeds(path):
    """从生产批次 CSV 取每帧的晶胞，作为下一帧的起点（贴近生产种子链）。"""
    if not path:
        return {}
    path = Path(path).expanduser()
    if not path.exists():
        print(f"（种子 CSV 不存在，退回 CIF 起点：{path}）")
        return {}
    out = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            frame = int(float(row["frame"]))
            try:
                out[frame] = {
                    "rich": tuple(float(row[f"rich_{axis}"])
                                  for axis in "abc"),
                    "poor": tuple(float(row[f"poor_{axis}"])
                                  for axis in "abc"),
                }
            except (KeyError, ValueError):
                continue
    return out


def _load_gsas(path):
    if not path:
        return {}
    path = Path(path).expanduser()
    if not path.exists():
        print(f"（GSAS-II 对照不存在，跳过：{path}）")
        return {}
    out = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            frame = int(float(row["frame"]))
            out[frame] = {key: float(val) for key, val in row.items()
                          if key != "frame" and val not in ("", None)}
    return out


def _fit_fixed_minority(batch, t, y, bg, res1, f_rich, endmember_cell):
    """把少数相晶胞钉在端元值（该相占多数时的晶胞），只修多数相 + 其余参数。

    这是两段拟合的"正解"版本：端元值来自该相占多数的帧，此时晶胞是
    可辨识的；少数相帧只借用这个结构值，不再让弱峰把它拉向多数相。
    """
    rich_cell = _cell(res1)
    poor_cell = _cell(res1, "LMFP_poor")
    init = _fit_initial(batch.instrument, rich_cell, poor_cell)
    try:
        init["zero"] = float(res1.params["zero"])
    except (KeyError, TypeError):
        pass
    rich_majority = (not f_rich < 0.5)
    if rich_majority:
        init.update({
            "LMFP_poor:a": float(endmember_cell[0]),
            "LMFP_poor:b": float(endmember_cell[1]),
            "LMFP_poor:c": float(endmember_cell[2]),
        })
        extra = _extra_phases(batch.poor, batch.al, endmember_cell,
                              refine_poor_cell=False)
        free = ("a", "b", "c")
        stages = [free]
    else:
        init.update({
            "a": float(endmember_cell[0]),
            "b": float(endmember_cell[1]),
            "c": float(endmember_cell[2]),
        })
        extra = _extra_phases(batch.poor, batch.al, poor_cell,
                              refine_poor_cell=True)
        free = ()
        stages = [("LMFP_poor:a", "LMFP_poor:b", "LMFP_poor:c")]
    return fit_pattern(
        batch.rich, t, y, wavelength=batch.lam, b_iso=0.0, background=bg,
        background_knots=0.25, background_smooth=BG_SMOOTH,
        weights="poisson", cell_refine=True, intensity_poly=2,
        extra_phases=extra, phase_label="LMFP_rich", free=free,
        initial=init, stages=stages, bounds=CELL_BOUNDS,
        max_nfev=FRAME_NFEV)


def _run(args):
    frames = [int(x) for x in args.frames.split(",") if x.strip()]
    # 帧号 = 排序后的序号（1..N），不是文件名尾号（尾号从 29 起、步长 2）
    ordered = frames_in_dir(args.data)
    files = {i + 1: p for i, p in enumerate(ordered)}
    missing = [f for f in frames if f not in files]
    if missing:
        raise SystemExit(f"这些帧不存在：{missing}")
    batch = prepare_setup(
        args.config, mask_path=args.mask, lab6_tif=args.lab6_tif,
        lab6_cif=args.lab6_cif, frame_nfev=args.max_nfev)
    seeds = _load_seeds(args.seed_csv)
    gsas = _load_gsas(args.gsas_csv)
    rows, data, results = [], {}, {}
    for frame in frames:
        path = files[frame]
        if frame - 1 in seeds:
            batch.seed = seeds[frame - 1]
        n_before = len(batch.fitted)
        row1 = batch.fit_frame(path)
        if len(batch.fitted) <= n_before:
            print(f"frame {frame}: 第一段失败（{row1['status']}），跳过")
            continue
        _i, _p, res1, f_rich, status = batch.fitted[-1]
        t, y, bg, _timers = load_frame(
            path, batch.geom, batch.mask, npt=batch.npt,
            tth_min=batch.tth_min, tth_max=batch.tth_max)
        data[frame] = (t, y, bg)
        results[frame] = res1
        res2 = _fit_second_stage(
            t, y, bg, batch.rich, batch.poor, batch.al, batch.lam,
            batch.instrument, res1, f_rich, max_nfev=args.max_nfev)
        cells1 = _cells(res1)
        cells2 = _cells(res2)
        minority = "poor" if f_rich >= 0.5 else "rich"
        item = {
            "frame": frame,
            "f_rich": f_rich,
            "status_stage1": status,
            "minority_phase": minority,
            "rwp_stage1": float(res1.rwp),
            "rwp_stage2": float(res2.rwp),
            "success_stage2": bool(res2.success),
        }
        for key in cells1:
            item[f"{key}_stage1"] = float(cells1[key])
            item[f"{key}_stage2"] = float(cells2[key])
        for axis in "abc":
            key = f"{minority}_{axis}"
            if frame in gsas and key in gsas[frame]:
                item[f"{key}_gsas"] = gsas[frame][key]
        rows.append(item)
        print(f"frame {frame:3d}  f_rich {f_rich:.3f}  minority {minority:4s}  "
              f"Rwp {res1.rwp:.2f}->{res2.rwp:.2f}  "
              f"{minority}_a {cells1[f'{minority}_a']:.4f}"
              f"->{cells2[f'{minority}_a']:.4f}", flush=True)
    # ── 端元约束：少数相晶胞钉在该相占多数时的值 ─────────────────
    if rows:
        poor_end = min(rows, key=lambda r: r["f_rich"])
        rich_end = max(rows, key=lambda r: r["f_rich"])
        end = {
            "poor": tuple(poor_end[f"poor_{axis}_stage1"] for axis in "abc"),
            "rich": tuple(rich_end[f"rich_{axis}_stage1"] for axis in "abc"),
        }
        print(f"endmembers (from majority frames): rich={end['rich']} "
              f"poor={end['poor']}")
        for item in rows:
            frame = item["frame"]
            if frame not in results:
                continue
            t, y, bg = data[frame]
            res_e = _fit_fixed_minority(
                batch, t, y, bg, results[frame], item["f_rich"],
                end[item["minority_phase"]])
            item["rwp_endmember"] = float(res_e.rwp)
            for i, axis in enumerate("abc"):
                item[f"minority_{axis}_endmember"] = float(
                    end[item["minority_phase"]][i])
    return rows


def _write_csv(outdir, rows):
    path = outdir / "minority_stage.csv"
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_summary(outdir, rows):
    lines = ["=== minority two-stage fit (development check) ===",
             "stage 1 = production double-start; stage 2 = majority cell "
             "fixed, minority cell free (starts from the stage-1 solution).",
             "endmember = minority cell pinned to the value measured when "
             "that phase was the majority.",
             "GSAS-II (if present) is a development reference only; it is "
             "never shown in the GUI.", ""]
    with_gsas = [r for r in rows
                 if all(f"{r['minority_phase']}_{axis}_gsas" in r
                        for axis in "abc")]
    for phase in ("poor", "rich"):
        sub = [r for r in rows if r["minority_phase"] == phase]
        if not sub:
            continue
        lines.append(f"minority = {phase} ({len(sub)} frames)")
        for axis in "abc":
            key = f"{phase}_{axis}"
            d1 = [abs(r[f"{key}_stage1"] - r[f"{key}_gsas"])
                  for r in with_gsas if r["minority_phase"] == phase]
            d2 = [abs(r[f"{key}_stage2"] - r[f"{key}_gsas"])
                  for r in with_gsas if r["minority_phase"] == phase]
            de = [abs(r[f"minority_{axis}_endmember"] - r[f"{key}_gsas"])
                  for r in sub if f"{key}_gsas" in r]
            lines.append(
                f"  {axis}: mean|ΔGSAS| stage1 "
                f"{np.mean(d1) if d1 else float('nan'):.4f} → stage2 "
                f"{np.mean(d2) if d2 else float('nan'):.4f} → endmember "
                f"{np.mean(de) if de else float('nan'):.4f} Å "
                f"(n={len(d1)})")
        rw1 = [r["rwp_stage1"] for r in sub]
        rw2 = [r["rwp_stage2"] for r in sub]
        rwe = [r["rwp_endmember"] for r in sub if "rwp_endmember" in r]
        lines.append(
            f"  Rwp: stage1 {np.mean(rw1):.2f}% → stage2 "
            f"{np.mean(rw2):.2f}% → endmember "
            f"{np.mean(rwe) if rwe else float('nan'):.2f}%")
        lines.append("")
    lines.append(
        "reading: stage 2 starting from the joint solution does not move "
        "the minority cell (the joint fit is already at its conditional "
        "optimum). The endmember route changes the minority cell by "
        "construction; its Rwp penalty tells whether the weak minority "
        "peaks accept the majority-regime structure. A near-zero penalty "
        "means the minority cell was never identifiable from that frame.")
    path = outdir / "minority_stage_summary.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_figure(outdir, rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x = [r["frame"] for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.8), sharex=True)
    for ax, phase in zip(axes, ("rich", "poor")):
        a1 = [r[f"{phase}_a_stage1"] for r in rows]
        a2 = [r[f"{phase}_a_stage2"] for r in rows]
        ax.plot(x, a1, "o:", color="0.45", ms=5, mfc="none",
                label="stage 1 (production)")
        ax.plot(x, a2, "s-", color="#0072B2", ms=3.5,
                label="stage 2 (minority free)")
        gsas = np.asarray([r.get(f"{phase}_a_gsas", np.nan)
                           for r in rows], dtype=float)
        if np.any(np.isfinite(gsas)):
            ax.plot(x, gsas, "x", color="#D55E00", ms=7,
                    label="GSAS-II (dev ref, minority frames only)")
        ax.set_title(f"{phase} a")
        ax.set_xlabel("frame")
        ax.set_ylabel("a (Å)")
        ax.grid(alpha=0.25, linestyle=":")
        ax.legend(fontsize=7)
    fig.tight_layout()
    path = outdir / "minority_stage.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=str(Path.home() / "Data/XRD/lmfpdata"))
    ap.add_argument("--frames", default="1,15,25,27,35,45,55,65,75,81")
    ap.add_argument("--mask",
                    default="outputs/masks/lmfp1_lab6_mask.npy")
    ap.add_argument("--config", default="lmfp1_lab6")
    ap.add_argument("--lab6-tif", default=str(ROOT / "data/lab6-00024.tif"))
    ap.add_argument("--lab6-cif",
                    default=str(ROOT / "data/structures/LaB6_NIST660c.cif"))
    ap.add_argument("--gsas-csv", default="")
    ap.add_argument("--seed-csv", default="outputs/insitu/insitu_fit.csv",
                    help="生产批次 CSV；用上一帧的晶胞作本帧起点")
    ap.add_argument("--outdir", default=str(ROOT / "outputs/minority_stage"))
    ap.add_argument("--max-nfev", type=int, default=FRAME_NFEV)
    args = ap.parse_args(argv)
    outdir = Path(args.outdir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    rows = _run(args)
    if not rows:
        raise SystemExit("没有可用帧")
    print(f"csv:     {_write_csv(outdir, rows)}")
    print(f"summary: {_write_summary(outdir, rows)}")
    print(f"figure:  {_write_figure(outdir, rows)}")


if __name__ == "__main__":
    main()
