"""2D 探测器掩膜：文件读写、手动/自动合并、径向坏点检测。

掩膜必须在**图像加载之后、方位角积分之前**生效：被 mask 的像素完全不
参与 2θ 平均。它挡的是几何/探测器问题，不是样品信号：

- beamstop 及其支撑臂的遮挡区；
- 拼接探测器的模块间隙；
- 坏像素/热像素簇（响应异常高，或死像素响应为 0）；
- 探测器最外圈信噪比差的边缘；
- 明显的寄生散射斑点/固定环（固定环也可能是真实环，需人工判断）。

约定：**True = 被遮掉（坏像素）**。多个来源合并用**并集**（`|`）：
手动遮掉的、局部坏点检测抓到的、径向 nσ 抓到的，任何一方认定坏就
不进积分。若误用交集（`&`），只被一边标出的坏点会漏掉。

自动检测只是补充，不能替代手动 mask：beamstop/模块缝不是"异常亮
像素"，径向统计抓不到；而真实衍射环（尤其强织构/大晶粒）可能被
径向 nσ 误判，需要人工擦除。
"""
from pathlib import Path

import numpy as np

# 支持的掩膜文件后缀。`.npy` 简单、无损；`.edf` 是衍射领域通用格式，
# pyFAI-drawmask 等工具也能直接读写。
MASK_SUFFIXES = (".npy", ".edf")


def _as_bool_mask(mask, *, name="mask"):
    """转成 2D bool 数组；True = 被遮掉。"""
    arr = np.asarray(mask)
    if arr.ndim != 2:
        raise ValueError(f"{name} 必须是 2D 数组（现在是 {arr.ndim}D）")
    return arr.astype(bool, copy=False)


def save_mask(path, mask) -> Path:
    """保存掩膜为 `.npy` 或 `.edf`；返回实际写入的路径。"""
    p = Path(path)
    arr = _as_bool_mask(mask)
    suffix = p.suffix.lower()
    if suffix not in MASK_SUFFIXES:
        raise ValueError(
            f"掩膜只支持 {MASK_SUFFIXES}，收到 {p.suffix!r}")
    if suffix == ".npy":
        np.save(str(p), arr)
    else:
        import fabio
        # EDF 用 uint8 0/1 存；fabio 的 EdfImage 会补最小头。
        fabio.edfimage.EdfImage(data=arr.astype(np.uint8)).write(str(p))
    return p


def load_mask(path) -> np.ndarray:
    """读取 `.npy` / `.edf` 掩膜，返回 bool 数组（True = 被遮掉）。"""
    p = Path(path)
    if p.suffix.lower() == ".npy":
        arr = np.load(str(p), allow_pickle=False)
    else:
        import fabio
        arr = fabio.open(str(p)).data
    return _as_bool_mask(arr, name=str(p))


def combine_masks(*masks) -> np.ndarray:
    """多个掩膜取并集（任一来源标坏 = 遮掉）；所有形状必须一致。"""
    out = None
    for m in masks:
        if m is None:
            continue
        arr = _as_bool_mask(m)
        if out is None:
            out = arr.copy()
        elif arr.shape != out.shape:
            raise ValueError(
                f"掩膜形状不一致：{out.shape} vs {arr.shape}")
        else:
            out |= arr
    if out is None:
        raise ValueError("combine_masks 至少需要一张掩膜")
    return out


def radial_outlier_mask(image, center, *, n_sigma=5.0, bin_px=4.0,
                        min_count=8) -> np.ndarray:
    """按径向分箱找异常像素（True = 坏）。

    对每个到束心距离的环带，取强度的中位数与稳健标准差
    （1.4826×MAD），把偏离中位数超过 `n_sigma` 个标准差的像素标为
    坏点。可以同时抓"异常亮"（热像素）和"异常暗"（死像素）。
    MAD=0 的箱（绝大多数像素同值）直接跳过：没有正常散布就无法
    判离群，硬用普通 std 会在窄箱里把真实强环误标（见实现注释）。

    参数：
        image   2D 强度数组
        center  (行, 列)，**图像索引坐标**（与 GUI 的 beam_center 同一套）
        n_sigma 阈值（默认 5）
        bin_px  径向箱宽（像素，默认 4）；太窄会把真实环的方位起伏
                当坏点，太宽会稀释局部坏点
        min_count 每个箱至少多少像素才做统计（默认 8）

    已知边界：真实环若有强织构/大晶粒，同一环带内方位角起伏可能
    超过 5σ，会被误标。**2026-10-27 在真实 LMFP 帧上实测**：
    bin_px=4、n_sigma=5 会沿真实衍射环标出 ~18k px；所以它只作
    实验性补充，默认不用于生产（见 auto_mask），最终仍由人工 mask
    判断。
    """
    img = np.asarray(image, dtype=float)
    if img.ndim != 2:
        raise ValueError("radial_outlier_mask 需要 2D 图像")
    if img.size == 0:
        return np.zeros(img.shape, dtype=bool)
    row, col = float(center[0]), float(center[1])
    rows = np.arange(img.shape[0], dtype=float)[:, None]
    cols = np.arange(img.shape[1], dtype=float)[None, :]
    r = np.hypot(rows - row, cols - col)
    bins = np.floor(r / max(float(bin_px), 1e-6)).astype(np.int64)
    flat_img = img.ravel()
    flat_bin = bins.ravel()
    # 按箱号排序后逐箱统计：比"每个箱做一次布尔掩码"少扫几遍。
    order = np.argsort(flat_bin, kind="stable")
    sorted_bin = flat_bin[order]
    sorted_img = flat_img[order]
    edges = np.flatnonzero(np.diff(sorted_bin)) + 1
    starts = np.concatenate([[0], edges])
    stops = np.concatenate([edges, [sorted_bin.size]])
    flat_mask = np.zeros(flat_img.size, dtype=bool)
    for lo, hi in zip(starts, stops):
        vals = sorted_img[lo:hi]
        if vals.size < int(min_count):
            continue
        med = float(np.median(vals))
        mad = float(np.median(np.abs(vals - med)))
        sigma = 1.4826 * mad
        if not np.isfinite(sigma) or sigma <= 0.0:
            # 箱内绝大多数像素同值（MAD=0）：没有可用的"正常散布"，
            # 无法判断谁离群。**不要退回普通 std**——强环在窄径向箱
            # 里会让 std 很大，5σ 仍可能把真实环当坏点（实测合成环
            # 误标 5% 像素）。孤立热像素交给 hot_pixels 的局部检测。
            continue
        bad = np.abs(vals - med) > float(n_sigma) * sigma
        if np.any(bad):
            flat_mask[order[lo:hi]] = bad
    return flat_mask.reshape(img.shape)


def auto_mask(image, center, *, n_sigma=5.0, bin_px=4.0,
              include_hot=True, include_radial=False,
              hot_kwargs=None) -> dict:
    """自动 mask：默认只跑**局部坏点检测**（生产路径）。

    径向 nσ 默认关闭：2026-10-27 在真实 LMFP 帧上实测，它会把真实
    衍射环的强弧段当离群点（18k px，红色沿环分布），直接用于生产会
    削掉真实信号。需要时显式 `include_radial=True`，并在 GUI 里预览
    后再决定。

    返回 dict，便于 GUI 分别勾选/查看：
        {"mask": 并集 bool 数组,
         "radial": 径向 nσ 结果（未启用时全 False）,
         "hot": 局部坏点结果（未启用时全 False）}
    """
    img = np.asarray(image)
    if include_radial:
        radial = radial_outlier_mask(
            image, center, n_sigma=n_sigma, bin_px=bin_px)
    else:
        radial = np.zeros(img.shape, dtype=bool)
    if include_hot:
        from xrd_toolkit.services.hot_pixels import mask_hot_pixels
        _clean, hot, _info = mask_hot_pixels(image, **(hot_kwargs or {}))
    else:
        hot = np.zeros(img.shape, dtype=bool)
    return {"mask": combine_masks(radial, hot),
            "radial": radial, "hot": hot}


def mask_stats(mask) -> dict:
    """掩膜统计（日志/报告用）。"""
    arr = _as_bool_mask(mask)
    n = int(arr.sum())
    total = int(arr.size)
    return {"masked_pixels": n, "total_pixels": total,
            "fraction": (n / total if total else 0.0)}
