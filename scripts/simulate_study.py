#!/usr/bin/env python3
"""XRD 模拟研究脚本（RY04 第七周起，2026-10-17）。

按任务逐步出图 + 打印数字，一张图对应任务清单里的一项产出：

    basic          任务一：基础模拟谱（理想晶体 + 常数展宽，带 hkl 标注）
    instrument     任务二：逐仪器参数（波长 / 零点 / 展宽 / 范围步长）四联图
    crosscheck     任务二·双引擎对拍：读 GSAS-II 的 npz → 叠图 + 数字差
    microstructure 任务三：逐样品参数（尺寸 / 应变 / B 因子 / 取向）
    compare        任务四：模拟 vs 实验（lab6 实测一维谱，GSAS 口径）
    fit            精修：实验谱 → 全谱最小二乘（Rwp/参数表/残差图）
    keys           一页看两条硬指标：面积不变 + 峰宽与 GSAS-II 一致

引擎分工（2026-10-22）：
    basic / instrument / microstructure 用第七周的教学引擎
    （`profile.simulate_pattern`，峰顶归一，便于逐参数演示）；
    crosscheck / compare / fit 用 GSAS-II 同口径引擎
    （`gsas_profile.simulate_pattern_gsas`，面积守恒、峰宽同式）。

用法：
    python scripts/simulate_study.py basic
    python scripts/simulate_study.py instrument --cif data/structures/LaB6_NIST660c.cif
    # crosscheck 两步走（GSAS-II 在独立环境）：
    conda run -n gsas2 python scripts/gsas2_crosscheck.py --cif … --out /tmp/g2.npz --w 18.033
    conda run -n gsas2 python scripts/gsas2_crosscheck.py --cif … --out /tmp/g2z.npz --w 18.033 --zero 0.05
    python scripts/simulate_study.py crosscheck --gsas2-npz /tmp/g2.npz --gsas2-zero-npz /tmp/g2z.npz
    python scripts/simulate_study.py fit

主 CIF 默认 LaB₆（与第三周的实验数据同材料，任务四要对得上）；
实验拟合/对照用 pyFAI 默认 LaB6 的晶胞数值（等同 SRM 660c；实际标样
证书未记录），与几何标定的参考一致。
"""
import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.simulation import (  # noqa: E402
    b_iso_from_cif, d_spacing, fit_entries, fit_pattern, fixed_cell_text,
    format_hkls, load_structure, pattern_from_cif, plot_fit,
    plot_fit_bottom, plot_intensity_ratios, plot_profile,
    simulate_pattern, simulate_pattern_gsas, summary_block,
    two_theta_from_d)

# 默认用 pyFAI 默认 LaB6 的晶胞数值（等同 SRM 660c，a=4.156826 Å；
# 证书未记录）：实验图的几何按同一套
# 参考标定，拟合/对照才不会带 2θ 系统性偏差。教学模拟用哪个晶胞都行。
DEFAULT_CIF = ROOT / "data" / "structures" / "LaB6_NIST660c.cif"


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
    b_iso = b_iso_from_cif(cif)               # 与 GSAS-II 自动用的 Uiso 对齐
    kw = dict(two_theta_range=(10.0, 80.0), step_deg=0.005, b_iso=b_iso,
              size_um=1e5, mustrain=0.0)     # 微结构中性化（与 npz 同口径）
    t_us, y_us, peaks, is_hex = simulate_pattern_gsas(
        structure, w=18.033, **kw)            # W=18.033 centideg² ↔ 0.1° FWHM
    t_uz, y_uz, _, _ = simulate_pattern_gsas(
        structure, w=18.033, zero_deg=0.05, **kw)
    gb = np.load(g2_base_npz)
    t_g, y_g = gb["x"], gb["ycalc"]
    gz = np.load(g2_zero_npz)
    t_gz, y_gz = gz["x"], gz["ycalc"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 4.8),
                                   gridspec_kw={"width_ratios": [2.1, 1]})
    ax1.plot(t_us, y_us / y_us.max(), color="#2b6cb0", lw=1.4,
             label="hand-written engine")
    ax1.plot(t_g, y_g / y_g.max(), color="#d94b2b", lw=1.2, ls="--",
             label="GSAS-II (W = 18.033 centideg² ↔ 0.1°)")
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
        ax2.text(right + 0.16, 0.5 + dy,
                 f"FWHM {right - left:.3f}°", ha="left", va="center",
                 fontsize=9, color=color,
                 bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none",
                           alpha=0.8))
    ax2.set_xlim(29.6, 31.2); ax2.set_ylim(0, 1.12)
    ax2.set_title("(110) zoom — widths agree after conversion", fontsize=10.5)
    ax2.set_xlabel(r"2$\theta$ (degrees)")
    for ax in (ax1, ax2):
        ax.grid(alpha=0.25, linestyle=":")
    fig.tight_layout()
    out = outdir / f"sim_xrd_engine_crosscheck.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)

    print(f"{material} 双引擎对拍（W=18.033 centideg² ↔ 0.1°，Zero 两侧同设）：")
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


def study_microstructure(cif: Path, outdir: Path) -> None:
    """任务三：样品微结构参数——晶粒尺寸 / 微观应变 / 热振动 / 择优取向。

    两张图：
      `sim_xrd_microstructure_{材料}.png`（2×2）——四个参数**各与理想晶体对比**：
        (a) 晶粒尺寸 50 nm：Scherrer 展宽（1/cosθ），放大 (110) 标 FWHM
        (b) 尺寸 vs 应变怎么分：FWHM(2θ) 三条曲线（仪器 / +尺寸 / +应变，
            1/cosθ 与 tanθ 的 θ 依赖不同——W-H 能把它俩分开的根）
        (c) 热振动（CIF Uiso → Debye-Waller）：按 (110) 对齐看**高角度峰被压低**
        (d) 择优取向（March-Dollase R=0.6、轴 (111)）：(111) 相对增强、(100) 变弱
      `sim_xrd_realistic_vs_ideal_{材料}.png`（1×2）——"理想晶体 vs 全部真实效应
        叠加"（50 nm + 0.2% 应变 + CIF 热振动）的全谱对比 + (110) 放大。
    并打印每个条件的实测数字（FWHM、相对强度比）。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from xrd_toolkit.simulation import march_dollase, scherrer_fwhm_deg, strain_fwhm_deg

    material = _material_name(cif)
    structure = load_structure(str(cif))
    b_iso = b_iso_from_cif(cif)
    P110, P311 = 30.38, 75.82
    NPT = dict(two_theta_range=(10.0, 80.0), step_deg=0.005)

    t0, y0, peaks, _ = simulate_pattern(structure, **NPT)              # 理想
    t_sz, y_sz, _, _ = simulate_pattern(structure, size_nm=50.0, **NPT)
    t_st, y_st, _, _ = simulate_pattern(structure, strain=0.002, **NPT)
    t_dw, y_dw, _, _ = simulate_pattern(structure, b_iso=b_iso, **NPT)
    t_md, y_md, _, _ = simulate_pattern(structure, md=((1, 1, 1), 0.6), **NPT)
    t_rz, y_rz, _, _ = simulate_pattern(
        structure, size_nm=50.0, strain=0.002, b_iso=b_iso, **NPT)

    def scale_by_110(t, y):
        """按 (110) 峰高归一到 100——比相对强度（强度再分布）必须同一把尺，
        各自按自己的最高峰归一反而会把效应归一化掉。"""
        m = (t > P110 - 0.5) & (t < P110 + 0.5)
        return y / y[m].max() * 100.0

    def height_at(t, y, c):
        m = (t > c - 0.5) & (t < c + 0.5)
        return float(y[m].max())

    # ── 图 A：四个参数各自与理想对比 ────────────────────────────────
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 9))
    ax_a, ax_b, ax_c, ax_d = axes.ravel()

    # (a) 晶粒尺寸：放大 (110)
    for t, y_, color, lab in ((t0, y0, "#888888", "ideal crystal"),
                              (t_sz, y_sz, "#2b6cb0", "crystallite size 50 nm")):
        m = (t > 29.6) & (t < 31.2)
        ax_a.plot(t[m], y_[m], color=color, lw=1.5, label=lab)
        ax_a.text(30.38, y_[m].max() * 0.55 if color == "#2b6cb0" else y_[m].max() * 0.4,
                  f"FWHM {_measure_fwhm(t, y_, P110):.3f}°", color=color,
                  fontsize=9, ha="center")
    ax_a.set_xlim(29.6, 31.2); ax_a.set_ylim(0, 115)
    ax_a.legend(loc="upper left", fontsize=9)
    ax_a.set_title("(a) crystallite size 50 nm — Scherrer (∝1/cosθ)",
                   fontsize=10.5)
    ax_a.set_xlabel(r"2$\theta$ (degrees)"); ax_a.set_ylabel("Intensity (a.u.)")

    # (b) 尺寸 vs 应变：FWHM(2θ) 三条曲线 + 实测点
    tt = np.linspace(10.0, 80.0, 200)
    curves = (
        (np.full_like(tt, 0.10), "#888888", "instrument only (0.1°)", t0, y0),
        (np.sqrt(0.10 ** 2 + np.asarray(scherrer_fwhm_deg(tt, 50.0)) ** 2),
         "#2b6cb0", "+ size 50 nm (∝1/cosθ)", t_sz, y_sz),
        (np.sqrt(0.10 ** 2 + np.asarray(strain_fwhm_deg(tt, 0.002)) ** 2),
         "#d94b2b", "+ strain 0.2% (∝tanθ)", t_st, y_st),
    )
    check = [p[0] for p in sorted(peaks, key=lambda q: -q[1])[:4]]
    for fw, color, lab, t, y_ in curves:
        ax_b.plot(tt, fw, color=color, lw=1.5, label=lab)
        ax_b.plot(check, [_measure_fwhm(t, y_, c) for c in check], "o",
                  color=color, ms=4)
    ax_b.set_title("(b) size vs strain — different θ-dependence "
                   "(dots = measured)", fontsize=10.5)
    ax_b.set_xlim(10, 80); ax_b.set_ylim(0, 0.45)
    ax_b.legend(loc="upper left", fontsize=8.5)
    ax_b.set_xlabel(r"2$\theta$ (degrees)"); ax_b.set_ylabel("FWHM (degrees)")

    # (c) 热振动：按 (110) 对齐
    ax_c.plot(t0, scale_by_110(t0, y0), color="#888888", lw=1.4,
              label="rigid lattice (no thermal motion)")
    b_txt = " / ".join(f"{v:.2f}" for v in b_iso.values())
    ax_c.plot(t_dw, scale_by_110(t_dw, y_dw), color="#d94b2b", lw=1.3,
              label=f"Debye-Waller from CIF Uiso (B = {b_txt} Å² per element)")
    ax_c.annotate("high-angle peaks damped", xy=(75.8, scale_by_110(t_dw, y_dw)[
        int(np.argmin(np.abs(t_dw - 75.82)))]), xytext=(52, 45),
        arrowprops=dict(arrowstyle="->", color="0.35"), fontsize=9.5,
        color="0.25")
    ax_c.set_xlim(10, 80); ax_c.set_ylim(0, 118)
    ax_c.legend(loc="upper right", fontsize=8.5)
    ax_c.set_title("(c) thermal motion — both curves scaled to the (110) peak",
                   fontsize=10.5)
    ax_c.set_xlabel(r"2$\theta$ (degrees)"); ax_c.set_ylabel("Intensity (a.u.)")

    # (d) 择优取向：按 (110) 对齐
    ax_d.plot(t0, scale_by_110(t0, y0), color="#888888", lw=1.4,
              label="random powder")
    ax_d.plot(t_md, scale_by_110(t_md, y_md), color="#2b6cb0", lw=1.3,
              label="March-Dollase R = 0.6, axis (111)")
    ax_d.set_xlim(20, 60); ax_d.set_ylim(0, 160)
    ax_d.legend(loc="upper right", fontsize=8.5)
    ax_d.set_title("(d) preferred orientation — (111) enhanced, (100) weakened",
                   fontsize=10.5)
    ax_d.set_xlabel(r"2$\theta$ (degrees)"); ax_d.set_ylabel("Intensity (a.u.)")

    for ax in axes.ravel():
        ax.grid(alpha=0.25, linestyle=":")
    fig.suptitle(f"{material} — sample microstructure, one effect at a time "
                 f"(compared with the ideal crystal)", fontsize=12.5)
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    out_a = outdir / f"sim_xrd_microstructure_{material}.png"
    fig.savefig(out_a, dpi=150)
    plt.close(fig)

    # ── 图 B：理想 vs 全部真实效应叠加 ─────────────────────────────
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 4.8),
                                   gridspec_kw={"width_ratios": [2.2, 1]})
    ax1.plot(t0, y0, color="#888888", lw=1.3, label="ideal crystal (0.1°)")
    ax1.plot(t_rz, y_rz, color="#2b6cb0", lw=1.3,
             label="50 nm + 0.2% strain + thermal (CIF)")
    ax1.set_xlim(10, 80); ax1.set_ylim(0, 118)
    ax1.legend(loc="upper right", fontsize=9)
    ax1.set_title(f"{material} — ideal vs all real effects together", fontsize=10.5)
    ax1.set_xlabel(r"2$\theta$ (degrees)"); ax1.set_ylabel("Intensity (a.u.)")
    for t, y_, color, tx in ((t0, y0, "#888888", 30.02),
                             (t_rz, y_rz, "#2b6cb0", 30.82)):
        m = (t > 29.5) & (t < 31.3)
        ax2.plot(t[m], y_[m], color=color, lw=1.5)
        left, right, top = _fwhm_bounds(t, y_, P110, span=0.9)
        ax2.hlines(top / 2, left, right, color=color, lw=1)
        ax2.text(tx, 106, f"FWHM {right - left:.3f}°",
                 ha="center", fontsize=9, color=color)
    ax2.set_xlim(29.5, 31.3); ax2.set_ylim(0, 118)
    ax2.set_title("(110) zoom", fontsize=10.5)
    ax2.set_xlabel(r"2$\theta$ (degrees)")
    for ax in (ax1, ax2):
        ax.grid(alpha=0.25, linestyle=":")
    fig.tight_layout()
    out_b = outdir / f"sim_xrd_realistic_vs_ideal_{material}.png"
    fig.savefig(out_b, dpi=150)
    plt.close(fig)

    # ── 实测数字表 ────────────────────────────────────────────────
    lat = structure.lattice.matrix
    p111 = march_dollase((1, 1, 1), (1, 1, 1), 0.6, lat)
    p110 = march_dollase((1, 1, 0), (1, 1, 1), 0.6, lat)
    p100 = march_dollase((1, 0, 0), (1, 1, 1), 0.6, lat)
    r111_0 = height_at(t0, scale_by_110(t0, y0), 37.44)
    r111_md = height_at(t_md, scale_by_110(t_md, y_md), 37.44)
    r311_0 = height_at(t0, scale_by_110(t0, y0), P311)
    r311_dw = height_at(t_dw, scale_by_110(t_dw, y_dw), P311)
    print(f"{material} 任务三实测数字（理想 = 常数 0.1° + 刚性晶格 + 无取向）：")
    print(f"  CIF 热振动  B = {b_iso}（B = 8π²·Uiso）")
    print(f"  尺寸 50nm   FWHM(110) {_measure_fwhm(t0, y0, P110):.3f}° → "
          f"{_measure_fwhm(t_sz, y_sz, P110):.3f}° ；"
          f"FWHM(311) {_measure_fwhm(t_sz, y_sz, P311):.3f}°")
    print(f"  应变 0.2%   FWHM(110) {_measure_fwhm(t_st, y_st, P110):.3f}° ；"
          f"FWHM(311) {_measure_fwhm(t_st, y_st, P311):.3f}°（tanθ：高角涨得快）")
    print(f"  热振动      (311) 相对 (110)：{r311_0:.1f}% → {r311_dw:.1f}%"
          f"（高角度被压低）")
    print(f"  取向 R=0.6  P(111)={p111:.2f} / P(110)={p110:.2f} / P(100)={p100:.2f}；"
          f"(111)/(110) 实测 {r111_0:.1f}% → {r111_md:.1f}%")
    print(f"  全部叠加    FWHM(110) {_measure_fwhm(t_rz, y_rz, P110):.3f}° ；"
          f"FWHM(311) {_measure_fwhm(t_rz, y_rz, P311):.3f}°")
    print(f"  → {out_a}")
    print(f"  → {out_b}")


def study_compare(cif: Path, outdir: Path, exp_tif: Path) -> None:
    """任务四：模拟 vs 实验（LaB₆ 标样 = 第三周处理的 `lab6-00024.tif`）。

    2026-10-22 起，对比图直接用**全谱拟合结果**，不再走"FWHM 反解
    U/V/W → 重新模拟"的简化路线：
      ① 用仓库引擎把实验图积分成 1D（几何 = config 里的 `lmfp1_lab6`，
         波长也从它读——这批是 0.1223 Å 的同步辐射数据）；
      ② 自研自动基线（estimate_baseline_sliding）作为固定背景；
      ③ 用 GSAS-II 同口径全谱最小二乘拟合：背景 + 0.25° 分段线性修正，
         zero/U/V/W/X/Y + Size + 全局 ΔB 一起精修；
      ④ 拟合曲线（扣掉背景后的峰部分）直接与实验对比；
      ⑤ 逐峰表：峰位残差 / FWHM（实验 vs 拟合）/ 相对强度。

    并存 `sim_xrd_vs_experiment_{材料}.png` 与同名 `.txt` 报告
    （全谱拟合参数表 + 逐峰对比表）。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from xrd_toolkit.config import get_config
    from xrd_toolkit.services.background import estimate_baseline_sliding
    from xrd_toolkit.services.data_loader import load_diffraction_image
    from xrd_toolkit.services.integrator import integrate_1d

    material = _material_name(cif)
    structure = load_structure(str(cif))
    b_iso = b_iso_from_cif(cif)
    cfg = get_config("lmfp1_lab6")
    geom = dict(cfg["geometry"])
    lam = geom["wavelength_m"] * 1e10          # m → Å（实验本身的波长）
    TMIN, TMAX = 1.0, 8.0                      # 第三周处理用的同一窗口
    # 2000 点 ≈ 每点 0.5 个探测器像素（1 px ≈ 0.0072°@2θ）。3000 点
    # （0.32 px/点）对这条斑驳的 LaB₆ 环属于过采样：相邻点从同一批
    # 像素里各取一部分，峰顶会被方位纹理顶出 2–3% 的假"双尖"。
    NPT = 2000

    # ① 实验 1D（同一条引擎与几何）
    img = load_diffraction_image(str(exp_tif))
    t_exp, y_exp = integrate_1d(img, npt=NPT, tth_min_deg=TMIN,
                                tth_max_deg=TMAX, **geom)
    # ② 扣背景（自研自动基线，作为固定背景；拟合再加 0.25° 分段修正）
    base = estimate_baseline_sliding(t_exp, y_exp, 0.2)

    # 理论峰位（无零点、无展宽要求——只要位置）
    st_tt, st_ii, peaks, _ = pattern_from_cif(
        str(cif), wavelength=lam, two_theta_range=(TMIN, TMAX))

    # ③ 全谱拟合：直接用最新模型（背景修正 + Size + 全局 ΔB）
    fit = fit_pattern(
        structure, t_exp, y_exp, wavelength=lam, b_iso=b_iso,
        background=base, background_knots=0.25, intensity_poly=2,
        weights="poisson",
        free=("zero", "u", "v", "w", "x", "y", "size_um", "b_iso"),
        initial={"size_um": 0.75, "b_iso": 0.0})
    t_sim = fit.tth
    y_obs_peaks = fit.y_obs - fit.background
    y_sim = fit.y_calc - fit.background
    zero_fit = float(fit.params["zero"])

    # ④ 实验找峰（窗口 ±0.08°，高度门槛 = 最强峰的 2%）
    thr = 0.02 * y_obs_peaks.max()
    matched = []
    for tt, ii in zip(st_tt, st_ii):
        m = (t_sim > tt - 0.08) & (t_sim < tt + 0.08)
        if not m.any() or y_obs_peaks[m].max() < thr:
            continue
        center = _peak_center(t_sim, y_obs_peaks, tt, span=0.05)
        fw = _measure_fwhm(t_sim, y_obs_peaks, tt, span=0.05)
        height = float(y_obs_peaks[
            (t_sim > tt - 0.05) & (t_sim < tt + 0.05)].max())
        matched.append((tt, ii, center, fw, height))
    if len(matched) < 4:
        print(f"实验谱上只对上 {len(matched)} 根峰，拟合样本不足")
        return

    # ⑤ 逐峰数字
    def sim_fwhm_at(center):
        return _measure_fwhm(t_sim, y_sim, center, span=0.1)

    h_max = max(h for *_, h in matched)
    rows = []
    for tt, ii, c, fw, h in matched:
        i_sim = float(y_sim[(t_sim > c - 0.05) & (t_sim < c + 0.05)].max())
        rows.append((tt, c, fw, sim_fwhm_at(c), h / h_max * 100.0, i_sim))
    i_sim_max = max(r[5] for r in rows)

    # 图：论文式布局——上排观测点/模拟线，中排差值，左列底部布拉格刻度
    main_color, obs_color = "#1f5fa8", "#333333"
    strongest = max(rows, key=lambda r: r[4])
    mid = min(rows, key=lambda r: abs(r[0] - (TMIN + TMAX) / 2))
    fig = plt.figure(figsize=(15, 7.4))
    gs = fig.add_gridspec(3, 3, width_ratios=[2.0, 1, 1],
                          height_ratios=[3.0, 1.0, 0.55],
                          hspace=0.07, wspace=0.22)
    axes = [fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1]),
            fig.add_subplot(gs[0, 2])]
    axes_r = [fig.add_subplot(gs[1, i], sharex=axes[i]) for i in range(3)]
    axb = fig.add_subplot(gs[2, 0], sharex=axes[0])
    axes_r[0].tick_params(labelbottom=False)   # 左列 x 轴让给布拉格刻度行
    diff = y_obs_peaks - y_sim
    axes[0].plot(t_sim, y_obs_peaks, ".", ms=1.7, color=obs_color,
                 alpha=0.85, label="Experiment (background subtracted)")
    axes[0].plot(t_sim, y_sim, color=main_color, lw=1.3,
                 label="Simulation (full-pattern fit)")
    axes[0].set_xlim(TMIN, TMAX); axes[0].set_ylim(bottom=0)
    axes[0].legend(loc="upper right", fontsize=8.5, frameon=True,
                   framealpha=0.9, edgecolor="0.85")
    axes[0].set_title(f"(a) {material} — simulation vs experiment "
                      f"(λ = {lam:.4f} Å, $R_{{wp}}$ {fit.rwp:.2f}%)", fontsize=10.5,
                      pad=9)
    axes[0].set_ylabel("Intensity (counts)")
    axes[0].tick_params(labelbottom=False)
    for k, (ax, axr, (tt, c, fw, fws, h_rel, _i)) in enumerate(
            zip(axes[1:], axes_r[1:], (strongest, mid)), start=1):
        w = 0.15
        ax.plot(t_sim, y_obs_peaks, ".", ms=1.9, color=obs_color, alpha=0.85)
        ax.plot(t_sim, y_sim, color=main_color, lw=1.3)
        ax.set_xlim(c - w, c + w); ax.set_ylim(bottom=0)
        # 标题压短：原来 (b)/(c) 两行在 15 in 画布上会横向撞在一起
        # （2026-10-26 重拍时发现），去掉 "zoom:" 与括号即可放下。
        ax.set_title(f"({'bc'[k - 1]}) $2\\theta$ ≈ {c:.3f}°  "
                     f"FWHM {fw:.3f} / {fws:.3f}°",
                     fontsize=9.0, pad=9)
        ax.tick_params(labelbottom=False)
    lim = float(np.percentile(np.abs(diff), 99.8))
    for i, axr in enumerate(axes_r):
        axr.axhline(0.0, color="0.55", lw=0.8)
        axr.plot(t_sim, diff, color=main_color, lw=0.8)
        if lim > 0:
            axr.set_ylim(-1.15 * lim, 1.15 * lim)
        axr.set_ylabel("Difference", fontsize=8.5)
        if i:                       # 左列的 x 轴标签让给布拉格刻度行
            axr.set_xlabel(r"2$\theta$ (degrees)")
        axr.grid(alpha=0.18, linestyle=":")
        axr.tick_params(labelsize=8.5)
    for ax in axes:
        ax.grid(alpha=0.18, linestyle=":")
        ax.tick_params(labelsize=9)
    # 布拉格刻度：用拟合后的峰位（含零点），与主拟合图同款
    bragg_pos = np.asarray(fit.peak_tth, dtype=float)
    axb.set_ylim(0.0, 1.0)
    axb.set_yticks([0.5])
    axb.set_yticklabels([material], fontsize=7.5)
    axb.tick_params(axis="y", length=0, pad=4)
    axb.set_ylabel("Bragg positions", fontsize=7.5)
    inside = bragg_pos[(bragg_pos >= TMIN) & (bragg_pos <= TMAX)]
    axb.vlines(inside, 0.30, 0.70, color=main_color, lw=0.85)
    for side in ("left", "right", "top"):
        axb.spines[side].set_visible(False)
    axb.tick_params(axis="x", labelsize=9)
    axb.set_xlabel(r"2$\theta$ (degrees)")
    fig.tight_layout()
    out = outdir / f"sim_xrd_vs_experiment_{material}.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)

    print(f"{material} 任务四：模拟 vs 实验（λ = {lam:.4f} Å，{TMIN:g}–{TMAX:g}°）")
    print(f"  全谱拟合：Rwp {fit.rwp:.3f}% / Rp {fit.rp:.3f}%")
    print(f"  GSAS 口径：zero={zero_fit:+.4f}°  U={fit.params['u']:.3f}  "
          f"V={fit.params['v']:.3f}  W={fit.params['w']:.3f}  "
          f"X={fit.params['x']:.3f}  Y={fit.params['y']:.3f}")
    print(f"  Size={fit.params['size_um']:.3f} µm；"
          f"ΔB={fit.params['b_iso']:+.3f} Å²（叠在 CIF 各元素 B 上）")
    a1 = fit.params.get("intensity_a1", 0.0)
    a2 = fit.params.get("intensity_a2", 0.0)
    print(f"  强度修正：1 {a1:+.3f}z {a2:+.3f}z²（z = 归一化 2θ）")
    print(f"  对上 {len(matched)} 根峰（理论 {len(st_tt)} 根）")
    print(f"  {'2θ理论':>8} {'Δ2θ':>7} {'FWHM实验':>9} {'FWHM模拟':>9} "
          f"{'I实验%':>7} {'I模拟%':>7}")
    for tt, c, fw, fws, h_rel, i_sim in rows:
        print(f"  {tt:8.3f} {c - tt - zero_fit:+7.3f} {fw:9.4f} {fws:9.4f} "
              f"{h_rel:7.1f} {i_sim / i_sim_max * 100:7.1f}")
    print(f"  → {out}")
    # 图旁边的完整参数报告（2026-10-26）：全谱拟合参数 + 逐峰对比表。
    entries, notes = fit_entries(
        exp_tif.name, material, fit,
        cell_fixed=fixed_cell_text(structure))
    entries.append(("峰匹配", f"{len(matched)} / {len(st_tt)} 根理论峰"))
    report = out.with_suffix(".txt")
    report.write_text(summary_block(entries, notes) + "\n" + "\n".join([
        f"material: {material}",
        f"data: {exp_tif.name}",
        "geometry: lmfp1_lab6",
        f"wavelength: {lam:.6f} A",
        f"range: {TMIN:g}-{TMAX:g} deg, {NPT} points",
        "background: auto sliding baseline 0.2 deg; "
        "0.25 deg piecewise-linear correction",
        f"matched peaks: {len(matched)} of {len(st_tt)} theoretical",
        "",
        "=== full-pattern fit ===",
        fit.report(),
        "",
        "=== per-peak comparison ===",
        f"{'2theta_theory':>14} {'d2theta':>8} {'FWHM_exp':>9} "
        f"{'FWHM_sim':>9} {'I_exp%':>7} {'I_sim%':>7}",
        *[f"{tt:14.4f} {c - tt - zero_fit:+8.4f} {fw:9.4f} "
          f"{fws:9.4f} {h_rel:7.2f} {i_sim / i_sim_max * 100:7.2f}"
          for tt, c, fw, fws, h_rel, i_sim in rows],
    ]) + "\n", encoding="utf-8")
    print(f"  → {report}")


def study_fit(cif: Path, outdir: Path, exp_tif: Path) -> None:
    """精修：用 GSAS-II 口径的全谱最小二乘拟合实验 1D（2026-10-22）。

    流程：实验图积分成 1D → 自动基线（固定背景）→ 两轮拟合：
      ① 仪器 + 常规峰形（zero/U/V/W/X/Y）；
      ② 再加 Lorentzian 尺寸项（Size）——它是 GSAS 默认的"长尾巴"通道，
         专门改善峰间底部（不是靠抬高基线去凑）。
    打印 Rwp/Rp、参数表、底部（y<5% 最强峰）残差的均值/RMS，
    并存 `sim_xrd_fit_{材料}.png`（观测/计算/背景 + 残差 + 放大窗）
    与同名 `.txt` 完整参数报告（三段拟合 + 底部统计）。
    """
    import matplotlib
    matplotlib.use("Agg")
    from xrd_toolkit.config import get_config
    from xrd_toolkit.services.background import estimate_baseline_sliding
    from xrd_toolkit.services.data_loader import load_diffraction_image
    from xrd_toolkit.services.integrator import integrate_1d

    material = _material_name(cif)
    structure = load_structure(str(cif))
    b_iso = b_iso_from_cif(cif)
    cfg = get_config("lmfp1_lab6")
    geom = dict(cfg["geometry"])
    lam = geom["wavelength_m"] * 1e10
    TMIN, TMAX = 1.0, 8.0
    # 2000 点 ≈ 0.5 像素/点；3000 点过采样会把斑驳环的峰顶顶出假双尖
    # （见 study_compare 里同一段说明）
    NPT = 2000

    img = load_diffraction_image(str(exp_tif))
    tth, y = integrate_1d(img, npt=NPT, tth_min_deg=TMIN,
                          tth_max_deg=TMAX, **geom)
    bg = estimate_baseline_sliding(tth, y, 0.2)

    instrument = fit_pattern(
        structure, tth, y, wavelength=lam, b_iso=b_iso,
        background=bg, background_knots=0.25, weights="poisson",
        free=("zero", "u", "v", "w", "x", "y"))
    tail = fit_pattern(
        structure, tth, y, wavelength=lam, b_iso=b_iso,
        background=bg, background_knots=0.25, intensity_poly=2,
        weights="poisson",
        free=("zero", "u", "v", "w", "x", "y", "size_um", "b_iso"),
        initial={**instrument.params, "size_um": 0.75, "b_iso": 0.0})
    # Le Bail 式：每峰积分强度单独解出——用来验证"峰形/背景公式"本身，
    # 把结构强度模型的残差（B/偏振/吸收）分离出去。
    lebail = fit_pattern(
        structure, tth, y, wavelength=lam, b_iso=b_iso,
        background=bg, background_knots=0.25, weights="poisson",
        refine_intensities=True,
        free=("zero", "u", "v", "w", "x", "y"),
        initial={**instrument.params, "size_um": 0.75})

    def bottom(res):
        m = res.y_obs < 0.05 * res.y_obs.max()
        r = res.residual[m]
        return int(m.sum()), float(r.mean()), float(np.sqrt(np.mean(r ** 2)))

    st_tt, st_ii, peaks, is_hex = pattern_from_cif(
        str(cif), wavelength=lam, two_theta_range=(TMIN, TMAX))
    dist = np.min(np.abs(tth[:, None] - st_tt[None, :]), axis=1)
    inter = dist > 0.12               # 离任何理论峰都 >0.12° 的"真底部"

    def inter_stats(res):
        r = res.residual[inter]
        return float(r.mean()), float(np.sqrt(np.mean(r ** 2)))

    n_low, mean_i, rms_i = bottom(instrument)
    _, mean_t, rms_t = bottom(tail)
    _, mean_l, rms_l = bottom(lebail)
    im_i, ir_i = inter_stats(instrument)
    im_t, ir_t = inter_stats(tail)
    im_l, ir_l = inter_stats(lebail)
    out = outdir / f"sim_xrd_fit_{material}.png"
    plot_fit(lebail, f"{material} — profile fit with Le Bail intensities, "
                     f"λ = {lam:.4f} Å", str(out),
             zoom=(1.6, 2.6))
    out_b = outdir / f"sim_xrd_fit_bottom_{material}.png"
    plot_fit_bottom(
        lebail, f"{material} — inter-peak bottom residual (Le Bail fit)",
        str(out_b), inter_mask=inter, zoom=(4.30, 4.70))
    labels = [format_hkls(p[2], is_hex) for p in peaks]
    out_i = outdir / f"sim_xrd_intensity_ratios_{material}.png"
    plot_intensity_ratios(tail, bg, st_tt, labels, str(out_i),
                          intensities=st_ii)

    print(f"{material} 精修（λ = {lam:.4f} Å，{TMIN:g}–{TMAX:g}°，"
          f"自动基线 + 0.25° 分段线性背景修正）")
    print(f"  ① 仪器+常规峰形：Rwp {instrument.rwp:.3f}% / Rp {instrument.rp:.3f}%")
    a1 = tail.params.get("intensity_a1", 0.0)
    a2 = tail.params.get("intensity_a2", 0.0)
    print(f"  ② + Lorentzian 尾巴 + 全局 ΔB + 光滑强度修正："
          f"Rwp {tail.rwp:.3f}% / Rp {tail.rp:.3f}%")
    print(tail.report())
    print(f"  Size = {tail.params['size_um']:.3f} µm（Lorentzian 尾巴项）；"
          f"ΔB = {tail.params['b_iso']:+.3f} Å²（叠在 CIF 各元素 B 上）；"
          f"强度 ×(1 {a1:+.3f}z {a2:+.3f}z²)")
    ratio = (lebail.peak_intensities
             / np.maximum(lebail.peak_table_intensities, 1e-12))
    print(f"  ③ Le Bail（每峰强度自由）：Rwp {lebail.rwp:.3f}% / "
          f"Rp {lebail.rp:.3f}% ；fit/table 强度比 "
          f"{ratio.min():.2f}–{ratio.max():.2f}（中位 {np.median(ratio):.2f}）")
    print(f"  底部（y < 5% 最强峰，{n_low} 点）obs−calc：")
    print(f"    ① mean {mean_i:+.1f}  RMS {rms_i:.1f} counts")
    print(f"    ② mean {mean_t:+.1f}  RMS {rms_t:.1f} counts")
    print(f"    ③ mean {mean_l:+.1f}  RMS {rms_l:.1f} counts")
    print(f"  峰间底部（离任何理论峰 >0.12°，{int(inter.sum())} 点）mean/RMS：")
    print(f"    ① {im_i:+.1f} / {ir_i:.1f} counts")
    print(f"    ② {im_t:+.1f} / {ir_t:.1f} counts")
    print(f"    ③ {im_l:+.1f} / {ir_l:.1f} counts")
    print(f"  → {out}")
    print(f"  → {out_b}")
    print(f"  → {out_i}")
    # 图旁边的完整参数报告（2026-10-26）：三项 FitResult 的参数表、
    # 底部统计与实验条件都落盘，下次不用重跑才能拿数字。
    entries, notes = fit_entries(
        exp_tif.name, material, tail, lebail=lebail,
        cell_fixed=fixed_cell_text(structure))
    report = out.with_suffix(".txt")
    report.write_text(summary_block(entries, notes) + "\n" + "\n".join([
        f"material: {material}",
        f"data: {exp_tif.name}",
        "geometry: lmfp1_lab6",
        f"wavelength: {lam:.6f} A",
        f"range: {TMIN:g}-{TMAX:g} deg, {NPT} points",
        "background: auto sliding baseline 0.2 deg; "
        "0.25 deg piecewise-linear correction",
        f"size: {tail.params['size_um']:.4f} um (Lorentzian tail term)",
        f"delta B: {tail.params['b_iso']:+.4f} A^2",
        f"intensity correction: 1 {a1:+.4f} z {a2:+.4f} z^2",
        f"bottom residual (y < 5% max, {n_low} points):",
        f"  1 mean {mean_i:+.2f} RMS {rms_i:.2f} counts",
        f"  2 mean {mean_t:+.2f} RMS {rms_t:.2f} counts",
        f"  3 mean {mean_l:+.2f} RMS {rms_l:.2f} counts",
        f"inter-peak bottom (>0.12 deg from any reflection, "
        f"{int(inter.sum())} points):",
        f"  1 {im_i:+.2f} / {ir_i:.2f} counts",
        f"  2 {im_t:+.2f} / {ir_t:.2f} counts",
        f"  3 {im_l:+.2f} / {ir_l:.2f} counts",
        "",
        "=== 1 instrument + conventional profile ===",
        instrument.report(),
        "",
        "=== 2 + Lorentzian size + global dB + smooth intensity correction ===",
        tail.report(),
        "",
        "=== 3 Le Bail (per-peak intensities) ===",
        lebail.report(),
    ]) + "\n", encoding="utf-8")
    print(f"  → {report}")


def study_keys(cif: Path, outdir: Path) -> None:
    """一页看两条硬指标：面积不变 + 峰宽与 GSAS-II 一致（2026-10-22）。

    (a) 面积：把 LaB₆ (110) 的仪器宽度从 0.05° 扫到 0.20°，峰高按
        1/FWHM 下降，积分面积保持——这就是"峰变宽只变矮，不长大"。
    (b) 宽度：四个通道（W / X / Size / Mustrain）在 2θ=30.38° 的 FWHM，
        自研 vs 真 GSAS-II 的相对差都在 ±1.2% 内（GSAS 值带背景测量）。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    material = _material_name(cif)
    structure = load_structure(str(cif))
    CENTER = 30.38

    def area_above_baseline(tth, y, half=1.5):
        m = (tth > CENTER - half) & (tth < CENTER + half)
        xs, ys = tth[m], y[m]
        base = np.linspace(ys[0], ys[-1], len(ys))
        return float(np.trapezoid(ys - base, xs))

    fwhms, heights, areas = [], [], []
    for w in (4.5, 18.033, 72.132):        # FWHM 0.05 / 0.10 / 0.20°
        tth, y, _, _ = simulate_pattern_gsas(
            structure, w=w, size_um=1e5, mustrain=0.0,
            two_theta_range=(CENTER - 3.0, CENTER + 3.0), step_deg=0.001,
            normalize=False)
        m = np.abs(tth - CENTER) < 0.6
        fwhms.append(2.3548 * np.sqrt(w) / 100.0)
        heights.append(float(y[m].max() - np.median(
            np.r_[y[:30], y[-30:]])))
        areas.append(area_above_baseline(tth, y))
    heights = np.asarray(heights) / heights[0]
    areas = np.asarray(areas) / areas[0]

    # GSAS-II 实测值（2026-10-22，见 scripts/gsas2_crosscheck.py 的说明）
    cases = [
        ("W = 18.033", 0.10000, 0.10031),
        ("X = 1", 0.01047, 0.01050),
        ("Size 0.05 µm", 0.18298, 0.18505),
        ("Mustrain 1000", 0.01564, 0.01566),
    ]

    fig, (ax_a, ax_b) = plt.subplots(
        1, 2, figsize=(13.5, 4.8), gridspec_kw={"width_ratios": [1.15, 1]})
    ax_a.plot(fwhms, heights, "o-", color="#2b6cb0", lw=1.6, ms=6,
              label="peak height (normalised)")
    ax_a.set_xlabel("instrument FWHM (degrees)")
    ax_a.set_ylabel("peak height / height at 0.05°", color="#2b6cb0")
    ax_a.tick_params(axis="y", labelcolor="#2b6cb0")
    ax_a.set_ylim(0, 1.25)
    ax_a2 = ax_a.twinx()
    ax_a2.plot(fwhms, areas, "s--", color="#d94b2b", lw=1.6, ms=6,
               label="integrated area (normalised)")
    ax_a2.set_ylabel("integrated area / area at 0.05°", color="#d94b2b")
    ax_a2.tick_params(axis="y", labelcolor="#d94b2b")
    ax_a2.set_ylim(0.8, 1.1)
    ax_a.set_title("(a) area is conserved — height ∝ 1/FWHM", fontsize=10.5)
    lines = (ax_a.get_lines() + ax_a2.get_lines())
    ax_a.legend(lines, [ln.get_label() for ln in lines], loc="center right",
                fontsize=8.5)
    ax_a.grid(alpha=0.25, linestyle=":")

    labels = [c[0] for c in cases]
    diff = [100.0 * (c[1] - c[2]) / c[2] for c in cases]
    xpos = np.arange(len(cases))
    ax_b.axhspan(-1.0, 1.0, color="#2b6cb0", alpha=0.08,
                 label="±1% band")
    ax_b.axhline(0.0, color="0.5", lw=0.8)
    bars = ax_b.bar(xpos, diff, width=0.55, color="#2b6cb0", alpha=0.85)
    for b, c in zip(bars, cases):
        ax_b.text(b.get_x() + b.get_width() / 2,
                  0.25 if b.get_height() >= 0 else -0.6,
                  f"{b.get_height():+.2f}%", ha="center", va="bottom",
                  fontsize=9)
    ax_b.set_xticks(xpos)
    ax_b.set_xticklabels(labels, fontsize=9)
    ax_b.set_ylim(-1.6, 1.6)
    ax_b.set_ylabel("(ours − GSAS-II) / GSAS-II  (%)")
    ax_b.set_title("(b) FWHM at 2θ = 30.38° vs GSAS-II", fontsize=10.5)
    ax_b.legend(loc="lower right", fontsize=8.5)
    ax_b.grid(axis="y", alpha=0.25, linestyle=":")

    fig.suptitle(f"{material} — the two keys: area invariance + width "
                 f"consistency", fontsize=12.5)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    out = outdir / "sim_xrd_gsas_keys.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"{material} 两条硬指标：")
    print("  面积  FWHM 0.05/0.10/0.20° → 峰高 "
          + " / ".join(f"{h:.3f}" for h in heights)
          + "，面积 " + " / ".join(f"{a:.3f}" for a in areas))
    print("  峰宽  自研 vs GSAS-II：" + "；".join(
        f"{c[0]} {c[1]:.5f}/{c[2]:.5f} ({100*(c[1]-c[2])/c[2]:+.2f}%)"
        for c in cases))
    print(f"  → {out}")


STUDIES = {
    "basic": study_basic,
    "instrument": study_instrument,
    "microstructure": study_microstructure,
    "compare": study_compare,
    "fit": study_fit,
    "keys": study_keys,
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
    ap.add_argument("--exp", default=str(ROOT / "data" / "lab6-00024.tif"),
                    help="compare 用：实验 LaB₆ 图（默认 data/lab6-00024.tif，"
                         "即第三周处理过的那张标样）")
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
    elif args.study in ("compare", "fit"):
        exp = Path(args.exp)
        if not exp.exists():
            ap.error(f"实验图不存在：{exp}（--exp 指定）")
        STUDIES[args.study](cif, outdir, exp)
    else:
        STUDIES[args.study](cif, outdir)


if __name__ == "__main__":
    main()
