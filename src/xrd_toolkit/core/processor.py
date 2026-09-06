"""XRD 图像处理：对读进来的 2D 强度数组做各种计算。
"""
import numpy as np


def line_profile(image: np.ndarray, center=None, angle_deg: float = 0.0):
    """沿过圆心、与水平方向成 angle_deg 的直线采样强度。

    参数：
        image     —— 2D numpy 数组，image[行][列] = 该像素的强度
        center    —— 圆心 (行, 列)。不传时默认图像几何中心；
                     真实数据必须传衍射环的圆心（直射光斑位置）
        angle_deg —— 直线与水平方向的夹角（度），0 = 水平线

    返回：
        t        —— 每个采样点到圆心的距离（像素）。圆心左侧为负、右侧为正
        intensity—— 每个采样点的强度（经过插值）

    双线性插值，用周围 4 个像素按距离加权平均，得到的曲线更平滑、更接近真实。
    """

    h, w = image.shape
    if center is None:
        cy, cx = h / 2.0, w / 2.0   # 几何中心
    else:
        cy, cx = center             # 注意：center 是 (行, 列) = (y, x)

    # np.radians 负责"角度 → 弧度"的换算。
    theta = np.radians(angle_deg)
    cos_t, sin_t = np.cos(theta), np.sin(theta)

    # 直线方程：x = cx + t·cosθ, y = cy + t·sinθ（t 是沿直线的距离）
    # 先算出 t 的合法范围——保证采样点不出图像边界。
    # 圆心不居中时两侧能走的距离不同，硬截断会让边缘像素被重复采样。
    t_min, t_max = -float(np.hypot(h, w)), float(np.hypot(h, w))
    for c, lo_edge, hi_edge in (
            (cos_t, -cx, w - 1 - cx),   # x 方向约束：0 ≤ x ≤ w-1
            (sin_t, -cy, h - 1 - cy)):  # y 方向约束：0 ≤ y ≤ h-1
        if abs(c) < 1e-12:
            continue  # 该方向没有移动（如水平线时 sinθ = 0）
        if c > 0:
            t_max = min(t_max, hi_edge / c)
            t_min = max(t_min, lo_edge / c)
        else:
            t_max = min(t_max, lo_edge / c)
            t_min = max(t_min, hi_edge / c)

    # 采样点序列：t_min 到 t_max，间隔 1 像素
    t = np.arange(np.ceil(t_min), np.floor(t_max) + 1, dtype=np.float64)

    # 向量化：t 是数组，
    # xs = cx + t * cos_t 是"对这一串里的每个数同时计算"。
    xs = cx + t * cos_t
    ys = cy + t * sin_t

    # 双线性插值：每个采样点用周围 4 个像素加权平均
    # x0, y0 = 采样点左上方的像素；fx, fy = 采样点在该像素内的小数位置 (0~1)
    x0 = np.floor(xs).astype(int)
    y0 = np.floor(ys).astype(int)
    fx = xs - x0
    fy = ys - y0

    # np.clip：把坐标夹在合法范围内（0 ~ w-1），防止越界报错
    x0c = np.clip(x0, 0, w - 1)
    y0c = np.clip(y0, 0, h - 1)
    x1c = np.clip(x0 + 1, 0, w - 1)
    y1c = np.clip(y0 + 1, 0, h - 1)

    # 周围四个点的强度
    i00 = image[y0c, x0c]  # 左上
    i10 = image[y0c, x1c]  # 右上
    i01 = image[y1c, x0c]  # 左下
    i11 = image[y1c, x1c]  # 右下

    # 按距离加权平均：离哪个像素近，它的权重就大
    intensity = (i00 * (1 - fx) * (1 - fy)
                 + i10 * fx * (1 - fy)
                 + i01 * (1 - fx) * fy
                 + i11 * fx * fy)
    return t, intensity
