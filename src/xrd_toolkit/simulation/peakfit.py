"""单峰峰形拟合（2026-10-23）：Gaussian / Lorentzian / pseudo-Voigt /
split-Pearson VII。

与 `fit.py` 的全谱拟合分工不同：这里只取一根峰附近的一小段数据，
用"局部线性背景 + 一个峰"做非线性最小二乘，回答"这根峰的峰位、
FWHM、线型、面积是多少"。全谱拟合仍用 GSAS 口径的伪 Voigt。

线型约定（x、c、FWHM 都用度，峰高在中心处为 1）：

* Gaussian     exp(−4ln2·z²)，z = 2(x−c)/Γ
* Lorentzian   1/(1+4z²)
* pseudo-Voigt η·L + (1−η)·G（η=0/1 即 Gaussian/Lorentzian）
* Pearson VII [1 + (2z)²(2^(1/m)−1)]^(−m)（m=1 → Lorentzian，
  m→∞ → Gaussian；FWHM = Γ）
* split-Pearson VII：左右各用自己的 Γ（Γₗ/Γᵣ 可以不同），m 共用

面积由拟合曲线在窗口内数值积分得到（不做解析归一）。
"""
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares

SHAPES = ("gaussian", "lorentzian", "pseudovoigt", "split_pearson7")


def gaussian(x, center, fwhm):
    z = 2.0 * (np.asarray(x, float) - center) / fwhm
    return np.exp(-np.log(2.0) * z * z)


def lorentzian(x, center, fwhm):
    z = 2.0 * (np.asarray(x, float) - center) / fwhm
    return 1.0 / (1.0 + z * z)


def pseudo_voigt(x, center, fwhm, eta):
    return eta * lorentzian(x, center, fwhm) + \
        (1.0 - eta) * gaussian(x, center, fwhm)


def pearson7(x, center, fwhm, m):
    z = 2.0 * (np.asarray(x, float) - center) / fwhm
    return (1.0 + z * z * (2.0 ** (1.0 / m) - 1.0)) ** (-m)


def split_pearson7(x, center, fwhm_left, fwhm_right, m):
    x = np.asarray(x, float)
    fw = np.where(x < center, fwhm_left, fwhm_right)
    z = 2.0 * (x - center) / fw
    return (1.0 + z * z * (2.0 ** (1.0 / m) - 1.0)) ** (-m)


@dataclass
class SinglePeakFit:
    """单峰拟合结果（局部背景 + 一个峰）。"""

    shape: str
    center: float
    fwhm: float                 # split 线型取 (Γₗ+Γᵣ)/2 的等效值
    fwhm_left: float = float("nan")
    fwhm_right: float = float("nan")
    eta: float = float("nan")   # 仅 pseudo-Voigt
    m: float = float("nan")     # 仅 Pearson VII
    height: float = float("nan")
    area: float = float("nan")
    background: object = None   # np.ndarray，窗口内的局部背景
    rwp: float = float("nan")
    params: dict = field(default_factory=dict)


def _shape(x, shape, c, fw, fwl, fwr, m, eta):
    if shape == "gaussian":
        return gaussian(x, c, fw)
    if shape == "lorentzian":
        return lorentzian(x, c, fw)
    if shape == "pseudovoigt":
        return pseudo_voigt(x, c, fw, eta)
    if shape == "split_pearson7":
        return split_pearson7(x, c, fwl, fwr, m)
    raise ValueError(f"未知线型：{shape}（可选 {SHAPES}）")


def _initial_fwhm(x, y, c0):
    i = int(np.argmin(np.abs(x - c0)))
    y0 = float(np.min(y))
    h = float(y[i]) - y0
    if h <= 0:
        return 0.02
    half = y0 + 0.5 * h
    # 左右半高宽平均
    left = right = None
    for j in range(i, 0, -1):
        if y[j - 1] <= half <= y[j]:
            left = float(np.interp(half, [y[j], y[j - 1]],
                                   [x[j], x[j - 1]]))
            break
    for j in range(i, len(y) - 1):
        if y[j + 1] <= half <= y[j]:
            right = float(np.interp(half, [y[j], y[j + 1]],
                                    [x[j], x[j + 1]]))
            break
    if left is None or right is None or right <= left:
        return 0.02
    return max(1e-4, right - left)


def fit_single_peak(tth, intensity, center, *, shape="pseudovoigt",
                    half_window=0.12, background="linear"):
    """拟合一根峰：局部背景（0/1/2 阶）+ 一个峰。

    参数：
        tth, intensity  完整 1D 数据（自动取 center ± half_window）
        center          初始峰位（度）
        shape           gaussian / lorentzian / pseudovoigt / split_pearson7
        half_window     窗口半宽（度）
        background      "none" / "linear" / "quadratic"

    返回 `SinglePeakFit`。拟合量是峰高（中心处），面积在窗口内积分。
    """
    if shape not in SHAPES:
        raise ValueError(f"未知线型：{shape}（可选 {SHAPES}）")
    x_all = np.asarray(tth, dtype=float)
    y_all = np.asarray(intensity, dtype=float)
    m = (x_all > center - half_window) & (x_all < center + half_window)
    x, y = x_all[m], y_all[m]
    if len(x) < 8:
        raise ValueError("窗口内数据点太少")
    fw0 = _initial_fwhm(x, y, center)
    amp0 = max(float(y.max() - np.min(y)), 1.0e-9)
    nb = {"none": 0, "linear": 1, "quadratic": 2}.get(background)
    if nb is None:
        raise ValueError("background 取 none / linear / quadratic")
    b0 = float(np.min(y))
    # 参数顺序：峰参数 + 背景系数
    if shape in ("gaussian", "lorentzian"):
        p0 = [center, fw0, amp0] + [b0] + [0.0] * nb
        lo = [center - 0.05, 1e-4, 0.0] + [-np.inf] * (1 + nb)
        hi = [center + 0.05, 0.6, np.inf] + [np.inf] * (1 + nb)
    elif shape == "pseudovoigt":
        p0 = [center, fw0, 0.5, amp0] + [b0] + [0.0] * nb
        lo = [center - 0.05, 1e-4, 0.0, 0.0] + [-np.inf] * (1 + nb)
        hi = [center + 0.05, 0.6, 1.0, np.inf] + [np.inf] * (1 + nb)
    else:                                   # split_pearson7
        p0 = [center, fw0, fw0, 2.0, amp0] + [b0] + [0.0] * nb
        lo = [center - 0.05, 1e-4, 1e-4, 0.3, 0.0] + [-np.inf] * (1 + nb)
        hi = [center + 0.05, 0.6, 0.6, 30.0, np.inf] + [np.inf] * (1 + nb)

    def unpack(q):
        if shape in ("gaussian", "lorentzian"):
            c, fw, amp = q[:3]
            fwl = fwr = fw
            eta = np.nan
            mm = np.nan
        elif shape == "pseudovoigt":
            c, fw, eta, amp = q[:4]
            fwl = fwr = fw
            mm = np.nan
        else:
            c, fwl, fwr, mm, amp = q[:5]
            fw = 0.5 * (fwl + fwr)
            eta = np.nan
        n_peak = {"gaussian": 3, "lorentzian": 3, "pseudovoigt": 4,
                  "split_pearson7": 5}[shape]
        bc = q[n_peak:]
        bg = np.zeros_like(x)
        for k, val in enumerate(bc):
            bg += val * (x - center) ** k
        return c, fw, fwl, fwr, eta, mm, amp, bg

    def residual(q):
        c, fw, fwl, fwr, eta, mm, amp, bg = unpack(q)
        return bg + amp * _shape(x, shape, c, fw, fwl, fwr, mm, eta) - y

    out = least_squares(residual, p0, bounds=(lo, hi), x_scale="jac",
                        max_nfev=4000)
    c, fw, fwl, fwr, eta, mm, amp, bg = unpack(out.x)
    model = bg + amp * _shape(x, shape, c, fw, fwl, fwr, mm, eta)
    area = float(np.trapezoid(np.maximum(model - bg, 0.0), x))
    rwp = 100.0 * float(np.sqrt(np.sum((model - y) ** 2)
                                / max(np.sum(y ** 2), 1e-30)))
    return SinglePeakFit(
        shape=shape, center=float(c), fwhm=float(fw),
        fwhm_left=float(fwl), fwhm_right=float(fwr),
        eta=float(eta), m=float(mm), height=float(amp), area=area,
        background=bg, rwp=rwp,
        params={"center": float(c), "fwhm": float(fw),
                "fwhm_left": float(fwl), "fwhm_right": float(fwr),
                "eta": float(eta), "m": float(mm), "height": float(amp),
                "area": area})
