"""Tests for the in-situ reference checker (2026-11-24)."""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_insitu_reference as ref  # noqa: E402


def _row(frame, *, rwp=2.20, f_rich=0.50, rich_a=10.30, poor_a=9.90,
         rich_c=4.70, poor_c=4.78, al_a=4.061, wt_rich=50.0,
         wt_poor=50.0, status="ok", usable="True", start_used=""):
    return {
        "frame": str(frame),
        "rwp": str(rwp),
        "f_rich": str(f_rich),
        "rich_a": str(rich_a),
        "rich_c": str(rich_c),
        "poor_a": str(poor_a),
        "poor_c": str(poor_c),
        "al_a": str(al_a),
        "wt_rich": str(wt_rich),
        "wt_poor": str(wt_poor),
        "status": status,
        "usable": usable,
        "start_used": start_used,
    }


class TestCompareRows(unittest.TestCase):
    def test_identical_passes(self):
        rows = {1: _row(1), 2: _row(2)}
        diffs, missing, extra = ref.compare_rows(rows, dict(rows))
        self.assertEqual(diffs, [])
        self.assertEqual(missing, [])
        self.assertEqual(extra, [])

    def test_fraction_tolerance_flags(self):
        a = {1: _row(1, f_rich=0.50)}
        b = {1: _row(1, f_rich=0.53)}
        diffs, _missing, _extra = ref.compare_rows(a, b)
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0][0], 1)
        self.assertEqual(diffs[0][1], "f_rich")
        self.assertAlmostEqual(diffs[0][4], 0.03)

    def test_cell_tolerance_flags(self):
        a = {1: _row(1, poor_a=9.90)}
        b = {1: _row(1, poor_a=10.01)}
        diffs, _missing, _extra = ref.compare_rows(a, b)
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0][1], "poor_a")

    def test_c_axis_is_compared(self):
        a = {1: _row(1, poor_c=4.78)}
        b = {1: _row(1, poor_c=4.86)}
        diffs, _missing, _extra = ref.compare_rows(a, b)
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0][1], "poor_c")

    def test_weight_fraction_is_compared(self):
        a = {1: _row(1, wt_rich=50.0)}
        b = {1: _row(1, wt_rich=51.0)}
        diffs, _missing, _extra = ref.compare_rows(a, b)
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0][1], "wt_rich")

    def test_rwp_tolerance_flags(self):
        a = {1: _row(1, rwp=2.20)}
        b = {1: _row(1, rwp=2.30)}
        diffs, _missing, _extra = ref.compare_rows(a, b)
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0][1], "rwp")

    def test_status_and_missing_extra(self):
        a = {1: _row(1, status="ok"), 2: _row(2)}
        b = {1: _row(1, status="ok_soft"), 3: _row(3)}
        diffs, missing, extra = ref.compare_rows(a, b)
        self.assertEqual(missing, [2])
        self.assertEqual(extra, [3])
        self.assertEqual(diffs[0][1], "status")

    def test_allow_frames_skips_all_fields(self):
        a = {28: _row(28, rwp=2.473, f_rich=0.7048, poor_a=10.105)}
        b = {28: _row(28, rwp=2.416, f_rich=0.7210, poor_a=9.891,
                      status="ok_soft")}
        diffs, _missing, _extra = ref.compare_rows(
            a, b, allow_frames=(28,))
        self.assertEqual(diffs, [])

    def test_provenance_only_when_both_have_it(self):
        a = {1: _row(1, start_used="")}
        b = {1: _row(1, start_used="cif")}
        diffs, _missing, _extra = ref.compare_rows(a, b)
        self.assertEqual(diffs, [])
        a = {1: _row(1, start_used="seed")}
        b = {1: _row(1, start_used="cif")}
        diffs, _missing, _extra = ref.compare_rows(a, b)
        self.assertEqual(diffs[0][1], "start_used")


class TestMain(unittest.TestCase):
    def _write(self, path, rows):
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_main_returns_one_on_difference(self):
        with tempfile.TemporaryDirectory() as td:
            ref_path = Path(td) / "ref.csv"
            cand_path = Path(td) / "cand.csv"
            self._write(ref_path, [_row(1)])
            self._write(cand_path, [_row(1, f_rich=0.60)])
            code = ref.main(["--reference", str(ref_path),
                             "--candidate", str(cand_path)])
        self.assertEqual(code, 1)

    def test_main_returns_zero_with_allow(self):
        with tempfile.TemporaryDirectory() as td:
            ref_path = Path(td) / "ref.csv"
            cand_path = Path(td) / "cand.csv"
            self._write(ref_path, [_row(28)])
            self._write(cand_path, [_row(28, f_rich=0.72)])
            code = ref.main(["--reference", str(ref_path),
                             "--candidate", str(cand_path),
                             "--allow-frames", "28"])
        self.assertEqual(code, 0)

    def test_manifest_supplies_allowed_frames(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            ref_path = Path(td) / "ref.csv"
            cand_path = Path(td) / "cand.csv"
            self._write(ref_path, [_row(28)])
            self._write(cand_path, [_row(28, f_rich=0.72)])
            ref_path.with_suffix(".json").write_text(json.dumps(
                {"version": "0.2.1", "allow_frames": [28]}),
                encoding="utf-8")
            code = ref.main(["--reference", str(ref_path),
                             "--candidate", str(cand_path)])
        self.assertEqual(code, 0)

    def test_cli_allow_frames_overrides_manifest(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            ref_path = Path(td) / "ref.csv"
            cand_path = Path(td) / "cand.csv"
            self._write(ref_path, [_row(28)])
            self._write(cand_path, [_row(28, f_rich=0.72)])
            ref_path.with_suffix(".json").write_text(json.dumps(
                {"allow_frames": [28]}), encoding="utf-8")
            code = ref.main(["--reference", str(ref_path),
                             "--candidate", str(cand_path),
                             "--allow-frames", ""])
        self.assertEqual(code, 1)

    def test_broken_manifest_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            ref_path = Path(td) / "ref.csv"
            self._write(ref_path, [_row(1)])
            ref_path.with_suffix(".json").write_text("{not json",
                                                     encoding="utf-8")
            self.assertEqual(ref.load_manifest(ref_path), {})


if __name__ == "__main__":
    unittest.main()
