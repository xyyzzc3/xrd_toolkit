"""GSAS-II 同口径的峰宽与峰形（2026-10-22 起，拟合/精修的主引擎）。

`profile.py` 是第七周的"教学版"：峰顶归一、尺寸/应变按 Gaussian 并进方差。
本模块把公式换成 GSAS-II 的 PXC 口径，只为两条硬指标：

1. **面积不变**（area invariance）
   每条反射的积分面积 = m·|F|²·LP·比例因子，与峰宽无关。峰变宽只
   会变矮（面积守恒），不会"又宽又高"。对拍/拟合时，比例因子才能
   同时管住峰高与峰间底部。

2. **峰宽一致**（width consistency）
   U/V/W/X/Y/Z、Size、Mustrain 与 GSAS-II 的
   `GetReflSigGamCW` / `GetSampleSigGam` 同单位、同公式；同一个数字在
   两边给出同一条 FWHM(2θ) 曲线。

单位表（与 GSAS-II 的 PXC 仪器参数文件一致）：

    U, V, W   centideg²       sig = U·tan²θ + V·tanθ + W
    X, Y, Z   centideg        gam = X/cosθ + Y·tanθ + Z
    Size      µm              Sgam = 1.8·λ/(π·Size·cosθ)
    Mustrain  GSAS 单位        Mgam = 0.018·Mustrain·tanθ/π
    size_mix, strain_mix      0 = 全 Gaussian，1 = 全 Lorentzian
                              （GSAS-II 默认 1，尺寸/应变都放 Lorentzian）

与 `profile.py` 的换算（旧参数 → GSAS 参数）：

    W_G = w·1803.3      （w 是 deg² 的 Gaussian FWHM²）
    U_G = u·1803.3, V_G = v·1803.3
    X_G = x·100, Y_G = y·100
    Size[µm] = size_nm/1000
    Mustrain = 4·ε·10⁶  （ε = Δd/d；GSAS 的 Mustrain 是这个口径）

两个容易踩的坑（2026-10-22 用真 GSAS-II 实测确认）：

* GSAS 的 Size/Mustrain 结构是 [i, a, **mx**]，第三个元素是 LG 混合
  系数，不是尺寸！把它误设成尺寸会让"中性化"变成额外展宽。
* GSAS 的峰形函数（`getFCJVoigt3`）**按面积归一**（积分 = 0.01/度），
  不是峰顶归一；这正是"面积不变"的实现方式。

已知简化：不建模 SH/L 轴向发散（GSAS 默认 0.002，实测对峰宽影响
<0.2%）、不建模 Kα2 双线（对比数据是同步辐射单波长）、不建模吸收/
消光。这些都可以以后加。
"""
import math

import numpy as np

from xrd_toolkit.simulation import core
from xrd_toolkit.simulation.profile import tch_fwhm_eta

# GSAS-II 的常量：ateln2 = 8·ln2（注意不是 4·ln2）
ATELN2 = 8.0 * math.log(2.0)

# 旧口径（deg²）→ GSAS 口径（centideg²）的换算因子
W_CONV = (100.0 / (2.0 * math.sqrt(2.0 * math.log(2.0)))) ** 2   # 1803.3
# 旧 Lorentzian 宽（deg）→ GSAS X/Y（centideg）
GAM_CONV = 100.0


def _theta(tt_deg):
    return np.radians(np.asarray(tt_deg, dtype=float) / 2.0)


def sample_sig_gam(tt_deg, wavelength=1.5406, *, size_um=None,
                   mustrain=None, size_mix=1.0, strain_mix=1.0):
    """样品微结构对 (sig, gam) 的贡献，GSAS-II `GetSampleSigGam` 同式。

    返回 (sig, gam)，单位分别是 centideg² 与 centideg。
      * 尺寸：Sgam = 1.8·λ/(π·Size·cosθ)（Size 用 µm；系数 1.8/π
        等价于 Scherrer 公式取 K = 1.0）
      * 应变：Mgam = 0.018·Mustrain·tanθ/π（Mustrain = 4ε·10⁶）
      * LG 混合：gam 取 mx 份、sig 取 (1−mx)²/ateln2 份——GSAS 原式
    """
    theta = _theta(tt_deg)
    costh = np.cos(theta)
    tanth = np.tan(theta)
    if size_um is not None and float(size_um) > 0:
        s_gam = 1.8 * float(wavelength) / (math.pi * float(size_um) * costh)
    else:
        s_gam = np.zeros_like(costh)
    if mustrain is not None and float(mustrain) > 0:
        m_gam = 0.018 * float(mustrain) * tanth / math.pi
    else:
        m_gam = np.zeros_like(tanth)
    gam = s_gam * float(size_mix) + m_gam * float(strain_mix)
    sig = ((s_gam * (1.0 - float(size_mix))) ** 2
           + (m_gam * (1.0 - float(strain_mix))) ** 2) / ATELN2
    return sig, gam


def instrument_sig_gam(tt_deg, *, u=0.0, v=0.0, w=0.0,
                       x=0.0, y=0.0, z=0.0):
    """仪器对 (sig, gam) 的贡献，GSAS-II `GetReflSigGamCW` 同式。

    sig 单位 centideg²，gam 单位 centideg。这里给**未夹紧**的原值；
    GSAS 是在仪器 + 样品求和后才把两者夹到 0.001（见
    `reflection_sig_gam`），顺序不能反。
    """
    theta = _theta(tt_deg)
    tanth = np.tan(theta)
    costh = np.cos(theta)
    sig = float(u) * tanth ** 2 + float(v) * tanth + float(w)
    gam = float(x) / costh + float(y) * tanth + float(z)
    return sig, gam


def reflection_sig_gam(tt_deg, wavelength=1.5406, *, u=0.0, v=0.0, w=0.0,
                       x=0.0, y=0.0, z=0.0, size_um=None, mustrain=None,
                       size_mix=1.0, strain_mix=1.0):
    """一条反射的总 (sig, gam)：仪器 + 样品，GSAS-II 同口径（求和后夹紧）。"""
    sig_i, gam_i = instrument_sig_gam(tt_deg, u=u, v=v, w=w, x=x, y=y, z=z)
    sig_s, gam_s = sample_sig_gam(
        tt_deg, wavelength, size_um=size_um, mustrain=mustrain,
        size_mix=size_mix, strain_mix=strain_mix)
    return np.maximum(sig_i + sig_s, 0.001), np.maximum(gam_i + gam_s, 0.001)


def sig_gam_to_fwhm_eta(sig, gam):
    """把 GSAS 的 (sig, gam) 换成 TCH 的总 FWHM（度）与混合度 η。

    Gaussian 的 FWHM = 2√(2ln2)·σ，σ = √sig/100；Lorentzian 的 FWHM =
    gam/100。两者再走 Thompson-Cox-Hastings 合成——与 GSAS-II 的
    FCJ 峰形实测同宽（2026-10-22 逐点核对，见 tests）。
    """
    fwhm_g = 2.0 * math.sqrt(2.0 * math.log(2.0)) * np.sqrt(
        np.maximum(np.asarray(sig, dtype=float), 0.0)) / 100.0
    fwhm_l = np.maximum(np.asarray(gam, dtype=float), 0.0) / 100.0
    return tch_fwhm_eta(fwhm_g, fwhm_l)


def pv_area_normalized(x, center, fwhm, eta):
    """**面积归一**的伪 Voigt：∫PV dx = 1（x 用度）。

    与 `profile.pseudo_voigt`（峰顶归一）的区别就在这里。面积：
        A_G = Γ·√(π/(4ln2))，A_L = Γ·π/2
        A   = Γ·[η·π/2 + (1−η)·√(π/(4ln2))]
    因此峰高 = 1/A ∝ 1/Γ——峰变宽必变矮，面积守恒。
    """
    fwhm = np.asarray(fwhm, dtype=float)
    eta = np.asarray(eta, dtype=float)
    z = (np.asarray(x, dtype=float) - center) / fwhm
    gauss = np.exp(-4.0 * math.log(2.0) * z * z)
    lorentz = 1.0 / (1.0 + 4.0 * z * z)
    shape = eta * lorentz + (1.0 - eta) * gauss
    area = fwhm * (eta * math.pi / 2.0
                   + (1.0 - eta) * math.sqrt(math.pi / (4.0 * math.log(2.0))))
    return shape / area


def polarization(tth_deg, polariz=0.5, azimuth_deg=0.0):
    """GSAS-II `Polarization` 同式（Azm=0 时 = (1−P)cos²2θ + P）。"""
    cazm = math.cos(math.radians(azimuth_deg)) ** 2
    sazm = math.sin(math.radians(azimuth_deg)) ** 2
    return ((1.0 - polariz) * cazm + polariz * sazm) * np.cos(
        np.radians(np.asarray(tth_deg, dtype=float))) ** 2 \
        + (1.0 - polariz) * sazm + polariz * cazm


def lorentz_factor(tth_deg):
    """GSAS-II 的 Lorentz 因子：1/(2·sin²θ·cosθ)（tth 用度）。"""
    theta = _theta(tth_deg)
    return 1.0 / (2.0 * np.sin(theta) ** 2 * np.cos(theta))


def lp_gsas(tth_deg, polariz=0.5):
    """GSAS 口径的 Lorentz-偏振积（相对强度用；常数因子可被比例吸收）。"""
    return polarization(tth_deg, polariz) * lorentz_factor(tth_deg)


def simulate_pattern_gsas(structure, *, wavelength=1.5406, zero_deg=0.0,
                          two_theta_range=(10.0, 80.0), step_deg=0.02,
                          u=0.0, v=0.0, w=18.033, x=0.0, y=0.0, z=0.0,
                          size_um=None, mustrain=None,
                          size_mix=1.0, strain_mix=1.0,
                          polariz=0.5, b_iso=None, md=None,
                          scale=100.0, normalize=True,
                          nmax=10, min_intensity=0.01):
    """GSAS-II 同口径的可调用函数：一组参数 → 一条面积守恒的模拟谱。

    参数单位见模块 docstring；默认 w=18.033 centideg² ↔ 0.1° FWHM
    （与旧 `profile.simulate_pattern` 的默认 0.1° 对齐）。`normalize=True`
    时整条谱按最大值缩放到 `scale`（画图用）；`normalize=False` 返回原始
    相对强度（拟合用，比例因子由拟合器自己解）。

    返回 (tth, intensity, peaks, is_hex)，与 `profile.simulate_pattern`
    同形，便于替换。
    """
    tth_sticks, inten_sticks, peaks, is_hex = core.pattern_from_structure(
        structure, wavelength=wavelength, two_theta_range=two_theta_range,
        nmax=nmax, min_intensity=min_intensity, b_iso=b_iso, md=md,
        polariz=polariz)

    lo, hi = float(two_theta_range[0]), float(two_theta_range[1])
    tth = np.arange(lo, hi + step_deg / 2.0, step_deg)
    curve = np.zeros_like(tth)
    if len(tth_sticks):
        shifted = tth_sticks + zero_deg
        sig, gam = reflection_sig_gam(
            shifted, wavelength, u=u, v=v, w=w, x=x, y=y, z=z,
            size_um=size_um, mustrain=mustrain,
            size_mix=size_mix, strain_mix=strain_mix)
        fwhm, eta = sig_gam_to_fwhm_eta(sig, gam)
        for tt, inten, fw, et in zip(shifted, inten_sticks, fwhm, eta):
            curve += inten * pv_area_normalized(tth, tt, fw, et)
    if normalize and curve.max() > 0 and scale:
        curve = curve / curve.max() * float(scale)
    return tth, curve, peaks, is_hex


# ── 旧参数 → GSAS 参数的小换算（脚本/拟合器共用） ─────────────────────
def legacy_to_gsas(*, u=0.0, v=0.0, w=0.0, x=0.0, y=0.0, size_nm=None,
                   strain=None):
    """把 `profile.py` 的旧参数换成 GSAS 口径的 dict。

    注意尺寸/应变的**模型也变了**（Gaussian → Lorentzian、K 0.9 → 1.0），
    这里只换单位，不承诺旧图逐位不变。
    """
    return {
        "u": float(u) * W_CONV,
        "v": float(v) * W_CONV,
        "w": float(w) * W_CONV,
        "x": float(x) * GAM_CONV,
        "y": float(y) * GAM_CONV,
        "size_um": None if size_nm is None else float(size_nm) / 1000.0,
        "mustrain": None if strain is None else 4.0e6 * float(strain),
    }
