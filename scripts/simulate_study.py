#!/usr/bin/env python3
"""XRD 模拟研究脚本（RY04 第七周起，2026-10-17）。

按任务逐步出图 + 打印数字，一张图对应任务清单里的一项产出：

    basic          任务一：基础模拟谱（理想晶体 + 常数展宽，带 hkl 标注）
    instrument     任务二：逐仪器参数（波长 / 零点 / 展宽 / 范围步长）四联图
    crosscheck     任务二·双引擎对拍：读 GSAS-II 的 npz → 叠图 + 数字差
    microstructure 任务三：逐样品参数（尺寸 / 应变 / B 因子 / 取向）    [待加]
    compare        任务四：模拟 vs 实验（lab6 实测一维谱）              [待加]

用法：
    python scripts/simulate_study.py basic
    python scripts/simulate_study.py instrument --cif data/structures/LaB6_1000055.cif
    # crosscheck 两步走（GSAS-II 在独立环境）：
    conda run -n gsas2 python scripts/gsas2_crosscheck.py --cif … --out /tmp/g2.npz --w 18.033
    conda run -n gsas2 python scripts/gsas2_crosscheck.py --cif … --out /tmp/g2z.npz --w 18.033 --zero 0.05
    python scripts/simulate_study.py crosscheck --gsas2-npz /tmp/g2.npz --gsas2-zero-npz /tmp/g2z.npz

主 CIF 默认 LaB₆（与第三周的实验数据同材料，任务四要对得上）。
"""
import argparse
import math
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.simulation import (  # noqa: E402
    d_spacing, format_hkls, load_structure, pattern_from_cif, plot_profile,
    simulate_pattern, two_theta_from_d)

DEFAULT_CIF = ROOT / "data" / "structures" / "LaB6_1000055.cif"


def _material_name(cif: Path) -> str:
    """从文件名取材料短名（LaB6_1000055.cif → LaB6）。"""
    return cif.stem.split("_")[0]


def study_basic(cif: Path, outdir: Path) -> None:
    """任务一：基础模拟谱。理想晶体 + 常数展宽（0.1° Gaussian），带 hkl 标注。"""
    material = _material_name(cif)
    structure = load_structure(str(cif))
    tth, curve, peaks, is_hex = simulate_pattern(
        structure, two_theta_range=(10.0, 80.0), step_deg=0.02)
    sticks_tt, sticks_ii, _, _ = pattern_from_cif(
        str(cif), two_theta_range=(10.0, 80.0))
    out = outdir / f"sim_xrd_basic_{material}.png"
    strongest = max(peaks, key=lambda p: p[1])
    plot_profile(
        tth, curve, peaks, is_hex,
        f"{material} (from CIF) — basic simulated pattern\n"
        f"Cu Kα 1.5406 Å · ideal crystal · 0.1° Gaussian broadening",
        str(out), sticks=(sticks_tt, sticks_ii),
        zoom=(strongest[0], 0.9))          # 放大最强峰：0.1° 的峰形看得见
    print(f"{material}：10°–80° 内 {len(peaks)} 个反射（≥1% 最强峰），"
          f"最强峰在 {strongest[0]:.2f}°")
    print(f"  → {out}")


def _fwhm_bounds(tth, curve, center, span=0.8):
    """半高两侧位置与峰顶（线性插值）——脚本里与测试同款口径。"""
    m = (tth > center - span) & (tth < center + span)
    xs, ys = tth[m], curve[m]
    i0 = int(np.argmax(ys))
    half = ys[i0] / 2.0
    left = float(np.interp(half, ys[:i0 + 1], xs[:i0 + 1]))
    right = float(np.interp(half, ys[i0:][::-1], xs[i0:][::-1]))
    return left, right, float(ys[i0])


def _measure_fwhm(tth, curve, center, span=0.8):
    """半高宽实测（线性插值半高两侧）。"""
    left, right, _ = _fwhm_bounds(tth, curve, center, span)
    return right - left


def _peak_center(tth, curve, around, span=0.8):
    """峰位实测（质心，亚步长精度）。"""
    m = (tth > around - span) & (tth < around + span)
    w = curve[m]
    return float(np.sum(tth[m] * w) / np.sum(w))


def study_instrument(cif: Path, outdir: Path) -> None:
    """任务二：仪器参数逐项对比——波长 / 零点偏移 / 仪器展宽 / 2θ 范围与步长。

    四联图（2×2）+ 打印实测数字表：
      (a) 波长：Cu Kα 1.5406 Å vs Mo Kα 0.7093 Å——峰位按布拉格定律整体移动
      (b) 零点偏移：zero_deg=+0.05°——每个峰平移 0.05°（仪器角度校准误差）
      (c) 仪器展宽：常数 0.05° / 常数 0.10° / Caglioti（随角度变宽）——
          FWHM(2θ) 曲线 + 峰位处实测点
      (d) 步长：0.01° 细网格上以 0.1° 取点——欠采样会把峰形测丢
    """
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from xrd_toolkit.simulation import caglioti_fwhm_deg

    material = _material_name(cif)
    structure = load_structure(str(cif))
    NPT = dict(two_theta_range=(10.0, 80.0), step_deg=0.005)

    # 基准 + 各条件下的曲线
    t_cu, y_cu, peaks, _ = simulate_pattern(structure, **NPT)
    t_mo, y_mo, _, _ = simulate_pattern(structure, wavelength=0.7093, **NPT)
    # 布拉格定律现算的 (110) 迁移（图上标注/数字表都用它，不写死）
    mo_110_pred = two_theta_from_d(
        d_spacing((1, 1, 0), structure.lattice.matrix), 0.7093)
    t_z, y_z, _, _ = simulate_pattern(structure, zero_deg=0.05, **NPT)
    t_w5, y_w5, _, _ = simulate_pattern(structure, w=0.0025, **NPT)   # 0.05°
    t_w15, y_w15, _, _ = simulate_pattern(structure, w=0.01, **NPT)   # 0.10°（默认）
    # Caglioti：u=0.08、w=0.0025 → 低角 ~0.09°、80° 处 ~0.24°（见数字表）
    t_ca, y_ca, _, _ = simulate_pattern(structure, u=0.08, w=0.0025, **NPT)

    fig, axes = plt.subplots(2, 2, figsize=(13.5, 9))
    ax_a, ax_b, ax_c, ax_d = axes.ravel()

    # (a) 波长
    ax_a.plot(t_cu, y_cu, color="#2b6cb0", lw=1.4, label="Cu Kα  1.5406 Å")
    ax_a.plot(t_mo, y_mo, color="#d94b2b", lw=1.4, label="Mo Kα  0.7093 Å")
    ax_a.annotate(f"(110): 30.38° → {mo_110_pred:.2f}°",
                  xy=(mo_110_pred, 100), xytext=(26, 78),
                  arrowprops=dict(arrowstyle="->", color="0.35"),
                  fontsize=9.5, color="0.25")
    ax_a.set_xlim(10, 80); ax_a.set_ylim(0, 118)
    ax_a.legend(loc="upper right", fontsize=9)
    ax_a.set_title("(a) wavelength — Bragg: sinθ ∝ λ", fontsize=10.5)
    ax_a.set_xlabel(r"2$\theta$ (degrees)"); ax_a.set_ylabel("Intensity (a.u.)")

    # (b) 零点偏移（放大 (110)/(100) 一带）
    ax_b.plot(t_cu, y_cu, color="#2b6cb0", lw=1.4, label="zero = 0°")
    ax_b.plot(t_z, y_z, color="#d94b2b", lw=1.2, ls="--",
              label="zero = +0.05°")
    ax_b.set_xlim(20.0, 32.0); ax_b.set_ylim(0, 118)
    for p_center, p_y in ((21.36, 45), (30.38, 60)):
        ax_b.annotate("", xy=(p_center + 0.05, p_y), xytext=(p_center, p_y),
                      arrowprops=dict(arrowstyle="<->", color="0.3"))
        ax_b.text(p_center + 0.025, p_y + 3, "+0.05°",
                  ha="center", fontsize=9, color="0.25")
    ax_b.legend(loc="upper right", fontsize=9)
    ax_b.set_title("(b) zero offset — every peak shifts together", fontsize=10.5)
    ax_b.set_xlabel(r"2$\theta$ (degrees)"); ax_b.set_ylabel("Intensity (a.u.)")

    # (c) 仪器展宽：FWHM(2θ) 曲线 + 实测点
    tt_grid = np.linspace(10.0, 80.0, 200)
    fw_const5 = np.full_like(tt_grid, 0.05)
    fw_const10 = np.full_like(tt_grid, 0.10)
    fw_cag = np.asarray(caglioti_fwhm_deg(tt_grid, u=0.08, w=0.0025))
    ax_c.plot(tt_grid, fw_const5, color="#2b6cb0", lw=1.4,
              label="constant 0.05° (narrow slits)")
    ax_c.plot(tt_grid, fw_const10, color="#888888", lw=1.4,
              label="constant 0.10° (default)")
    ax_c.plot(tt_grid, fw_cag, color="#d94b2b", lw=1.4,
              label="Caglioti √(U·tan²θ+W), U=0.08, W=0.0025")
    check = [p[0] for p in sorted(peaks, key=lambda q: -q[1])[:3]]
    for t, y_, color in ((t_w5, y_w5, "#2b6cb0"), (t_w15, y_w15, "#888888"),
                         (t_ca, y_ca, "#d94b2b")):
        ax_c.plot(check, [_measure_fwhm(t, y_, center) for center in check], "o",
                  color=color, ms=4)
    ax_c.set_title("(c) instrument broadening — FWHM vs 2θ (dots = measured)",
                   fontsize=10.5)
    ax_c.set_xlim(10, 80); ax_c.set_ylim(0, 0.30)
    ax_c.legend(loc="upper left", fontsize=8.5)
    ax_c.set_xlabel(r"2$\theta$ (degrees)"); ax_c.set_ylabel("FWHM (degrees)")

    # (d) 步长：细网格上按 0.1° 取点
    t_f, y_f, _, _ = simulate_pattern(structure, two_theta_range=(28.0, 34.0),
                                      step_deg=0.002)
    step_marks = np.arange(28.0, 34.001, 0.1)
    y_marks = np.interp(step_marks, t_f, y_f)
    ax_d.plot(t_f, y_f, color="#2b6cb0", lw=1.4, label="true profile (step 0.002°)")
    ax_d.plot(step_marks, y_marks, "o", color="#d94b2b", ms=4.5,
              label="measured at step 0.1°")
    ax_d.set_xlim(28.0, 34.0); ax_d.set_ylim(0, 118)
    ax_d.legend(loc="upper right", fontsize=9)
    ax_d.set_title("(d) step size — 0.1° undersamples the 0.10°-wide peak",
                   fontsize=10.5)
    ax_d.set_xlabel(r"2$\theta$ (degrees)"); ax_d.set_ylabel("Intensity (a.u.)")

    for ax in axes.ravel():
        ax.grid(alpha=0.25, linestyle=":")
    fig.suptitle(f"{material} — instrument parameters, one at a time "
                 f"(hand-written engine, Cu Kα unless stated)", fontsize=12.5)
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    out = outdir / f"sim_xrd_instrument_{material}.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)

    # ── 实测数字表（打印；周报素材的原始数字）──────────────────────────
    p110, p311 = 30.38, 75.82
    print(f"{material} 任务二实测数字：")
    print(f"  (a) 波长   (110)：Cu {_peak_center(t_cu, y_cu, p110):.3f}° → "
          f"Mo {_peak_center(t_mo, y_mo, mo_110_pred):.3f}°"
          f"（布拉格预测 {mo_110_pred:.3f}°）")
    print(f"  (b) 零点   +0.05° → (110) {_peak_center(t_cu, y_cu, p110):.3f}° → "
          f"{_peak_center(t_z, y_z, p110 + 0.05):.3f}°（Δ = "
          f"{_peak_center(t_z, y_z, p110 + 0.05) - _peak_center(t_cu, y_cu, p110):+.3f}°）")
    print(f"  (c) 展宽   FWHM@(110)：0.05° 条件 {_measure_fwhm(t_w5, y_w5, p110):.3f}° ｜ "
          f"0.10° 条件 {_measure_fwhm(t_w15, y_w15, p110):.3f}° ｜ "
          f"Caglioti {_measure_fwhm(t_ca, y_ca, p110):.3f}°")
    print(f"             FWHM@(311) 75.82°：0.05° 条件 {_measure_fwhm(t_w5, y_w5, p311):.3f}° ｜ "
          f"0.10° 条件 {_measure_fwhm(t_w15, y_w15, p311):.3f}° ｜ "
          f"Caglioti {_measure_fwhm(t_ca, y_ca, p311):.3f}°")
    n_marks = int(np.sum((step_marks > 30.2) & (step_marks < 30.6)))
    print(f"  (d) 步长   0.1° 取点：(110)（FWHM 0.10°）只落 {n_marks} 个点"
          f"（28–34° 段共 {len(step_marks)} 点；步长 ≲ 峰宽/5 才测得住峰形）")
    print(f"  → {out}")


def _uiso_from_cif(cif: Path) -> dict:
    """从 CIF 原子行读 Uiso → {元素: B}（B = 8π²·Uiso，Å²）。

    对拍时两面强度要同口径：GSAS-II 会自动用 CIF 里的 Uiso，我们的
    `simulate_pattern(b_iso=…)` 得显式喂同样的值。（任务三会把这一步
    固化进模块，这里先按对拍需要就地解析——行如"La .0 .0 .0 1.0 Uiso .00858(29) 1"。）
    """
    out = {}
    for raw in Path(cif).read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith(("#", "_", "loop_")):
            continue
        parts = s.split()
        if "Uiso" in parts:
            u = float(parts[parts.index("Uiso") + 1].split("(")[0])
            elem = re.match(r"[A-Za-z]+", parts[0]).group(0)
            out[elem] = 8.0 * math.pi ** 2 * u
    return out


def study_crosscheck(cif: Path, outdir: Path, g2_base_npz: Path,
                     g2_zero_npz: Path) -> None:
    """任务二·双引擎对拍：GSAS-II 与本引擎同参数叠图 + 逐个数字差。

    两个输入 npz 由 `gsas2_crosscheck.py` 在装了 GSAS-II 的环境里先跑出来：
      base：W=18.033（= 我们 w=0.01 的换算值）、Zero=0、微结构中性化
      zero：同上、Zero=+0.05
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    material = _material_name(cif)
    structure = load_structure(str(cif))
    b_iso = _uiso_from_cif(cif)               # 与 GSAS-II 自动用的 Uiso 对齐
    kw = dict(two_theta_range=(10.0, 80.0), step_deg=0.005, b_iso=b_iso)
    t_us, y_us, peaks, is_hex = simulate_pattern(structure, **kw)
    t_uz, y_uz, _, _ = simulate_pattern(structure, zero_deg=0.05, **kw)
    gb = np.load(g2_base_npz)
    t_g, y_g = gb["x"], gb["ycalc"]
    gz = np.load(g2_zero_npz)
    t_gz, y_gz = gz["x"], gz["ycalc"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 4.8),
                                   gridspec_kw={"width_ratios": [2.1, 1]})
    ax1.plot(t_us, y_us / y_us.max(), color="#2b6cb0", lw=1.4,
             label="hand-written engine")
    ax1.plot(t_g, y_g / y_g.max(), color="#d94b2b", lw=1.2, ls="--",
             label="GSAS-II (W = 18.033 = 0.1° converted)")
    ax1.set_xlim(10, 80); ax1.set_ylim(0, 1.12)
    ax1.legend(loc="upper right", fontsize=9)
    ax1.set_title(f"{material} — same parameters, two engines "
                  f"(matched Uiso, neutralised size/strain)", fontsize=10.5)
    ax1.set_xlabel(r"2$\theta$ (degrees)"); ax1.set_ylabel("normalised intensity")

    for t, y_, color, dy in ((t_us, y_us, "#2b6cb0", 0.06),
                             (t_g, y_g, "#d94b2b", -0.13)):
        ymax = y_.max()
        norm = y_ / ymax
        m = (t > 29.6) & (t < 31.2)
        ax2.plot(t[m], norm[m], color=color, lw=1.5)
        left, right, _top = _fwhm_bounds(t, norm, 30.38, span=0.64)
        ax2.hlines(0.5, left, right, color=color, lw=1)
        ax2.text((left + right) / 2, 0.5 + dy,
                 f"FWHM {right - left:.3f}°", ha="center",
                 fontsize=9, color=color)
    ax2.set_xlim(29.6, 31.2); ax2.set_ylim(0, 1.12)
    ax2.set_title("(110) zoom — widths agree after conversion", fontsize=10.5)
    ax2.set_xlabel(r"2$\theta$ (degrees)")
    for ax in (ax1, ax2):
        ax.grid(alpha=0.25, linestyle=":")
    fig.tight_layout()
    out = outdir / f"week7_engine_crosscheck.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)

    print(f"{material} 双引擎对拍（W=18.033 ↔ w=0.01，Zero 两侧同设）：")
    top = [p for p in sorted(peaks, key=lambda q: -q[1])[:5]]
    for tt, ii, refs in top:
        cu = _peak_center(t_us, y_us, tt)
        cg = _peak_center(t_g, y_g, tt)
        fu = _measure_fwhm(t_us, y_us, tt)
        fg = _measure_fwhm(t_g, y_g, tt)
        print(f"  {format_hkls(refs, is_hex):>12}  Δ2θ = {cu - cg:+.3f}°   "
              f"FWHM 自研 {fu:.3f}° / GSAS {fg:.3f}°")
    z_us = _peak_center(t_uz, y_uz, 30.38) - _peak_center(t_us, y_us, 30.38)
    z_g = _peak_center(t_gz, y_gz, 30.38) - _peak_center(t_g, y_g, 30.38)
    print(f"  零点 +0.05°：(110) 实测位移  自研 {z_us:+.3f}° / GSAS {z_g:+.3f}°")
    print(f"  → {out}")


STUDIES = {
    "basic": study_basic,
    "instrument": study_instrument,
    # "microstructure": study_microstructure,  # 任务三
    # "compare": study_compare,              # 任务四
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("study", choices=sorted(set(STUDIES) | {"crosscheck"}),
                    help="要跑的课题")
    ap.add_argument("--cif", default=str(DEFAULT_CIF),
                    help=f"输入 CIF（默认 {DEFAULT_CIF.name}）")
    ap.add_argument("--outdir", default=str(ROOT / "showcase" / "simulation"),
                    help="输出目录（默认 showcase/simulation）")
    ap.add_argument("--gsas2-npz", default=None,
                    help="crosscheck 用：GSAS-II 基准模拟的 npz"
                         "（装了 GSAS-II 的环境跑 scripts/gsas2_crosscheck.py 产出）")
    ap.add_argument("--gsas2-zero-npz", default=None,
                    help="crosscheck 用：GSAS-II 零点 +0.05° 的 npz")
    args = ap.parse_args()
    cif = Path(args.cif)
    if not cif.exists():
        ap.error(f"CIF 不存在：{cif}")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    if args.study == "crosscheck":
        if not (args.gsas2_npz and args.gsas2_zero_npz):
            ap.error("crosscheck 需要 --gsas2-npz 与 --gsas2-zero-npz")
        study_crosscheck(cif, outdir, Path(args.gsas2_npz),
                         Path(args.gsas2_zero_npz))
    else:
        STUDIES[args.study](cif, outdir)


if __name__ == "__main__":
    main()
