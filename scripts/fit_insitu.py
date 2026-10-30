#!/usr/bin/env python3
"""LMFP 原位序列的三相全精修批处理（2026-10-27，Week 9 任务二）。

命令行入口；引擎在 `xrd_toolkit.simulation.insitu`，GUI [原位] 工作台
共用同一份流程。

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
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.simulation import plot_fit  # noqa: E402
from xrd_toolkit.simulation.insitu import (  # noqa: E402
    DEFAULT_AL, DEFAULT_POOR, DEFAULT_RICH, FIRST_NFEV, FRAME_NFEV,
    InSituBatch, _cell, _extra_phases, _fractions, _fraction_esd,
    _frames, _instrument, _row, _status, _tag, _write_summary,
    frames_in_dir, prepare_setup)

DEFAULT_DATA = Path.home() / "Desktop" / "lmfpdata"
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
    x = [r["frame"] for r in good]
    fig, axes = plt.subplots(2, 2, figsize=(11, 7))
    ax = axes[0, 0]
    ax.plot(x, [r["f_rich"] for r in good], "o-", ms=2.5, label="LMFP rich")
    ax.plot(x, [r["f_poor"] for r in good], "s-", ms=2.5, label="LMFP poor")
    ax.set_ylabel("relative fraction")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25, linestyle=":")
    for i, key in enumerate(("rich", "poor")):
        ax = axes[0, 1] if i == 0 else axes[1, 0]
        for j, axis in enumerate(("a", "b", "c")):
            ax.plot(x, [r[f"{key}_{axis}"] for r in good], "o-", ms=2.0,
                    label=axis)
        ax.set_ylabel(f"{key} cell (Å)")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.25, linestyle=":")
    ax = axes[1, 1]
    ax.plot(x, [r["rwp"] for r in good], "o-", ms=2.5, color="tab:red")
    ax.set_ylabel("Rwp (%)")
    ax.set_xlabel("frame")
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
    frames = frames_in_dir(data_dir, args.limit, args.stride)
    if not frames:
        ap.error(f"目录里没有 .tif：{data_dir}")
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
            print(f"警告：mask 不存在（{mp}），按不遮运行")

    try:
        batch = prepare_setup(
            args.config, rich_path=args.rich, poor_path=args.poor,
            al_path=args.al, mask_path=mask_path, max_rwp=args.max_rwp,
            first_nfev=args.first_nfev, frame_nfev=args.frame_nfev)
    except Exception as err:                       # noqa: BLE001
        ap.error(f"准备失败：{err}")

    inst = batch.instrument
    print(f"LaB6: zero={inst.params['zero']:+.5f}  "
          f"U={inst.params['u']:.3f} V={inst.params['v']:.3f} "
          f"W={inst.params['w']:.3f} X={inst.params['x']:.3f} "
          f"Y={inst.params['y']:.3f}  (Rwp {inst.rwp:.3f}%)")
    if batch.mask is not None:
        import numpy as np
        print(f"mask: {mask_path} ({int(np.sum(batch.mask))} px)")

    csv_path = outdir / "insitu_fit.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = None
        for i, path in enumerate(frames, start=1):
            print(f"[{i}/{len(frames)}] {path.name}", flush=True)
            row = batch.fit_frame(path)
            if writer is None:
                writer = csv.DictWriter(fh, fieldnames=list(row))
                writer.writeheader()
            writer.writerow(row)
            fh.flush()
            if row["status"].startswith("error"):
                print(f"  异常：{row['status']}", flush=True)
            print(f"  status={row['status']}  Rwp={row['rwp']:.3f}%  "
                  f"f_rich={row['f_rich']:.3f}  "
                  f"fit={row['t_fit_s']:.1f}s", flush=True)

    rows = batch.rows
    summary_path, means, total = _write_summary(
        outdir, rows, batch.mask is not None)
    print(f"\nCSV: {csv_path}\nsummary: {summary_path}")

    if batch.fitted:
        picks = []
        for idx in (0, len(batch.fitted) // 2, len(batch.fitted) - 1):
            if idx not in {p[0] for p in picks}:
                picks.append(batch.fitted[idx])
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
