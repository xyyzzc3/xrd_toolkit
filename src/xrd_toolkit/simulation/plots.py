"""XRD 模拟图谱的绘图工具（与计算分离，matplotlib 属主依赖）。

两个绘图入口：

- `plot_pattern`：竖线（棒状）谱图。理论峰是无限窄的，模拟谱的标准画法
  就是一排竖线；
- `plot_annotated`：在每根峰上方竖排标注 (hkl)（六方用四位指数 hkil），
  相邻标签过近时自动抬高一档，避免叠字。
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from xrd_toolkit.simulation.core import format_hkls

FOOTPRINT_DEG = 1.35   # 竖排标签的横向"占地"（度）：小于这个距离就躲到上一档
TIER_GAP = 15.0        # 每档抬高多少（强度单位）


def plot_pattern(two_thetas, intensities, title, out_path):
    """竖线谱图：横轴 2θ，纵轴相对强度（最强峰 100）。返回输出路径。"""
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.vlines(two_thetas, 0, intensities, color="#2b6cb0", lw=1.8)
    ax.plot(two_thetas, intensities, "o", color="#2b6cb0", ms=3)
    ax.set_xlabel(r"2$\theta$ (degrees)")
    ax.set_ylabel("Intensity (a.u.)")
    ax.set_title(title)
    ax.set_xlim(float(two_thetas.min()) - 2, float(two_thetas.max()) + 2)
    ax.set_ylim(0, 110)
    ax.grid(alpha=0.25, linestyle=":")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_annotated(two_thetas, intensities, peaks, is_hex, title, out_path):
    """带 (hkl) 标注的谱图（第六周最终交付图的画法）。

    peaks 用 core.pattern_from_structure 返回的峰表（含代表 hkl 与多重性）。
    返回输出路径。
    """
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.vlines(two_thetas, 0, intensities, color="#2b6cb0", lw=1.8)
    ax.plot(two_thetas, intensities, "o", color="#2b6cb0", ms=3.2)

    last_x = []   # 每一档最后放过的 x（度）
    for tt, ii, refs in peaks:
        tier = 0
        while tier < len(last_x) and tt - last_x[tier] < FOOTPRINT_DEG:
            tier += 1
        if tier == len(last_x):
            last_x.append(tt)
        else:
            last_x[tier] = tt
        ax.text(tt, ii + 3 + tier * TIER_GAP, format_hkls(refs, is_hex),
                rotation=90, ha="center", va="bottom",
                fontsize=8.5, color="#333333")

    ax.set_xlim(8, 82)
    ax.set_ylim(0, 140)
    ax.set_xlabel(r"2$\theta$ (degrees)")
    ax.set_ylabel("Intensity (a.u.)")
    ax.set_title(title)
    ax.text(0.012, 0.02, "peak labels: (hkl) — hand-written implementation",
            transform=ax.transAxes, ha="left", fontsize=7.5, color="0.45")
    ax.grid(alpha=0.25, linestyle=":")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path
