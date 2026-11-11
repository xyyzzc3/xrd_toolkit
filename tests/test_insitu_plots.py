"""原位趋势图（2026-11-11 用户第 15 条）。

趋势页要能一眼看出"多相/少相变化与彼此关系"：
  * (a) 相分数堆叠面积 + 50/50 交叉线 + 交叉帧虚线；
  * (b)(c) 两相的同一根晶胞轴放在一起看相对变化（Δa/a₀、Δc/c₀）；
  * (d) Rwp。
不跑拟合：直接造行。
"""
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from matplotlib.figure import Figure  # noqa: E402

from xrd_toolkit.simulation.insitu_plots import draw_trends  # noqa: E402


def _row(i, f_rich, status="ok"):
    return {
        "frame": i, "status": status,
        "f_rich": f_rich, "f_poor": 1.0 - f_rich, "f_rich_esd": 0.01,
        "rich_a": 10.35 - 0.01 * i, "rich_b": 6.02,
        "rich_c": 4.70 + 0.005 * i,
        "poor_a": 10.00 + 0.01 * i, "poor_b": 5.85,
        "poor_c": 4.78 + 0.004 * i,
        "rwp": 2.0 + 0.1 * abs(math.sin(i)),
    }


class TestInsituTrends(unittest.TestCase):
    def test_four_panels_and_crossover_marker(self):
        rows = [_row(i, 0.92 - 0.17 * i) for i in range(1, 6)]
        rows[2]["status"] = "cell_jump"
        fig = Figure()
        draw_trends(fig, rows)
        self.assertEqual(len(fig.axes), 4)
        ax1 = fig.axes[0]
        self.assertEqual(ax1.get_title(), "(a) Phase fractions")
        # 0.5 交叉线（点线）+ 至少一条交叉帧竖线（虚线）
        dashed = [ln for ln in ax1.lines
                  if ln.get_linestyle() in ("--", ":")]
        self.assertGreaterEqual(len(dashed), 2)
        # (b)(c)：两相都在同一根轴上（不是 rich/poor 各一格）
        for ax in fig.axes[1:3]:
            labels = [ln.get_label() for ln in ax.lines]
            self.assertTrue(any(label.startswith("rich") for label in labels))
            self.assertTrue(any(label.startswith("poor") for label in labels))
        self.assertAlmostEqual(fig.axes[0].get_ylim()[1], 1.0)

    def test_no_finite_rows_gives_placeholder(self):
        fig = Figure()
        draw_trends(fig, [])
        self.assertEqual(len(fig.axes), 1)
        self.assertFalse(fig.axes[0].axison)


if __name__ == "__main__":
    unittest.main()
