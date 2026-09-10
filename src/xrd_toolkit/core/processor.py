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


def find_ring_center(image: np.ndarray):
    """自动定位衍射环圆心（= 直射束落点），亚像素精度，无需任何参数。

    原理（类比"对折找中心"）：
        粉末衍射图关于圆心是中心对称的——物理上由 Friedel 定律保证：
        每个衍射信号（环、甚至单晶斑点）都有一个"穿过圆心的对跖点"，
        两者强度相同。所以把整张图绕几何中心旋转 180° 后，得到的图
        与原图只差一个平移，且平移量 = 圆心偏移几何中心的 2 倍。
        用 FFT 互相关找到这个平移量，除以 2 即得圆心。

    三步（每步都有实测依据，见纠错点记录）：
        1. 裁剪最亮的 0.1% 像素。强单晶亮斑会互相"假配对"，在相关
           面上制造假峰（LMFP 数据实测假峰比真峰还高）；裁剪后亮斑
           变成平顶，假峰消失，环的结构成为相关面的主导。
        2. 原图与 180° 旋转图做互相关（FFT）。相关峰位置 = 2×偏移，
           峰可以出现在任何位置，所以哪怕圆心偏移几百像素也能找到。
        3. 峰附近 5×5 最小二乘二次拟合，把整数峰位细化到亚像素
           （峰是"山"不是"台阶"，真正的峰顶在两格之间）。

    实测精度：lab6 → (1022.2, 1021.7)，LMFP → (1021.3, 1021.9)，
    与标定值 B = (1022.0, 1022.3) 相差 < 1 px（约 0.05% 图像宽度）。

    参数：
        image —— 2D numpy 数组，image[行][列] = 该像素的强度

    返回：
        (cy, cx) —— 圆心（行, 列），与 line_profile 的 center 顺序一致
    """
    h, w = image.shape
    f = np.asarray(image, dtype=np.float64)

    # 1) 裁剪亮斑：超过 99.9% 分位数的像素压平，消除亮斑假配对峰
    cap = np.percentile(f, 99.9)
    f = np.clip(f, 0, cap)

    # 2) 绕几何中心旋转 180°：np.rot90(f, 2) 绕的正是阵列中心
    #    ((w-1)/2, (h-1)/2) = 几何中心 C，正是我们要的旋转轴
    g = np.rot90(f, 2)

    # 3) 互相关：corr[k] = Σ_p f[p]·g[p−k]。
    #    先去均值（减掉背景平台，避免直流分量盖住相关峰），
    #    fftshift 后平移量 0 在数组正中心 (h//2, w//2)。
    corr = np.fft.fftshift(np.fft.ifft2(
        np.fft.fft2(f - f.mean()) * np.conj(np.fft.fft2(g - g.mean()))
    )).real
    cy0, cx0 = h // 2, w // 2
    iy, ix = np.unravel_index(np.argmax(corr), corr.shape)
    sy = float(iy - cy0)              # 纵向平移量（整数部分）
    sx = float(ix - cx0)              # 横向平移量

    # 4) 亚像素细化：把峰附近 5×5 小块的"行和 / 列和"各拟一条二次曲线
    #    （最小二乘，5 个点求 3 个系数），顶点位置就是小数部分。
    def fit_axis(patch_sums: np.ndarray) -> float:
        idx = np.arange(-2, 3, dtype=np.float64)
        a, b, _ = np.linalg.lstsq(
            np.stack([idx ** 2, idx, np.ones(5)], axis=1),
            patch_sums, rcond=None)[0]
        return -b / (2 * a) if abs(a) > 1e-12 else 0.0

    # 峰贴边时切片会负数回绕，拟合没意义（贴边峰 = 周期延拓伪峰，直接用整数位）
    if 2 <= iy < h - 2 and 2 <= ix < w - 2:
        patch = corr[iy - 2:iy + 3, ix - 2:ix + 3]
        sy += fit_axis(patch.sum(axis=1))  # 行方向（y）
        sx += fit_axis(patch.sum(axis=0))  # 列方向（x）

    # 5) 平移量 k = 2(c - C)  →  圆心 c = C + k/2
    cx = (w - 1) / 2.0 + sx / 2.0
    cy = (h - 1) / 2.0 + sy / 2.0
    return cy, cx
