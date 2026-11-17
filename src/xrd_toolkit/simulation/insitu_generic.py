"""通用原位多相顺序精修（2026-11-11 用户第 14 条）。

专用的 LMFP 三相快速模式留在 `simulation.insitu`：它快、带 f_rich
口径与两段少数相处理。这里给"任意 N 相"的通用入口：

  * 第一相 = 主相（结构因子；正交/立方晶胞可选自由）；
  * 其余相 = 额外相（默认逐反射强度自由，即 Le Bail；晶胞可选自由）；
  * 每帧读图 → 1D 积分 → 自动基线 → 一次全谱拟合，从上一帧晶胞起步；
  * 输出每相的相对强度分数 f_i = A_i / ΣA 与晶胞；不做 LMFP 的
    rich/poor 语义，也不做双起点/两段少数相——通用模式先保证能跑通。

分数口径与 `insitu._fractions` 同源：
    A_main = scale × Σ(peak_table_intensities) × norm / 100
    A_extra(Le Bail) = Σ(i_fit) × norm / 100
    A_extra(结构因子) = scale × table_sum × norm / 100
"""
import csv
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from xrd_toolkit.simulation import b_iso_from_cif, fit_pattern, load_structure
from xrd_toolkit.simulation.phase_set import (
    BatchPolicy, PhaseSet, PhaseSpec, REFERENCE, SAMPLE, _safe_label)
from xrd_toolkit.simulation.insitu import frames_in_dir, load_frame
from xrd_toolkit.services.mask import load_mask

CELL_JUMP = 0.30          # 通用模式：任一晶胞轴跳 > 0.30 Å 标 cell_jump


def _safe_label(path, used):
    base = re.sub(r"[^0-9A-Za-z_]", "_", Path(path).stem) or "phase"
    label = base
    i = 2
    while label in used:
        label = f"{base}_{i}"
        i += 1
    used.add(label)
    return label


def cell_free_params(structure):
    """该结构允许精修的晶胞参数：立方 a；正交 a/b/c；其它不放开。"""
    lat = structure.lattice
    ortho = all(abs(float(ang) - 90.0) < 1e-3
                for ang in (lat.alpha, lat.beta, lat.gamma))
    if not ortho:
        return ()
    if (abs(float(lat.a) - float(lat.b)) < 1e-3
            and abs(float(lat.b) - float(lat.c)) < 1e-3):
        return ("a",)
    return ("a", "b", "c")


# 兼容旧名字：PhaseSpec 就是通用相描述（额外加了 display_name/role/
# include_in_fraction 等字段）。旧代码读 `.lebail` 的用 property 映射。
GenericPhase = PhaseSpec


def make_phases(paths, *, cell_free=True, lebail=False):
    """读 CIF 列表 → GenericPhase 列表（第一相是主相）。"""
    used = set()
    phases = []
    for i, path in enumerate(paths):
        path = Path(path)
        structure = load_structure(str(path))
        phases.append(GenericPhase(
            structure=structure, label=_safe_label(path, used),
            path=path, display_name=Path(path).stem,
            role=SAMPLE, cell_free=bool(cell_free),
            intensity="lebail" if (lebail and i > 0) else "structure",
            include_in_fraction=True,
            b_iso=b_iso_from_cif(str(path)) or 0.0))
    if not phases:
        raise ValueError("至少需要一张 CIF")
    return phases


def _main_label(phases):
    for phase in phases:
        if phase.is_sample():
            return phase.label
    return phases[0].label if phases else None


def _cells(result, phases, seed):
    """从结果取每相晶胞；没参与精修的轴退回种子值。"""
    out = {}
    main_label = _main_label(phases)
    for phase in phases:
        prefix = "" if phase.label == main_label else f"{phase.label}:"
        vals = []
        for j, key in enumerate(("a", "b", "c")):
            try:
                vals.append(float(result.params[prefix + key]))
            except (KeyError, TypeError, ValueError):
                vals.append(float(seed[phase.label][j]))
        out[phase.label] = tuple(vals)
    return out


def _amplitudes(result, phases):
    """每相的绝对散射强度 A_i（与 insitu._fractions 同口径）。"""
    amps = {}
    main_label = _main_label(phases)
    main = next((p for p in phases if p.label == main_label), None)
    try:
        amps[main.label] = (float(result.scale)
                            * float(np.sum(result.peak_table_intensities))
                            * float(result.peak_table_norm) / 100.0)
    except (AttributeError, TypeError, ValueError):
        amps[main.label] = float("nan")
    for phase in phases:
        if phase is main:
            continue
        try:
            if phase.lebail:
                block = result.extra_lebail[phase.label]
                amps[phase.label] = (float(np.sum(block["i_fit"]))
                                     * float(block["norm"]) / 100.0)
            else:
                amps[phase.label] = (
                    float(result.params[f"{phase.label}:scale"])
                    * float(result.extra_phase_table_sum[phase.label])
                    * float(result.extra_phase_norm[phase.label]) / 100.0)
        except (AttributeError, KeyError, TypeError, ValueError):
            amps[phase.label] = float("nan")
    return amps


def fractions(result, phases):
    """每相相对强度分数 f_i = A_i / ΣA（只除计入分母的相）。

    参考相（Al 等）默认 `include_in_fraction=False`：它的 A_i 仍算出来，
    但 f 记 NaN、不参与样品相归一；调用方可用 `amplitudes` 单独报告。
    全非有限时返回全 NaN。
    """
    amps = _amplitudes(result, phases)
    included = {p.label for p in phases
                if getattr(p, "include_in_fraction", True)}
    total = sum(v for label, v in amps.items()
                if label in included and math.isfinite(v) and v > 0)
    if not (math.isfinite(total) and total > 0):
        return {label: float("nan") for label in amps}
    return {label: (value / total
                    if label in included and math.isfinite(value)
                    and value > 0 else float("nan"))
            for label, value in amps.items()}


def window_stats(tth, y, window):
    """模型无关峰窗指标：局部线性背景后的面积/质心/峰高。

    `window` 是 PhaseSpec 的 PeakWindow；两侧平带优先用 side_lo/side_hi，
    没给就取窗口两端各 15% 的点做背景。返回 (area, centroid, height)。
    """
    t = np.asarray(tth, dtype=float)
    y = np.asarray(y, dtype=float)
    lo, hi = float(window.tth_min), float(window.tth_max)
    m = (t >= lo) & (t <= hi) & np.isfinite(y)
    if np.count_nonzero(m) < 8:
        return float("nan"), float("nan"), float("nan")
    side = np.zeros_like(m)
    if window.side_lo:
        side |= (t >= window.side_lo[0]) & (t <= window.side_lo[1])
    if window.side_hi:
        side |= (t >= window.side_hi[0]) & (t <= window.side_hi[1])
    if np.count_nonzero(side) < 4:
        span = max(hi - lo, 1e-9)
        side = ((t >= lo) & (t <= lo + 0.15 * span)) | \
               ((t >= hi - 0.15 * span) & (t <= hi))
        side &= m
    if np.count_nonzero(side) >= 4:
        coef = np.polyfit(t[side], y[side], 1)
        yy = y[m] - np.polyval(coef, t[m])
    else:
        yy = y[m] - float(np.median(y[m]))
    area = float(np.trapezoid(yy, t[m]))
    height = float(np.max(yy)) if yy.size else float("nan")
    if not math.isfinite(area) or area <= 0:
        return area, float("nan"), height
    centroid = float(np.trapezoid(t[m] * yy, t[m]) / area)
    return area, centroid, height


def _status(result, cells, seed, max_rwp, phases=(), policy=None,
            *, check_cells=True):
    if result is None:
        return "error"
    if not math.isfinite(float(result.rwp)) or result.rwp > max_rwp:
        return f"high_rwp({float(result.rwp):.1f}%)"
    default = (policy.cell_jump_default if policy is not None else CELL_JUMP)
    if not check_cells:
        phases = ()
    for phase in phases or ():
        label = phase.label
        got = cells.get(label)
        if got is None:
            continue
        tol = phase.jump_tol if phase.jump_tol is not None else default
        if any(abs(a - b) > tol for a, b in zip(got, seed[label])):
            return "cell_jump"
    return "ok" if bool(getattr(result, "success", True)) else "ok_soft"


class GenericBatch:
    """任意多相的顺序精修批次。"""

    def __init__(self, phase_paths, geom, wavelength, *, instrument=None,
                 mask=None, max_rwp=20.0, first_nfev=120, frame_nfev=40,
                 npt=3000, tth_min=1.0, tth_max=8.0, outdir=None,
                 extra_lebail=False, extra_cell=False,
                 phases=None, phase_set=None, policy=None):
        """phase_paths（旧接口）或 phases/phase_set（PhaseSpec）二选一。

        旧接口保持原行为：第一相主相、其余额外相，额外相的 Le Bail/
        晶胞由两个全局开关决定。新接口直接用 PhaseSpec 的 role/cell_free/
        intensity/include_in_fraction，全局开关不再生效。
        """
        self.phase_set = phase_set
        if phase_set is not None:
            self.phases = list(phase_set.phases)
            base_policy = phase_set.policy
        elif phases is not None:
            self.phases = list(phases)
            base_policy = policy or BatchPolicy.generic()
        else:
            self.phases = make_phases(phase_paths)
            for i, phase in enumerate(self.phases):
                phase.cell_free = True if i == 0 else bool(extra_cell)
                phase.lebail = bool(extra_lebail and i > 0)
            base_policy = policy or BatchPolicy.generic()
        self.policy = base_policy
        self.windows = list(getattr(getattr(phase_set, "views", None),
                                    "peak_windows", []) or [])
        self.geom = dict(geom)
        self.lam = float(wavelength)
        self.instrument = dict(instrument or {})
        self.mask = mask
        self.max_rwp = float(max_rwp)
        self.first_nfev = int(first_nfev)
        self.frame_nfev = int(frame_nfev)
        self.npt = int(npt)
        self.tth_min = float(tth_min)
        self.tth_max = float(tth_max)
        self.outdir = Path(outdir) if outdir else None
        self.rows = []
        self.fitted = []          # [(i, path, result, fracs, status)]
        self.pending_seed = None
        self.pending_count = 0
        self.frame_no = 0
        self.seed = {phase.label: (tuple(float(x) for x in phase.cell0)
                                   if getattr(phase, "cell0", None)
                                   else tuple(float(x) for x in
                                              phase.structure.lattice.abc))
                     for phase in self.phases}

    def _main_phase(self):
        """第一张样品相是 fit_pattern 的 main；其余相（含样品相）走 extra。"""
        for phase in self.phases:
            if phase.is_sample():
                return phase
        raise ValueError("至少要有一张样品相（role=sample）")

    # ── 拟合一帧 ────────────────────────────────────────────
    def _fit_once(self, t, y, bg, max_nfev, seed=None):
        seed = self.seed if seed is None else seed
        main = self._main_phase()
        main_free = cell_free_params(main.structure) if main.cell_free else ()
        # 样品展宽必须放开：固定成"无限大晶粒"会把真实峰的宽度算错，
        # 实测 Rwp 会从几个百分点冲到 60% 以上。
        free = ("zero",) + tuple(main_free) + (
            ("size_um", "mustrain")
            if (main.width_free or getattr(main, "free_extra", ())) else ())
        if "b_iso" in (getattr(main, "free_extra", ()) or ()):
            free = free + ("b_iso",)
        init = {"zero": float(self.instrument.get("zero", 0.0)),
                "u": float(self.instrument.get("u", 0.0)),
                "v": float(self.instrument.get("v", 0.0)),
                "w": float(self.instrument.get("w", 0.0)),
                "x": float(self.instrument.get("x", 0.0)),
                "y": float(self.instrument.get("y", 0.0)),
                "size_um": float(main.size_um),
                "mustrain": float(main.mustrain)}
        for j, key in enumerate(("a", "b", "c")):
            init[key] = float(seed[main.label][j])
        extra_bounds = {}
        for key, (lo, hi) in (getattr(main, "cell_bounds", {}) or {}).items():
            extra_bounds[key] = (float(lo), float(hi))
        extras = []
        for phase in self.phases:
            if phase is main:
                continue
            for key, (lo, hi) in (getattr(phase, "cell_bounds", {}) or {}).items():
                extra_bounds[f"{phase.label}:{key}"] = (float(lo), float(hi))
            spec = {
                "structure": phase.structure,
                "label": phase.label,
                "b_iso": float(phase.b_iso),
                "size_um": float(phase.size_um),
                "mustrain": float(phase.mustrain),
                "refine_intensities": bool(phase.lebail),
                "refine_cell": bool(phase.cell_free
                                    and cell_free_params(phase.structure)),
                "free": tuple(getattr(phase, "free_extra", ()) or ()),
            }
            for j, key in enumerate(("a", "b", "c")):
                spec[key] = float(seed[phase.label][j])
            extras.append(spec)
        stages = None
        if self.policy.joint_stage:
            names = list(free)
            for phase in self.phases:
                if phase is main or not phase.cell_free:
                    continue
                for key in cell_free_params(phase.structure):
                    names.append(f"{phase.label}:{key}")
            stages = [tuple(names)]
        return fit_pattern(
            main.structure, t, y, wavelength=self.lam,
            b_iso=float(main.b_iso),
            background=bg, background_knots=0.25, background_smooth=0.1,
            weights="poisson", cell_refine=bool(main_free),
            extra_phases=extras, phase_label=main.label,
            free=free, initial=init, max_nfev=int(max_nfev),
            stages=stages, bounds=extra_bounds or None,
            xtol=self.policy.xtol, ftol=self.policy.ftol,
            gtol=self.policy.gtol)

    def _cif_seed(self):
        return {phase.label: (tuple(float(x) for x in phase.cell0)
                              if getattr(phase, "cell0", None)
                              else tuple(float(x) for x in
                                         phase.structure.lattice.abc))
                for phase in self.phases}

    def _is_cif_seed(self, seed):
        cif = self._cif_seed()
        for phase in self.phases:
            if not phase.cell_free:
                continue
            if any(abs(float(a) - float(b)) > 1e-4
                   for a, b in zip(seed[phase.label], cif[phase.label])):
                return False
        return True

    def _choose_fit(self, seed_res, cif_res):
        """Rwp 优先；差 ≤ policy.tie_rwp 时取晶胞变化小的解。"""
        cands = [r for r in (seed_res, cif_res)
                 if r is not None and math.isfinite(float(r.rwp))]
        if not cands:
            return seed_res if seed_res is not None else cif_res
        best = min(cands, key=lambda r: float(r.rwp))
        if (len(cands) == 2
                and abs(float(seed_res.rwp) - float(cif_res.rwp))
                <= self.policy.tie_rwp):
            def _change(result):
                cells = _cells(result, self.phases, self.seed)
                return sum(abs(float(a) - float(b))
                           for label, got in cells.items()
                           for a, b in zip(got, self.seed[label]))
            best = min(cands, key=lambda r: _change(r))
        return best

    def fit_frame(self, path):
        """跑一帧，返回 row dict；异常也返回 error 行，不抛出。"""
        self.frame_no += 1
        i = self.frame_no
        path = Path(path)
        result = None
        timers = {}
        status = "error"
        try:
            t, y, bg, timers = load_frame(
                path, self.geom, self.mask, npt=self.npt,
                tth_min=self.tth_min, tth_max=self.tth_max)
            win_vals = []
            for window in self.windows:
                win_vals.append(window_stats(t, y, window))
            t0 = time.perf_counter()
            nfev = self.first_nfev if i == 1 else self.frame_nfev
            res_seed = self._fit_once(t, y, bg, nfev)
            res_cif = None
            cif_is_seed = self._is_cif_seed(self.seed)
            mode = str(self.policy.dual_start or "off")
            if mode == "always":
                need_second = not cif_is_seed
            elif mode == "auto":
                need_second = (not cif_is_seed
                               and not bool(getattr(res_seed, "success",
                                                    True)))
            else:
                need_second = False
            if need_second:
                res_cif = self._fit_once(t, y, bg, nfev,
                                         seed=self._cif_seed())
            result = self._choose_fit(res_seed, res_cif)
            timers["fit"] = time.perf_counter() - t0
            timers["dual_start"] = int(res_cif is not None)
            cells = _cells(result, self.phases, self.seed)
            status = _status(result, cells, self.seed, self.max_rwp,
                             self.phases, self.policy,
                             check_cells=(i > 1))
        except Exception as err:                       # noqa: BLE001
            status = f"error: {type(err).__name__}: {err}"
            win_vals = [(float("nan"),) * 3 for _ in self.windows]
            cells = {phase.label: self.seed[phase.label]
                     for phase in self.phases}
        cells = _cells(result, self.phases, self.seed) if result is not None \
            else {phase.label: self.seed[phase.label] for phase in self.phases}
        fracs = (fractions(result, self.phases) if result is not None
                 else {phase.label: float("nan") for phase in self.phases})
        amps = (_amplitudes(result, self.phases) if result is not None
                else {phase.label: float("nan") for phase in self.phases})
        row = {"frame": i, "file": path.name, "status": status,
               "usable": (result is not None
                          and not status.startswith(("error", "high_rwp"))
                          and status != "no_phase_signal"),
               "rwp": (float(result.rwp) if result is not None
                       else float("nan")),
               "rp": (float(result.rp) if result is not None
                      else float("nan"))}
        weights = {}
        for weight in (getattr(result, "phase_weights", None) or []):
            try:
                weights[str(weight.label)] = float(weight.weight_percent)
            except (AttributeError, TypeError, ValueError):
                continue
        # 样品相的 wt 在样品相之间归一（参考相不进分母）；参考相保留
        # 对全部晶态相的原始 wt，单独报告。
        sample_labels = {p.label for p in self.phases if p.is_sample()}
        wt_total = sum(v for label, v in weights.items()
                       if label in sample_labels and math.isfinite(v) and v > 0)
        if math.isfinite(wt_total) and wt_total > 0:
            weights = {label: (100.0 * v / wt_total
                               if label in sample_labels else v)
                       for label, v in weights.items()}
        stderr = dict(getattr(result, "stderr", None) or {}) if result else {}
        main_label = _main_label(self.phases)
        for phase in self.phases:
            row[f"f_{phase.label}"] = fracs.get(phase.label, float("nan"))
            row[f"a_{phase.label}"] = amps.get(phase.label, float("nan"))
            row[f"wt_{phase.label}"] = weights.get(phase.label, float("nan"))
            prefix = "" if phase.label == main_label else f"{phase.label}:"
            for key, value in zip(("a", "b", "c"), cells[phase.label]):
                row[f"{phase.label}_{key}"] = float(value)
                try:
                    row[f"{phase.label}_{key}_esd"] = float(
                        stderr.get(prefix + key, float("nan")))
                except (TypeError, ValueError):
                    row[f"{phase.label}_{key}_esd"] = float("nan")
        for k, vals in enumerate(win_vals):
            for name, value in zip(("area", "centroid", "height"), vals):
                row[f"win_{k}_{name}"] = float(value)
        for key in ("load", "integrate", "bg", "fit", "total"):
            row[f"t_{key}_s"] = float(timers.get(key, float("nan")))
        self.rows.append(row)
        main_label = _main_label(self.phases)
        f_main = fracs.get(main_label, float("nan"))
        self.fitted.append((i, path, result, f_main, status))
        # 种子更新：晶胞正常立刻接；跳变后连续 pending_frames 帧落在
        # 同一个新晶胞也接受（与 LMFP 专用引擎同口径）。
        if row["usable"]:
            new_seed = {label: tuple(cells[label]) for label in cells}
            cell_ok = (i == 1) or all(
                all(abs(float(a) - float(b))
                    <= (phase.jump_tol if phase.jump_tol is not None
                        else self.policy.cell_jump_default)
                    for a, b in zip(cells[phase.label], self.seed[phase.label]))
                for phase in self.phases)
            if cell_ok:
                self.seed = new_seed
                self.pending_seed = None
                self.pending_count = 0
            elif self.policy.pending_frames > 0:
                tol = float(self.policy.pending_tol)
                pending = self.pending_seed
                same = (pending is not None and all(
                    all(abs(float(a) - float(b)) <= tol
                        for a, b in zip(cells[label], pending[label]))
                    for label in cells))
                if same:
                    self.pending_count += 1
                else:
                    self.pending_seed = new_seed
                    self.pending_count = 1
                if self.pending_count >= int(self.policy.pending_frames):
                    self.seed = new_seed
                    self.pending_seed = None
                    self.pending_count = 0
        return row

    # ── 输出 ────────────────────────────────────────────────
    def columns(self):
        cols = ["frame", "file", "status", "usable", "rwp", "rp"]
        for phase in self.phases:
            cols.append(f"f_{phase.label}")
            cols.append(f"a_{phase.label}")
            cols.append(f"wt_{phase.label}")
            cols += [f"{phase.label}_{key}" for key in ("a", "b", "c")]
            cols += [f"{phase.label}_{key}_esd" for key in ("a", "b", "c")]
        for k in range(len(self.windows)):
            cols += [f"win_{k}_{name}"
                     for name in ("area", "centroid", "height")]
        cols += [f"t_{key}_s" for key in ("load", "integrate", "bg", "fit",
                                          "total")]
        return cols

    def write_csv(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.columns())
            writer.writeheader()
            writer.writerows(self.rows)
        return path


def frames_for_dir(folder, limit=0):
    """帧序列（复用 insitu 的排序/上限规则）。"""
    return frames_in_dir(Path(folder).expanduser(), limit=limit)


def load_mask_file(path):
    """掩膜文件（.npy/.edf/.mask.npz）→ bool 数组；None = 不遮。"""
    if not path:
        return None
    return load_mask(Path(path).expanduser())


class LMFPBatch(GenericBatch):
    """LMFP 三相等价实现的适配器（2026-11-17）。

    统一引擎下 LMFP 只是 lmfp_phase_set() 预设；旧工作台/报告仍读
    `wt_al` / `al_a` / `al_ref` / `f_rich_esd` 这些字段，这里补齐别名。
    结果几何/分数与专用 insitu.py 同口径（同一 fit_pattern + 同一策略）。
    """

    @staticmethod
    def _fraction_esd(result, a_rich, a_poor):
        """f_rich 的线性协方差传播（与旧引擎同口径，scale 块）。"""
        try:
            names = list(result.linear_names)
            cov = np.asarray(result.linear_cov, dtype=float)
            i_r = names.index("scale")
            i_p = names.index("poor:scale")
            k_r = (float(np.sum(result.peak_table_intensities))
                   * float(result.peak_table_norm) / 100.0)
            k_p = (float(result.extra_phase_table_sum["poor"])
                   * float(result.extra_phase_norm["poor"]) / 100.0)
        except (AttributeError, KeyError, TypeError, ValueError):
            return float("nan")
        total = float(a_rich) + float(a_poor)
        if not (math.isfinite(total) and total > 0
                and math.isfinite(k_r) and math.isfinite(k_p)
                and k_r != 0 and k_p != 0 and cov.ndim == 2):
            return float("nan")
        jr = k_r * float(a_poor) / total ** 2
        jp = -k_p * float(a_rich) / total ** 2
        sub = cov[np.ix_([i_r, i_p], [i_r, i_p])]
        var = float(np.array([jr, jp]) @ sub @ np.array([jr, jp]))
        return math.sqrt(var) if math.isfinite(var) and var >= 0 else float("nan")

    def fit_frame(self, path):
        row = super().fit_frame(path)
        if "wt_Al" in row:
            row["wt_al"] = row["wt_Al"]
        if "Al_a" in row:
            row["al_a"] = row["Al_a"]
        row["al_ref"] = row.get("a_Al", float("nan"))
        row["f_rich_esd"] = self._fraction_esd(
            self.fitted[-1][2] if self.fitted else None,
            row.get("a_rich", float("nan")), row.get("a_poor", float("nan")))
        return row
