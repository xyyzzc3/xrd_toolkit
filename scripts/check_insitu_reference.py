#!/usr/bin/env python3
"""Compare an in-situ batch CSV with the tracked v0.2.1 reference.

The reference is `tests/data/insitu_reference_v021_81.csv`: the 81-frame
batch produced by v0.2.1 (81/81 ok, 189 s).  This script is the release
gate for later versions.  It reports frames whose Rwp, phase fraction or
cell parameter moved beyond tolerance, plus status/usable changes and the
optional dual-start provenance columns.

Usage:
    python scripts/check_insitu_reference.py \
        --candidate outputs/insitu_v021_81/insitu_fit.csv

Expected changes can be whitelisted, for example the three transition
frames whose branch selection changed with the 2026-11-24 trigger fix:
    --allow-frames 28,29,30

Exit code 0 = within tolerance; 1 = unexpected differences.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE = ROOT / "tests" / "data" / "insitu_reference_v021_81.csv"

NUMERIC_FIELDS = ("rwp", "f_rich", "rich_a", "poor_a")


def _read(path):
    rows = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                frame = int(float(row["frame"]))
            except (KeyError, TypeError, ValueError):
                continue
            rows[frame] = row
    return rows


def _float(row, key):
    try:
        value = float(row.get(key, ""))
    except (TypeError, ValueError):
        return float("nan")
    return value


def _text(row, key):
    return str(row.get(key, "")).strip().lower()


def compare_rows(reference, candidate, *, f_tol=0.01, a_tol=0.05,
                 rwp_tol=0.05, allow_frames=()):
    """Compare two {frame: row} maps.

    Returns (differences, missing, extra).  `differences` is a list of
    (frame, field, reference_value, candidate_value, delta) tuples.
    """
    allow = {int(frame) for frame in allow_frames}
    differences = []
    missing = sorted(set(reference) - set(candidate))
    extra = sorted(set(candidate) - set(reference))
    for frame in sorted(set(reference) & set(candidate)):
        if frame in allow:
            continue
        ref = reference[frame]
        cand = candidate[frame]
        for key in NUMERIC_FIELDS:
            if key == "rwp":
                tol = rwp_tol
            elif key == "f_rich":
                tol = f_tol
            else:
                tol = a_tol
            a = _float(ref, key)
            b = _float(cand, key)
            if not (math.isfinite(a) and math.isfinite(b)):
                if math.isfinite(a) != math.isfinite(b):
                    differences.append(
                        (frame, key, a, b, float("nan")))
                continue
            delta = b - a
            if abs(delta) > tol:
                differences.append((frame, key, a, b, delta))
        if _text(ref, "status") != _text(cand, "status"):
            differences.append(
                (frame, "status", _text(ref, "status"),
                 _text(cand, "status"), float("nan")))
        if _text(ref, "usable") != _text(cand, "usable"):
            differences.append(
                (frame, "usable", _text(ref, "usable"),
                 _text(cand, "usable"), float("nan")))
        # Provenance is optional: only compare when both files have it.
        if ref.get("start_used") and cand.get("start_used"):
            if _text(ref, "start_used") != _text(cand, "start_used"):
                differences.append(
                    (frame, "start_used", _text(ref, "start_used"),
                     _text(cand, "start_used"), float("nan")))
    return differences, missing, extra


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare an in-situ batch with the v0.2.1 reference.")
    parser.add_argument("--reference", default=str(DEFAULT_REFERENCE),
                        help="reference CSV (default: the tracked v0.2.1 batch)")
    parser.add_argument("--candidate", required=True,
                        help="new batch CSV to check")
    parser.add_argument("--f-tol", type=float, default=0.01,
                        help="phase-fraction tolerance (default 0.01)")
    parser.add_argument("--a-tol", type=float, default=0.05,
                        help="cell-parameter tolerance in Angstrom (default 0.05)")
    parser.add_argument("--rwp-tol", type=float, default=0.05,
                        help="Rwp tolerance in percentage points (default 0.05)")
    parser.add_argument("--allow-frames", default="",
                        help="comma-separated frames whose differences are expected")
    args = parser.parse_args(argv)

    ref_path = Path(args.reference).expanduser()
    cand_path = Path(args.candidate).expanduser()
    if not ref_path.is_file():
        parser.error(f"reference not found: {ref_path}")
    if not cand_path.is_file():
        parser.error(f"candidate not found: {cand_path}")

    reference = _read(ref_path)
    candidate = _read(cand_path)
    allow = [int(x) for x in args.allow_frames.split(",") if x.strip()]
    differences, missing, extra = compare_rows(
        reference, candidate,
        f_tol=args.f_tol, a_tol=args.a_tol, rwp_tol=args.rwp_tol,
        allow_frames=allow)

    print(f"reference: {ref_path} ({len(reference)} frames)")
    print(f"candidate: {cand_path} ({len(candidate)} frames)")
    print(f"tolerances: f_rich {args.f_tol}, cell {args.a_tol} A, "
          f"Rwp {args.rwp_tol} pp")
    if allow:
        print("allowed frames: " + ", ".join(str(f) for f in sorted(allow)))
    if missing:
        print("missing frames: " + ", ".join(str(f) for f in missing))
    if extra:
        print("extra frames: " + ", ".join(str(f) for f in extra))
    if differences:
        print(f"\n{'frame':>5}  {'field':<10}  {'reference':>10}  "
              f"{'candidate':>10}  {'delta':>9}")
        for frame, field, old, new, delta in differences:
            if math.isfinite(delta):
                print(f"{frame:5d}  {field:<10}  {old:10.5f}  "
                      f"{new:10.5f}  {delta:+9.5f}")
            else:
                print(f"{frame:5d}  {field:<10}  {str(old):>10}  "
                      f"{str(new):>10}  {'':>9}")
        print(f"\n{len(differences)} difference(s) outside tolerance")
        return 1
    if missing or extra:
        return 1
    print("\nwithin tolerance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
