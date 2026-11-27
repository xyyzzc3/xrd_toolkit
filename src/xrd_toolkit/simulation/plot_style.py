"""统一的 matplotlib 视觉参数（2026-11-27）。

只在 GUI 入口调用；不改字号（各图已经按可读性手调过），只统一
底色、坐标轴/网格灰阶和默认色环，让 1D / 精修 / 掩膜 / 原位四类图
放在一起像同一套软件。
"""
from __future__ import annotations

from cycler import cycler
from matplotlib import rcParams

# 与 GUI 主题同一套冷色：深蓝、琥珀、青绿、紫、砖红、灰
SERIES_COLORS = ("#1f5fa8", "#e08a1e", "#2a9d8f", "#8e5aa8",
                 "#c0392b", "#16a085", "#7f8c8d", "#d35400")


def apply_plot_style() -> None:
    rcParams.update({
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "savefig.edgecolor": "white",
        "axes.edgecolor": "#5b6773",
        "axes.linewidth": 0.8,
        "axes.titlesize": 10,
        "figure.titlesize": 11,
        "grid.color": "#d9e0e8",
        "grid.alpha": 0.7,
        "axes.prop_cycle": cycler(color=list(SERIES_COLORS)),
    })
