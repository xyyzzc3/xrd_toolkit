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


@dataclass
class GenericPhase:
    """通用模式里的一相：CIF + 是否放开晶胞 + 额外相是否 Le Bail。"""

    path: Path
    structure: object
    label: str
    cell_free: bool = True
    lebail: bool = False       # 额外相峰强是否逐反射自由（Le Bail）
    b_iso: float = 0.0


def make_phases(paths, *, cell_free=True, lebail=False):
    """读 CIF 列表 → GenericPhase 列表（第一相是主相）。"""
    used = set()
    phases = []
    for i, path in enumerate(paths):
        path = Path(path)
        structure = load_structure(str(path))
        phases.append(GenericPhase(
            path=path, structure=structure,
            label=_safe_label(path, used),
            cell_free=bool(cell_free), lebail=bool(lebail and i > 0),
            b_iso=b_iso_from_cif(str(path)) or 0.0))
    if not phases:
        raise ValueError("至少需要一张 CIF")
    return phases


def _cells(result, phases, seed):
    """从结果取每相晶胞；没参与精修的轴退回种子值。"""
    out = {}
    for i, phase in enumerate(phases):
        prefix = "" if i == 0 else f"{phase.label}:"
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
    main = phases[0]
    try:
        amps[main.label] = (float(result.scale)
                            * float(np.sum(result.peak_table_intensities))
                            * float(result.peak_table_norm) / 100.0)
    except (AttributeError, TypeError, ValueError):
        amps[main.label] = float("nan")
    for phase in phases[1:]:
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
    """每相相对强度分数 f_i = A_i / ΣA；全非有限时返回全 NaN。"""
    amps = _amplitudes(result, phases)
    total = sum(v for v in amps.values() if math.isfinite(v) and v > 0)
    if not (math.isfinite(total) and total > 0):
        return {label: float("nan") for label in amps}
    return {label: (value / total if math.isfinite(value) and value > 0
                    else float("nan"))
            for label, value in amps.items()}


def _status(result, cells, seed, max_rwp):
    if result is None:
        return "error"
    if not math.isfinite(float(result.rwp)) or result.rwp > max_rwp:
        return f"high_rwp({float(result.rwp):.1f}%)"
    for label, got in cells.items():
        if any(abs(a - b) > CELL_JUMP for a, b in zip(got, seed[label])):
            return "cell_jump"
    return "ok" if bool(getattr(result, "success", True)) else "ok_soft"


class GenericBatch:
    """任意多相的顺序精修批次。"""

    def __init__(self, phase_paths, geom, wavelength, *, instrument=None,
                 mask=None, max_rwp=20.0, first_nfev=120, frame_nfev=40,
                 npt=3000, tth_min=1.0, tth_max=8.0, outdir=None,
                 extra_lebail=False, extra_cell=False):
        self.phases = make_phases(phase_paths)
        for i, phase in enumerate(self.phases):
            phase.cell_free = True if i == 0 else bool(extra_cell)
            phase.lebail = bool(extra_lebail and i > 0)
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
        self.frame_no = 0
        self.seed = {phase.label: tuple(float(x)
                                        for x in phase.structure.lattice.abc)
                     for phase in self.phases}

    # ── 拟合一帧 ────────────────────────────────────────────
    def _fit_once(self, t, y, bg, max_nfev):
        main = self.phases[0]
        main_free = cell_free_params(main.structure) if main.cell_free else ()
        # 样品展宽必须放开：固定成"无限大晶粒"会把真实峰的宽度算错，
        # 实测 Rwp 会从几个百分点冲到 60% 以上。
        free = ("zero",) + tuple(main_free) + ("size_um", "mustrain")
        init = {"zero": float(self.instrument.get("zero", 0.0)),
                "u": float(self.instrument.get("u", 0.0)),
                "v": float(self.instrument.get("v", 0.0)),
                "w": float(self.instrument.get("w", 0.0)),
                "x": float(self.instrument.get("x", 0.0)),
                "y": float(self.instrument.get("y", 0.0)),
                "size_um": 0.1, "mustrain": 0.0}
        for j, key in enumerate(("a", "b", "c")):
            init[key] = float(self.seed[main.label][j])
        extras = []
        for phase in self.phases[1:]:
            spec = {
                "structure": phase.structure,
                "label": phase.label,
                "b_iso": float(phase.b_iso),
                "refine_intensities": bool(phase.lebail),
                "refine_cell": bool(phase.cell_free
                                    and cell_free_params(phase.structure)),
            }
            for j, key in enumerate(("a", "b", "c")):
                spec[key] = float(self.seed[phase.label][j])
            extras.append(spec)
        return fit_pattern(
            main.structure, t, y, wavelength=self.lam, b_iso=0.0,
            background=bg, background_knots=0.25, background_smooth=0.1,
            weights="poisson", cell_refine=bool(main_free),
            extra_phases=extras, phase_label=main.label,
            free=free, initial=init, max_nfev=int(max_nfev))

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
            t0 = time.perf_counter()
            result = self._fit_once(
                t, y, bg,
                self.first_nfev if i == 1 else self.frame_nfev)
            timers["fit"] = time.perf_counter() - t0
            cells = _cells(result, self.phases, self.seed)
            status = _status(result, cells, self.seed, self.max_rwp)
        except Exception as err:                       # noqa: BLE001
            status = f"error: {type(err).__name__}: {err}"
            cells = {phase.label: self.seed[phase.label]
                     for phase in self.phases}
        cells = _cells(result, self.phases, self.seed) if result is not None \
            else {phase.label: self.seed[phase.label] for phase in self.phases}
        fracs = (fractions(result, self.phases) if result is not None
                 else {phase.label: float("nan") for phase in self.phases})
        row = {"frame": i, "file": path.name, "status": status,
               "usable": (result is not None
                          and not status.startswith(("error", "high_rwp"))
                          and status != "no_phase_signal"),
               "rwp": (float(result.rwp) if result is not None
                       else float("nan")),
               "rp": (float(result.rp) if result is not None
                      else float("nan"))}
        for phase in self.phases:
            row[f"f_{phase.label}"] = fracs.get(phase.label, float("nan"))
            for key, value in zip(("a", "b", "c"), cells[phase.label]):
                row[f"{phase.label}_{key}"] = float(value)
        for key in ("load", "integrate", "bg", "fit", "total"):
            row[f"t_{key}_s"] = float(timers.get(key, float("nan")))
        self.rows.append(row)
        if row["usable"] and status != "cell_jump":
            self.seed = {label: tuple(cells[label])
                         for label in cells}
        return row

    # ── 输出 ────────────────────────────────────────────────
    def columns(self):
        cols = ["frame", "file", "status", "usable", "rwp", "rp"]
        for phase in self.phases:
            cols.append(f"f_{phase.label}")
            cols += [f"{phase.label}_{key}" for key in ("a", "b", "c")]
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
