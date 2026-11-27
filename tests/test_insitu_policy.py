"""Shared dual-start policy tests (2026-11-27)."""
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.simulation.insitu_policy import (  # noqa: E402
    DEFAULT_AUTO_RWP_GUARD, needs_second_start)


class TestNeedsSecondStart(unittest.TestCase):
    def test_cif_seed_never_runs_second(self):
        self.assertFalse(needs_second_start(
            False, 99.0, 0.5, True, "auto", 2.45))

    def test_off_and_always(self):
        self.assertFalse(needs_second_start(
            False, 99.0, 0.5, False, "off", 2.45))
        self.assertTrue(needs_second_start(
            True, 1.0, 0.5, False, "always", 2.45))

    def test_non_strict_seed_runs_second(self):
        self.assertTrue(needs_second_start(
            False, 1.0, 0.5, False, "auto", 2.45))

    def test_low_rwp_converged_seed_skips(self):
        self.assertFalse(needs_second_start(
            True, 2.10, 0.5, False, "auto", 2.45))

    def test_high_rwp_converged_seed_runs(self):
        self.assertTrue(needs_second_start(
            True, 2.45, 0.5, False, "auto", 2.45))
        self.assertTrue(needs_second_start(
            True, 2.473, 0.5, False, "auto", 2.45))

    def test_none_or_zero_guard_only_uses_success(self):
        for guard in (None, 0, 0.0, -1):
            self.assertFalse(needs_second_start(
                True, 99.0, 0.5, False, "auto", guard))
            self.assertTrue(needs_second_start(
                False, 1.0, 0.5, False, "auto", guard))

    def test_missing_fraction_is_conservative(self):
        for fraction in (None, "", "x", float("nan")):
            self.assertTrue(needs_second_start(
                True, 1.0, fraction, False, "auto", 2.45))

    def test_missing_rwp_is_conservative(self):
        # None 转不成数字 → 跑第二起点；NaN 说明优化器给了个坏值，
        # 旧规则是“不因它触发”（与 2026-11-24 行为一致）。
        self.assertTrue(needs_second_start(
            True, None, 0.5, False, "auto", 2.45))
        self.assertFalse(needs_second_start(
            True, float("nan"), 0.5, False, "auto", 2.45))

    def test_unknown_mode_is_auto(self):
        self.assertTrue(needs_second_start(
            True, 2.5, 0.5, False, "bogus", 2.45))
        self.assertFalse(needs_second_start(
            True, 2.1, 0.5, False, "bogus", 2.45))

    def test_default_guard_constant(self):
        self.assertAlmostEqual(DEFAULT_AUTO_RWP_GUARD, 2.45)
        self.assertTrue(math.isfinite(DEFAULT_AUTO_RWP_GUARD))


if __name__ == "__main__":
    unittest.main()
