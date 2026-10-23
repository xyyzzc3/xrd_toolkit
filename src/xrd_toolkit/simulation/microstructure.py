"""微结构参数：Scherrer 尺寸与 Williamson–Hall 分析（2026-10-23）。

单峰 FWHM → 平均晶粒尺寸/晶格应变的标准做法：

* **Scherrer**：D = K·λ/(β·cosθ)（β 用弧度；K 默认 0.9，球状晶粒常用）
* **Williamson–Hall**（方差形式）：
      (β·cosθ)² = (Kλ/D)² + (4ε·sinθ)²
  对 sin²θ 做直线：截距开方 → 尺寸，斜率开方 /4 → 应变。
  用**平方形式**而不是教材的线性形式，是因为尺寸/应变按方差合成
  （Gaussian 世界）；线性形式会系统高估尺寸（实测约 +50%）。

仪器展宽（LaB6/Si 标样）必须先扣掉，否则小晶粒会被标样的宽度淹没：
* `combine="quadrature"`（默认，Gaussian 口径）：β² = β_obs² − β_inst²
* `combine="linear"`（Lorentzian 口径）：β = β_obs − β_inst
"""
from dataclasses import dataclass, field

import numpy as np


def scherrer_size_nm(fwhm_deg, two_theta_deg, *, wavelength=1.5406,
                     K=0.9):
    """Scherrer 尺寸（nm）：D = K·λ/(β·cosθ)，β 为弧度。"""
    beta = np.radians(np.asarray(fwhm_deg, dtype=float))
    theta = np.radians(np.asarray(two_theta_deg, dtype=float) / 2.0)
    if np.any(beta <= 0):
        raise ValueError("FWHM 必须为正")
    size_ang = K * float(wavelength) / (beta * np.cos(theta))
    return size_ang / 10.0


@dataclass
class WilliamsonHallResult:
    size_nm: float
    strain: float
    intercept: float
    slope: float
    x: np.ndarray                 # sin²θ
    y: np.ndarray                 # (β cosθ)²，rad²
    beta_sample_deg: np.ndarray
    warning: str = ""
    params: dict = field(default_factory=dict)


def williamson_hall(centers_deg, fwhm_deg, *, wavelength=1.5406, K=0.9,
                    instrument_fwhm_deg=None, combine="quadrature"):
    """从一组峰的 2θ 与实测 FWHM 做 W-H 分析。

    参数：
        centers_deg, fwhm_deg   峰位（度）与实测 FWHM（度），同长
        wavelength              波长 Å
        K                       Scherrer 常数（默认 0.9）
        instrument_fwhm_deg     同长度的仪器 FWHM（度）；None = 不校正
        combine                 quadrature / linear（默认 quadrature）

    返回 `WilliamsonHallResult`；截距/斜率为负时 size/strain 记 NaN 并
    在 warning 里说明（数据或校正不足时会出现）。
    """
    c = np.asarray(centers_deg, dtype=float)
    b = np.asarray(fwhm_deg, dtype=float)
    if c.shape != b.shape or len(c) < 3:
        raise ValueError("至少 3 根峰，且 centers/fwhm 同长")
    if np.any(b <= 0):
        raise ValueError("FWHM 必须为正")
    if instrument_fwhm_deg is not None:
        bi = np.asarray(instrument_fwhm_deg, dtype=float)
        if bi.shape != b.shape:
            raise ValueError("instrument_fwhm_deg 必须与 fwhm 同长")
        if combine == "quadrature":
            b = np.sqrt(np.maximum(b * b - bi * bi, 0.0))
        elif combine == "linear":
            b = np.maximum(b - bi, 1e-12)
        else:
            raise ValueError("combine 取 quadrature / linear")
    theta = np.radians(c / 2.0)
    x = np.sin(theta) ** 2
    y = (np.radians(b) * np.cos(theta)) ** 2
    slope, intercept = np.polyfit(x, y, 1)
    warn = []
    if intercept <= 0:
        size_nm = float("nan")
        warn.append("截距 ≤ 0：尺寸不可辨识（峰宽没有随角度收敛）")
    else:
        size_nm = K * float(wavelength) / np.sqrt(intercept) / 10.0
    if slope < 0:
        strain = float("nan")
        warn.append("斜率 < 0：应变不可辨识（峰宽随角度下降）")
    else:
        strain = float(np.sqrt(slope) / 4.0)
    return WilliamsonHallResult(
        size_nm=float(size_nm), strain=float(strain),
        intercept=float(intercept), slope=float(slope),
        x=x, y=y, beta_sample_deg=np.asarray(b, dtype=float),
        warning="；".join(warn),
        params={"size_nm": float(size_nm), "strain": float(strain),
                "intercept": float(intercept), "slope": float(slope),
                "K": float(K), "wavelength": float(wavelength),
                "combine": combine})
