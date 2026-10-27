#!/usr/bin/env python3
"""LMFP 原位序列的三相全精修批处理（2026-10-27，Week 9 任务二）。

三相：
  * LMFP_rich  富锂相（LiMn0.6Fe0.4PO4，主相，晶胞 a/b/c 自由）
  * LMFP_poor  贫锂相（Mn0.6Fe0.4PO4 heterosite，额外相，晶胞 a/b/c 自由）
  * Al         集流体参考相（立方，a 固定 4.061 Å；峰强逐反射自由）

流程：
  1. LaB6 标样定 zero/U/V/W/X/Y（之后固定，逐帧不再精修峰形）；
  2. 每帧：读图 → mask → 1D 积分 → 自动基线；
  3. 第一帧用较多迭代充分收敛；后续帧继承前一帧的 zero 与两相晶胞；
  4. 不收敛/高 Rwp/晶胞跳变的帧标异常，**不中断**，下一帧仍用上一个
     好种子；
  5. 输出 CSV（帧号、相分数、两相晶胞、Rwp/Rp、状态、分段耗时）、
     首/中/末三帧拟合图和相分数趋势图；实测耗时写进 summary。

相分数口径（2026-10-27 与用户定的第一版）：
  A_p = scale_p × Σ(table.inten_p) × table.norm_p / 100
  f_rich = A_rich / (A_rich + A_poor)，Al 只作参考、不参与 LMFP 分数。
  这是**相对散射强度分数**，不是 Rietveld 重量分数（绝对标度/密度/
  分子量未校正）；看趋势可以，别当 wt% 写。

用法（数据在桌面）：
    python scripts/fit_insitu.py --data ~/Desktop/lmfpdata \
        --mask outputs/masks/lmfp1_lab6_mask.npy \
        --outdir outputs/insitu

    # 先跑 3 帧看流程：
    python scripts/fit_insitu.py ... --limit 3
"""
import argparse
import csv
import math
import re
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.config import get_config  # noqa: E402
from xrd_toolkit.services.background import (  # noqa: E402
    estimate_baseline_sliding)
from xrd_toolkit.services.data_loader import load_diffraction_image  # noqa: E402
from xrd_toolkit.services.integrator import integrate_1d  # noqa: E402
from xrd_toolkit.services.mask import (  # noqa: E402
    load_mask, mask_stats)
from xrd_toolkit.simulation import (  # noqa: E402
    b_iso_from_cif, fit_pattern, load_structure, plot_fit)

DEFAULT_DATA = Path.home() / "Desktop" / "lmfpdata"
DEFAULT_MASK = Path("outputs") / "masks" / "lmfp1_lab6_mask.npy"
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
    'a': (10.20, 10.50), 'b': (5.90, 6.15), 'c': (4.65, 4.76),
    'LMFP_poor:a': (9.50, 10.35),
    'LMFP_poor:b': (5.50, 6.05),
    'LMFP_poor:c': (4.65, 4.90),
}
# 背景曲率罚项（同 fit_lmfp.py）
BG_SMOOTH = 0.1


def _tag(path: Path) -> int:
    """从 LMFP_1_atten0-00029.tif 取 29；取不到时退回 0。"""
    m = re.search(r"(\d+)$", path.stem)
    return int(m.group(1)) if m else 0


def _frames(data_dir: Path, limit: int, stride: int) -> list:
    files = sorted(Path(data_dir).glob("*.tif"), key=_tag)
    files = files[::max(1, int(stride))]
    if limit:
        files = files[:int(limit)]
    return files


def _instrument(geom, lam):
    """LaB6 标样：Le Bail 定 zero/U/V/W/X/Y；失败时返回 None。"""
    if not LAB6_TIF.exists():
        return None
    st = load_structure(str(LAB6_CIF))
    b6 = b_iso_from_cif(str(LAB6_CIF))
    img = load_diffraction_image(str(LAB6_TIF))
    t, y = integrate_1d(img, npt=3000, tth_min_deg=1.0,
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


def _cell(result, label=None):
    keys = ("a", "b", "c") if label is None else (
        f"{label}:a", f"{label}:b", f"{label}:c")
    try:
        return tuple(float(result.params[k]) for k in keys)
    except KeyError:
        return (float("nan"),) * 3


def _fit_one(path, rich, poor, al, geom, lam, inst, mask,
             seed, *, max_nfev):
    """一帧：读图/积分/背景/三相结构精修 + 计时。"""
    timers = {}
    t0 = time.perf_counter()
    img = load_diffraction_image(str(path))
    timers["load"] = time.perf_counter() - t0
    t1 = time.perf_counter()
    t, y = integrate_1d(img, npt=3000, tth_min_deg=1.0,
                       tth_max_deg=8.0, mask=mask, **geom)
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
    """异常判据：不收敛/高 Rwp/晶胞跳变/无相信号。"""
    if res is None:
        return "error"
    if not res.success:
        return "not_converged"
    if not math.isfinite(res.rwp) or res.rwp > max_rwp:
        return f"high_rwp({res.rwp:.1f}%)"
    if not math.isfinite(f_rich):
        return "no_phase_signal"
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
    _, _, A_al, _ = _fractions(res) if res is not None else (
        float("nan"),) * 4
    esd = res.stderr if res is not None else {}

    def _e(name):
        v = esd.get(name)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return float("nan")
        return v if math.isfinite(v) else float("nan")

    return {
        "frame": frame,
        "file": path.name,
        "status": status,
        "usable": bool(usable),
        "rwp": res.rwp if res is not None else float("nan"),
        "rp": res.rp if res is not None else float("nan"),
        "f_rich": f_rich,
        "f_poor": (1.0 - f_rich if math.isfinite(f_rich)
                   else float("nan")),
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


def _write_summary(outdir, rows, rigged_mask):
    ok = [r for r in rows if r["status"] == "ok"]
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
        f"ok: {len(ok)}  anomalies: {n - len(ok)}",
        f"usable: {len(usable)}/{n} (Rwp<=max, finite cells, no cell jump)",
        "status: " + ", ".join(f"{k}={v}" for k, v in sorted(status.items())),
        f"mask: {'on' if rigged_mask else 'off'}",
        f"total: {total:.1f} s  ({total / 60.0:.1f} min)",
        f"mean per frame: {means['total']:.1f} s",
        "mean split: load {load:.2f} s + integrate {integrate:.2f} s"
        " + bg {bg:.2f} s + fit {fit:.2f} s".format(**means),
        f"convergence: {100.0 * len(ok) / max(n, 1):.1f}%",
        f"2x frames estimate: {2 * total / 60.0:.1f} min"
        " (linear; integration and fit both scale with N)",
        "phase fractions: relative scattering intensity, not wt%.",
    ]
    path = outdir / "fit_summary.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path, means, total


def _trend_figure(outdir, rows):
    """相分数与晶胞随帧号的趋势；没有成功帧就不画。"""
    good = [r for r in rows
            if math.isfinite(r["f_rich"]) and math.isfinite(r["rwp"])]
    if not good:
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    x = [r["frame"] for r in good]
    fig, axes = plt.subplots(2, 2, figsize=(11, 7))
    ax = axes[0, 0]
    ax.plot(x, [r["f_rich"] for r in good], "o-", ms=2.5, label="LMFP rich")
    ax.plot(x, [r["f_poor"] for r in good], "s-", ms=2.5, label="LMFP poor")
    ax.set_ylabel("relative fraction"); ax.legend(fontsize=8)
    ax.grid(alpha=0.25, linestyle=":")
    for i, key in enumerate(("rich", "poor")):
        ax = axes[0, 1] if i == 0 else axes[1, 0]
        for j, axis in enumerate(("a", "b", "c")):
            ax.plot(x, [r[f"{key}_{axis}"] for r in good], "o-", ms=2.0,
                    label=axis)
        ax.set_ylabel(f"{key} cell (Å)"); ax.legend(fontsize=8)
        ax.grid(alpha=0.25, linestyle=":")
    ax = axes[1, 1]
    ax.plot(x, [r["rwp"] for r in good], "o-", ms=2.5, color="tab:red")
    ax.set_ylabel("Rwp (%)"); ax.set_xlabel("frame")
    ax.grid(alpha=0.25, linestyle=":")
    for ax in axes.ravel():
        ax.tick_params(labelsize=7)
    fig.suptitle("LMFP in-situ three-phase fit trends", fontsize=11)
    fig.tight_layout()
    path = outdir / "insitu_trends.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=str(DEFAULT_DATA),
                    help="原位帧目录（默认 ~/Desktop/lmfpdata）")
    ap.add_argument("--mask", default=str(DEFAULT_MASK),
                    help="mask 文件（.npy/.edf；空字符串 = 不遮）")
    ap.add_argument("--config", default="lmfp1_lab6")
    ap.add_argument("--rich", default=str(DEFAULT_RICH))
    ap.add_argument("--poor", default=str(DEFAULT_POOR))
    ap.add_argument("--al", default=str(DEFAULT_AL))
    ap.add_argument("--outdir", default=str(
        Path(tempfile.gettempdir()) / "xrd_toolkit_insitu"),
        help="输出目录（默认系统临时目录；交付时用 outputs/insitu）")
    ap.add_argument("--limit", type=int, default=0,
                    help="只跑前 N 帧（0 = 全部）")
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--max-rwp", type=float, default=20.0)
    ap.add_argument("--first-nfev", type=int, default=FIRST_NFEV)
    ap.add_argument("--frame-nfev", type=int, default=FRAME_NFEV)
    args = ap.parse_args()

    data_dir = Path(args.data).expanduser()
    if not data_dir.is_dir():
        ap.error(f"数据目录不存在：{data_dir}")
    frames = _frames(data_dir, args.limit, args.stride)
    if not frames:
        ap.error(f"目录里没有 .tif：{data_dir}")
    outdir = Path(args.outdir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)

    geom = dict(get_config(args.config)["geometry"])
    lam = geom["wavelength_m"] * 1e10
    rich = load_structure(args.rich)
    poor = load_structure(args.poor)
    al = load_structure(args.al)

    mask = None
    if args.mask:
        mp = Path(args.mask).expanduser()
        if not mp.is_absolute():
            mp = ROOT / mp
        if mp.exists():
            mask = load_mask(mp)
            print(f"mask: {mp} ({mask_stats(mask)['masked_pixels']} px)")
        else:
            print(f"警告：mask 不存在（{mp}），按不遮运行")

    inst = _instrument(geom, lam)
    if inst is None:
        ap.error("没有 LaB6 标样图，无法固定仪器峰形")
    print(f"LaB6: zero={inst.params['zero']:+.5f}  "
          f"U={inst.params['u']:.3f} V={inst.params['v']:.3f} "
          f"W={inst.params['w']:.3f} X={inst.params['x']:.3f} "
          f"Y={inst.params['y']:.3f}  (Rwp {inst.rwp:.3f}%)")

    # 首帧种子：用富锂 CIF 晶胞 + 贫锂 CIF 晶胞；之后逐帧继承
    seed = {"rich": tuple(float(x) for x in rich.lattice.abc),
            "poor": tuple(float(x) for x in poor.lattice.abc)}
    csv_path = outdir / "insitu_fit.csv"
    rows = []
    fitted = []
    fieldnames = None
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = None
        for i, path in enumerate(frames, start=1):
            print(f"[{i}/{len(frames)}] {path.name}", flush=True)
            timers = {}
            res = None
            status = "error"
            try:
                res, timers = _fit_one(
                    path, rich, poor, al, geom, lam, inst, mask, seed,
                    max_nfev=(args.first_nfev if i == 1
                              else args.frame_nfev))
                _, _, _, f_rich = _fractions(res)
                rich_cell = _cell(res)
                poor_cell = _cell(res, "LMFP_poor")
                status = _status(res, f_rich, rich_cell, poor_cell,
                                 seed, args.max_rwp,
                                 check_cells=(i > 1))
            except Exception as err:                   # noqa: BLE001
                f_rich = float("nan")
                status = f"error: {type(err).__name__}: {err}"
                print(f"  异常：{status}", flush=True)
            rich_cell = _cell(res) if res is not None else (
                float("nan"),) * 3
            poor_cell = _cell(res, "LMFP_poor") if res is not None else (
                float("nan"),) * 3
            finite_cells = all(math.isfinite(x)
                               for x in rich_cell + poor_cell)
            # not_converged 的帧也要检查晶胞跳变（_status 在不收敛时
            # 提前返回，没做这一步）；首帧允许从 CIF 大步收敛。
            cell_ok = (i == 1) or (
                all(abs(a - b) <= CELL_JUMP_RICH
                    for a, b in zip(rich_cell, seed["rich"]))
                and all(abs(a - b) <= CELL_JUMP_POOR
                        for a, b in zip(poor_cell, seed["poor"])))
            # 种子/usable 口径：Rwp 合格、晶胞有限且没有跳变、不是高
            # Rwp/错误就继承——`not_converged` 只是优化器退出标志，
            # Rwp 仍好时它的晶胞对下一帧比旧种子更有用（2026-10-27
            # 实测：只看 ok 会在相变段一直用旧种子）。
            # usable = 可用于趋势（Rwp 合格、晶胞/分数有限、非错误）；
            # cell_jump 仍标在 status 里，但不影响这一帧进趋势图。
            # seed_ok 才要求不跳变——它决定下一帧初值。
            usable = (res is not None and finite_cells
                      and math.isfinite(res.rwp)
                      and res.rwp <= args.max_rwp
                      and not status.startswith("error")
                      and not status.startswith("high_rwp")
                      and status != "no_phase_signal")
            seed_ok = usable and cell_ok
            row = _row(i, path, res, f_rich, timers, status,
                       usable=usable)
            if fieldnames is None:
                fieldnames = list(row)
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
            writer.writerow(row)
            fh.flush()
            rows.append(row)
            if seed_ok:
                seed = {"rich": rich_cell, "poor": poor_cell}
            if (res is not None and math.isfinite(f_rich)
                    and not status.startswith("error")):
                fitted.append((i, path, res, f_rich, status))
            print(f"  status={status}  Rwp={row['rwp']:.3f}%  "
                  f"f_rich={row['f_rich']:.3f}  "
                  f"fit={row['t_fit_s']:.1f}s", flush=True)

    summary_path, means, total = _write_summary(outdir, rows,
                                                mask is not None)
    print(f"\nCSV: {csv_path}\nsummary: {summary_path}")

    # 首/中/末三帧拟合图（只取成功帧）
    if fitted:
        # 首/中/末取**所有有限结果**（含 not_converged）：相变段
        # 往往是不收敛帧，只看 ok 会漏掉序列末端。
        fig_frames = fitted
        picks = [fig_frames[0], fig_frames[len(fig_frames) // 2],
                 fig_frames[-1]]
        for i, path, res, f_rich, status in picks:
            out = outdir / f"insitu_fit_frame_{i:03d}.png"
            plot_fit(
                res,
                f"{path.name} — frame {i}/{len(frames)}, "
                f"f_rich={f_rich:.3f}, Rwp={res.rwp:.2f}% ({status})",
                str(out), bragg="auto")
            print(f"figure: {out}")
    trend = _trend_figure(outdir, rows)
    if trend is not None:
        print(f"trend: {trend}")
    print(f"\n总计 {total:.1f} s（{total / 60.0:.1f} min），"
          f"平均 {means['total']:.1f} s/帧")


if __name__ == "__main__":
    main()
