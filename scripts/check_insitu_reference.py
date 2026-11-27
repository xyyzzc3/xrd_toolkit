#!/usr/bin/env python3
"""Compare an in-situ batch CSV with a tracked reference batch.

The current reference is `tests/data/insitu_reference_v022_81.csv`: the
81-frame generic-engine batch produced on 2026-11-27 with the targeted
dual-start trigger and provenance columns.  The v0.2.1 reference
(`insitu_reference_v021_81.csv`) is kept as the historical baseline.

A reference can carry a sidecar JSON (same stem, `.json`) describing the
version, generation command, tolerances and frames whose differences are
expected.  That saves typing `--allow-frames 28,29,30` every time and
keeps the gate self-documenting.  CLI flags override the manifest.

Usage:
    python scripts/check_insitu_reference.py \
        --candidate outputs/insitu_v022_81/insitu_fit.csv

    # compare against the old baseline, explicitly allowing known changes
    python scripts/check_insitu_reference.py \
        --reference tests/data/insitu_reference_v021_81.csv \
        --candidate outputs/insitu_v022_81/insitu_fit.csv \
        --allow-frames 28,29,30

Exit code 0 = within tolerance; 1 = unexpected differences.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE = ROOT / "tests" / "data" / "insitu_reference_v022_81.csv"

# 2026-11-27: compare every cell axis and both fraction/weight conventions,
# not just a/poor_a.  LMFP's phase change is most visible in c.
NUMERIC_FIELDS = ("rwp", "f_rich", "rich_a", "rich_c", "poor_a", "poor_c",
                  "al_a", "wt_rich", "wt_poor")


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


def _manifest_path(reference_path):
    return Path(reference_path).with_suffix(".json")


def load_manifest(reference_path):
    """Read the optional sidecar JSON; missing/broken = empty dict."""
    path = _manifest_path(reference_path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def compare_rows(reference, candidate, *, f_tol=0.01, a_tol=0.05,
                 rwp_tol=0.05, wt_tol=0.10, allow_frames=()):
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
            elif key.startswith("wt_"):
                tol = wt_tol
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
        description="Compare an in-situ batch with a tracked reference batch.")
    parser.add_argument("--reference", default=str(DEFAULT_REFERENCE),
                        help="reference CSV (default: the tracked v0.2.2 batch)")
    parser.add_argument("--candidate", required=True,
                        help="new batch CSV to check")
    parser.add_argument("--f-tol", type=float, default=None,
                        help="phase-fraction tolerance (default 0.01)")
    parser.add_argument("--a-tol", type=float, default=None,
                        help="cell-parameter tolerance in Angstrom (default 0.05)")
    parser.add_argument("--rwp-tol", type=float, default=None,
                        help="Rwp tolerance in percentage points (default 0.05)")
    parser.add_argument("--wt-tol", type=float, default=None,
                        help="weight-fraction tolerance in wt%% (default 0.10)")
    parser.add_argument("--allow-frames", default=None,
                        help="comma-separated frames whose differences are "
                             "expected; overrides the sidecar manifest")
    parser.add_argument("--no-manifest", action="store_true",
                        help="ignore the sidecar <reference>.json")
    args = parser.parse_args(argv)

    ref_path = Path(args.reference).expanduser()
    cand_path = Path(args.candidate).expanduser()
    if not ref_path.is_file():
        parser.error(f"reference not found: {ref_path}")
    if not cand_path.is_file():
        parser.error(f"candidate not found: {cand_path}")

    manifest = {} if args.no_manifest else load_manifest(ref_path)
    tolerances = manifest.get("tolerances") or {}
    f_tol = (args.f_tol if args.f_tol is not None
             else float(tolerances.get("f_rich", 0.01)))
    a_tol = (args.a_tol if args.a_tol is not None
             else float(tolerances.get("cell", 0.05)))
    rwp_tol = (args.rwp_tol if args.rwp_tol is not None
               else float(tolerances.get("rwp", 0.05)))
    wt_tol = (args.wt_tol if args.wt_tol is not None
              else float(tolerances.get("wt", 0.10)))
    if args.allow_frames is not None:
        allow = [int(x) for x in args.allow_frames.split(",") if x.strip()]
        allow_source = "command line"
    else:
        allow = [int(x) for x in (manifest.get("allow_frames") or [])]
        allow_source = "manifest" if manifest else "none"

    reference = _read(ref_path)
    candidate = _read(cand_path)
    differences, missing, extra = compare_rows(
        reference, candidate,
        f_tol=f_tol, a_tol=a_tol, rwp_tol=rwp_tol, wt_tol=wt_tol,
        allow_frames=allow)

    version = str(manifest.get("version") or "").strip()
    print(f"reference: {ref_path} ({len(reference)} frames"
          + (f", v{version}" if version else "") + ")")
    print(f"candidate: {cand_path} ({len(candidate)} frames)")
    print(f"tolerances: f_rich {f_tol}, cell {a_tol} A, "
          f"Rwp {rwp_tol} pp, wt {wt_tol} wt%")
    if allow:
        print("allowed frames (" + allow_source + "): "
              + ", ".join(str(f) for f in sorted(set(allow))))
    elif manifest:
        print("allowed frames: none (manifest)")
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
