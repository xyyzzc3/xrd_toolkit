"""从峰位精修晶胞（全七个晶系，2026-10-31）。

全谱引擎只精修立方/正交晶胞；物相筛选/化学分析常见的六方、单斜、
三斜相没有入口。这里走**峰位级**的经典做法（CELREF 一类）：

1. 用初始晶胞从 CIF 建理论反射表（hkl、d、2θ）；
2. 在 0.90–1.10 的晶胞缩放下做 d 值匹配（Hungarian 一一对应），
   选匹配最多、残差最小的缩放；
3. 按晶系约束（立方只留 a；四方/六方留 a,c；单斜留一个非直角……）
   用 `least_squares` 最小化 d_calc − d_obs；
4. 用精修后的晶胞再匹配/精修一遍，报告匹配率与未匹配峰。

这不是全谱 Rietveld：只回答"晶胞是多少、这些峰能不能用这个相
解释"，不碰强度/占位/峰形。全谱引擎继续负责立方/正交的严格精修。
"""
from dataclasses import dataclass, field
import math

import numpy as np
from scipy.optimize import least_squares, linear_sum_assignment


@dataclass
class CellMatch:
    observed_deg: float
    hkl: tuple
    d_obs: float
    d_calc: float
    delta_deg: float          # observed − calculated 2θ


@dataclass
class CellRefineResult:
    system: str
    cell: dict
    cell0: dict
    stderr: dict
    matches: list
    unmatched_obs: list
    unmatched_calc: list
    scale: float
    zero_deg: float
    rms_deg: float
    rms_d: float
    success: bool
    message: str
    warnings: list = field(default_factory=list)

    def report(self):
        """人读报告（英文，参数 + 匹配明细）。"""
        order = ("a", "b", "c", "alpha", "beta", "gamma")
        units = {"a": "Å", "b": "Å", "c": "Å", "alpha": "°",
                 "beta": "°", "gamma": "°"}
        lines = [
            "=== cell refinement (peak positions) ===",
            f"system: {self.system}",
            f"success: {self.success} ({self.message})",
            f"initial scale scan: {self.scale:.5f}",
            f"matched: {len(self.matches)} peaks; "
            f"unmatched observed: {len(self.unmatched_obs)}; "
            f"unmatched calculated: {len(self.unmatched_calc)}",
            f"zero shift: {self.zero_deg:+.5f}°",
            f"RMS: {self.rms_deg:.5f}° 2theta / {self.rms_d:.6f} Å",
            f"{'param':>8} {'initial':>12} {'refined':>12} {'esd≈':>10}",
        ]
        for key in order:
            if key not in self.cell:
                continue
            esd = self.stderr.get(key, float("nan"))
            esd_text = f"{esd:10.5g}" if np.isfinite(esd) else f"{'—':>10}"
            lines.append(
                f"{key:>8} {self.cell0[key]:12.6f} "
                f"{self.cell[key]:12.6f} {esd_text}")
        if self.warnings:
            lines.append("warnings: " + "；".join(self.warnings))
        lines += ["", f"{'2theta_obs':>11} {'hkl':>12} {'d_obs':>10} "
                        f"{'d_calc':>10} {'delta_deg':>10}"]
        for m in self.matches:
            hkl = "(" + " ".join(str(int(v)) for v in m.hkl) + ")"
            lines.append(
                f"{m.observed_deg:11.4f} {hkl:>12} {m.d_obs:10.5f} "
                f"{m.d_calc:10.5f} {m.delta_deg:10.5f}")
        if self.unmatched_obs:
            lines.append("unmatched observed 2theta: "
                         + " ".join(f"{v:.4f}" for v in self.unmatched_obs))
        return "\n".join(lines)


def cell_dict(cell):
    """(a,b,c,α,β,γ) → 报告用字典。"""
    a, b, c, al, be, ga = (float(v) for v in cell)
    return {"a": a, "b": b, "c": c, "alpha": al, "beta": be, "gamma": ga}


def crystal_system(lattice, *, angle_tol=0.5, length_tol=1e-3):
    """按晶胞参数判断 7 个晶系之一（P1 也按参数判）。"""
    a, b, c = (float(v) for v in lattice.abc)
    al, be, ga = (float(v) for v in lattice.angles)
    right = (abs(al - 90.0) <= angle_tol
             and abs(be - 90.0) <= angle_tol
             and abs(ga - 90.0) <= angle_tol)
    eq = (lambda x, y: abs(x - y) <= length_tol * max(abs(x), abs(y), 1.0))
    if eq(a, b) and eq(b, c):
        if right:
            return "cubic"
        if (abs(al - be) <= angle_tol and abs(be - ga) <= angle_tol):
            return "rhombohedral"
    if (eq(a, b) and abs(al - 90.0) <= angle_tol
            and abs(be - 90.0) <= angle_tol and abs(ga - 120.0) <= angle_tol):
        return "hexagonal"
    if eq(a, b) and right:
        return "tetragonal"
    if right:
        return "orthorhombic"
    non_right = sum(abs(v - 90.0) > angle_tol for v in (al, be, ga))
    if non_right == 1:
        return "monoclinic"
    return "triclinic"


def _param_names(system):
    return {
        "cubic": ("a",),
        "tetragonal": ("a", "c"),
        "hexagonal": ("a", "c"),
        "rhombohedral": ("a", "alpha"),
        "orthorhombic": ("a", "b", "c"),
        "monoclinic": ("a", "b", "c", "angle"),
        "triclinic": ("a", "b", "c", "alpha", "beta", "gamma"),
    }[system]


def _cell_from_params(x, system, cell0):
    a0, b0, c0, al0, be0, ga0 = (float(v) for v in cell0)
    if system == "cubic":
        a = float(x[0])
        return (a, a, a, 90.0, 90.0, 90.0)
    if system == "tetragonal":
        a, c = (float(v) for v in x)
        return (a, a, c, 90.0, 90.0, 90.0)
    if system == "hexagonal":
        a, c = (float(v) for v in x)
        return (a, a, c, 90.0, 90.0, 120.0)
    if system == "rhombohedral":
        a, al = (float(v) for v in x)
        return (a, a, a, al, al, al)
    if system == "orthorhombic":
        a, b, c = (float(v) for v in x)
        return (a, b, c, 90.0, 90.0, 90.0)
    if system == "monoclinic":
        a, b, c, angle = (float(v) for v in x)
        al, be, ga = al0, be0, ga0
        # 哪个角不是 90°，就放开哪个（晶体学等价的三条设置都认）
        diffs = [abs(al - 90.0), abs(be - 90.0), abs(ga - 90.0)]
        idx = int(np.argmax(diffs))
        full = [a, b, c, al, be, ga]
        full[3 + idx] = angle
        return tuple(full)
    return tuple(float(v) for v in x)      # triclinic


def _initial_params(cell, system):
    a, b, c, al, be, ga = (float(v) for v in cell)
    if system == "cubic":
        return [a]
    if system in ("tetragonal", "hexagonal"):
        return [a, c]
    if system == "rhombohedral":
        return [a, al]
    if system == "orthorhombic":
        return [a, b, c]
    if system == "monoclinic":
        diffs = [abs(al - 90.0), abs(be - 90.0), abs(ga - 90.0)]
        return [a, b, c, (al, be, ga)[int(np.argmax(diffs))]]
    return [a, b, c, al, be, ga]


def d_spacing(hkl, cell):
    """任意晶系的 d 值（Å）；hkl 为 (N,3) 数组。"""
    a, b, c, al, be, ga = (float(v) for v in cell)
    al, be, ga = math.radians(al), math.radians(be), math.radians(ga)
    ca, cb, cg = math.cos(al), math.cos(be), math.cos(ga)
    h, k, l = (np.asarray(hkl, dtype=float)[:, i] for i in range(3))
    inv2 = (h * h / (a * a) * math.sin(al) ** 2
            + k * k / (b * b) * math.sin(be) ** 2
            + l * l / (c * c) * math.sin(ga) ** 2
            + 2.0 * h * k / (a * b) * (ca * cb - cg)
            + 2.0 * k * l / (b * c) * (cb * cg - ca)
            + 2.0 * h * l / (a * c) * (cg * ca - cb))
    denom = (1.0 - ca * ca - cb * cb - cg * cg + 2.0 * ca * cb * cg)
    return 1.0 / np.sqrt(np.maximum(inv2 / max(denom, 1e-12), 1e-12))


def _d_from_2theta(tth, wavelength):
    return float(wavelength) / (2.0 * np.sin(np.radians(tth) / 2.0))


def _tth_from_d(d, wavelength):
    sin_theta = float(wavelength) / (2.0 * np.asarray(d, dtype=float))
    return np.degrees(2.0 * np.arcsin(np.clip(sin_theta, -1.0, 1.0)))


def _theoretical(cell, wavelength, lo, hi, nmax):
    """枚举整数 hkl → 唯一 d/2θ（不依赖空间群/代表 hkl）。

    不用 `core.reflection_table` 的代表 hkl：R-3m 等设置下它的
    代表元可能与 d 不对应；晶胞精修只需要 d 的唯一集合。
    """
    n = int(nmax)
    grid = np.arange(-n, n + 1)
    hh, kk, ll = np.meshgrid(grid, grid, grid, indexing="ij")
    hkl = np.column_stack([hh.ravel(), kk.ravel(), ll.ravel()])
    hkl = hkl[np.any(hkl != 0, axis=1)]
    d = d_spacing(hkl, cell)
    tth = _tth_from_d(d, wavelength)
    good = np.isfinite(tth) & (tth >= lo) & (tth <= hi)
    hkl, d, tth = hkl[good], d[good], tth[good]
    order = np.argsort(tth)
    hkl, d, tth = hkl[order], d[order], tth[order]
    keep_hkl, keep_d, keep_t = [], [], []
    for h, dval, tval in zip(hkl, d, tth):
        if keep_d and abs(dval - keep_d[-1]) <= 1e-5 * max(abs(dval), 1.0):
            continue
        keep_hkl.append(h)
        keep_d.append(float(dval))
        keep_t.append(float(tval))
    return (np.asarray(keep_hkl, dtype=int),
            np.asarray(keep_d, dtype=float),
            np.asarray(keep_t, dtype=float))


def _match(obs_t, theo_t, theo_d, theo_hkl, wavelength, tol, scale):
    """Hungarian 一对一匹配；返回 (indices_obs, indices_theo, diffs)。"""
    obs_t = np.asarray(obs_t, dtype=float)
    d_obs = _d_from_2theta(obs_t, wavelength)
    t_calc = _tth_from_d(theo_d * float(scale), wavelength)
    diff = np.abs(obs_t[:, None] - t_calc[None, :])
    cost = np.abs(d_obs[:, None] - theo_d[None, :] * float(scale))
    cost = np.where(diff <= tol, cost, 1e6)
    rows, cols = linear_sum_assignment(cost)
    keep = diff[rows, cols] <= tol
    return rows[keep], cols[keep], diff[rows[keep], cols[keep]]


def _pick_scale(obs_t, theo_t, theo_d, theo_hkl, wavelength, tol):
    """粗扫晶胞缩放：匹配最多，其次残差中位数最小。"""
    best = None
    for scale in np.linspace(0.90, 1.10, 41):
        rows, cols, diff = _match(
            obs_t, theo_t, theo_d, theo_hkl, wavelength, tol, scale)
        key = (len(rows), -float(np.median(diff)) if len(diff) else -1e9)
        if best is None or key > best[0]:
            best = (key, float(scale), rows, cols)
    return best[1], best[2], best[3]


def _fit_cell(obs_t, hkl, cell0, system, wavelength, *,
              refine_zero=True, zero0=0.0):
    names = _param_names(system)
    n_cell = len(names)
    x0_cell = np.asarray(_initial_params(cell0, system), dtype=float)
    angle_names = {"alpha", "beta", "gamma", "angle"}
    lo = np.array([
        0.85 * abs(v) if name not in angle_names else max(10.0, v - 15.0)
        for name, v in zip(names, x0_cell)], dtype=float)
    hi = np.array([
        1.15 * abs(v) if name not in angle_names else min(170.0, v + 15.0)
        for name, v in zip(names, x0_cell)], dtype=float)
    lo[:3] = np.maximum(lo[:3], 0.1)
    x0 = x0_cell
    if refine_zero:
        x0 = np.r_[x0_cell, float(zero0)]
        lo = np.r_[lo, -1.0]
        hi = np.r_[hi, 1.0]

    def residual(x):
        cell = _cell_from_params(x[:n_cell], system, cell0)
        zero = float(x[n_cell]) if refine_zero else 0.0
        return _tth_from_d(d_spacing(hkl, cell), wavelength) + zero - obs_t

    fit = least_squares(residual, x0, bounds=(lo, hi), max_nfev=500,
                        x_scale="jac", xtol=1e-12, ftol=1e-12, gtol=1e-12)
    cell = _cell_from_params(fit.x[:n_cell], system, cell0)
    zero = float(fit.x[n_cell]) if refine_zero else 0.0
    stderr = {"zero": float("nan")}
    try:
        jac = np.asarray(fit.jac, dtype=float)
        m = jac.shape[0]
        n = jac.shape[1]
        dof = max(1, m - n)
        cov = np.linalg.pinv(jac.T @ jac) * (2.0 * fit.cost / dof)
        sd = np.sqrt(np.maximum(np.diag(cov), 0.0))
        for name, value in zip(names, sd[:n_cell]):
            if name == "angle":
                diffs = [abs(v - 90.0) for v in cell0[3:]]
                idx = int(np.argmax(diffs))
                key = ("alpha", "beta", "gamma")[idx]
            else:
                key = name
            stderr[key] = float(value)
        if refine_zero:
            stderr["zero"] = float(sd[n_cell])
    except (np.linalg.LinAlgError, ValueError):
        pass
    return fit, cell, zero, stderr


def refine_cell(structure, observed_deg, *, wavelength=1.5406,
                match_tol_deg=0.08, max_hkl=12, nmax_passes=2,
                refine_zero=True, zero0=0.0):
    """从观测峰位精修晶胞；返回 `CellRefineResult`。

    structure      pymatgen Structure（给初始晶胞与 hkl 表）
    observed_deg   观测峰位（2θ，度），至少比自由参数多 3 根
    match_tol_deg  峰位匹配容差（度）
    max_hkl        理论反射表的 |h|,|k|,|l| 上限
    refine_zero    同时精修 2θ 零位（习惯上总是需要；要固定就给 False）
    """
    obs = np.asarray(sorted(float(v) for v in observed_deg), dtype=float)
    obs = obs[np.isfinite(obs)]
    if obs.size < 3:
        raise ValueError("至少需要 3 根观测峰")
    # 同一 2θ 的重复峰（简并/重复列出）只留一根，否则一一匹配会被
    # 迫把不同 hkl 分给同位置的峰，给晶胞带来偏差。
    uniq = [obs[0]]
    for value in obs[1:]:
        if abs(value - uniq[-1]) > 1e-4:
            uniq.append(value)
    obs = np.asarray(uniq, dtype=float)
    lam = float(wavelength)
    cell0 = tuple(float(v) for v in structure.lattice.parameters)
    system = crystal_system(structure.lattice)
    names = _param_names(system)
    need = len(names) + (3 if refine_zero else 2)
    if obs.size < need:
        raise ValueError(
            f"{system} 晶系有 {len(names)} 个自由参数"
            + (" + zero" if refine_zero else "")
            + f"，至少需要 {need} 根峰（现在 {obs.size} 根）")
    # 初始表比观测范围略宽，避免边界峰被丢掉
    lo = float(obs.min()) - 2.0 * float(match_tol_deg)
    hi = float(obs.max()) + 2.0 * float(match_tol_deg)
    theo_hkl, theo_d, theo_t = _theoretical(
        cell0, lam, lo, hi, int(max_hkl))
    if theo_hkl.size < len(names) + 2:
        raise ValueError("理论反射太少：加大 max_hkl 或检查 2θ 范围")
    scale, rows, cols = _pick_scale(
        obs, theo_t, theo_d, theo_hkl, lam, float(match_tol_deg))
    fit = None
    cell = cell0
    zero = float(zero0) if refine_zero else 0.0
    stderr = {}
    for _ in range(max(1, int(nmax_passes))):
        if len(rows) < need:
            break
        obs_m = obs[rows]
        hkl_m = theo_hkl[cols]
        fit, cell, zero, stderr = _fit_cell(
            obs_m, hkl_m, cell0, system, lam, refine_zero=refine_zero,
            zero0=zero0)
        # 用精修后的晶胞重建理论表并重新匹配（防止初始 hkl 配错）
        theo_hkl, theo_d, theo_t = _theoretical(
            cell, lam, lo, hi, int(max_hkl))
        d_ref = d_spacing(theo_hkl, cell)
        t_new = _tth_from_d(d_ref, lam) + zero
        diff = np.abs(obs[:, None] - t_new[None, :])
        cost = np.abs(_d_from_2theta(obs, lam)[:, None] - d_ref[None, :])
        cost = np.where(diff <= float(match_tol_deg), cost, 1e6)
        rr, cc = linear_sum_assignment(cost)
        keep = diff[rr, cc] <= float(match_tol_deg)
        rows, cols = rr[keep], cc[keep]
    if fit is None:
        raise ValueError("没有足够的匹配峰，无法精修晶胞")

    # 最终匹配与残差（用精修后的晶胞）
    matches = []
    used_t = set()
    for r, c in zip(rows, cols):
        d_obs = _d_from_2theta(obs[r], lam)
        d_calc = float(d_spacing(np.asarray([theo_hkl[c]]), cell)[0])
        t_calc = float(_tth_from_d(d_calc, lam)) + zero
        matches.append(CellMatch(
            observed_deg=float(obs[r]),
            hkl=tuple(int(v) for v in theo_hkl[c]),
            d_obs=float(d_obs), d_calc=d_calc,
            delta_deg=float(obs[r] - t_calc)))
        used_t.add(int(c))
    matched_obs = {int(r) for r in rows}
    unmatched_obs = [float(obs[i]) for i in range(obs.size)
                     if i not in matched_obs]
    in_range = (theo_t >= obs.min() - 1e-9) & (theo_t <= obs.max() + 1e-9)
    unmatched_calc = [float(theo_t[i]) for i in range(theo_t.size)
                      if in_range[i] and i not in used_t]
    if matches:
        rms_deg = float(np.sqrt(np.mean([m.delta_deg ** 2 for m in matches])))
        rms_d = float(np.sqrt(np.mean(
            [(m.d_calc - m.d_obs) ** 2 for m in matches])))
    else:
        rms_deg = rms_d = float("nan")
    warnings = []
    if len(matches) < 3:
        warnings.append("匹配峰少于 3 根，结果不可靠")
    if unmatched_obs:
        warnings.append(f"{len(unmatched_obs)} 根观测峰没有理论峰对应"
                        "（可能是杂相/坏点）")
    return CellRefineResult(
        system=system, cell=cell_dict(cell), cell0=cell_dict(cell0),
        stderr={k: float(v) for k, v in stderr.items()},
        matches=matches, unmatched_obs=unmatched_obs,
        unmatched_calc=unmatched_calc, scale=float(scale),
        zero_deg=float(zero),
        rms_deg=rms_deg, rms_d=rms_d,
        success=bool(fit.success), message=str(fit.message),
        warnings=warnings)
