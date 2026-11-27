"""Tests for the dual-start guard sweep summariser (2026-11-27)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sweep_dual_start_guard as sweep  # noqa: E402


def _row(frame, **kwargs):
    row = {"frame": str(frame), "rwp": "2.20", "f_rich": "0.50",
           "rich_a": "10.30", "rich_c": "4.70", "poor_a": "9.90",
           "poor_c": "4.78", "dual_start": "0", "start_used": "seed",
           "t_total_s": "2.0"}
    row.update({key: str(value) for key, value in kwargs.items()})
    return row


class TestSweepSummary(unittest.TestCase):
    def test_summary_counts_cif_and_dual(self):
        rows = {
            1: _row(1, dual_start=1, start_used="cif"),
            2: _row(2),
            3: _row(3, dual_start=1, start_used="seed"),
        }
        stats = sweep._summary(rows, {})
        self.assertEqual(stats["frames"], 3)
        self.assertEqual(stats["dual_frames"], 2)
        self.assertEqual(stats["cif_frames"], 1)
        self.assertAlmostEqual(stats["total_s"], 6.0)

    def test_diff_frames_uses_tolerances(self):
        base = {1: _row(1), 2: _row(2), 3: _row(3)}
        cand = {
            1: _row(1, rwp=2.24),       # within 0.05 pp
            2: _row(2, rwp=2.30),       # outside
            3: _row(3, poor_c=4.90),    # outside cell tolerance
        }
        self.assertEqual(sweep._diff_frames(base, cand), [2, 3])

    def test_missing_finite_value_counts_as_change(self):
        base = {1: _row(1)}
        cand = {1: _row(1, rwp="nan")}
        self.assertEqual(sweep._diff_frames(base, cand), [1])


if __name__ == "__main__":
    unittest.main()
