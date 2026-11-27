#!/usr/bin/env python3
"""Accept an in-situ batch CSV as the new tracked reference.

Copies the candidate CSV next to its sidecar manifest so
`check_insitu_reference.py` can pick up the version, tolerances and
allowed frames without command-line flags.

Usage:
    python scripts/accept_insitu_reference.py \
        --candidate outputs/insitu_v022_81/insitu_fit.csv \
        --reference tests/data/insitu_reference_v022_81.csv \
        --version 0.2.2 --engine generic

The 2026-11-24 v0.2.1 reference is kept under its old name; accepting a
new one never overwrites the historical file.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from pathlib import Path


def _frame_count(path):
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if "frame" not in (reader.fieldnames or []):
            raise ValueError(f"CSV has no 'frame' column: {path}")
        return sum(1 for _ in reader)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Accept a batch CSV as the tracked in-situ reference.")
    parser.add_argument("--candidate", required=True,
                        help="batch CSV produced by the current code")
    parser.add_argument("--reference", required=True,
                        help="destination CSV (sidecar JSON is written too)")
    parser.add_argument("--version", required=True,
                        help="version label for the manifest, e.g. 0.2.2")
    parser.add_argument("--engine", choices=("legacy", "generic"),
                        default="generic",
                        help="engine that produced the batch")
    parser.add_argument("--command", default="",
                        help="command line that produced the batch")
    parser.add_argument("--allow-frames", default="",
                        help="frames with expected differences (empty = none)")
    parser.add_argument("--f-tol", type=float, default=0.01)
    parser.add_argument("--a-tol", type=float, default=0.05)
    parser.add_argument("--rwp-tol", type=float, default=0.05)
    parser.add_argument("--wt-tol", type=float, default=0.10)
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing reference CSV")
    args = parser.parse_args(argv)

    candidate = Path(args.candidate).expanduser()
    reference = Path(args.reference).expanduser()
    if not candidate.is_file():
        parser.error(f"candidate not found: {candidate}")
    if reference.exists() and not args.force:
        parser.error(f"reference already exists (use --force): {reference}")
    try:
        frames = _frame_count(candidate)
    except (OSError, ValueError) as err:
        parser.error(str(err))

    allow = [int(x) for x in args.allow_frames.split(",") if x.strip()]
    reference.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(candidate, reference)
    manifest = {
        "version": str(args.version),
        "generated": time.strftime("%Y-%m-%d"),
        "engine": args.engine,
        "command": args.command,
        "frames": frames,
        "tolerances": {
            "f_rich": float(args.f_tol),
            "cell": float(args.a_tol),
            "rwp": float(args.rwp_tol),
            "wt": float(args.wt_tol),
        },
        "allow_frames": allow,
    }
    sidecar = reference.with_suffix(".json")
    sidecar.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)
                       + "\n", encoding="utf-8")
    print(f"reference: {reference} ({frames} frames)")
    print(f"manifest:  {sidecar}")
    if allow:
        print("allowed frames: " + ", ".join(str(f) for f in allow))
    return 0


if __name__ == "__main__":
    sys.exit(main())
