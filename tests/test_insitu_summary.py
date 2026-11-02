"""4-panel 原位汇总图测试（2026-10-28）。

不跑拟合：造一份小 CSV，确认绘图入口能读列、画 4 个 panel、落盘。
"""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import plot_insitu_summary as ps  # noqa: E402

FIELDS = [
    "frame", "status", "usable", "rwp", "rp", "f_rich", "f_poor",
    "rich_a", "rich_b", "rich_c", "rich_a_esd", "rich_b_esd",
    "rich_c_esd", "poor_a", "poor_b", "poor_c", "poor_a_esd",
    "poor_b_esd", "poor_c_esd",
]


class TestInsituSummary(unittest.TestCase):
    def test_plot_writes_four_panels(self):
        rows = []
        for i, (status, usable, fr) in enumerate((
                ("ok", "True", 0.90),
                ("ok_soft", "True", 0.50),
                ("not_converged", "True", 0.10),
                ("cell_jump", "True", 0.20),
                ("ok_soft", "True", 0.30),
                ("ok", "True", 0.70)), start=1):
            rows.append({
                "frame": i, "status": status, "usable": usable,
                "rwp": 2.0 + 0.1 * i, "rp": 1.2,
                "f_rich": fr, "f_poor": 1.0 - fr,
                "rich_a": 10.35, "rich_b": 6.02, "rich_c": 4.70,
                "rich_a_esd": 0.001, "rich_b_esd": 0.001,
                "rich_c_esd": 0.001,
                "poor_a": 10.0, "poor_b": 5.85, "poor_c": 4.78,
                "poor_a_esd": 0.01, "poor_b_esd": 0.01,
                "poor_c_esd": 0.01,
            })
        with tempfile.TemporaryDirectory() as td:
            csv_path = Path(td) / "fit.csv"
            out = Path(td) / "summary.png"
            with open(csv_path, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=FIELDS)
                w.writeheader()
                w.writerows(rows)
            fig, axes = ps.plot_summary(csv_path, out, dpi=80)
            try:
                self.assertTrue(out.exists())
                self.assertGreater(out.stat().st_size, 1000)
                self.assertEqual(len(axes.ravel()), 4)
            finally:
                import matplotlib.pyplot as plt
                plt.close(fig)

    def test_two_frames_do_not_collapse_axes(self):
        """只有 2 帧时标注点容易跑到 x 范围外，tight_layout 会把坐标轴
        压成一条缝（2026-11-02 在 GUI 2 帧预览里发现）。"""
        rows = []
        for i, fr in ((1, 0.91), (2, 0.90)):
            rows.append({
                "frame": i, "status": "ok", "usable": "True",
                "rwp": 2.1, "rp": 1.2, "f_rich": fr, "f_poor": 1.0 - fr,
                "rich_a": 10.35, "rich_b": 6.02, "rich_c": 4.70,
                "rich_a_esd": 0.001, "rich_b_esd": 0.001,
                "rich_c_esd": 0.001,
                "poor_a": 10.0, "poor_b": 5.85, "poor_c": 4.78,
                "poor_a_esd": 0.01, "poor_b_esd": 0.01,
                "poor_c_esd": 0.01,
            })
        with tempfile.TemporaryDirectory() as td:
            csv_path = Path(td) / "fit.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=FIELDS)
                w.writeheader()
                w.writerows(rows)
            fig, axes = ps.plot_summary(
                csv_path, Path(td) / "summary.png", dpi=80)
            try:
                widths = [ax.get_position().width for ax in axes]
                self.assertGreater(min(widths), 0.2,
                                   f"坐标轴被压扁了：{widths}")
            finally:
                import matplotlib.pyplot as plt
                plt.close(fig)


if __name__ == "__main__":
    unittest.main()
