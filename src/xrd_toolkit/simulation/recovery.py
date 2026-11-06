"""已知 wt% 混合物的回收实验（2026-11-06，定量验证 A）。

目的不是给用户加功能，而是回答"wt% 到底准不准"：

  * **公式本身**：真模型 + 真背景时，回收值必须回到已知配比（脚本用
    `background="oracle"`）。
  * **生产背景处理的偏差**：原位管线先用滑动基线估背景，再在固定背景上
    加 0.25° 分段修正和曲率罚项（`insitu._fit_once`）。背景估计误差会
    和两相的峰重叠耦合，可能系统性拉偏 wt%——本模块负责量化它。
  * **线性 esd 是不是下限**：`FitResult.linear_cov` 只含线性块（各相
    scale、强度修正、背景），晶胞/峰形的不确定度不在内。用多个噪声
    种子算实际离散度，和 `weight_percent_esd` 比，就能回答这个问题。

合成口径与拟合器逐点一致：峰位来自 `core.pattern_from_structure`
（normalize=False 的原始 m·|F|²LP），峰形用面积守恒伪 Voigt
（`pv_area_normalized`），晶胞/温度因子/微结构项都走 GSAS-II 同式。
因此"真背景"档的回收误差应当只来自数值精度（<0.1 wt%）。

注意：本模块是开发/验证工具，不接 GUI。GSAS-II 对照只作为开发参考，
不出现在界面文案里。
"""
from dataclasses import dataclass
import math

import numpy as np

from xrd_toolkit.simulation import core
from xrd_toolkit.simulation.fit import fit_pattern
from xrd_toolkit.simulation.gsas_profile import (
    pv_area_normalized, reflection_sig_gam, sig_gam_to_fwhm_eta)
from xrd_toolkit.simulation.quantitative import phase_mass_factor


@dataclass
class MixturePhase:
    """一个参与合成的相：结构 + 目标重量分数 + 峰形/温度因子。"""
    structure: object
    weight_percent: float
    label: str
    b_iso: object = None        # float 或 None；与拟合器口径一致
    size_um: float = 1.0e4      # 无限大晶粒（无尺寸展宽）
    mustrain: float = 0.0
    md: tuple = None


def phase_curve(structure, grid, *, wavelength, zero_deg=0.0,
                u=0.0, v=0.0, w=0.0, x=0.0, y=0.0, z=0.0,
                size_um=None, mustrain=None, size_mix=1.0, strain_mix=1.0,
                b_iso=None, md=None, polariz=0.5, nmax=10,
                two_theta_range=None):
    """单相在给定 2θ 网格上的原始（未归一化）强度曲线。

    与 `gsas_profile.simulate_pattern_gsas(raw=True)` 同一套数学，区别是
    直接落在调用方的网格上——原位数据的网格是 3000 点 linspace，不是
    arange，插值会把 0.007° 宽的峰插歪。返回 m·|F|²LP 卷积伪 Voigt 后
    的曲线（绝对相对强度，比例因子由调用方给）。
    """
    grid = np.asarray(grid, dtype=float)
    if two_theta_range is None:
        two_theta_range = (float(grid[0]), float(grid[-1]))
    tt, inten, _peaks, _is_hex = core.pattern_from_structure(
        structure, wavelength=wavelength, two_theta_range=two_theta_range,
        nmax=nmax, min_intensity=0.0, b_iso=b_iso, md=md,
        polariz=polariz, normalize=False)
    tt = np.asarray(tt, dtype=float) + float(zero_deg)
    inten = np.asarray(inten, dtype=float)
    curve = np.zeros_like(grid)
    if not len(tt):
        return curve
    sig, gam = reflection_sig_gam(
        tt, wavelength, u=u, v=v, w=w, x=x, y=y, z=z,
        size_um=size_um, mustrain=mustrain,
        size_mix=size_mix, strain_mix=strain_mix)
    fwhm, eta = sig_gam_to_fwhm_eta(sig, gam)
    for center, amp, fw, et in zip(tt, inten, fwhm, eta):
        curve += amp * pv_area_normalized(grid, center, fw, et)
    return curve


def simulate_mixture(phases, grid, *, wavelength, background=0.0,
                     peak_max=None, noise_scale=0.0, seed=0,
                     profile=None):
    """按已知 wt% 合成一条混合谱，返回 (y_true, y_obs)。

    各相振幅 S_p 由 Rietveld 关系反推：

        wt_p = S_p·(Z_p·M_p·V_p) / Σ_q S_q·(Z_q·M_q·V_q)
        ⇒ S_p ∝ wt_p / (Z_p·M_p·V_p)

    所以直接取 S_p = wt_p / (Z_p·M_p·V_p)，再乘常数缩放不改变配比。
    `peak_max` 给定时把整条相曲线缩放到该峰高（计数口径）；`noise_scale`
    是噪声相对 Poisson 的倍数（1.0 = √N Poisson；本批实测数据的点间
    噪声约 0.11–0.15，见 `scripts/quantitative_recovery.py` 的注释）。
    背景可以是标量或与 grid 等长的数组。
    """
    grid = np.asarray(grid, dtype=float)
    profile = dict(profile or {})
    curves = []
    raw_scales = []
    for phase in phases:
        curve = phase_curve(
            phase.structure, grid, wavelength=wavelength,
            b_iso=phase.b_iso, size_um=phase.size_um,
            mustrain=phase.mustrain, md=phase.md, **profile)
        z, molar, vol, warn = phase_mass_factor(phase.structure)
        if warn or not all(math.isfinite(v) and v > 0
                           for v in (z, molar, vol)):
            raise ValueError(f"相 {phase.label} 的 Z/M/V 不可用：{warn}")
        curves.append(curve)
        raw_scales.append((float(phase.weight_percent) / 100.0)
                          / (z * molar * vol))
    phase_sum = np.zeros_like(grid)
    for curve, scale in zip(curves, raw_scales):
        phase_sum += scale * curve
    if peak_max is not None and phase_sum.max() > 0:
        phase_sum = phase_sum * (float(peak_max) / phase_sum.max())
    y_true = phase_sum + np.asarray(background, dtype=float)
    if noise_scale and noise_scale > 0:
        rng = np.random.default_rng(seed)
        sigma = float(noise_scale) * np.sqrt(np.maximum(y_true, 1.0))
        y_obs = y_true + rng.normal(0.0, sigma)
        y_obs = np.maximum(y_obs, 0.0)
    else:
        y_obs = y_true.copy()
    return y_true, y_obs


def recovery_fit(main, extra, tth, intensity, *, wavelength, instrument,
                 main_cell, extra_cell, background="estimated",
                 background_window=0.2, main_label="LMFP_rich",
                 extra_label="LMFP_poor", main_b_iso=0.0,
                 extra_b_iso=0.5, main_size_um=0.093, extra_size_um=0.1,
                 intensity_poly=0, background_knots=0.25,
                 background_smooth=0.1, cell_bounds=None, max_nfev=120,
                 background_model="estimated", background_order=10):
    """按原位生产口径拟合两相混合物（仪器固定，zero + 两相晶胞自由）。

    与 `insitu._fit_once` 的差异只有 Al 相（回收实验先做两相；Al 是参考
    相、不进 LMFP 分母）。`background` 可以是 "estimated"（生产背景）、
    "none"（无背景）或一个与观测等长的背景数组（验证公式时给已知背景）。

    `background_model="chebyshev"` 时忽略上面的 `background`，改用
    与峰联合拟合的 Chebyshev 背景（阶数 `background_order`）；这是
    2026-11-06 定量验证里的候选改进，默认仍是生产口径 "estimated"。
    """
    model = str(background_model)
    if model == "chebyshev":
        bg_kwargs = {"background": int(background_order)}
        bg_smooth = 0.0
    elif model == "estimated":
        if isinstance(background, str):
            if background == "estimated":
                from xrd_toolkit.services.background import (
                    estimate_baseline_sliding)
                bg = estimate_baseline_sliding(tth, intensity,
                                               background_window)
            elif background == "none":
                bg = None
            else:
                raise ValueError(f"未知 background 模式：{background}")
        else:
            bg = np.asarray(background, dtype=float)
        bg_kwargs = {"background": bg}
        if bg is not None:
            bg_kwargs["background_knots"] = background_knots
        bg_smooth = background_smooth
    else:
        raise ValueError(f"未知背景模型：{model}")
    instrument = dict(instrument)
    main_abc = tuple(float(v) for v in main.lattice.abc)
    extra_abc = tuple(float(v) for v in extra.lattice.abc)
    main_cubic = max(main_abc) - min(main_abc) < 1e-4
    extra_cubic = max(extra_abc) - min(extra_abc) < 1e-4
    main_keys = ("a",) if main_cubic else ("a", "b", "c")
    extra_keys = (f"{extra_label}:a",) if extra_cubic else (
        f"{extra_label}:a", f"{extra_label}:b", f"{extra_label}:c")
    init = {key: float(instrument[key])
            for key in ("zero", "u", "v", "w", "x", "y")}
    init.update({
        "a": float(main_cell[0]), "b": float(main_cell[1]),
        "c": float(main_cell[2]),
        "size_um": float(main_size_um), "mustrain": 0.0,
        "b_iso": float(main_b_iso),
        f"{extra_label}:a": float(extra_cell[0]),
        f"{extra_label}:b": float(extra_cell[1]),
        f"{extra_label}:c": float(extra_cell[2]),
    })
    extra_spec = {
        "structure": extra, "label": extra_label,
        "a": float(extra_cell[0]), "b": float(extra_cell[1]),
        "c": float(extra_cell[2]),
        "size_um": float(extra_size_um), "mustrain": 0.0,
        "b_iso": float(extra_b_iso), "refine_cell": True,
        "refine_intensities": False,
    }
    return fit_pattern(
        main, tth, intensity, wavelength=wavelength,
        b_iso=float(main_b_iso), **bg_kwargs,
        background_smooth=bg_smooth, weights="poisson",
        cell_refine=True, intensity_poly=intensity_poly,
        extra_phases=[extra_spec], phase_label=main_label,
        free=("zero",) + main_keys,
        initial=init,
        stages=[("zero",) + main_keys + extra_keys],
        bounds=(cell_bounds or {}), max_nfev=max_nfev)


def weight_percent_esd(result):
    """从线性协方差传播各相 wt% 的 esd；返回 {label: esd}。

    只含线性块（scale/强度修正/背景）的协方差，因此是**下限**：晶胞、
    峰形等非线性参数的不确定度不在内。Le Bail 相（逐反射强度）没有单一
    scale 参数，返回 NaN。
    """
    rows = list(getattr(result, "phase_weights", None) or [])
    names = list(getattr(result, "linear_names", None) or [])
    cov = getattr(result, "linear_cov", None)
    if not rows or not names or cov is None:
        return {row.label: float("nan") for row in rows}
    cov = np.asarray(cov, dtype=float)
    out = {}
    masses = []
    for row in rows:
        mass = (float(row.scale) * float(row.z) * float(row.molar_mass)
                * float(row.volume))
        masses.append(mass if math.isfinite(mass) and mass > 0
                      else float("nan"))
    total = float(np.nansum(masses))
    # 每个相 → (线性参数名, dS_raw/dparam, F=ZMV)
    links = []
    for row in rows:
        if row.source != "structure" or not (total > 0):
            links.append(None)
            continue
        if row.label == getattr(result, "phase_label", None):
            param, norm = "scale", float(result.peak_table_norm)
        else:
            param = f"{row.label}:scale"
            norm = float((result.extra_phase_norm or {})
                         .get(row.label, float("nan")))
        if param not in names or not math.isfinite(norm) or norm == 0:
            links.append(None)
            continue
        links.append((names.index(param), 100.0 / norm,
                      float(row.z) * float(row.molar_mass)
                      * float(row.volume)))
    for i, row in enumerate(rows):
        link = links[i]
        mass_i = masses[i]
        if link is None or not math.isfinite(mass_i):
            out[row.label] = float("nan")
            continue
        jac = []
        for j, other in enumerate(rows):
            if masses[j] is None or not math.isfinite(masses[j]):
                continue
            other_link = links[j]
            if other_link is None:
                continue
            idx, dS, f_other = other_link
            d_mass = (float(dS) * f_other)
            d_wt = (100.0 * ((d_mass if j == i else 0.0) * total
                             - mass_i * d_mass) / total ** 2)
            jac.append((idx, d_wt))
        var = 0.0
        for a, da in jac:
            for b, db in jac:
                var += da * db * float(cov[a, b])
        out[row.label] = (math.sqrt(var) if math.isfinite(var)
                          and var >= 0 else float("nan"))
    return out


def summarize_recovery(rows, truth_key="target_wt", got_key="recovered_wt"):
    """回收表的偏差统计：{n, bias, rms, max_abs, slope}。"""
    truth = np.asarray([float(r[truth_key]) for r in rows], dtype=float)
    got = np.asarray([float(r[got_key]) for r in rows], dtype=float)
    good = np.isfinite(truth) & np.isfinite(got)
    truth, got = truth[good], got[good]
    if truth.size == 0:
        return {"n": 0, "bias": float("nan"), "rms": float("nan"),
                "max_abs": float("nan"), "slope": float("nan")}
    diff = got - truth
    slope = (float(np.polyfit(truth, got, 1)[0])
             if truth.size >= 2 and np.ptp(truth) > 0 else float("nan"))
    return {
        "n": int(truth.size),
        "bias": float(np.mean(diff)),
        "rms": float(np.sqrt(np.mean(diff ** 2))),
        "max_abs": float(np.max(np.abs(diff))),
        "slope": slope,
    }
