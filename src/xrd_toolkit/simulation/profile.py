"""峰形与真实效应：把"理想晶体"的峰表铺成一条接近真实的连续谱。

2026-10-22 起：本模块是第七周的**教学版**（峰顶归一；尺寸/应变按
Gaussian 并进方差）。要向 GSAS-II 对齐（**面积守恒 + 峰宽同式**），
用同包的 `gsas_profile.simulate_pattern_gsas`；全谱拟合用 `fit.py`。
本模块保留是为了让第七周的图与测试逐位可复现。

RY04 第七周任务（2026-10-17）并入。分工：core 给**峰位与相对强度**
（理想晶体：多重性·|F|²·LP + 温度因子/择优取向可选），本模块给**峰形**
与**仪器/微结构展宽**——每个峰按自己的半高宽（FWHM）铺成伪 Voigt 曲线
再叠加成整条谱，最终交出任务要的"可调用函数"：

    simulate_pattern(结构, **一套参数)  →  (2θ 网格, 强度曲线, 峰表)

宽度模型（全部是教科书公式；参数名与 GSAS-II 对齐，方便对拍）：

  **仪器**（Caglioti 三系数 + Lorentzian 两系数，GSAS-II 同款写法）：
      Γ_G²(2θ) = U·tan²θ + V·tanθ + W          （Gaussian 部分）
      Γ_L(2θ)  = (X + Y·tanθ)/cosθ             （Lorentzian 部分）
  **样品微结构**：
      Scherrer 晶粒尺寸：Γ_size = K·λ/(D·cosθ)，K=0.9（尺寸越小峰越宽）
      微观应变：        Γ_strain = 4·ε·tanθ   （应变越高、高角度越宽）
  **合成**（Thompson-Cox-Hastings，GSAS-II 同一套组合公式）：
      Γ⁵ = Γ_G⁵ + 2.69269Γ_G⁴Γ_L + 2.42843Γ_G³Γ_L²
         + 4.47163Γ_G²Γ_L³ + 0.07842Γ_GΓ_L⁴ + Γ_L⁵
      η  = 1.36603q − 0.47719q² + 0.11116q³，q = Γ_L/Γ
      峰形 = η·Lorentzian + (1−η)·Gaussian（同一个 Γ，峰顶归一）

已知简化（写明白，免得当它是全真）：
  * 尺寸/应变展宽都按 Gaussian 并进 Γ_G 的平方和（本科 W-H 口径）；
    严格模型里尺寸偏 Lorentz、应变偏 Gauss（GSAS-II 用 LGmix 描述）；
  * 不建模吸收/表面粗糙度/轴向发散（SH/L）、K 固定 0.9；
  * 零点偏移只移峰位、不重算峰强（角度差远小于峰宽时该近似成立）。
"""
import math

import numpy as np

from xrd_toolkit.simulation import core

# Scherrer 常数：形状因子取 0.9（球状晶粒的常用值）
SCHERRER_K = 0.9


def caglioti_fwhm_deg(tt_deg, u=0.0, v=0.0, w=0.0):
    """仪器 Gaussian 展宽 Γ_G(2θ)，度：Γ_G² = U·tan²θ + V·tanθ + W。

    三个系数说人话（实验室衍射仪典型量级，GSAS-II 参数表同口径）：
      W —— 常数项：跟角度无关的那部分（狭缝高度等），0<W<4（单位 deg²）；
      V —— tanθ 项：常被精修成小的负值（−1<V<0 同步辐射常见）；
      U —— tan²θ 项：高角度占主导（探测器距离、样品穿透深度等），0<U<3。
    默认全 0 → 不展宽（配合 simulate_pattern 的 w 默认值走"常数 0.1°"）。
    """
    theta = np.radians(np.asarray(tt_deg, dtype=float) / 2.0)
    t = np.tan(theta)
    return np.sqrt(np.maximum(u * t * t + v * t + w, 0.0))


def lorentz_fwhm_deg(tt_deg, x=0.0, y=0.0):
    """仪器 Lorentzian 展宽 Γ_L(2θ)，度：(X + Y·tanθ)/cosθ（GSAS-II 写法）。

    X、Y 是 Lorentzian 系数：实验室 1<X<3、0<Y<3，同步辐射更小。
    默认 0 = 纯 Gaussian 峰形。
    """
    theta = np.radians(np.asarray(tt_deg, dtype=float) / 2.0)
    return np.maximum((x + y * np.tan(theta)) / np.cos(theta), 0.0)


def scherrer_fwhm_deg(tt_deg, size_nm, wavelength=1.5406):
    """晶粒尺寸展宽（Scherrer），度：Γ = K·λ/(D·cosθ)。

    物理：晶粒有限 ⇒ 相干衍射的晶面层数有限 ⇒ 倒空间斑点变胖 ⇒ 峰变宽。
    尺寸越小、峰越宽；同样的 D 在越高角度越宽（1/cosθ）。
    size_nm 单位**纳米**（GSAS-II 里是微米——对拍时注意 ×1000）。
    """
    theta = np.radians(np.asarray(tt_deg, dtype=float) / 2.0)
    d_angstrom = float(size_nm) * 10.0
    return np.degrees(SCHERRER_K * wavelength / (d_angstrom * np.cos(theta)))


def strain_fwhm_deg(tt_deg, strain):
    """微观应变展宽，度：Γ = 4·ε·tanθ。

    物理：晶格畸变的统计分布让 d 值有分布 ⇒ Δ(2θ) ∝ tanθ（布拉格微分），
    所以**高角度**峰展宽得最明显——这正是 W-H 分析能把"尺寸"和"应变"
    分开的原因（尺寸按 1/cosθ、应变按 tanθ，两者对 θ 的依赖不同）。
    """
    theta = np.radians(np.asarray(tt_deg, dtype=float) / 2.0)
    return np.degrees(4.0 * float(strain) * np.tan(theta))


def tch_fwhm_eta(fwhm_g, fwhm_l):
    """Thompson-Cox-Hastings 合成：把 Gaussian/Lorentzian 两组 FWHM 合成
    一个总 Γ 与混合度 η（GSAS-II 同一套公式）。两者都为 0 → 返回 Γ=0、η=0。
    """
    fg = np.asarray(fwhm_g, dtype=float)
    fl = np.asarray(fwhm_l, dtype=float)
    total = (fg ** 5 + 2.69269 * fg ** 4 * fl + 2.42843 * fg ** 3 * fl ** 2
             + 4.47163 * fg ** 2 * fl ** 3 + 0.07842 * fg * fl ** 4
             + fl ** 5) ** 0.2
    with np.errstate(divide="ignore", invalid="ignore"):
        q = np.where(total > 0, fl / np.where(total > 0, total, 1.0), 0.0)
    eta = 1.36603 * q - 0.47719 * q ** 2 + 0.11116 * q ** 3
    return total, eta


def pseudo_voigt(x, center, fwhm, eta):
    """峰顶归一的伪 Voigt：η·L + (1−η)·G（同一个 FWHM，中心处都是 1）。

    L = 1/(1+4((x−c)/Γ)²)（Lorentzian，尾巴长）；G = exp(−4ln2·((x−c)/Γ)²)
    （Gaussian，尾巴掉得快）。峰顶归一意味着"乘上强度就是峰高"。
    """
    z = (np.asarray(x, dtype=float) - center) / fwhm
    gauss = np.exp(-4.0 * math.log(2.0) * z * z)
    lorentz = 1.0 / (1.0 + 4.0 * z * z)
    return eta * lorentz + (1.0 - eta) * gauss


def profile_fwhm_eta(tt_deg, *, u=0.0, v=0.0, w=0.0, x=0.0, y=0.0,
                     size_nm=None, strain=None, wavelength=1.5406):
    """在 2θ 处，把四个展宽来源合成成 (总 FWHM, η)（内部与测试共用一把尺）。

    Γ_G² = U·tan²θ + V·tanθ + W + Γ_size² + Γ_strain²（见模块 docstring
    的"已知简化"）；Γ_L = (X+Y·tanθ)/cosθ；两者再走 TCH 合成。
    """
    fg2 = np.asarray(caglioti_fwhm_deg(tt_deg, u, v, w), dtype=float) ** 2
    if size_nm is not None and float(size_nm) > 0:
        fg2 = fg2 + scherrer_fwhm_deg(tt_deg, size_nm, wavelength) ** 2
    if strain is not None and float(strain) > 0:
        fg2 = fg2 + strain_fwhm_deg(tt_deg, strain) ** 2
    return tch_fwhm_eta(np.sqrt(fg2), lorentz_fwhm_deg(tt_deg, x, y))


def simulate_pattern(structure, *, wavelength=1.5406, zero_deg=0.0,
                     two_theta_range=(10.0, 80.0), step_deg=0.02,
                     u=0.0, v=0.0, w=0.01, x=0.0, y=0.0,
                     size_nm=None, strain=None, b_iso=None, md=None,
                     scale=100.0, nmax=10, min_intensity=0.01):
    """**本周的可调用函数**：一组参数 → 一条接近真实的模拟谱。

    参数（都可留默认——不传时退化成"常数 0.1° 展宽的理想晶体"）：
        structure        pymatgen Structure（CIF 用 core.load_structure 读）
        wavelength       波长 Å（Cu Kα 1.5406；Mo Kα 0.7093）
        zero_deg         仪器零点偏移（°）：整条谱平移，正 = 峰位整体右移
        two_theta_range  2θ 起止（°，同实测范围）
        step_deg         步长（°）：与实验一致才能逐点比
        u, v, w          仪器 Gaussian（Caglioti，deg²/deg²/deg²，见 caglioti_fwhm_deg）
        x, y             仪器 Lorentzian（见 lorentz_fwhm_deg；默认 0 = 纯 Gauss）
        size_nm          平均晶粒尺寸（nm）→ Scherrer 展宽；None = 无限大
        strain           微观应变 ε（Δd/d）→ 4ε·tanθ 展宽；None = 无
        b_iso            温度因子 B（Å²）：float 或 {元素: B}；None = 刚性晶格
        md               择优取向 (轴 hkl, R)（见 core.march_dollase）；None = 无
        scale            归一化上限（默认峰顶 100，与 core 的 % 口径一致）

    返回 (tth, intensity, peaks, is_hex)：
        tth, intensity   细网格上的连续曲线（绘图/对比用）
        peaks            理想峰表（[2θ, 强度%, [(hkl, m), …]]，标注用）
        is_hex           是否六方（指数排版用）

    迭代方向（下周精修）：这就是要喂给最小二乘的函数本体——
    θ = (zero, u, v, w, size, strain, …) → 曲线，与实验逐点比 Rwp。
    """
    tth_sticks, inten_sticks, peaks, is_hex = core.pattern_from_structure(
        structure, wavelength=wavelength, two_theta_range=two_theta_range,
        nmax=nmax, min_intensity=min_intensity, b_iso=b_iso, md=md)

    lo, hi = float(two_theta_range[0]), float(two_theta_range[1])
    tth = np.arange(lo, hi + step_deg / 2.0, step_deg)
    curve = np.zeros_like(tth)
    if len(tth_sticks):
        shifted = tth_sticks + zero_deg          # 零点偏移：整条谱平移
        fwhm, eta = profile_fwhm_eta(
            shifted, u=u, v=v, w=w, x=x, y=y,
            size_nm=size_nm, strain=strain, wavelength=wavelength)
        for tt, inten, fw, et in zip(shifted, inten_sticks, fwhm, eta):
            if fw <= 0:
                continue                          # 零宽 = 没开的峰形通道
            curve += inten * pseudo_voigt(tth, tt, fw, et)
    if curve.max() > 0 and scale:
        curve = curve / curve.max() * float(scale)
    return tth, curve, peaks, is_hex
