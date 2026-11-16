"""绘图白边收窄（2026-11-16 用户："校准和掩膜的图可以再大一点"）。"""
import sys
import unittest
from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.gui.figure_layout import compact_margins  # noqa: E402


class TestCompactMargins(unittest.TestCase):
    def _fig(self, w=8.0, h=6.0):
        fig = Figure(figsize=(w, h), dpi=100)
        FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)
        fig.canvas.draw()
        return fig, ax

    def test_margins_are_smaller_than_matplotlib_defaults(self):
        fig, ax = self._fig()
        compact_margins(fig)
        fig.canvas.draw()
        x, y, w, h = ax.get_position().bounds
        # matplotlib 默认 left 0.125 / right 0.9 / bottom 0.11 / top 0.88
        self.assertLess(x, 0.125)
        self.assertGreater(x + w, 0.9)
        self.assertLess(y, 0.11)
        self.assertGreater(y + h, 0.88)

    def test_bigger_canvas_gets_smaller_fractional_margin(self):
        small, _ = self._fig(4.0, 3.0)
        large, _ = self._fig(16.0, 12.0)
        compact_margins(small)
        compact_margins(large)
        self.assertLess(large.subplotpars.left, small.subplotpars.left)

    def test_small_canvas_never_crops_to_zero(self):
        fig, ax = self._fig(1.0, 1.0)
        compact_margins(fig)
        self.assertGreaterEqual(fig.subplotpars.left, 0.05)
        self.assertGreaterEqual(fig.subplotpars.bottom, 0.04)
        self.assertLessEqual(fig.subplotpars.right, 0.99)


if __name__ == "__main__":
    unittest.main()
