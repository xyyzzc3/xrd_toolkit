"""绘图区白边收窄（2026-11-16 用户："校准和掩膜的图可以再大一点，白边还挺大"）。

matplotlib 默认的 subplot 参数是**画布百分比**（left 12.5%、right 10%、
bottom 11%、top 12%）。工作台子窗口铺满主窗口以后，这圈按比例算出来的
白边很宽：1400×900 主窗口实测校准画布 727×639，四周白边左右 235 px、
上下 147 px，2D 图只有 492 px 见方；掩膜画布 818×755 时 2D 图只有
433 px 见方。

这里按"标签/刻度实际需要的像素"折算成比例，并夹在 4%–20%：窗口越小
越不会把字裁掉，窗口越大白边越薄。只动坐标区位置，不改数据/视野。
"""
import numpy as np


def compact_margins(fig, *, left_px=56.0, right_px=8.0,
                    bottom_px=38.0, top_px=42.0, hspace=None):
    """把 `fig` 的四周白边收到标签/刻度够用的最小值。

    参数是四边各留多少**像素**（不是比例）：
        left_px   y 轴刻度 + 轴标签
        right_px  右侧呼吸位（通常很小）
        bottom_px x 轴刻度 + 轴标签
        top_px    标题（两行标题约 35–40 px）

    比例再夹到 [0.04/0.05, 0.2]，小画布上不至于把标签裁掉。`hspace`
    原样传给 `subplots_adjust`（掩膜 2D + 1D 预览两行用）。
    """
    size = np.asarray(fig.get_size_inches(), dtype=float) * float(fig.dpi)
    width = max(float(size[0]) if size.size else 0.0, 1.0)
    height = max(float(size[1]) if size.size > 1 else 0.0, 1.0)
    left = min(0.20, max(0.05, float(left_px) / width))
    right = min(0.20, max(0.01, float(right_px) / width))
    bottom = min(0.20, max(0.04, float(bottom_px) / height))
    top = min(0.20, max(0.04, float(top_px) / height))
    kwargs = {
        "left": left,
        "right": 1.0 - right,
        "bottom": bottom,
        "top": 1.0 - top,
    }
    if hspace is not None:
        kwargs["hspace"] = float(hspace)
    fig.subplots_adjust(**kwargs)
