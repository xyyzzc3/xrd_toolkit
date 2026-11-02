#!/usr/bin/env python3
"""把原位三相批量 CSV 画成一张 4-panel 展示图（2026-10-28）。

读 `fit_insitu.py` 输出的 CSV，不重拟合：
  (a) 富锂/贫锂相对相分数 vs 帧号（f_rich 带线性参数 esd 阴影），
      标出 ok / ok_soft / cell_jump；
  (b) rich 相 a/b/c（带 esd 误差棒）；
  (c) poor 相 a/b/c（带 esd 误差棒）；
  (d) Rwp / Rp vs 帧号，标异常帧。

横轴是帧号（没有电压/时间列，不假装有）。相分数是相对散射强度分数，
不是 wt%——标题和脚注都写清楚。充电/放电底色由 f_rich 最小值自动推断
（本批第 45 帧），只作视觉分区。

用法：
    python scripts/plot_insitu_summary.py \
        --csv outputs/insitu/insitu_fit.csv \
        --out outputs/insitu/insitu_summary.png
"""
import argparse
import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.simulation.insitu_plots import draw_summary  # noqa: E402


def _read_rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        def f(key):
            try:
                v = float(r.get(key, "nan"))
            except (TypeError, ValueError):
                return float("nan")
            return v
        status = str(r.get("status", ""))
        group = status.split("(", 1)[0]
        out.append({
            "frame": int(float(r["frame"])),
            "status": status,
            "group": group,
            "usable": str(r.get("usable", "")).lower() == "true",
            "f_rich": f("f_rich"), "f_poor": f("f_poor"),
            "f_rich_esd": f("f_rich_esd"),
            "rwp": f("rwp"), "rp": f("rp"),
            "rich_a": f("rich_a"), "rich_b": f("rich_b"),
            "rich_c": f("rich_c"),
            "rich_a_esd": f("rich_a_esd"),
            "rich_b_esd": f("rich_b_esd"),
            "rich_c_esd": f("rich_c_esd"),
            "poor_a": f("poor_a"), "poor_b": f("poor_b"),
            "poor_c": f("poor_c"),
            "poor_a_esd": f("poor_a_esd"),
            "poor_b_esd": f("poor_b_esd"),
            "poor_c_esd": f("poor_c_esd"),
        })
    return out


def plot_summary(csv_path, out_path, *, title=None, dpi=160):
    """读 CSV 画 4-panel；返回 (figure, axes)。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = _read_rows(csv_path)
    if not rows:
        raise ValueError(f"CSV 没有数据：{csv_path}")
    fig = plt.figure(figsize=(12.5, 8.0))
    draw_summary(fig, rows, title=title)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=dpi)
    return fig, np.asarray(fig.axes)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", default="outputs/insitu/insitu_fit.csv")
    ap.add_argument("--out", default="outputs/insitu/insitu_summary.png")
    ap.add_argument("--title", default=None)
    ap.add_argument("--dpi", type=int, default=160)
    args = ap.parse_args()
    fig, _axes = plot_summary(args.csv, args.out,
                              title=args.title, dpi=args.dpi)
    import matplotlib.pyplot as plt
    plt.close(fig)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
