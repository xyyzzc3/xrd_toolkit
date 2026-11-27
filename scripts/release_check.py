#!/usr/bin/env python3
"""One-command release gate: environment + tests + in-situ reference.

The in-situ reference check needs the real 81-frame batch, which CI does
not have.  This script keeps the manual release step in one place:

    python scripts/release_check.py \
        --candidate outputs/insitu_v022_81/insitu_fit.csv

By default it runs the full unit-test suite via `scripts/run_tests.py`
(the same watchdog runner used day to day).  Use `--fast` for the quick
tier, or `--skip-tests` when CI already ran them.

Exit code 0 = all requested checks passed.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(label, command, *, cwd=ROOT):
    print(f"\n=== {label} ===", flush=True)
    print("$ " + " ".join(str(part) for part in command), flush=True)
    proc = subprocess.run(command, cwd=str(cwd))
    ok = proc.returncode == 0
    print(f"[{'OK' if ok else 'FAIL'}] {label} (exit {proc.returncode})",
          flush=True)
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the manual release gate in one command.")
    parser.add_argument("--candidate", default="",
                        help="in-situ batch CSV for the reference check")
    parser.add_argument("--reference", default="",
                        help="reference CSV (default: the v0.2.2 baseline)")
    parser.add_argument("--allow-frames", default=None,
                        help="override the reference manifest's allowed frames")
    parser.add_argument("--skip-env", action="store_true",
                        help="skip scripts/check_env.py")
    parser.add_argument("--skip-tests", action="store_true",
                        help="skip the unit-test suite")
    parser.add_argument("--fast", action="store_true",
                        help="run the fast test tier (skip GUI integration)")
    args = parser.parse_args(argv)

    python = sys.executable
    checks = []

    if not args.skip_env:
        checks.append(("environment", [
            python, str(ROOT / "scripts" / "check_env.py")]))

    if not args.skip_tests:
        suite = [python, str(ROOT / "scripts" / "run_tests.py")]
        if args.fast:
            suite.append("--fast")
        checks.append(("unit tests", suite))

    if args.candidate:
        gate = [python, str(ROOT / "scripts" / "check_insitu_reference.py"),
                "--candidate", args.candidate]
        if args.reference:
            gate += ["--reference", args.reference]
        if args.allow_frames is not None:
            gate += ["--allow-frames", args.allow_frames]
        checks.append(("in-situ reference", gate))
    elif not args.skip_tests:
        print("\nnote: no --candidate given; in-situ reference check skipped",
              flush=True)

    failed = []
    for label, command in checks:
        if not _run(label, command):
            failed.append(label)

    print("\n=== release check summary ===")
    for label, _command in checks:
        print(f"[{'FAIL' if label in failed else 'OK  '}] {label}")
    if failed:
        print("\nrelease blocked: " + ", ".join(failed))
        return 1
    print("\nrelease checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
