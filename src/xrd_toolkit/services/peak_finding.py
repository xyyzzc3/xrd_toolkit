"""1D 衍射谱自动寻峰（2026-10-23）。

粉末谱分析里的"寻峰"：输入一条 2θ–强度曲线（可带基线），返回每根峰
的**峰位、峰高、FWHM、面积**，以及判定用的显著度（prominence）和
左右基点。纯函数、无 Qt，CLI 与界面共用。

判据（默认值针对本项目的积分谱；换数据可在参数里调）：

- 噪声 σ 用**一阶差分的中位数绝对偏差**估计
  （σ ≈ 1.4826·MAD(Δy)/√2）。对缓变背景不敏感，不会被基线斜率
  虚抬；有峰的位置只占少数样本，中位数也不受峰影响。
- 显著度下限 = max(5σ, 2% × 最大信号)。前者滤噪声，后者保证
  极高的峰旁边的小峰也能被看到（纯 5σ 在强峰旁会漏掉弱峰）。
- 峰间距下限默认 0.008°（本数据 FWHM 约 0.018°）。
- FWHM 取**半显著度宽度**（scipy `peak_widths(rel_height=0.5)`），
  即峰高一半处的宽度。它与单峰拟合的 FWHM 定义一致，但**不是**
  对所有线型都精确：重叠峰、强背景斜率会带偏。要精确的 FWHM/面积
  请把峰位交给 `simulation.peakfit.fit_single_peak` 做局部拟合。
- 面积在**峰的基区**内对"信号 − 背景线"积分；背景线取基区两端
  各 10% 样本的中位数连线（直接连两个端点会被单点噪声放大）。两根峰
  重叠时重叠区会各算一次，所以面积只在孤立峰上等于真实积分强度。

用法：
    from xrd_toolkit.services.peak_finding import find_peaks_1d
    peaks = find_peaks_1d(tth, counts, baseline=bg, max_peaks=12)
    for p in peaks:
        print(f"{p.center:.4f}°  FWHM {p.fwhm:.4f}°  面积 {p.area:.1f}")
"""
from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks, peak_widths

DEFAULT_PROMINENCE_FACTOR = 5.0
DEFAULT_MIN_PEAK_FRACTION = 0.02
DEFAULT_MIN_DISTANCE_DEG = 0.008


@dataclass(frozen=True)
class FoundPeak:
    """一根自动检出的峰。角度单位与输入一致（度）。"""

    center: float       # 峰位
    height: float       # 峰高：给 baseline 时 = 强度 − 基线；否则 = 显著度
    fwhm: float         # 半显著度宽度（度）
    area: float         # 基区内"信号 − 两端连线"的积分
    prominence: float   # 显著度（相对左右基点的较低者）
    index: int          # 峰位在输入数组里的下标
    left: float         # 基区左端 2θ
    right: float        # 基区右端 2θ


def _noise_sigma(signal):
    """一阶差分的稳健噪声估计（对缓变背景不敏感）。"""
    d = np.diff(signal)
    if d.size == 0:
        return 0.0
    med = float(np.median(d))
    mad = float(np.median(np.abs(d - med)))
    return 1.4826 * mad / np.sqrt(2.0)


def find_peaks_1d(tth, intensity, *, baseline=None, min_prominence=None,
                  prominence_factor=DEFAULT_PROMINENCE_FACTOR,
                  min_peak_fraction=DEFAULT_MIN_PEAK_FRACTION,
                  min_distance_deg=DEFAULT_MIN_DISTANCE_DEG,
                  min_fwhm_deg=None, max_fwhm_deg=None,
                  max_peaks=None):
    """在一条 1D 曲线上找峰，按 2θ 升序返回 `FoundPeak` 列表。

    参数：
        tth, intensity   等间距 2θ（度）与强度（counts）；长度一致、
                         2θ 严格递增、全部有限
        baseline         可选基线数组（与 intensity 同形）。给了它，
                         峰高 = 强度 − 基线，面积也按同一残差算
        min_prominence   直接指定显著度下限；省略 = max(kσ, f×最大值)
        prominence_factor  默认 5（σ 的倍数）
        min_peak_fraction  默认 0.02（最大信号的 2%）
        min_distance_deg 两峰最小间距（度），默认 0.008
        min_fwhm_deg     只保留 FWHM 不小于它的峰
        max_fwhm_deg     只保留 FWHM 不大于它的峰
        max_peaks        最多返回几根；按峰高从大到小挑，再按峰位排序

    返回：`FoundPeak` 列表（峰位升序）。没有峰时返回空列表。
    """
    t = np.asarray(tth, dtype=float)
    y = np.asarray(intensity, dtype=float)
    if t.ndim != 1 or y.ndim != 1:
        raise ValueError("find_peaks_1d 需要 1D 的 tth / intensity")
    if t.size != y.size:
        raise ValueError("tth 与 intensity 长度不一致")
    if t.size < 3:
        raise ValueError("至少需要 3 个点才能寻峰")
    if not np.all(np.isfinite(t)) or not np.all(np.isfinite(y)):
        raise ValueError("tth / intensity 里有 NaN 或 Inf")
    dt = np.diff(t)
    if not np.all(dt > 0):
        raise ValueError("tth 必须严格递增")
    step = float(np.median(dt))
    if min_distance_deg < 0:
        raise ValueError("min_distance_deg 不能为负")
    if (min_fwhm_deg is not None and max_fwhm_deg is not None
            and min_fwhm_deg > max_fwhm_deg):
        raise ValueError("min_fwhm_deg 不能大于 max_fwhm_deg")
    if max_peaks is not None and int(max_peaks) < 1:
        raise ValueError("max_peaks 至少为 1")
    if min_prominence is not None and min_prominence < 0:
        raise ValueError("min_prominence 不能为负")
    if prominence_factor < 0 or min_peak_fraction < 0:
        raise ValueError("prominence_factor / min_peak_fraction 不能为负")

    signal = y
    if baseline is not None:
        bg = np.asarray(baseline, dtype=float)
        if bg.shape != y.shape:
            raise ValueError("baseline 与 intensity 形状不一致")
        if not np.all(np.isfinite(bg)):
            raise ValueError("baseline 里有 NaN 或 Inf")
        signal = y - bg

    top = float(np.max(signal))
    if top <= 0:
        return []
    if min_prominence is None:
        sigma = _noise_sigma(signal)
        threshold = max(prominence_factor * sigma,
                        min_peak_fraction * top)
    else:
        threshold = float(min_prominence)
    distance = max(1, int(round(min_distance_deg / step)))

    idx, props = find_peaks(signal, prominence=threshold,
                            distance=distance)
    if idx.size == 0:
        return []
    # 半显著度宽度：与单峰拟合的 FWHM 定义一致（重叠峰/陡背景会偏）
    widths, _, _, _ = peak_widths(signal, idx, rel_height=0.5)
    # 基区（显著度的左右基点），面积在这里积分
    _, _, base_l, base_r = peak_widths(signal, idx, rel_height=1.0)

    peaks = []
    for k, i in enumerate(idx):
        fwhm = float(widths[k]) * step
        if not np.isfinite(fwhm):
            continue
        if min_fwhm_deg is not None and fwhm < min_fwhm_deg:
            continue
        if max_fwhm_deg is not None and fwhm > max_fwhm_deg:
            continue
        lo = max(0, int(np.floor(base_l[k])))
        hi = min(t.size - 1, int(np.ceil(base_r[k])))
        seg = signal[lo:hi + 1]
        if seg.size >= 2:
            # 用两端各 10% 样本的中位数定背景线：直接连两个端点会被
            # 端点上的噪声放大（实测端点 -3.9/-3.1 时面积虚高 25%）
            edge = max(1, seg.size // 10)
            line = np.linspace(float(np.median(seg[:edge])),
                               float(np.median(seg[-edge:])), seg.size)
            area = float(np.trapezoid(seg - line, x=t[lo:hi + 1]))
        else:
            area = 0.0
        # 给基线时峰高 = 强度 − 基线；不给时用局部显著度，避免把
        # 1000 counts 的本底算进峰高
        height = (float(props["prominences"][k]) if baseline is None
                  else float(signal[i]))
        peaks.append(FoundPeak(
            center=float(t[i]),
            height=height,
            fwhm=fwhm,
            area=area,
            prominence=float(props["prominences"][k]),
            index=int(i),
            left=float(np.interp(base_l[k], np.arange(t.size), t)),
            right=float(np.interp(base_r[k], np.arange(t.size), t)),
        ))

    if max_peaks is not None and len(peaks) > int(max_peaks):
        keep = sorted(peaks, key=lambda p: (-p.height, p.center))
        peaks = keep[:int(max_peaks)]
    peaks.sort(key=lambda p: p.center)
    return peaks
