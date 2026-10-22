"""全谱最小二乘拟合（2026-10-22，向 GSAS-II 口径看齐的"精修本体"）。

第七周只做到"一组参数 → 一条谱"；本模块把方向反过来：给一条实验谱，
解出让模拟谱最像实验的那组参数。目标函数与 GSAS-II 一样是加权最小
二乘（Rwp），峰形用 `gsas_profile` 的面积守恒伪 Voigt，所以峰宽变化
时峰面积守恒——这正是能同时拟合"峰顶"和"峰间底部"的前提。

模型（逐点）：

    y_calc(x) = scale · Σ_k I_k · PV_area(x; 2θ_k, Γ_k, η_k)
                + B(x)

    I_k       = m_k·|F_k|²·LP(θ_k)（来自 core 的峰表，面积口径）
    Γ_k, η_k  由 U/V/W/X/Y/Z + Size/Mustrain 按 GSAS-II 公式给出
    B(x)      背景：Chebyshev 多项式（或外部固定数组）
    scale     比例因子

**scale 与背景系数是线性参数**：每步非线性参数给定时，用加权线性
最小二乘一次解出（变量投影），所以优化器只管峰形参数，收敛更稳。

用法（最小）：

    from xrd_toolkit.simulation.fit import fit_pattern
    res = fit_pattern(structure, tth, counts, wavelength=0.1223,
                      free=("zero", "u", "v", "w", "x", "y"),
                      background=6, weights="poisson")
    print(res.report())

拟合结果里的 `params` 是 GSAS 口径（U/V/W centideg²、X/Y/Z centideg、
Size µm、Mustrain = 4ε·10⁶），报告直接可与 GSAS-II 的参数表对照。
"""
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.optimize import least_squares

from xrd_toolkit.simulation import core
from xrd_toolkit.simulation.gsas_profile import (
    polarization,
    pv_area_normalized,
    reflection_sig_gam,
    sig_gam_to_fwhm_eta,
)

# 可优化的参数与默认边界（GSAS-II 口径）
DEFAULT_BOUNDS = {
    "zero": (-1.0, 1.0),
    "u": (-1.0e5, 1.0e5),
    "v": (-1.0e5, 1.0e5),
    "w": (0.0, 1.0e6),
    # X/Y 是 Lorentzian 宽，负值没有物理意义；负 Y 会变成"抑制尾巴"的
    # 非物理旋钮（实测会把峰间底部拉成 -20 counts）。需要时用 bounds 覆盖。
    # a/b/c 的默认边界会在 fit_pattern 里按 CIF 的初始晶胞覆盖（±1 Å）
    "a": (0.1, 1.0e3),
    "b": (0.1, 1.0e3),
    "c": (0.1, 1.0e3),
    "x": (0.0, 1.0e4),
    "y": (0.0, 1.0e4),
    "z": (-1.0e4, 1.0e4),
    "size_um": (1.0e-4, 1.0e6),
    "mustrain": (0.0, 1.0e7),
    "b_iso": (0.0, 50.0),
    "polariz": (0.0, 1.0),
}

# 默认分阶段（先标度/背景/零点/常数宽，再 Caglioti，再样品微结构，
# 再仪器 Lorentzian，最后温度因子/偏振）。**Size 放在 X/Y 之前**：
# 两者都长尾巴，先让尺寸项吃样品/基线留下的尾巴，避免 X/Y 用负值
# 去"抑制"尾巴（非物理，还会把峰间底部拉低）。
DEFAULT_STAGES = (
    ("zero", "w"),
    ("a", "b", "c"),
    ("u", "v", "w"),
    ("size_um", "mustrain"),
    ("x", "y"),
    ("b_iso",),
    ("polariz",),
)

_ALLOWED = set(DEFAULT_BOUNDS)


@dataclass
class FitResult:
    """一次全谱拟合的结果（数字 + 曲线 + 报告）。"""
    params: dict
    stderr: dict
    rwp: float
    rp: float
    chi2: float
    reduced_chi2: float
    n_obs: int
    n_params: int
    success: bool
    message: str
    tth: np.ndarray
    y_obs: np.ndarray
    y_calc: np.ndarray
    residual: np.ndarray
    background: np.ndarray
    scale: float
    background_coeffs: np.ndarray = field(default_factory=lambda: np.array([]))
    initial: dict = field(default_factory=dict)
    stages: tuple = ()
    peak_intensities: Optional[np.ndarray] = None
    peak_table_intensities: Optional[np.ndarray] = None
    peak_tth: Optional[np.ndarray] = None

    def report(self) -> str:
        """人读的参数表 + 拟合优度（英文，便于直接贴报告）。"""
        lines = [
            f"Rwp = {self.rwp:.3f}%   Rp = {self.rp:.3f}%   "
            f"chi2 = {self.chi2:.4g}   reduced chi2 = {self.reduced_chi2:.4g}",
            f"n_obs = {self.n_obs}   n_params = {self.n_params}   "
            f"success = {self.success}   ({self.message})",
            f"{'parameter':>10} {'initial':>12} {'fitted':>12} {'esd≈':>10}",
        ]
        for name in self.params:
            if name in ("scale", "size_mix", "strain_mix") or \
                    name.startswith("intensity_a") or ":" in name:
                continue
            init = self.initial.get(name, float("nan"))
            esd = self.stderr.get(name, float("nan"))
            lines.append(f"{name:>10} {init:12.6g} {self.params[name]:12.6g} "
                         f"{esd:10.3g}")
        lines.append(f"{'scale':>10} {'':>12} {self.scale:12.6g}")
        if (self.peak_intensities is not None
                and self.peak_table_intensities is not None):
            good = self.peak_table_intensities > 0.0
            if np.any(good):
                ratio = (self.peak_intensities[good]
                         / self.peak_table_intensities[good])
                lines.append(
                    f"Le Bail: {len(ratio)} per-peak intensities; "
                    f"fit/table ratio median {np.median(ratio):.3f} "
                    f"range {ratio.min():.2f}–{ratio.max():.2f}")
        a_names = [n for n in self.params if n.startswith("intensity_a")]
        if a_names:
            terms = " ".join(
                f"{'+' if self.params[n] >= 0 else '−'}"
                f"{abs(self.params[n]):.4f}·z^{i}"
                for i, n in enumerate(a_names, start=1))
            lines.append(f"intensity correction: 1 {terms}")
        labels = sorted({n.split(":", 1)[0] for n in self.params if ":" in n})
        for label in labels:
            p = self.params
            lines.append(
                f"extra phase {label}: scale={p.get(f'{label}:scale', float('nan')):.4g} "
                f"cell=({p.get(f'{label}:a', float('nan')):.4f}, "
                f"{p.get(f'{label}:b', float('nan')):.4f}, "
                f"{p.get(f'{label}:c', float('nan')):.4f}) "
                f"size={p.get(f'{label}:size_um', float('nan')):.4g} µm "
                f"B={p.get(f'{label}:b_iso', float('nan')):.3g}")
        if len(self.background_coeffs):
            lines.append(f"background: correction coefficients "
                         f"{np.array2string(self.background_coeffs, precision=4)}")
        if self.stages:
            lines.append("stages: " + " -> ".join("+".join(s) for s in self.stages))
        return "\n".join(lines)


class _PeakTable:
    """固定结构给出的反射表：位置、相对强度、s²（温度因子用）。

    强度在表内归一化到最强峰 = 100，绝对标度交给 scale。
    """

    def __init__(self, structure, wavelength, two_theta_range, nmax,
                 b_iso, polariz, keep_unmerged=False, md=None):
        self.a0, self.b0, self.c0 = (float(x) for x in structure.lattice.abc)
        self.cell_refine = bool(keep_unmerged)
        if keep_unmerged:
            hkl, d0, tth, inten = core.reflection_table(
                structure, wavelength=wavelength,
                two_theta_range=two_theta_range, nmax=nmax,
                b_iso=b_iso, md=md, polariz=polariz)
            self.hkl = np.asarray(hkl, dtype=int)
            self.d0 = np.asarray(d0, dtype=float)
            self.orthorhombic = all(
                abs(float(a) - 90.0) < 1e-3 for a in structure.lattice.angles)
        else:
            tth, inten, _peaks, _is_hex = core.pattern_from_structure(
                structure, wavelength=wavelength,
                two_theta_range=two_theta_range, nmax=nmax,
                min_intensity=0.0, b_iso=b_iso, md=md, polariz=polariz)
            self.hkl = None
            self.d0 = None
            self.orthorhombic = False
        self.tth = np.asarray(tth, dtype=float)
        inten = np.asarray(inten, dtype=float)
        self.inten = (inten / inten.max() * 100.0
                      if inten.size and inten.max() > 0 else inten)
        theta = np.radians(self.tth / 2.0)
        self.s2 = (np.sin(theta) / float(wavelength)) ** 2
        self.pol_base = polarization(self.tth, polariz)
        self.b_iso_base = None if isinstance(b_iso, dict) else b_iso


def _measure_observed_fwhm(tth, y, center, span):
    """在 center ± span 内量观测峰的高度半宽（局部基线以上的半高）。"""
    m = (tth >= center - span) & (tth <= center + span)
    if int(m.sum()) < 5:
        return None
    xs, ys = tth[m], y[m]
    i = int(np.argmax(ys))
    base = float(np.percentile(ys, 10))
    height = float(ys[i]) - base
    if height <= 0:
        return None
    half = base + 0.5 * height
    left = right = None
    for j in range(i - 1, 0, -1):
        if ys[j] <= half <= ys[j + 1]:
            left = float(np.interp(half, [ys[j], ys[j + 1]],
                                   [xs[j], xs[j + 1]]))
            break
    for j in range(i + 1, len(ys)):
        if ys[j] <= half <= ys[j - 1]:
            right = float(np.interp(half, [ys[j], ys[j - 1]],
                                    [xs[j], xs[j - 1]]))
            break
    if left is None or right is None or not (0.0 < right - left < 2.0 * span):
        return None
    return right - left


def _estimate_initial_profile(tth, y, table):
    """从观测谱自动估 (zero, U, V, W) 初值。

    做法：取峰表里最强的若干条**孤立**反射，量观测 FWHM，然后按
    GSAS 的 Gaussian 方差形式 FWHM² = U·tan²θ+V·tanθ+W（centideg²）
    做一次加权线性最小二乘。这是给非线性优化一个物理上靠谱的起点；
    洛伦兹项（X/Y）仍从 0 起，后面单独一阶段放开。
    """
    zero = 0.0
    if len(table.tth):
        idx = int(np.argmax(table.inten))
        c0 = table.tth[idx]
        m = (tth > c0 - 0.5) & (tth < c0 + 0.5)
        if m.any():
            zero = float(tth[m][int(np.argmax(y[m]))] - c0)
    rows = []
    for idx in np.argsort(table.inten)[::-1][:20]:
        c = table.tth[idx] + zero
        if len(table.tth) > 1:
            dist = np.abs(table.tth - table.tth[idx])
            dist[idx] = np.inf
            span = min(0.5, max(0.05, 0.45 * float(np.min(dist))))
        else:
            span = 0.5
        fw = _measure_observed_fwhm(tth, y, c, span)
        if fw is not None:
            rows.append((float(table.tth[idx]), fw, float(table.inten[idx])))
    if len(rows) >= 3:
        t = np.tan(np.radians(np.array([r[0] for r in rows]) / 2.0))
        f2 = (np.array([r[1] for r in rows]) * 100.0) ** 2
        wt = np.sqrt(np.array([r[2] for r in rows], dtype=float))
        A = np.column_stack([t * t, t, np.ones_like(t)])
        coef, *_ = np.linalg.lstsq(A * wt[:, None], f2 * wt, rcond=None)
        u, v, w = (float(x) for x in coef)
        if not np.isfinite([u, v, w]).all() or w <= 0.0:
            u, v = 0.0, 0.0
            w = max(0.01, float(np.median(f2)) * 0.7)
        return zero, max(u, 0.0), v, w
    return zero, 0.0, 0.0, _estimate_initial_w(tth, y)


def _estimate_initial_w(tth, y):
    """从最强峰的高度半宽估一个 W（centideg²）初值；失败给 1.0。"""
    y = np.asarray(y, dtype=float)
    tth = np.asarray(tth, dtype=float)
    if len(y) < 5:
        return 1.0
    i = int(np.argmax(y))
    span = min(0.5, max(0.05, 0.05 * float(tth[-1] - tth[0])))
    fw = _measure_observed_fwhm(tth, y, float(tth[i]), span)
    if fw is None:
        return 1.0
    return max(0.01, (fw * 100.0 / 2.3548200450309493) ** 2)


class _Model:
    """在固定峰表上，把 (峰形参数 → 曲线) 包成最小二乘可调用的对象。"""

    def __init__(self, table, tth, y_target, weights, wavelength, fixed,
                 background_basis, refine_intensities=False,
                 intensity_poly=0, extra_models=None):
        self.table = table
        self.tth = np.asarray(tth, dtype=float)
        self.y = np.asarray(y_target, dtype=float)
        self.sqrt_w = np.sqrt(np.asarray(weights, dtype=float))
        self.wavelength = float(wavelength)
        self.fixed = dict(fixed)
        self.free_names = []
        self.background_basis = background_basis
        self.refine_intensities = bool(refine_intensities)
        self.intensity_poly = int(intensity_poly)
        self.extra_models = list(extra_models or [])
        self.extra_scales = []

    def _extra_curves(self, p):
        """额外相（如 Al 基片）的峰形曲线；仪器参数跟随主相。"""
        out = []
        for _label, em in self.extra_models:
            em.fixed.update({k: p[k] for k in
                             ("zero", "u", "v", "w", "x", "y", "z",
                              "polariz", "size_mix", "strain_mix")})
            out.append(em.peak_curve(em.fixed))
        return out

    def params(self, vec):
        p = dict(self.fixed)
        p.update(dict(zip(self.free_names, vec)))
        return p

    def _parts(self, p):
        """当前非线性参数下，每条峰的 (位置, 相对强度, FWHM, η)。"""
        zero = float(p["zero"])
        if self.table.orthorhombic:
            a = float(p["a"])
            b = float(p["b"])
            c = float(p["c"])
            h = self.table.hkl[:, 0].astype(float)
            k = self.table.hkl[:, 1].astype(float)
            l = self.table.hkl[:, 2].astype(float)
            inv_d2 = h * h / (a * a) + k * k / (b * b) + l * l / (c * c)
            d = 1.0 / np.sqrt(np.maximum(inv_d2, 1e-12))
            sin_t = np.clip(0.5 * self.wavelength / d, 0.0, 1.0)
            centers = 2.0 * np.degrees(np.arcsin(sin_t)) + zero
        else:                       # 非正交晶胞暂不支持精修，用固定位置
            centers = self.table.tth + zero
        sig, gam = reflection_sig_gam(
            centers, self.wavelength,
            u=p["u"], v=p["v"], w=p["w"], x=p["x"], y=p["y"], z=p["z"],
            size_um=p["size_um"], mustrain=p["mustrain"],
            size_mix=p["size_mix"], strain_mix=p["strain_mix"])
        fwhm, eta = sig_gam_to_fwhm_eta(sig, gam)
        inten = self.table.inten.copy()
        # 温度因子：全局 B 对 |F|² 整体乘 exp(−2ΔB·s²)
        b = float(p["b_iso"])
        if self.table.b_iso_base is not None:
            inten *= np.exp(-2.0 * (b - float(self.table.b_iso_base))
                            * self.table.s2)
        # 偏振度：只改偏振项，Lorentz 项不动
        pol = polarization(centers, float(p["polariz"]))
        inten *= pol / self.table.pol_base
        return centers, inten, fwhm, eta

    def _window(self, tt, fw):
        # GSAS-II 的峰范围：低角侧 50 FWHM、高角侧 75 FWHM（2θ>90° 对调）
        half_lo = (75.0 if tt > 90.0 else 50.0) * fw
        half_hi = (50.0 if tt > 90.0 else 75.0) * fw
        return (int(np.searchsorted(self.tth, tt - half_lo)),
                int(np.searchsorted(self.tth, tt + half_hi)))

    def peak_curve(self, p):
        """面积守恒的峰形曲线（不含 scale 与背景）。"""
        y = np.zeros_like(self.tth)
        for tt, ii, fw, et in zip(*self._parts(p)):
            if fw <= 0 or ii == 0:
                continue
            lo, hi = self._window(tt, fw)
            if hi <= lo:
                continue
            y[lo:hi] += ii * pv_area_normalized(self.tth[lo:hi], tt, fw, et)
        return y

    def peak_columns(self, p):
        """每条峰单独一列（面积归一），供 Le Bail 式逐峰强度拟合。"""
        centers, inten, fwhm, eta = self._parts(p)
        cols = []
        for tt, ii, fw, et in zip(centers, inten, fwhm, eta):
            col = np.zeros_like(self.tth)
            if fw > 0 and ii != 0:
                lo, hi = self._window(tt, fw)
                if hi > lo:
                    col[lo:hi] = ii * pv_area_normalized(
                        self.tth[lo:hi], tt, fw, et)
            cols.append(col)
        return np.column_stack(cols) if cols else np.empty((len(self.tth), 0))

    def intensity_columns(self, p):
        """结构模式的光滑强度修正列：1 + a1·z + a2·z²（z 归一化 2θ）。

        物理上像吸收/消光/偏振的平滑响应；它只改**强度**，不改峰宽。
        构造为额外线性列，拟合器一次解出 a1、a2——不增加非线性参数。
        """
        centers, inten, fwhm, eta = self._parts(p)
        degree = self.intensity_poly
        span = max(0.5 * float(centers.max() - centers.min()), 1e-9) \
            if len(centers) else 1.0
        z = (centers - float(centers.mean())) / span if len(centers) else \
            np.zeros_like(centers)
        cols = [np.zeros_like(self.tth) for _ in range(degree + 1)]
        for tt, ii, fw, et, zz in zip(centers, inten, fwhm, eta, z):
            if fw <= 0 or ii == 0:
                continue
            lo, hi = self._window(tt, fw)
            if hi <= lo:
                continue
            pv = pv_area_normalized(self.tth[lo:hi], tt, fw, et)
            for d in range(degree + 1):
                cols[d][lo:hi] += ii * (zz ** d) * pv
        return np.column_stack(cols)

    def solve_linear(self, p):
        """解线性参数：结构模式 = scale + 背景；Le Bail = 每峰强度 + 背景。"""
        if self.refine_intensities:
            M = self.peak_columns(p)
            n_peaks = M.shape[1]
            extras = self._extra_curves(p)
            n_extra = len(extras)
            cols_all = (np.column_stack([M] + extras) if n_extra else M)
            n_all = n_peaks + n_extra
            bg_basis = self.background_basis
            # 主相逐峰强度 + 额外相 scale 都必须非负：无约束 lstsq 后
            # 把负列剔掉重解（active-set）；比 lsq_linear 快且稳。
            active = np.ones(n_all, dtype=bool)
            coef = np.zeros(0)
            a_active = np.zeros(0)
            for _ in range(6):
                cols = [cols_all[:, active]]
                if bg_basis is not None:
                    cols.append(bg_basis)
                A = np.column_stack(cols) if cols[0].shape[1] or len(cols) > 1 \
                    else np.empty((len(self.y), 0))
                coef, *_ = np.linalg.lstsq(
                    A * self.sqrt_w[:, None], self.y * self.sqrt_w, rcond=None)
                n_act = int(active.sum())
                a_active = np.asarray(coef[:n_act], dtype=float)
                if not active.any() or np.all(a_active >= 0.0):
                    break
                new_active = active.copy()
                new_active[active] = a_active >= 0.0
                if not new_active.any():
                    active = new_active
                    a_active = np.zeros(0)
                    if bg_basis is not None:
                        coef, *_ = np.linalg.lstsq(
                            bg_basis * self.sqrt_w[:, None],
                            self.y * self.sqrt_w, rcond=None)
                    else:
                        coef = np.zeros(0)
                    break
                active = new_active
            n_act = int(active.sum())
            # 最终 active 与循环里的 coef 可能不同步（第 6 轮刚剔列），
            # 按最终 active 重解一次，保证系数与列一一对应。
            cols = [cols_all[:, active]]
            if bg_basis is not None:
                cols.append(bg_basis)
            A = (np.column_stack(cols)
                 if (n_act or bg_basis is not None)
                 else np.empty((len(self.y), 0)))
            coef, *_ = np.linalg.lstsq(
                A * self.sqrt_w[:, None], self.y * self.sqrt_w, rcond=None)
            a_active = np.asarray(coef[:n_act], dtype=float)
            coef_all = np.zeros(n_all, dtype=float)
            if n_act:
                coef_all[active] = a_active[:n_act]
            amps = coef_all[:n_peaks]
            self.extra_scales = [float(x) for x in coef_all[n_peaks:]]
            if bg_basis is None:
                bg = np.zeros_like(self.y)
                coeffs = np.array([])
            else:
                bcoef = np.asarray(coef[n_act:], dtype=float)
                bg = bg_basis @ bcoef
                coeffs = bcoef
            return A @ coef, 1.0, bg, coeffs, amps, None

        if self.intensity_poly:
            M = self.intensity_columns(p)
            extras = self._extra_curves(p)
            n_extra = len(extras)
            n_main = M.shape[1]
            cols = [M] + extras
            if self.background_basis is not None:
                cols.append(self.background_basis)
            A = np.column_stack(cols)
            coef, *_ = np.linalg.lstsq(
                A * self.sqrt_w[:, None], self.y * self.sqrt_w, rcond=None)
            scale = float(coef[0])
            if scale > 0:
                a = np.asarray(coef[1:n_main], dtype=float) / scale
            else:                       # pragma: no cover - 极罕见
                a = np.zeros(self.intensity_poly)
            self.extra_scales = [float(x)
                                 for x in coef[n_main:n_main + n_extra]]
            if self.background_basis is None:
                bg = np.zeros_like(self.y)
                coeffs = np.array([])
            else:
                bg = self.background_basis @ coef[n_main + n_extra:]
                coeffs = np.asarray(coef[n_main + n_extra:], dtype=float)
            return A @ coef, max(scale, 0.0), bg, coeffs, None, a

        y_model = self.peak_curve(p)
        extras = self._extra_curves(p)
        n_extra = len(extras)
        if self.background_basis is None:
            A = np.column_stack([y_model] + extras)
            coef, *_ = np.linalg.lstsq(
                A * self.sqrt_w[:, None], self.y * self.sqrt_w, rcond=None)
            scale = float(coef[0])
            self.extra_scales = [float(x) for x in coef[1:]]
            bg = np.zeros_like(self.y)
            y_calc = A @ coef
            coeffs = np.array([])
        else:
            A = np.column_stack(
                [y_model] + extras + [self.background_basis])
            coef, *_ = np.linalg.lstsq(
                A * self.sqrt_w[:, None], self.y * self.sqrt_w, rcond=None)
            scale = float(coef[0])
            if scale < 0.0:                 # 物理上比例因子不能为负
                A2 = np.column_stack(
                    extras + [self.background_basis])
                coef2, *_ = np.linalg.lstsq(
                    A2 * self.sqrt_w[:, None], self.y * self.sqrt_w,
                    rcond=None)
                scale = 0.0
                self.extra_scales = [float(x) for x in coef2[:n_extra]]
                bg = self.background_basis @ coef2[n_extra:]
                y_calc = A2 @ coef2
                coeffs = np.asarray(coef2[n_extra:], dtype=float)
            else:
                self.extra_scales = [float(x) for x in coef[1:1 + n_extra]]
                bg = self.background_basis @ coef[1 + n_extra:]
                y_calc = A @ coef
                coeffs = np.asarray(coef[1 + n_extra:], dtype=float)
        return y_calc, scale, bg, coeffs, None, None

    def residual(self, vec):
        y_calc, *_ = self.solve_linear(self.params(vec))
        return self.sqrt_w * (y_calc - self.y)


def _numerical_stderr(model, vec, free_names, reduced_chi2):
    """最终参数点的近似 esd：数值雅可比 → (JᵀJ)⁻¹ · 约化卡方。"""
    vec = np.asarray(vec, dtype=float)
    if len(vec) == 0:
        return {}
    base = model.residual(vec)
    cols = []
    for i in range(len(vec)):
        step = max(1e-7, abs(vec[i]) * 1e-6)
        vp = vec.copy()
        vp[i] += step
        cols.append((model.residual(vp) - base) / step)
    jac = np.column_stack(cols)
    try:
        cov = np.linalg.pinv(jac.T @ jac) * max(reduced_chi2, 1e-30)
        errs = np.sqrt(np.maximum(np.diag(cov), 0.0))
    except np.linalg.LinAlgError:       # pragma: no cover - 数值兜底
        errs = np.full(len(vec), np.nan)
    return {n: float(e) for n, e in zip(free_names, errs)}


def _piecewise_linear_basis(tth, spacing):
    """分段线性（帽函数）背景基：节点每隔 spacing 度，局部支撑。

    与全局 Chebyshev 不同，某个节点只影响左右各一个间隔；窄峰（~0.02°）
    不可能被 0.25–0.5° 的节点吃掉，所以适合"自动基线 + 局部修正"。
    """
    lo, hi = float(tth.min()), float(tth.max())
    knots = np.arange(lo, hi + spacing / 2.0, spacing)
    if knots[-1] < hi:
        knots = np.append(knots, hi)
    if len(knots) < 2:                  # pragma: no cover - 极短数据兜底
        knots = np.array([lo, hi + 1e-12])
    idx = np.clip(np.searchsorted(knots, tth) - 1, 0, len(knots) - 2)
    x0, x1 = knots[idx], knots[idx + 1]
    t = (tth - x0) / (x1 - x0)
    basis = np.zeros((len(tth), len(knots)))
    rows = np.arange(len(tth))
    basis[rows, idx] = 1.0 - t
    basis[rows, idx + 1] = t
    return basis, knots


def fit_pattern(structure, tth, intensity, *, wavelength=1.5406,
                polariz=0.5, b_iso=None, md=None,
                two_theta_range=None, nmax=10,
                background=4, background_poly=0, background_knots=0.0,
                weights="poisson", refine_intensities=False,
                intensity_poly=0, cell_refine=None, extra_phases=None,
                free=("zero", "u", "v", "w"),
                stages=None, initial=None, bounds=None,
                size_mix=1.0, strain_mix=1.0,
                max_nfev=2000):
    """用 GSAS-II 口径的全谱模型拟合一条 1D 谱。

    参数：
        structure          pymatgen Structure（CIF 读入）
        tth, intensity     观测网格与计数（长度相同；网格不要求等距）
        wavelength         波长 Å
        polariz           光束偏振度；0.5 = 非偏振
        b_iso              初始/固定温度因子：float、{元素: B} 或 None。
                           若 free 含 b_iso：标量 = 绝对 B；字典 = 在 CIF
                           各元素 B 上叠加的**全局 ΔB**。
        two_theta_range    峰表范围；None = 由观测网格首尾决定
        background         背景：int = Chebyshev 阶数；None = 无；
                           数组 = 固定背景曲线（如自动基线）
        background_poly    仅当 background 是数组时：在固定背景上再叠加
                           一个 n 阶 Chebyshev **修正**（n=0 只修常数偏移）。
        background_knots   分段线性背景修正的节点间距（度，建议 0.25–0.5）。
                           与 background_poly 互斥；局部支撑，不会吃掉窄峰。
        weights            "poisson"（默认）、"uniform" 或与数据同长的数组
        refine_intensities True = Le Bail 式：每条峰的积分强度单独作为
                           线性参数解出（不再受结构因子/B/LP 约束）。
                           用来区分"峰形/背景没拟合好"还是"强度模型没拟合好"；
                           b_iso/polariz 此时简并，不能放进 free。
        intensity_poly     结构模式的光滑强度修正阶数（0/1/2）：
                           强度乘 1 + a1·z + a2·z²（z = 归一化 2θ）。
                           吸收/消光/偏振这类平滑响应用它，比逐峰自由强度
                           更有物理约束；系数与 scale 一起线性解出。
        cell_refine        True = 用逐反射 hkl/d 重建峰位，a/b/c 即使不在
                           free 里也按 initial 固定在该晶胞；None（默认）
                           = 仅当 free 含 a/b/c 时启用。
        extra_phases       额外相列表（如 Al 基片）：每项 dict 支持
                           structure（必填）、label、a/b/c、size_um、
                           mustrain、b_iso、md。额外相的晶胞/微结构固定，
                           只有 scale 是自由线性参数；仪器峰形跟随主相。
        free               要精修的参数名（zero/U/V/W/X/Y/Z/size_um/
                           mustrain/b_iso/polariz；正交晶胞可加 a/b/c）；
                           scale 与背景总是线性解出。a/b/c 只支持
                           α=β=γ=90° 的正交晶胞（橄榄石就是）。
        stages             分阶段精修，默认 DEFAULT_STAGES 按 free 过滤
        initial            初值覆盖（同名参数）
        bounds             边界覆盖（同名参数）
        size_mix/strain_mix 尺寸/应变的 LG 混合，GSAS 默认 1（Lorentzian）

    返回 `FitResult`。拟合在**观测点**上逐点比较，不做插值。
    """
    tth = np.asarray(tth, dtype=float)
    y_raw = np.asarray(intensity, dtype=float)
    if tth.shape != y_raw.shape:
        raise ValueError("tth 与 intensity 长度不一致")
    order = np.argsort(tth)
    tth, y_raw = tth[order], y_raw[order]
    if two_theta_range is None:
        two_theta_range = (float(tth[0]), float(tth[-1]))

    free = tuple(free)
    if cell_refine is None:
        cell_refine = any(n in free for n in ("a", "b", "c"))
    else:
        cell_refine = bool(cell_refine)

    # ── 峰表（固定结构；精修晶胞时保留 hkl/d、不做 2θ 合并） ────────
    table = _PeakTable(structure, wavelength, two_theta_range, nmax,
                       b_iso, polariz, keep_unmerged=cell_refine, md=md)

    # ── 权重（用原始计数；固定背景扣除不改变权重口径） ─────────────
    if isinstance(weights, str):
        if weights == "poisson":
            w = 1.0 / np.maximum(y_raw, 1.0)
        elif weights == "uniform":
            w = np.ones_like(y_raw)
        else:
            raise ValueError(f"未知权重口径：{weights}")
    else:
        w = np.asarray(weights, dtype=float)
        if w.shape != y_raw.shape:
            raise ValueError("weights 必须与数据同长")

    # ── 背景：固定数组 / Chebyshev 多项式 / 分段线性修正 ──────────
    if background_poly and not isinstance(background, np.ndarray):
        raise ValueError("background_poly 只用于 background 是固定数组的情形")
    if background_knots and background_poly:
        raise ValueError("background_knots 与 background_poly 只能二选一")
    if background_knots and isinstance(background, (int, np.integer)):
        raise ValueError("background_knots 不能与多项式背景同时用")
    z = 2.0 * (tth - tth.min()) / max(tth.max() - tth.min(), 1e-12) - 1.0
    if background is None:
        y_target = y_raw
        fixed_bg = None
        if background_knots:
            basis, _knots = _piecewise_linear_basis(tth, background_knots)
        else:
            basis = None
    elif isinstance(background, (int, np.integer)):
        if background < 0:
            raise ValueError("background 阶数不能为负")
        basis = np.polynomial.chebyshev.chebvander(z, int(background))
        y_target = y_raw
        fixed_bg = None
    else:
        fixed_bg = np.asarray(background, dtype=float)
        if fixed_bg.shape != y_raw.shape:
            raise ValueError("固定背景必须与数据同长")
        if background_knots:
            basis, _knots = _piecewise_linear_basis(tth, background_knots)
        elif background_poly:
            basis = np.polynomial.chebyshev.chebvander(
                z, int(background_poly))
        else:
            basis = None
        y_target = y_raw - fixed_bg

    # ── 初值 ────────────────────────────────────────────────────────
    bad = [n for n in free if n not in _ALLOWED]
    if bad:
        raise ValueError(f"不可精修的参数：{bad}")
    if refine_intensities:
        bad = [n for n in free if n in ("b_iso", "polariz")]
        if bad:
            raise ValueError(
                f"Le Bail 模式下每峰强度已自由，{bad} 会简并，不能精修")
        if intensity_poly:
            raise ValueError("Le Bail 模式下 intensity_poly 没有意义")
    if int(intensity_poly) < 0 or int(intensity_poly) > 4:
        raise ValueError("intensity_poly 取 0–4")
    if cell_refine and not table.orthorhombic:
        raise ValueError("a/b/c 精修目前只支持正交晶胞（α=β=γ=90°）")
    auto_zero, auto_u, auto_v, auto_w = _estimate_initial_profile(
        tth, y_target, table)
    init = {
        "zero": auto_zero,
        "u": auto_u,
        "v": auto_v,
        "w": auto_w,
        "a": table.a0,
        "b": table.b0,
        "c": table.c0,
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        # 要放开的微结构项给一个"有宽度但不夸张"的起点（1 µm / 1 个
        # GSAS 微应变单位）；不放开时用"无限大晶粒、零应变"。
        "size_um": 1.0 if "size_um" in free else 1.0e4,
        "mustrain": 1.0 if "mustrain" in free else 0.0,
        "b_iso": 0.0 if b_iso is None or isinstance(b_iso, dict) else float(b_iso),
        "polariz": float(polariz),
        "size_mix": float(size_mix),
        "strain_mix": float(strain_mix),
    }
    if isinstance(b_iso, dict):
        init["b_iso"] = 0.0          # 峰表已带各元素 B；自由参数是 ΔB
    if "b_iso" in free:
        # 峰表已带基准 B（标量或逐元素）时，自由 b_iso 表示叠加的
        # **全局 ΔB**；峰表没有基准 B 时，它就是绝对 B。
        if table.b_iso_base is None:
            table.b_iso_base = 0.0
        if b_iso is None or isinstance(b_iso, dict):
            init["b_iso"] = 0.0
    if initial:
        init.update(initial)
    user_bounds = bounds or {}
    cell_bounds = {
        "a": (table.a0 - 1.0, table.a0 + 1.0),
        "b": (table.b0 - 1.0, table.b0 + 1.0),
        "c": (table.c0 - 1.0, table.c0 + 1.0),
    }
    bounds = {**DEFAULT_BOUNDS, **cell_bounds, **user_bounds}
    if isinstance(b_iso, dict) and "b_iso" not in user_bounds:
        # 字典 B 时自由参数是 ΔB，允许为负（实验样品的 B 可能比 CIF 小）
        bounds["b_iso"] = (-50.0, 50.0)

    # ── 分阶段 ──────────────────────────────────────────────────────
    if stages is None:
        stages = []
        for s in DEFAULT_STAGES:
            hit = tuple(n for n in s if n in free)
            if hit:
                stages.append(hit)
        covered = {n for s in stages for n in s}
        rest = tuple(n for n in free if n not in covered)
        if rest:
            stages.append(rest)
    else:
        stages = [tuple(s) for s in stages]
        bad = [n for s in stages for n in s if n not in free]
        if bad:
            raise ValueError(f"stages 中有未在 free 里的参数：{bad}")

    extra_models = []
    for spec in (extra_phases or []):
        st = spec.get("structure")
        if st is None:
            raise ValueError("extra_phases 每项必须给 structure")
        label = str(spec.get("label", "extra"))
        b_extra = spec.get("b_iso")
        extra_table = _PeakTable(
            st, wavelength, two_theta_range, nmax, b_extra, polariz,
            keep_unmerged=True, md=spec.get("md"))
        extra_fixed = {
            "zero": init["zero"], "u": init["u"], "v": init["v"],
            "w": init["w"], "x": init["x"], "y": init["y"],
            "z": init["z"],
            "a": float(spec.get("a", extra_table.a0)),
            "b": float(spec.get("b", extra_table.b0)),
            "c": float(spec.get("c", extra_table.c0)),
            "size_um": float(spec.get("size_um", 1e4)),
            "mustrain": float(spec.get("mustrain", 0.0)),
            "b_iso": (0.0 if b_extra is None or isinstance(b_extra, dict)
                      else float(b_extra)),
            "polariz": float(polariz),
            "size_mix": float(spec.get("size_mix", 1.0)),
            "strain_mix": float(spec.get("strain_mix", 1.0)),
        }
        em = _Model(extra_table, tth, y_target, w, wavelength,
                    extra_fixed, None, refine_intensities=False,
                    intensity_poly=0)
        em.free_names = []
        extra_models.append((label, em))

    model = _Model(table, tth, y_target, w, wavelength, init, basis,
                   refine_intensities=refine_intensities,
                   intensity_poly=intensity_poly,
                   extra_models=extra_models)
    values = dict(init)
    result = None
    used_stages = []
    for stage in stages:
        names = [n for n in free if n in stage]
        if not names:
            continue
        used_stages.append(tuple(names))
        model.free_names = names
        model.fixed = {k: v for k, v in values.items() if k not in names}
        x0 = np.array([values[n] for n in names], dtype=float)
        lo = np.array([bounds[n][0] for n in names], dtype=float)
        hi = np.array([bounds[n][1] for n in names], dtype=float)
        x0 = np.clip(x0, lo, hi)
        result = least_squares(
            model.residual, x0, bounds=(lo, hi), x_scale="jac",
            max_nfev=max_nfev, xtol=1e-12, ftol=1e-12, gtol=1e-12)
        values.update(dict(zip(names, result.x)))

    # ── 最终曲线与指标（全部 free 参数一起） ─────────────────────────
    model.free_names = list(free)
    model.fixed = {k: v for k, v in values.items() if k not in free}
    vec = np.array([values[n] for n in free], dtype=float)
    p_final = model.params(vec)
    y_calc_target, scale, bg, coeffs, amps, inten_coeffs = \
        model.solve_linear(p_final)
    if fixed_bg is not None:
        y_calc_total = y_calc_target + fixed_bg
        # solve_linear 返回的是固定背景之上的修正项；报告里给总背景
        bg = fixed_bg + bg
    else:
        y_calc_total = y_calc_target
    resid = y_calc_total - y_raw
    rwp = 100.0 * math.sqrt(float(np.sum(w * resid ** 2))
                            / max(float(np.sum(w * y_raw ** 2)), 1e-300))
    rp = (100.0 * float(np.sum(np.abs(resid)))
          / max(float(np.sum(np.abs(y_raw))), 1e-300))
    chi2 = float(np.sum(w * resid ** 2))
    n_peaks = len(table.tth)
    n_params = (len(free) + n_peaks
                if refine_intensities else len(free) + 1 + int(intensity_poly))
    n_params += basis.shape[1] if basis is not None else 0
    n_params += len(extra_models)          # 每相一个自由 scale
    reduced = chi2 / max(1, len(y_raw) - n_params)
    stderr = _numerical_stderr(model, vec, list(free), reduced) \
        if len(free) else {}

    params = {n: float(values[n]) for n in free}
    params["scale"] = float(scale)
    params["size_mix"] = float(size_mix)
    params["strain_mix"] = float(strain_mix)
    if inten_coeffs is not None:
        for i, a in enumerate(np.asarray(inten_coeffs).ravel(), start=1):
            params[f"intensity_a{i}"] = float(a)
    for (label, em), sc in zip(extra_models, model.extra_scales):
        params[f"{label}:scale"] = float(sc)
        for key in ("a", "b", "c", "size_um", "mustrain", "b_iso"):
            params[f"{label}:{key}"] = float(em.fixed[key])
    peak_intensities = (None if amps is None
                        else np.asarray(amps, dtype=float) * table.inten)
    return FitResult(
        params=params, stderr=stderr, rwp=rwp, rp=rp, chi2=chi2,
        reduced_chi2=reduced, n_obs=len(y_raw), n_params=n_params,
        success=bool(result.success if result is not None else True),
        message=(result.message if result is not None else "no free parameters"),
        tth=tth, y_obs=y_raw, y_calc=y_calc_total, residual=resid,
        background=bg, scale=float(scale), background_coeffs=coeffs,
        initial=init, stages=tuple(used_stages),
        peak_intensities=peak_intensities,
        peak_table_intensities=(None if amps is None else table.inten.copy()),
        peak_tth=(None if amps is None else model._parts(p_final)[0]))


def rwp(y_obs, y_calc, weights="poisson"):
    """独立的 Rwp 计算（不依赖拟合器，测试/报告共用）。"""
    y_obs = np.asarray(y_obs, dtype=float)
    y_calc = np.asarray(y_calc, dtype=float)
    if isinstance(weights, str):
        w = (1.0 / np.maximum(y_obs, 1.0) if weights == "poisson"
             else np.ones_like(y_obs))
    else:
        w = np.asarray(weights, dtype=float)
    return 100.0 * math.sqrt(float(np.sum(w * (y_obs - y_calc) ** 2))
                             / max(float(np.sum(w * y_obs ** 2)), 1e-300))
