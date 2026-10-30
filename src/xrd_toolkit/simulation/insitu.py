"""LMFP 原位序列的三相全精修引擎（2026-10-30 从脚本抽出）。

命令行 `scripts/fit_insitu.py` 与 GUI [原位] 工作台共用这份流程：

三相：
  * LMFP_rich  富锂相（LiMn0.6Fe0.4PO4，主相，晶胞 a/b/c 自由）
  * LMFP_poor  贫锂相（Mn0.6Fe0.4PO4 heterosite，额外相，晶胞 a/b/c 自由）
  * Al         集流体参考相（立方，a 固定 4.061 Å；峰强逐反射自由）

每帧：读图 → mask → 1D 积分 → 自动基线 → 三相结构精修。
第一帧用较多迭代充分收敛；后续帧继承前一帧的零点和两相晶胞；不收敛/
高 Rwp/晶胞跳变的帧标异常，不中断，下一帧仍用上一个好种子。

相分数口径（2026-10-27 与用户定的第一版）：
  A_p = scale_p × Σ(table.inten_p) × table.norm_p / 100
  f_rich = A_rich / (A_rich + A_poor)，Al 只作参考、不参与 LMFP 分数。
这是**相对散射强度分数**，不是 Rietveld 重量分数；看趋势可以，别当 wt%。
"""
import math
import re
import time
from pathlib import Path

import numpy as np

from xrd_toolkit.config import get_config
from xrd_toolkit.paths import ROOT
from xrd_toolkit.services.background import estimate_baseline_sliding
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.services.integrator import integrate_1d
from xrd_toolkit.services.mask import load_mask
from xrd_toolkit.simulation import (
    b_iso_from_cif, fit_pattern, load_structure)

DEFAULT_RICH = ROOT / "data" / "structures" / "LiMn0.6Fe0.4PO4_olivine.cif"
DEFAULT_POOR = (ROOT / "data" / "structures"
                / "Mn0.6Fe0.4PO4_heterosite_rich_axis.cif")
DEFAULT_AL = ROOT / "data" / "structures" / "Al_metal.cif"
LAB6_TIF = ROOT / "data" / "lab6-00024.tif"
LAB6_CIF = ROOT / "data" / "structures" / "LaB6_NIST660c.cif"

# 与 fit_lmfp.py 一致的 Al 参考晶胞（实测峰位）
AL_A = 4.061
# 第一帧/后续帧的最大函数评估次数（时间-稳定性折中）
FIRST_NFEV = 120
FRAME_NFEV = 40
# 异常判据：晶胞相对上一好种子跳超过这么多 Å 就标记（不中止）
CELL_JUMP_RICH = 0.12
CELL_JUMP_POOR = 0.25
# 物理晶胞边界：贫锂相不能漂到比富锂还大（pilot 里 b 曾冲到 6.83）
CELL_BOUNDS = {
    "a": (10.20, 10.50), "b": (5.90, 6.15), "c": (4.65, 4.76),
    "LMFP_poor:a": (9.50, 10.35),
    "LMFP_poor:b": (5.50, 6.05),
    "LMFP_poor:c": (4.65, 4.90),
}
# 背景曲率罚项（同 fit_lmfp.py）
BG_SMOOTH = 0.1
# 积分口径（与脚本一致）
NPT = 3000
TTH_MIN = 1.0
TTH_MAX = 8.0


def _tag(path):
    """从 LMFP_1_atten0-00029.tif 取 29；取不到时退回 0。"""
    m = re.search(r"(\d+)$", Path(path).stem)
    return int(m.group(1)) if m else 0


def frames_in_dir(data_dir, limit=0, stride=1):
    """目录里的 .tif，按帧号排序；limit=0 表示全部。"""
    files = sorted(Path(data_dir).glob("*.tif"), key=_tag)
    files = files[::max(1, int(stride))]
    if limit:
        files = files[:int(limit)]
    return files


# 脚本里叫 _frames；保留这个名字给旧调用点/测试
_frames = frames_in_dir


def _instrument(geom, lam, *, lab6_tif=None, lab6_cif=None):
    """LaB6 标样：Le Bail 定 zero/U/V/W/X/Y；失败时返回 None。"""
    lab6_tif = Path(lab6_tif) if lab6_tif is not None else LAB6_TIF
    lab6_cif = Path(lab6_cif) if lab6_cif is not None else LAB6_CIF
    if not lab6_tif.exists():
        return None
    st = load_structure(str(lab6_cif))
    b6 = b_iso_from_cif(str(lab6_cif))
    img = load_diffraction_image(str(lab6_tif))
    t, y = integrate_1d(img, npt=NPT, tth_min_deg=1.0,
                        tth_max_deg=8.0, **geom)
    bg = estimate_baseline_sliding(t, y, 0.2)
    return fit_pattern(
        st, t, y, wavelength=lam, b_iso=b6, background=bg,
        background_knots=0.25, weights="poisson",
        refine_intensities=True, free=("zero", "u", "v", "w", "x", "y"),
        max_nfev=200)


def _extra_phases(poor, al, poor_cell):
    """三相里的两个额外相：poor 结构 scale + 晶胞；Al 逐反射。"""
    return [
        {"structure": poor, "label": "LMFP_poor",
         "a": float(poor_cell[0]), "b": float(poor_cell[1]),
         "c": float(poor_cell[2]),
         "size_um": 0.1, "mustrain": 0.0, "b_iso": 0.5,
         "refine_cell": True, "refine_intensities": False},
        {"structure": al, "label": "Al",
         "a": AL_A, "b": AL_A, "c": AL_A,
         "size_um": 0.1, "mustrain": 0.0, "b_iso": 0.5,
         "refine_intensities": True},
    ]


def _fractions(result):
    """相对散射强度分数 + Al 参考强度。"""
    A_rich = (float(result.scale)
              * float(np.sum(result.peak_table_intensities))
              * float(result.peak_table_norm) / 100.0
              if result.peak_table_intensities is not None
              and result.peak_table_norm else float("nan"))
    A_poor = float("nan")
    try:
        A_poor = (float(result.params["LMFP_poor:scale"])
                  * float(result.extra_phase_table_sum["LMFP_poor"])
                  * float(result.extra_phase_norm["LMFP_poor"]) / 100.0)
    except (KeyError, TypeError, AttributeError):
        pass
    A_al = float("nan")
    try:
        eb = result.extra_lebail["Al"]
        A_al = float(np.sum(eb["i_fit"])) * float(eb["norm"]) / 100.0
    except (KeyError, TypeError, AttributeError):
        pass
    total = A_rich + A_poor
    f_rich = (A_rich / total
              if math.isfinite(total) and total > 0 else float("nan"))
    return A_rich, A_poor, A_al, f_rich


def _fraction_esd(result, A_rich, A_poor):
    """f_rich 的线性参数 esd（scale 协方差传播）。

    只含线性块（scale/强度修正/背景）的协方差；非线性参数（晶胞/峰形）
    的不确定度不在内，所以是**下限**。Al 的逐反射强度不进 f_rich。
    """
    try:
        names = list(result.linear_names)
        cov = np.asarray(result.linear_cov, dtype=float)
        if not names or cov.ndim != 2:
            return float("nan")
        i_r = names.index("scale")
        i_p = names.index("LMFP_poor:scale")
        k_r = (float(np.sum(result.peak_table_intensities))
               * float(result.peak_table_norm) / 100.0)
        k_p = (float(result.extra_phase_table_sum["LMFP_poor"])
               * float(result.extra_phase_norm["LMFP_poor"]) / 100.0)
    except (AttributeError, KeyError, TypeError, ValueError):
        return float("nan")
    total = A_rich + A_poor
    if not (math.isfinite(total) and total > 0
            and math.isfinite(k_r) and math.isfinite(k_p)
            and k_r != 0 and k_p != 0):
        return float("nan")
    Jr = k_r * A_poor / total ** 2
    Jp = -k_p * A_rich / total ** 2
    sub = cov[np.ix_([i_r, i_p], [i_r, i_p])]
    var = float(np.array([Jr, Jp]) @ sub @ np.array([Jr, Jp]))
    return math.sqrt(var) if math.isfinite(var) and var >= 0 else float("nan")


def _cell(result, label=None):
    keys = ("a", "b", "c") if label is None else (
        f"{label}:a", f"{label}:b", f"{label}:c")
    try:
        return tuple(float(result.params[k]) for k in keys)
    except KeyError:
        return (float("nan"),) * 3


def _fit_one(path, rich, poor, al, geom, lam, inst, mask, seed, *,
             max_nfev, npt=NPT, tth_min=TTH_MIN, tth_max=TTH_MAX):
    """一帧：读图/积分/背景/三相结构精修 + 计时。"""
    timers = {}
    t0 = time.perf_counter()
    img = load_diffraction_image(str(path))
    timers["load"] = time.perf_counter() - t0
    t1 = time.perf_counter()
    t, y = integrate_1d(img, npt=npt, tth_min_deg=tth_min,
                        tth_max_deg=tth_max, mask=mask, **geom)
    timers["integrate"] = time.perf_counter() - t1
    t2 = time.perf_counter()
    bg = estimate_baseline_sliding(t, y, 0.2)
    timers["bg"] = time.perf_counter() - t2
    init = {
        "zero": float(inst.params["zero"]),
        "u": float(inst.params["u"]), "v": float(inst.params["v"]),
        "w": float(inst.params["w"]), "x": float(inst.params["x"]),
        "y": float(inst.params["y"]),
        "a": float(seed["rich"][0]), "b": float(seed["rich"][1]),
        "c": float(seed["rich"][2]),
        "size_um": 0.093, "mustrain": 0.0, "b_iso": 0.0,
        "LMFP_poor:a": float(seed["poor"][0]),
        "LMFP_poor:b": float(seed["poor"][1]),
        "LMFP_poor:c": float(seed["poor"][2]),
    }
    t3 = time.perf_counter()
    res = fit_pattern(
        rich, t, y, wavelength=lam, b_iso=0.0, background=bg,
        background_knots=0.25, background_smooth=BG_SMOOTH,
        weights="poisson", cell_refine=True, intensity_poly=2,
        extra_phases=_extra_phases(poor, al, seed["poor"]),
        phase_label="LMFP_rich", free=("zero", "a", "b", "c"),
        initial=init,
        stages=[("zero", "a", "b", "c",
                 "LMFP_poor:a", "LMFP_poor:b", "LMFP_poor:c")],
        bounds=CELL_BOUNDS, max_nfev=max_nfev)
    timers["fit"] = time.perf_counter() - t3
    timers["total"] = time.perf_counter() - t0
    return res, timers


def _status(res, f_rich, rich_cell, poor_cell, seed, max_rwp,
            *, check_cells=True):
    """异常判据：高 Rwp/无相信号/晶胞跳变；不收敛但 Rwp 合格 = ok_soft。

    2026-10-28：`least_squares` 的 success=False 只说明优化器退出标志
    （相变/放电段 max_nfev 撞上限），不等于结果不可用。Rwp 和相分数
    都合格时改标 `ok_soft`，把「严格收敛」和「可用」分开报；真正发散
    （高 Rwp/无相信号）仍标异常。
    """
    if res is None:
        return "error"
    if not math.isfinite(res.rwp) or res.rwp > max_rwp:
        return f"high_rwp({res.rwp:.1f}%)"
    if not math.isfinite(f_rich):
        return "no_phase_signal"
    if not res.success:
        return "ok_soft"
    if check_cells:
        for got, want, lim in ((rich_cell, seed["rich"], CELL_JUMP_RICH),
                               (poor_cell, seed["poor"], CELL_JUMP_POOR)):
            if any(abs(a - b) > lim for a, b in zip(got, want)):
                return "cell_jump"
    return "ok"


def _row(frame, path, res, f_rich, timers, status, *, usable=False):
    rich_cell = _cell(res) if res is not None else (float("nan"),) * 3
    poor_cell = _cell(res, "LMFP_poor") if res is not None else (
        float("nan"),) * 3
    al_a = (float(res.params.get("Al:a", float("nan")))
            if res is not None else float("nan"))
    if res is not None:
        A_rich, A_poor, A_al, _ = _fractions(res)
        f_esd = _fraction_esd(res, A_rich, A_poor)
    else:
        A_al = f_esd = float("nan")
    esd = res.stderr if res is not None else {}

    def _e(name):
        value = esd.get(name)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return float("nan")
        return value if math.isfinite(value) else float("nan")

    return {
        "frame": frame,
        "file": Path(path).name,
        "status": status,
        "usable": bool(usable),
        "rwp": res.rwp if res is not None else float("nan"),
        "rp": res.rp if res is not None else float("nan"),
        "f_rich": f_rich,
        "f_poor": (1.0 - f_rich if math.isfinite(f_rich)
                   else float("nan")),
        "f_rich_esd": f_esd,
        "rich_a": rich_cell[0], "rich_b": rich_cell[1],
        "rich_c": rich_cell[2],
        "rich_a_esd": _e("a"), "rich_b_esd": _e("b"),
        "rich_c_esd": _e("c"),
        "poor_a": poor_cell[0], "poor_b": poor_cell[1],
        "poor_c": poor_cell[2],
        "poor_a_esd": _e("LMFP_poor:a"),
        "poor_b_esd": _e("LMFP_poor:b"),
        "poor_c_esd": _e("LMFP_poor:c"),
        "al_a": al_a,
        "al_ref": A_al,
        "t_load_s": timers.get("load", float("nan")),
        "t_integrate_s": timers.get("integrate", float("nan")),
        "t_bg_s": timers.get("bg", float("nan")),
        "t_fit_s": timers.get("fit", float("nan")),
        "t_total_s": timers.get("total", float("nan")),
    }


def write_summary(outdir, rows, mask_on):
    """把批处理摘要写到 outdir/fit_summary.txt。"""
    strict = [r for r in rows if r["status"] == "ok"]
    soft = [r for r in rows if r["status"] == "ok_soft"]
    usable = [r for r in rows if r.get("usable")]
    n = len(rows)
    total = float(np.nansum([r["t_total_s"] for r in rows]))
    means = {k: float(np.nanmean([r[f"t_{k}_s"] for r in rows]))
             for k in ("load", "integrate", "bg", "fit", "total")}
    status = {}
    for r in rows:
        key = r["status"].split("(", 1)[0]
        status[key] = status.get(key, 0) + 1
    lines = [
        "=== LMFP in-situ three-phase batch ===",
        f"frames: {n}",
        f"strict ok: {len(strict)}  ok_soft: {len(soft)}  "
        f"others: {n - len(strict) - len(soft)}",
        f"usable: {len(usable)}/{n} (Rwp<=max, finite cells, non-error)",
        "status: " + ", ".join(f"{k}={v}" for k, v in sorted(status.items())),
        f"mask: {'on' if mask_on else 'off'}",
        f"total: {total:.1f} s  ({total / 60.0:.1f} min)",
        f"mean per frame: {means['total']:.1f} s",
        "mean split: load {load:.2f} s + integrate {integrate:.2f} s"
        " + bg {bg:.2f} s + fit {fit:.2f} s".format(**means),
        f"strict convergence: {100.0 * len(strict) / max(n, 1):.1f}%"
        "  (ok_soft = optimizer exit flag, Rwp/phase fractions still good)",
        f"2x frames estimate: {2 * total / 60.0:.1f} min"
        " (linear; integration and fit both scale with N)",
        "phase fractions: relative scattering intensity, not wt%.",
    ]
    path = Path(outdir) / "fit_summary.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path, means, total


# 旧脚本名（测试与命令行沿用）
_write_summary = write_summary


class InSituBatch:
    """一批评分的状态机：种子继承、异常标记、逐帧结果。

    `fit_frame` 是纯计算（可在后台线程调用）：读图、积分、三相精修，
    把结果追加到 `rows`/`fitted`，并在可用帧上更新下一帧的种子。
    同一批只能串行调用（GUI 逐帧链式跑，不会并发）。
    """

    def __init__(self, *, rich, poor, al, geom, lam, instrument, mask,
                 max_rwp=20.0, first_nfev=FIRST_NFEV,
                 frame_nfev=FRAME_NFEV, npt=NPT,
                 tth_min=TTH_MIN, tth_max=TTH_MAX):
        self.rich = rich
        self.poor = poor
        self.al = al
        self.geom = dict(geom)
        self.lam = float(lam)
        self.instrument = instrument
        self.mask = mask
        self.max_rwp = float(max_rwp)
        self.first_nfev = int(first_nfev)
        self.frame_nfev = int(frame_nfev)
        self.npt = int(npt)
        self.tth_min = float(tth_min)
        self.tth_max = float(tth_max)
        self.seed = {
            "rich": tuple(float(x) for x in rich.lattice.abc),
            "poor": tuple(float(x) for x in poor.lattice.abc),
        }
        self.rows = []
        self.fitted = []
        self.frame_no = 0

    def fit_frame(self, path):
        """跑一帧，返回 row dict（异常也返回 error 行，不抛出）。"""
        self.frame_no += 1
        i = self.frame_no
        path = Path(path)
        res = None
        timers = {}
        status = "error"
        f_rich = float("nan")
        try:
            res, timers = _fit_one(
                path, self.rich, self.poor, self.al, self.geom, self.lam,
                self.instrument, self.mask, self.seed,
                max_nfev=(self.first_nfev if i == 1 else self.frame_nfev),
                npt=self.npt, tth_min=self.tth_min, tth_max=self.tth_max)
            _a, _p, _al, f_rich = _fractions(res)
            rich_cell = _cell(res)
            poor_cell = _cell(res, "LMFP_poor")
            status = _status(res, f_rich, rich_cell, poor_cell, self.seed,
                             self.max_rwp, check_cells=(i > 1))
        except Exception as err:                       # noqa: BLE001
            f_rich = float("nan")
            status = f"error: {type(err).__name__}: {err}"
        rich_cell = _cell(res) if res is not None else (
            float("nan"),) * 3
        poor_cell = _cell(res, "LMFP_poor") if res is not None else (
            float("nan"),) * 3
        finite_cells = all(math.isfinite(x)
                           for x in rich_cell + poor_cell)
        cell_ok = (i == 1) or (
            all(abs(a - b) <= CELL_JUMP_RICH
                for a, b in zip(rich_cell, self.seed["rich"]))
            and all(abs(a - b) <= CELL_JUMP_POOR
                    for a, b in zip(poor_cell, self.seed["poor"])))
        usable = (res is not None and finite_cells
                  and math.isfinite(res.rwp)
                  and res.rwp <= self.max_rwp
                  and not status.startswith("error")
                  and not status.startswith("high_rwp")
                  and status != "no_phase_signal")
        seed_ok = usable and cell_ok
        row = _row(i, path, res, f_rich, timers, status, usable=usable)
        self.rows.append(row)
        if seed_ok:
            self.seed = {"rich": rich_cell, "poor": poor_cell}
        if (res is not None and math.isfinite(f_rich)
                and not status.startswith("error")):
            self.fitted.append((i, path, res, f_rich, status))
        return row


def prepare_setup(config_name="lmfp1_lab6", *, rich_path=None, poor_path=None,
                  al_path=None, mask_path=None, max_rwp=20.0,
                  first_nfev=FIRST_NFEV, frame_nfev=FRAME_NFEV,
                  npt=NPT, tth_min=TTH_MIN, tth_max=TTH_MAX):
    """GUI [原位] 的准备步骤：读结构、定仪器、读 mask，返回 InSituBatch。

    这一步含 LaB6 拟合（数秒），GUI 要放在后台线程跑。
    """
    geom = dict(get_config(config_name)["geometry"])
    lam = geom["wavelength_m"] * 1e10
    rich = load_structure(str(rich_path or DEFAULT_RICH))
    poor = load_structure(str(poor_path or DEFAULT_POOR))
    al = load_structure(str(al_path or DEFAULT_AL))
    inst = _instrument(geom, lam)
    if inst is None:
        raise RuntimeError("没有 LaB₆ 标样图，无法固定仪器峰形")
    mask = None
    if mask_path:
        mask = load_mask(Path(mask_path).expanduser())
    return InSituBatch(
        rich=rich, poor=poor, al=al, geom=geom, lam=lam,
        instrument=inst, mask=mask, max_rwp=max_rwp,
        first_nfev=first_nfev, frame_nfev=frame_nfev,
        npt=npt, tth_min=tth_min, tth_max=tth_max)
