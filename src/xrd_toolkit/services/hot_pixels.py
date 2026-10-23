"""固定坏点/坏点团的检测与掩膜（2026-10-23）。

LMFP 数据里发现**探测器固定的亮斑团**（同一像素在两帧里逐位相同，
峰值可达 ~1.2M counts），它在方位角平均后会在 1D 谱上制造假峰
（2.8–2.9° 那簇"算不出来"的强峰就是这么来的，见
`detector-hot-pixels-lmfp-2026-10-23`）。

做法：用局部中值找"孤立的小亮团"，只把这类像素替换成局部中值，
**不碰大面积的真实衍射环**（真实环在 15×15 窗口里面积很大）。

判据（针对当前 2048²、环计数 ~10³ 的面板；换探测器/增益时要调）：

- 局部中值窗口 15×15；
- 像素 > 8 × 局部中值；
- 连通域面积 ≤ 300 px（真实环是长弧，面积远大于此）；
- 连通域峰值 ≥ 20 000 counts（避免把弱峰/噪声当坏点）。

注意 LaB6 标样不能套默认阈值：它的"亮斑"是真实衍射斑点，会被
同一判据选中（实测 3885 个团/5.7 万 px，标样拟合会被毁掉）。
标样要么不掩，要么只共享"探测器固定坏点坐标表"。
"""
import numpy as np
from scipy import ndimage

DEFAULT_WINDOW = 15
DEFAULT_RATIO = 8.0
DEFAULT_MAX_AREA = 300
DEFAULT_MIN_PEAK = 20000.0
DEFAULT_DOWNSAMPLE = 2


def _local_median(img, window, downsample):
    """局部中值（downsample ≥2 时先在 2×2 均值图上算，再放大）。

    2048² 上直接 15×15 中值约 10 s；2×2 降采样后约 2.4 s，坏点团
    （≥6×6 px）依然能被识别。
    """
    d = int(downsample)
    if d < 2:
        return ndimage.median_filter(img, size=int(window))
    h, w = img.shape
    h2, w2 = h // d, w // d
    small = img[:h2 * d, :w2 * d].reshape(h2, d, w2, d).mean(axis=(1, 3))
    med_small = ndimage.median_filter(small, size=int(window))
    med = np.repeat(np.repeat(med_small, d, axis=0), d, axis=1)
    full = np.empty_like(img)
    full[:h2 * d, :w2 * d] = med
    full[h2 * d:, :] = med[-1:, :]
    full[:, w2 * d:] = med[:, -1:]
    return full


def mask_hot_pixels(image, *, window=DEFAULT_WINDOW, ratio=DEFAULT_RATIO,
                    max_area=DEFAULT_MAX_AREA, min_peak=DEFAULT_MIN_PEAK,
                    downsample=DEFAULT_DOWNSAMPLE):
    """检测孤立亮团并替换为局部中值。

    参数：
        image     2D 强度数组（同 `load_diffraction_image` 的返回）
        window    局部中值窗口（奇数，像素）
        ratio     判定"亮"的倍数（相对局部中值）
        max_area  连通域面积上限（px）；比它大的视为真实环/亮区，不动
        min_peak  连通域峰值下限（counts）
        downsample 局部中值的降采样倍数（2 = 先 2×2 均值再 15×15 中值；
                   1 = 全分辨率中值，慢约 4 倍）

    返回：
        (cleaned, mask, info)
        cleaned   坏点替换成局部中值后的图像（copy）
        mask      bool 数组，True = 被替换的像素
        info      dict：候选连通域数、掩掉的连通域数、掩掉像素数
    """
    img = np.asarray(image, dtype=float)
    if img.ndim != 2:
        raise ValueError("mask_hot_pixels 需要 2D 图像")
    med = _local_median(img, window, downsample)
    cand = img > float(ratio) * np.maximum(med, 1.0)
    labels, n = ndimage.label(cand, structure=np.ones((3, 3)))
    mask = np.zeros(img.shape, dtype=bool)
    kept = 0
    for i, sl in enumerate(ndimage.find_objects(labels), start=1):
        if sl is None:
            continue
        sel = labels[sl] == i
        area = int(sel.sum())
        if area > int(max_area):
            continue
        if float(img[sl][sel].max()) < float(min_peak):
            continue
        mask[sl] |= sel
        kept += 1
    cleaned = img.copy()
    cleaned[mask] = med[mask]
    info = {"candidates": int(n), "masked_components": kept,
            "masked_pixels": int(mask.sum())}
    return cleaned, mask, info
