#!/usr/bin/env python3
"""LMFP 原位序列的三相全精修批处理（2026-10-27，Week 9 任务二）。

命令行入口；默认走 `xrd_toolkit.simulation.insitu`（旧 LMFP 引擎，
81 帧金标准口径）。`--engine generic` 改走通用引擎 + LMFP 预设
（`simulation.insitu_generic`），与 GUI [原位] 工作台同一条代码路径。

三相：
  * LMFP_rich  富锂相（LiMn0.6Fe0.4PO4，主相，晶胞 a/b/c 自由）
  * LMFP_poor  贫锂相（Mn0.6Fe0.4PO4 heterosite，额外相，晶胞 a/b/c 自由）
  * Al         集流体参考相（立方，a 固定 4.061 Å；峰强逐反射自由）

流程：
  1. LaB6 标样定 zero/U/V/W/X/Y（之后固定，逐帧不再精修峰形）；
  2. 每帧：读图 → mask → 1D 积分 → 自动基线；
  3. 第一帧用较多迭代充分收敛；后续帧先跑「上一帧种子」；不收敛或
     种子 Rwp ≥ 2.45% 时再跑「CIF 端元」起点，取 Rwp 更低者
     （2026-11-24 定向双起点）；晶胞跳变后连续 2 帧落在同一个新晶胞
     时重锁种子；
  4. 不收敛/高 Rwp/晶胞跳变的帧标异常，**不中断**，下一帧仍用上一个
     好种子；
  5. 输出 CSV（帧号、相分数、两相晶胞、Rwp/Rp、状态、分段耗时）、
     首/中/末三帧拟合图和相分数趋势图；实测耗时写进 summary。

相分数口径（2026-10-27 与用户定的第一版）：
  A_p = scale_p × Σ(table.inten_p) × table.norm_p / 100
  f_rich = A_rich / (A_rich + A_poor)，Al 只作参考、不参与 LMFP 分数。
这是**相对散射强度分数**，看趋势用。2026-10-31 起 CSV/摘要另有
`wt_rich` / `wt_poor`（Rietveld 重量分数，只对晶态相；Al 单列、
不进 LMFP 分母），两种口径不要混着引用。

用法（数据在桌面）：
    python scripts/fit_insitu.py --data ~/Data/XRD/lmfpdata \
        --mask outputs/masks/lmfp1_lab6_mask.npy \
        --outdir outputs/insitu

    # 先跑 3 帧看流程：
    python scripts/fit_insitu.py ... --limit 3
"""
import argparse
import csv
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.simulation import plot_fit  # noqa: E402
from xrd_toolkit.simulation.insitu import (  # noqa: E402
    AUTO_RWP_GUARD, DEFAULT_AL, DEFAULT_POOR, DEFAULT_RICH, FIRST_NFEV,
    FRAME_NFEV, LAB6_CIF, LAB6_TIF, InSituBatch, _cell, _extra_phases,
    _fractions, _fraction_esd, _frames, _instrument, _row, _status, _tag,
    _write_summary, frames_in_dir, load_seed_cells, prepare_setup,
    INTENSITY_POLY)

DEFAULT_DATA = Path.home() / "Data" / "XRD" / "lmfpdata"
DEFAULT_MASK = Path("outputs") / "masks" / "lmfp1_lab6_mask.npy"


def _trend_figure(outdir, rows):
    """相分数与晶胞随帧号的趋势；没有成功帧就不画。"""
    good = [r for r in rows
            if math.isfinite(r["f_rich"]) and math.isfinite(r["rwp"])]
    if not good:
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from xrd_toolkit.simulation.insitu_plots import draw_trends

    fig = plt.figure(figsize=(11, 7))
    draw_trends(fig, rows)
    path = outdir / "insitu_trends.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def _guard_value(text):
    """--auto-rwp-guard：数字或 off/0（关闭 Rwp 守卫）。"""
    raw = str(text).strip().lower()
    if raw in ("off", "none", "0", ""):
        return 0.0
    try:
        return float(raw)
    except ValueError as err:
        raise argparse.ArgumentTypeError(
            f"需要数字或 off/0：{text}") from err


def _build_generic_batch(args, mask_path):
    """通用引擎 + LMFP 预设（与 GUI 同一条代码路径）。"""
    from xrd_toolkit.simulation.insitu_generic import prepare_batch
    from xrd_toolkit.simulation.phase_set import (
        lmfp_phase_set, phase_set_to_dict)

    pset = lmfp_phase_set(args.rich, args.poor, args.al)
    pset.policy.dual_start = "auto"
    pset.policy.auto_rwp_guard = args.auto_rwp_guard
    pset.policy.background_model = args.background_model
    pset.policy.background_order = args.background_order
    pset.policy.intensity_poly = args.intensity_poly
    if args.lab6:
        pset.instrument.lab6_tif = str(Path(args.lab6).expanduser())
    if args.lab6_cif:
        pset.instrument.lab6_cif = str(Path(args.lab6_cif).expanduser())
    batch = prepare_batch(
        args.config, phase_set_to_dict(pset), mask_path=mask_path,
        max_rwp=args.max_rwp, first_nfev=args.first_nfev,
        frame_nfev=args.frame_nfev)
    return batch, pset


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=str(DEFAULT_DATA),
                    help="原位帧目录（默认 ~/Data/XRD/lmfpdata）")
    ap.add_argument("--mask", default=str(DEFAULT_MASK),
                    help="掩膜文件（.npy/.edf；空字符串 = 不遮）")
    ap.add_argument("--config", default="lmfp1_lab6")
    ap.add_argument("--rich", default=str(DEFAULT_RICH))
    ap.add_argument("--poor", default=str(DEFAULT_POOR))
    ap.add_argument("--al", default=str(DEFAULT_AL))
    ap.add_argument("--lab6", default=str(LAB6_TIF),
                    help="LaB₆ 标样帧（用来定仪器峰形）")
    ap.add_argument("--lab6-cif", default=str(LAB6_CIF))
    ap.add_argument("--outdir", default=str(
        Path(tempfile.gettempdir()) / "xrd_toolkit_insitu"),
        help="输出目录（默认系统临时目录；交付时用 outputs/insitu）")
    ap.add_argument("--limit", type=int, default=0,
                    help="只跑前 N 帧（0 = 全部）")
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--frames", default="",
                    help="只跑指定帧（1 起的序号，逗号分隔；"
                         "给定时忽略 --limit/--stride）")
    ap.add_argument("--seed-csv", default="",
                    help="生产批次 insitu_fit.csv；跳帧时用上一帧晶胞作种子")
    ap.add_argument("--max-rwp", type=float, default=20.0)
    ap.add_argument("--first-nfev", type=int, default=FIRST_NFEV)
    ap.add_argument("--frame-nfev", type=int, default=FRAME_NFEV)
    ap.add_argument("--background-model",
                    choices=("estimated", "chebyshev"), default="estimated",
                    help="estimated = 滑动基线（生产默认）；"
                         "chebyshev = 背景与峰联合拟合")
    ap.add_argument("--background-order", type=int, default=10,
                    help="chebyshev 背景阶数")
    ap.add_argument("--intensity-poly", type=int, default=INTENSITY_POLY,
                    help="主相平滑强度修正阶数；与相比例简并，"
                         "定量验证建议 0")
    ap.add_argument("--engine", choices=("legacy", "generic"),
                    default="legacy",
                    help="legacy = 旧 LMFP 引擎（金标准）；"
                         "generic = 通用引擎 + LMFP 预设（与 GUI 同路径）")
    ap.add_argument("--auto-rwp-guard", type=_guard_value,
                    default=AUTO_RWP_GUARD,
                    help=f"自动双起点的 Rwp 守卫（默认 {AUTO_RWP_GUARD}；"
                         "off/0 = 只看 success）")
    args = ap.parse_args()

    data_dir = Path(args.data).expanduser()
    if not data_dir.is_dir():
        ap.error(f"数据目录不存在：{data_dir}")
    if args.frames:
        ordered = frames_in_dir(data_dir)
        index = {i + 1: p for i, p in enumerate(ordered)}
        wanted = sorted({int(x) for x in args.frames.split(",")
                         if x.strip()})
        missing = [n for n in wanted if n not in index]
        if missing:
            ap.error(f"帧不存在：{missing}（共 {len(index)} 帧）")
        frames = [index[n] for n in wanted]
        frame_numbers = wanted
    else:
        frames = frames_in_dir(data_dir, args.limit, args.stride)
        frame_numbers = list(range(1, len(frames) + 1))
    if not frames:
        ap.error(f"目录里没有 .tif：{data_dir}")
    seeds = load_seed_cells(args.seed_csv) if args.seed_csv else {}
    outdir = Path(args.outdir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)

    mask_path = None
    if args.mask:
        mp = Path(args.mask).expanduser()
        if not mp.is_absolute():
            mp = ROOT / mp
        if mp.exists():
            mask_path = str(mp)
        else:
            print(f"警告：掩膜文件不存在（{mp}），按不遮运行")

    pset = None
    try:
        if args.engine == "generic":
            batch, pset = _build_generic_batch(args, mask_path)
        else:
            batch = prepare_setup(
                args.config, rich_path=args.rich, poor_path=args.poor,
                al_path=args.al, mask_path=mask_path,
                lab6_tif=args.lab6, lab6_cif=args.lab6_cif,
                max_rwp=args.max_rwp,
                first_nfev=args.first_nfev, frame_nfev=args.frame_nfev,
                background_model=args.background_model,
                background_order=args.background_order,
                intensity_poly=args.intensity_poly,
                auto_rwp_guard=args.auto_rwp_guard)
    except Exception as err:                       # noqa: BLE001
        ap.error(f"准备失败：{err}")

    print(f"engine: {args.engine}")
    inst = batch.instrument
    if isinstance(inst, dict):
        bits = "  ".join(
            f"{key}={float(inst[key]):+.4g}"
            for key in ("zero", "u", "v", "w", "x", "y")
            if key in inst)
        print(f"LaB6: {bits}  (fixed from standard frame)")
    else:
        print(f"LaB6: zero={inst.params['zero']:+.5f}  "
              f"U={inst.params['u']:.3f} V={inst.params['v']:.3f} "
              f"W={inst.params['w']:.3f} X={inst.params['x']:.3f} "
              f"Y={inst.params['y']:.3f}  (Rwp {inst.rwp:.3f}%)")
    if batch.mask is not None:
        import numpy as np
        print(f"mask: {mask_path} ({int(np.sum(batch.mask))} px)")
    background_model = getattr(batch, "background_model", None)
    if background_model is None:
        background_model = batch.policy.background_model
    background_order = getattr(batch, "background_order", None)
    if background_order is None:
        background_order = batch.policy.background_order
    intensity_poly = (getattr(batch, "intensity_poly", 0)
                      if pset is None else pset.policy.intensity_poly)
    print(f"background: {background_model}"
          + (f" (order {background_order})"
             if background_model == "chebyshev" else "")
          + f"; intensity_poly={intensity_poly}")

    csv_path = outdir / "insitu_fit.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = None
        for i, path in enumerate(frames, start=1):
            number = frame_numbers[i - 1]
            print(f"[{i}/{len(frames)}] frame {number}: {path.name}",
                  flush=True)
            # 真实帧号写进 CSV；跳帧时用生产 CSV 上一帧的晶胞作种子。
            batch.frame_no = number - 1
            if (number - 1) in seeds:
                if pset is None:
                    batch.seed = seeds[number - 1]
                else:
                    # 通用引擎的种子还带参考相（Al），只覆盖 CSV 里的
                    # rich/poor 两相，别把 Al 丢掉。
                    batch.seed.update(seeds[number - 1])
            row = batch.fit_frame(path)
            if writer is None:
                writer = csv.DictWriter(fh, fieldnames=list(row))
                writer.writeheader()
            writer.writerow(row)
            fh.flush()
            if row["status"].startswith("error"):
                print(f"  异常：{row['status']}", flush=True)
            start = str(row.get("start_used") or "")
            start_note = f"  start={start}" if start else ""
            print(f"  status={row['status']}  Rwp={row['rwp']:.3f}%  "
                  f"f_rich={row.get('f_rich', float('nan')):.3f}  "
                  f"wt_rich={row.get('wt_rich', float('nan')):.2f}%  "
                  f"fit={row['t_fit_s']:.1f}s{start_note}", flush=True)

    rows = batch.rows
    summary_path, means, total = _write_summary(
        outdir, rows, batch.mask is not None, phase_set=pset)
    print(f"\nCSV: {csv_path}\nsummary: {summary_path}")

    if batch.fitted:
        picks = []
        seen = set()
        for pos in (0, len(batch.fitted) // 2, len(batch.fitted) - 1):
            if pos not in seen:          # 少帧时三个位置会重合，只画一次
                seen.add(pos)
                picks.append(batch.fitted[pos])
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
