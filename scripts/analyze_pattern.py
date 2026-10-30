#!/usr/bin/env python3
"""只给数据，自动分析（LMFP 第一版，2026-10-22）。

输入：2D TIFF（或 1D 文本暂未接） + 批次几何配置。
输出：自动找峰（`services/peak_finding`）→ 在相库里筛相
（粗扫晶胞缩放）→ 自动识别 Al 集流体 (111) → 结构拟合
（晶胞/Size/应变/B/背景）→ Le Bail 非负峰强 → 图 + 文本报告
（相筛选摘要 + 仪器/结构/Le Bail 三段完整参数表）。

相库/几何仍是先验：
  * 几何来自 `--config`（LMFP 默认 `lmfp1_lab6`，λ=0.1223 Å）；
  * 相库 = `--lib` 目录下的全部 `.cif`（`Al_metal.cif` 除外，它只作
    基片相）；`--elements` 可按允许的元素集合先剪枝（大库建议给）。
    未知相不在库里时只会报“未识别”，不会硬认。

用法：
    python scripts/analyze_pattern.py                       # 两张 LMFP
    python scripts/analyze_pattern.py data/LMFP_1_atten0-00029.tif
"""
import argparse
import re
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.config import get_config  # noqa: E402
from xrd_toolkit.services.background import estimate_baseline_sliding  # noqa: E402
from xrd_toolkit.services.data_loader import load_diffraction_image  # noqa: E402
from xrd_toolkit.services.hot_pixels import mask_hot_pixels  # noqa: E402
from xrd_toolkit.services.integrator import integrate_1d  # noqa: E402
from xrd_toolkit.services.peak_finding import find_peaks_1d  # noqa: E402
from xrd_toolkit.simulation import (  # noqa: E402
    b_iso_from_cif, core, fit_entries, fit_pattern, load_structure,
    plot_fit, summary_block)

STRUCT = ROOT / "data" / "structures"
AL_CIF = STRUCT / "Al_metal.cif"
# LMFP 先验：样品 Mn:Fe = 6:4（2026-10-27）。只用于**同分**排序——这套
# 数据对 Mn/Fe 比不敏感，0.5:0.5 与 0.6:0.4 的 score/Rwp 基本相同，
# 所以按已知组分把 6:4 排在前面；打分本身不看这个表。
PREFERRED = (
    "LiMn0.6Fe0.4PO4_olivine.cif",
    "LiMn0.5Fe0.5PO4_olivine.cif",
    "LiFePO4_olivine.cif",
    "LiMnPO4_olivine.cif",
)


def phase_library(lib):
    """相库目录里的全部 CIF，按文件名排序。

    `Al_metal.cif` 永远不进入主相候选：它是集流体的第二相，由
    `al_substrate_ranges` 单独识别、在 fit 里按额外相处理。把它当主相
    候选会让“纯 Al 样品”拿到最高分，结果整个分析跑偏。
    """
    lib = Path(lib)
    al = (lib / AL_CIF.name).resolve()
    return [p for p in sorted(lib.glob("*.cif"))
            if p.is_file() and p.resolve() != al]


def phase_candidates(lib, elements=()):
    """扫描相库并读结构，按允许的元素集合剪枝；返回 [(path, Structure)]。

    elements 为空 = 不筛。给了集合时只保留**元素全部落在集合里**的
    相（例如 'Li Mn Fe P O' 会排掉 Al2O3、LaB6）。读不出来的 CIF
    跳过，不让一张坏卡片打断整批分析。
    """
    wanted = {str(e).strip().capitalize()
              for e in elements if str(e).strip()}
    out = []
    for path in phase_library(lib):
        try:
            structure = load_structure(str(path))
        except Exception:                          # noqa: BLE001
            continue
        if wanted:
            have = {el.symbol for el in structure.composition.elements}
            if not have.issubset(wanted):
                continue
        out.append((path, structure))
    # 同分时按 LMFP 先验排（稳定排序；打分排序也保持这个先后）
    rank = {name: i for i, name in enumerate(PREFERRED)}
    out.sort(key=lambda item: (rank.get(item[0].name, len(PREFERRED)),
                               item[0].name))
    return out
DEFAULT_IMAGES = [
    ROOT / "data" / "LMFP_1_atten0-00029.tif",
    ROOT / "data" / "LMFP_1_atten0-00031.tif",
]
LAB6_TIF = ROOT / "data" / "lab6-00024.tif"
# 实验拟合用 pyFAI 默认 LaB6 的晶胞数值（等同 SRM 660c；证书未记录）
LAB6_CIF = STRUCT / "LaB6_NIST660c.cif"
# 背景曲率罚项（同 fit_lmfp.py：0.1 把 LMFP 7.5° 的背景塌陷压掉）
BG_SMOOTH = 0.1
# 默认输出到系统临时目录：脚本不许往仓库的 outputs/ 写东西（隔离护栏）
DEFAULT_OOB = Path(tempfile.gettempdir()) / "xrd_toolkit_analysis"


def load_1d(path, geom, tmin, tmax, npt=3000, hot_mask=False):
    img = load_diffraction_image(str(path))
    if hot_mask:
        img, _mask, info = mask_hot_pixels(img)
        print(f"  坏点掩膜：{info['masked_components']} 团 / "
              f"{info['masked_pixels']} px（候选 {info['candidates']}）")
    return integrate_1d(img, npt=npt, tth_min_deg=tmin, tth_max_deg=tmax,
                        **geom)


def instrument_profile(geom, lam):
    """标样的峰形用 Le Bail 定 U/V/W/X/Y（逐峰强度放开，避免结构强度
    模型的误差挤进峰形参数）；没有标样图时退化为 None。"""
    if not LAB6_TIF.exists():
        return None
    st = load_structure(str(LAB6_CIF))
    b6 = b_iso_from_cif(str(LAB6_CIF))
    t, y = load_1d(LAB6_TIF, geom, 1.0, 8.0)
    bg = estimate_baseline_sliding(t, y, 0.2)
    return fit_pattern(
        st, t, y, wavelength=lam, b_iso=b6, background=bg,
        background_knots=0.25, weights="poisson", refine_intensities=True,
        free=("zero", "u", "v", "w", "x", "y"), max_nfev=200)


def detect_peaks(t, y, bg):
    """自动找峰，返回 (2θ, 峰高)；峰高 = 强度 − 基线。"""
    peaks = find_peaks_1d(t, y, baseline=bg)
    return (np.array([p.center for p in peaks]),
            np.array([p.height for p in peaks]))


def _reflection_data(cif, lam, tmin, tmax, structure=None):
    """一次算好 hkl 无关的 d0 与相对强度，供晶胞缩放扫描复用。

    structure 给定时直接用（相库扫描已经读过 CIF，不重复加载）。
    """
    st = structure if structure is not None else load_structure(str(cif))
    _hkl, d0, _tth, inten = core.reflection_table(
        st, wavelength=lam, two_theta_range=(tmin, tmax), nmax=10)
    inten = np.asarray(inten, dtype=float)
    if inten.size and inten.max() > 0:
        inten = inten / inten.max()
    return np.asarray(d0, dtype=float), inten


def _positions(d0, inten, lam, scale, zero):
    sin_t = np.clip(0.5 * lam / (d0 * scale), 0.0, 1.0)
    return 2.0 * np.degrees(np.arcsin(sin_t)) + zero, inten


def theoretical_positions(cif, lam, tmin, tmax, scale=1.0, zero=0.0,
                          structure=None):
    d0, inten = _reflection_data(cif, lam, tmin, tmax, structure=structure)
    return _positions(d0, inten, lam, scale, zero)


def score_phase(obs_t, cif, lam, tmin, tmax, zero, tol=0.04,
                structure=None):
    """粗扫晶胞缩放，按观测峰覆盖 + 理论峰纯度打分。"""
    best = None
    d0_all, int_all = _reflection_data(
        cif, lam, tmin, tmax, structure=structure)
    strong = int_all >= 0.02
    d0_all, int_all = d0_all[strong], int_all[strong]
    for scale in np.linspace(0.96, 1.05, 91):
        tt, inten = _positions(d0_all, int_all, lam, scale, zero)
        if not len(tt):
            continue
        used = set()
        matched_theo = 0
        for x in tt:
            j = int(np.argmin(np.abs(obs_t - x)))
            if abs(float(obs_t[j]) - x) <= tol and j not in used:
                used.add(j)
                matched_theo += 1
        coverage = len(used) / max(len(obs_t), 1)
        purity = matched_theo / max(len(tt), 1)
        score = coverage + 0.2 * purity
        if best is None or score > best[0]:
            best = (score, float(scale), coverage, purity)
    return best


def al_substrate_ranges(obs_t, obs_h, lam, tmin, tmax, zero, primary_cif,
                        primary_scale, al_cif=None, primary_structure=None):
    """识别 Al 集流体的强峰（主要看 Al (111)）。返回 [(lo,hi), …]。"""
    al_cif = AL_CIF if al_cif is None else Path(al_cif)
    if not al_cif.exists():
        return []
    al_t, al_i = theoretical_positions(al_cif, lam, tmin, tmax, 1.0, zero)
    prim_t, prim_i = theoretical_positions(
        primary_cif, lam, tmin, tmax, primary_scale, zero,
        structure=primary_structure)
    hmax = float(np.max(obs_h)) if len(obs_h) else 1.0
    out = []
    for x, ai in zip(al_t, al_i):
        if ai < 0.5:                       # 只认最强的 Al 线（111）
            continue
        j = int(np.argmin(np.abs(obs_t - x)))
        if abs(float(obs_t[j]) - x) > 0.05 or obs_h[j] < 0.5 * hmax:
            continue
        # 主相在这个位置是否本来就有**很强**的峰？有就不当基片
        if len(prim_t):
            k = int(np.argmin(np.abs(prim_t - x)))
            if abs(float(prim_t[k]) - x) <= 0.03 and prim_i[k] >= 0.5:
                continue
        out.append((float(x) - 0.09, float(x) + 0.09))
    return out


def fit_one(path, primary, primary_name, cell0, geom, lam, inst, outdir,
            exclude, tmin, tmax, use_al=False, hot_mask=True, al_cif=None):
    t, y = load_1d(path, geom, tmin, tmax, hot_mask=hot_mask)
    bg = estimate_baseline_sliding(t, y, 0.2)
    w = 1.0 / np.maximum(y, 1.0)
    w_leb = w.copy()
    for lo, hi in exclude:
        w_leb[(t > lo) & (t < hi)] = 0.0
    w_struct = w if use_al else w_leb
    extras = []
    al_cif = AL_CIF if al_cif is None else Path(al_cif)
    if use_al and al_cif.exists():
        al = load_structure(str(al_cif))
        extras.append({
            "structure": al, "label": "Al",
            "a": 4.061, "b": 4.061, "c": 4.061,
            "size_um": 0.1, "mustrain": 0.0, "b_iso": 0.5,
            # R 由 (200)/(220)/(311) 弱反射定出（0.72）；LiF 与 Al
            # 同为岩盐且晶胞几乎相同，位置上完全退化，不再另加 LiF。
            "md": ((1, 1, 1), 0.72),
            # 单轴 MD 在 (331) 上失效（2026-10-27）：Al 峰强逐反射自由，
            # 避免背景被峰模型误差拉出 7.5° 的坑。
            "refine_intensities": True,
        })
    ip = {} if inst is None else {
        "zero": inst.params["zero"], "u": inst.params["u"],
        "v": inst.params["v"], "w": inst.params["w"],
        "x": inst.params["x"], "y": inst.params["y"]}
    if inst is None:
        ip.update(zero=0.0, u=0.0, v=0.0, w=0.65, x=0.0, y=0.0)
    init = {**ip, "a": cell0[0], "b": cell0[1], "c": cell0[2],
            "size_um": 0.2, "mustrain": 1000.0, "b_iso": 0.5}
    label = Path(primary_name).stem.split("_")[0]
    struct = fit_pattern(
        primary, t, y, wavelength=lam, b_iso=0.5, background=bg,
        background_knots=0.25, background_smooth=BG_SMOOTH,
        intensity_poly=2, weights=w_struct,
        extra_phases=extras, phase_label=label,
        free=("a", "b", "c", "size_um", "mustrain", "b_iso"),
        initial=init, max_nfev=300)
    # 强度/展示用 Le Bail（带 Al 相时把 Al scale 也解出来）；结构拟合
    # 只负责晶胞/微结构。
    leb_weights = w if use_al else w_leb
    leb_extras = extras if use_al else []
    lebail = fit_pattern(
        primary, t, y, wavelength=lam, b_iso=0.5, background=bg,
        background_knots=0.25, background_smooth=BG_SMOOTH,
        weights=leb_weights, cell_refine=True,
        refine_intensities=True, phase_label=label,
        extra_phases=leb_extras,
        free=("size_um", "mustrain"),
        initial={**init, "a": struct.params["a"], "b": struct.params["b"],
                 "c": struct.params["c"],
                 "size_um": struct.params["size_um"],
                 "mustrain": struct.params["mustrain"]}, max_nfev=200)
    tag = path.stem.rsplit("-", 1)[-1]
    out = outdir / f"sim_xrd_fit_LMFP_{tag}.png"
    plot_src = lebail
    phase_txt = " + ".join(["Al substrate (per-reflection)"] if use_al else [])
    plot_fit(plot_src, f"{path.name} — auto: {primary_name}"
                       + (f" + {phase_txt}" if phase_txt else "")
                       + " Le Bail fit, "
                       f"λ = {lam:.4f} Å",
             str(out), zoom=(2.6, 3.2),
             exclude_ranges=[] if use_al else list(exclude))
    return struct, lebail, out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("images", nargs="*", default=[str(p) for p in DEFAULT_IMAGES])
    ap.add_argument("--config", default="lmfp1_lab6")
    ap.add_argument("--lib", default=str(STRUCT),
                    help="包含 .cif 的相库目录（默认 data/structures；"
                         "目录下所有 .cif 都是主相候选，Al_metal.cif 除外）")
    ap.add_argument("--elements", default="",
                    help="只保留元素全在这张表里的相（空格或逗号分隔，"
                         "如 'Li Mn Fe P O'；默认不筛）")
    ap.add_argument("--outdir", default=str(DEFAULT_OOB))
    ap.add_argument("--tmin", type=float, default=1.0)
    ap.add_argument("--tmax", type=float, default=8.0)
    ap.add_argument("--no-hot-mask", action="store_true",
                    help="关掉坏点团掩膜（默认开；标样本来就不过掩膜）")
    a = ap.parse_args()
    hot_mask = not a.no_hot_mask
    geom = dict(get_config(a.config)["geometry"])
    lam = geom["wavelength_m"] * 1e10
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    lib = Path(a.lib)
    allowed = [e for e in re.split(r"[,\s]+", a.elements) if e]
    candidates = phase_candidates(lib, allowed)
    if not candidates:
        ap.error(f"相库里没有可用的 CIF：{lib}"
                 + (f"（元素筛选：{a.elements}）" if allowed else ""))
    print(f"相库 {lib}：{len(phase_library(lib))} 张 CIF，"
          f"元素筛选后 {len(candidates)} 张")
    inst = instrument_profile(geom, lam)
    print(f"几何 {a.config}：λ={lam:.4f} Å；"
          + (f"仪器来自 LaB₆（Rwp {inst.rwp:.2f}%）" if inst else
             "无 LaB₆ 标样，仪器用默认值（结果需谨慎）"))

    for image in a.images:
        p = Path(image)
        if not p.exists():
            print(f"跳过（不存在）：{p}")
            continue
        t, y = load_1d(p, geom, a.tmin, a.tmax, hot_mask=hot_mask)
        bg = estimate_baseline_sliding(t, y, 0.2)
        obs_t, obs_h = detect_peaks(t, y, bg)
        print(f"\n{p.name}：检出 {len(obs_t)} 根峰")
        if len(obs_t) < 4:
            print("  峰太少，无法自动分析")
            continue
        zero = inst.params["zero"] if inst else 0.0
        ranked = []
        for cif, structure in candidates:
            sc = score_phase(obs_t, cif, lam, a.tmin, a.tmax, zero,
                             structure=structure)
            if sc:
                ranked.append((sc, cif, structure))
        if not ranked:
            print("  相筛选：没有候选相能在观测峰上打分")
            continue
        ranked.sort(key=lambda x: -x[0][0])
        top = ranked[0]
        primary = top[2]
        scale = top[0][1]
        cell0 = tuple(v * scale for v in primary.lattice.abc)
        print("  相筛选：")
        for (sc, scale_i, cov, pur), cif, _st in ranked[:3]:
            print(f"    {cif.name:34} score {sc:.3f} "
                  f"scale {scale_i:.4f} coverage {cov:.2f} purity {pur:.2f}")
        al_cif = lib / AL_CIF.name
        exclude = al_substrate_ranges(
            obs_t, obs_h, lam, a.tmin, a.tmax, zero, top[1], scale,
            al_cif=al_cif, primary_structure=primary)
        use_al = bool(exclude)
        if use_al:
            print("  自动识别 Al 集流体峰，按 Al 基片（逐反射强度）建模："
                  + ", ".join(f"{lo:.2f}–{hi:.2f}°" for lo, hi in exclude))
        else:
            print("  未识别到 Al 集流体强峰")
        struct, lebail, out = fit_one(
            p, primary, top[1].name, cell0, geom, lam, inst, outdir,
            exclude, a.tmin, a.tmax, use_al=use_al, hot_mask=hot_mask,
            al_cif=al_cif)
        al_txt = (f" Al={struct.params['Al:scale']:.3g}"
                  if "Al:scale" in struct.params else "")

        print(f"  结构：Rwp {struct.rwp:.3f}%  a={struct.params['a']:.4f} "
              f"b={struct.params['b']:.4f} c={struct.params['c']:.4f} "
              f"Size={struct.params['size_um']:.3f} µm "
              f"ε={struct.params['mustrain']/4e6*1e3:.2f}e-3 "
              f"B={struct.params['b_iso']:.2f}{al_txt}")
        print(f"  Le Bail：Rwp {lebail.rwp:.3f}%")
        if struct.params["b_iso"] <= 0.01:
            print("  警告：B 撞到 0，结构强度模型不可信；以 Le Bail 峰强为准")
        print(f"  → {out}")
        entries, notes = fit_entries(
            p.name, top[1].stem.split("_")[0], struct, lebail=lebail,
            al_scale=(struct.params.get("Al:scale") if use_al else None))
        with open(out.with_suffix(".txt"), "w", encoding="utf-8") as fh:
            fh.write(summary_block(entries, notes) + "\n")
            fh.write(f"{p.name}\nprimary: {top[1].name} scale={scale:.4f}\n")
            for (sc, scale_i, cov, pur), cif, _st in ranked:
                fh.write(f"  {cif.name}: score={sc:.3f} scale={scale_i:.4f} "
                         f"coverage={cov:.3f} purity={pur:.3f}\n")
            fh.write(f"exclude: {exclude}\n")
            fh.write(f"structural Rwp={struct.rwp:.3f}% "
                     f"a={struct.params['a']:.4f} "
                     f"b={struct.params['b']:.4f} "
                     f"c={struct.params['c']:.4f} "
                     f"Size={struct.params['size_um']:.4f} "
                     f"strain={struct.params['mustrain']/4e6:.6f} "
                     f"B={struct.params['b_iso']:.4f}"
                     + (f" Al_scale={struct.params['Al:scale']:.6g}"
                        if "Al:scale" in struct.params else "")

                     + "\n")
            fh.write(f"Le Bail Rwp={lebail.rwp:.3f}%\n")
            # 2026-10-26：摘要之后附完整参数表，“能出的数据都写上”
            fh.write("\n=== instrument (LaB6, Le Bail) ===\n")
            fh.write((inst.report() if inst is not None
                      else "not available") + "\n")
            fh.write("\n=== structural fit ===\n")
            fh.write(struct.report() + "\n")
            fh.write("\n=== Le Bail fit ===\n")
            fh.write(lebail.report() + "\n")


if __name__ == "__main__":
    main()
