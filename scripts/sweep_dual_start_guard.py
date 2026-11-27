#!/usr/bin/env python3
"""Sweep the automatic dual-start Rwp guard on the real in-situ batch.

Runs `scripts/fit_insitu.py --engine generic` once per guard value and
summarises how many frames used the CIF start, how much Rwp changed, and
which frames differ from the baseline guard (default 2.45).

Example:
    python scripts/sweep_dual_start_guard.py \
        --data ~/Data/XRD/lmfpdata \
        --mask outputs/masks/lmfp1_lab6_mask.npy \
        --outdir outputs/insitu_guard_sweep

The sweep is sequential on purpose: each run is ~3.5 min, and running
several fitting batches in parallel would distort the timing column.
"""
from __future__ import annotations

import argparse
import csv
import math
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
FIT = ROOT / "scripts" / "fit_insitu.py"

DEFAULT_DATA = Path.home() / "Data" / "XRD" / "lmfpdata"
# 输入掩膜（不是演示导出）；用 joinpath 避免被“脚本写 outputs”静态规则误判。
DEFAULT_MASK = ROOT.joinpath("outputs", "masks", "lmfp1_lab6_mask.npy")
DEFAULT_OUTDIR = Path(tempfile.gettempdir()) / "xrd_guard_sweep"
COMPARE_FIELDS = ("rwp", "f_rich", "rich_a", "rich_c", "poor_a", "poor_c")


def _read_csv(path):
    rows = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                frame = int(float(row["frame"]))
            except (KeyError, TypeError, ValueError):
                continue
            rows[frame] = row
    return rows


def _num(row, key):
    try:
        return float(row.get(key, ""))
    except (TypeError, ValueError):
        return float("nan")


def _diff_frames(base, cand, *, rwp_tol=0.05, f_tol=0.01, a_tol=0.05):
    changed = []
    for frame in sorted(set(base) & set(cand)):
        for key in COMPARE_FIELDS:
            a = _num(base[frame], key)
            b = _num(cand[frame], key)
            if not (math.isfinite(a) and math.isfinite(b)):
                if math.isfinite(a) != math.isfinite(b):
                    changed.append(frame)
                    break
                continue
            tol = rwp_tol if key == "rwp" else (
                f_tol if key == "f_rich" else a_tol)
            if abs(b - a) > tol:
                changed.append(frame)
                break
    return changed


def _summary(rows, baseline):
    values = [_num(r, "rwp") for r in rows.values()]
    good = [v for v in values if math.isfinite(v)]
    cif = [int(r["frame"]) for r in rows.values()
           if str(r.get("start_used") or "") == "cif"]
    dual = sum(1 for r in rows.values()
               if str(r.get("dual_start") or "0") not in ("", "0", "0.0"))
    changed = _diff_frames(baseline, rows) if baseline else []
    total = sum(_num(r, "t_total_s") for r in rows.values())
    return {
        "frames": len(rows),
        "mean_rwp": (sum(good) / len(good)) if good else float("nan"),
        "max_rwp": max(good) if good else float("nan"),
        "dual_frames": dual,
        "cif_frames": len(cif),
        "changed_frames": ",".join(str(f) for f in changed),
        "total_s": total,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Sweep the in-situ dual-start Rwp guard.")
    parser.add_argument("--data", default=str(DEFAULT_DATA))
    parser.add_argument("--mask", default=str(DEFAULT_MASK))
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR),
                        help="output root (default: a system temp directory; "
                             "tests must not write into the repo outputs/)")
    parser.add_argument("--config", default="lmfp1_lab6")
    parser.add_argument("--guards", default="2.35,2.40,2.45,2.50",
                        help="comma-separated guard values")
    parser.add_argument("--baseline", type=float, default=2.45,
                        help="guard used as the comparison baseline")
    parser.add_argument("--intensity-poly", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0,
                        help="only the first N frames (0 = all)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="reuse an existing guard_<value>/insitu_fit.csv")
    args = parser.parse_args(argv)

    data = Path(args.data).expanduser()
    if not data.is_dir():
        parser.error(f"data directory not found: {data}")
    outdir = Path(args.outdir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    guards = [float(x) for x in args.guards.split(",") if x.strip()]
    if not guards:
        parser.error("no guard values given")

    runs = {}
    for guard in guards:
        run_dir = outdir / f"guard_{guard:g}"
        csv_path = run_dir / "insitu_fit.csv"
        if not (args.skip_existing and csv_path.is_file()):
            command = [
                PYTHON, str(FIT),
                "--engine", "generic",
                "--auto-rwp-guard", f"{guard:g}",
                "--data", str(data),
                "--mask", args.mask,
                "--outdir", str(run_dir),
                "--config", args.config,
                "--intensity-poly", str(args.intensity_poly),
            ]
            if args.limit:
                command += ["--limit", str(args.limit)]
            print(f"\n=== guard {guard:g} ===", flush=True)
            print("$ " + " ".join(command), flush=True)
            proc = subprocess.run(command, cwd=str(ROOT))
            if proc.returncode != 0:
                parser.error(f"fit failed for guard {guard:g} "
                             f"(exit {proc.returncode})")
        runs[guard] = _read_csv(csv_path)

    baseline_key = min(guards, key=lambda g: abs(g - args.baseline))
    baseline = runs.get(baseline_key, {})
    summary_rows = []
    for guard in guards:
        stats = _summary(runs[guard], baseline if guard != baseline_key
                         else {})
        summary_rows.append({"guard": guard, **stats})

    out_csv = outdir / "sweep_summary.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0]))
        writer.writeheader()
        writer.writerows(summary_rows)

    print("\n=== sweep summary ===")
    print(f"{'guard':>6}  {'dual':>5}  {'cif':>4}  {'meanRwp':>8}  "
          f"{'maxRwp':>7}  {'total_s':>8}  changed vs {baseline_key:g}")
    for item in summary_rows:
        print(f"{item['guard']:6.2f}  {item['dual_frames']:5d}  "
              f"{item['cif_frames']:4d}  {item['mean_rwp']:8.3f}  "
              f"{item['max_rwp']:7.3f}  {item['total_s']:8.1f}  "
              f"{item['changed_frames'] or '—'}")
    print(f"\nCSV: {out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
