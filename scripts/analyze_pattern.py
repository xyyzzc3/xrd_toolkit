#!/usr/bin/env python3
"""只给数据，自动分析（LMFP 第一版，2026-10-22）。

输入：2D TIFF（或 1D 文本暂未接） + 批次几何配置。
输出：自动找峰 → 在内置橄榄石相库里筛相（粗扫晶胞缩放）→ 自动识别
Al 集流体 (111) → 结构拟合（晶胞/Size/应变/B/背景）→ Le Bail 非负峰强
→ 图 + 文本报告。

相库/几何仍是先验：
  * 几何来自 `--config`（LMFP 默认 `lmfp1_lab6`，λ=0.1223 Å）；
  * 相库默认 `data/structures` 里的三种橄榄石 + Al；未知相不在库里时
    只会报“未识别”，不会硬认。

用法：
    python scripts/analyze_pattern.py                       # 两张 LMFP
    python scripts/analyze_pattern.py data/LMFP_1_atten0-00029.tif
"""
import argparse
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scipy.signal import find_peaks  # noqa: E402

from xrd_toolkit.config import get_config  # noqa: E402
from xrd_toolkit.services.background import estimate_baseline_sliding  # noqa: E402
from xrd_toolkit.services.data_loader import load_diffraction_image  # noqa: E402
from xrd_toolkit.services.hot_pixels import mask_hot_pixels  # noqa: E402
from xrd_toolkit.services.integrator import integrate_1d  # noqa: E402
from xrd_toolkit.simulation import (  # noqa: E402
    b_iso_from_cif, core, fit_pattern, load_structure, plot_fit)

STRUCT = ROOT / "data" / "structures"
OLIVINE_LIB = [
    STRUCT / "LiMn0.5Fe0.5PO4_olivine.cif",
    STRUCT / "LiFePO4_olivine.cif",
    STRUCT / "LiMnPO4_olivine.cif",
]
AL_CIF = STRUCT / "Al_metal.cif"
# 电极里常见的 LiF（SEI/电解液残留）：有效 a=4.062 Å
LIF_CIF = STRUCT / "LiF_rock_salt.cif"
DEFAULT_IMAGES = [
    ROOT / "data" / "LMFP_1_atten0-00029.tif",
    ROOT / "data" / "LMFP_1_atten0-00031.tif",
]
LAB6_TIF = ROOT / "data" / "lab6-00024.tif"
# 实验拟合用 NIST SRM 660c 室温晶胞（与几何标定参考一致）
LAB6_CIF = STRUCT / "LaB6_NIST660c.cif"
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
    ys = y - bg
    med = float(np.median(ys))
    sigma = 1.4826 * float(np.median(np.abs(ys - med)))
    prom = max(5.0 * sigma, 0.02 * float(np.max(ys)))
    dist = max(2, int(round(0.008 / max(float(np.median(np.diff(t))), 1e-9))))
    idx, _ = find_peaks(ys, prominence=prom, distance=dist)
    return t[idx], ys[idx]


def _reflection_data(cif, lam, tmin, tmax):
    """一次算好 hkl 无关的 d0 与相对强度，供晶胞缩放扫描复用。"""
    st = load_structure(str(cif))
    _hkl, d0, _tth, inten = core.reflection_table(
        st, wavelength=lam, two_theta_range=(tmin, tmax), nmax=10)
    inten = np.asarray(inten, dtype=float)
    if inten.size and inten.max() > 0:
        inten = inten / inten.max()
    return np.asarray(d0, dtype=float), inten


def _positions(d0, inten, lam, scale, zero):
    sin_t = np.clip(0.5 * lam / (d0 * scale), 0.0, 1.0)
    return 2.0 * np.degrees(np.arcsin(sin_t)) + zero, inten


def theoretical_positions(cif, lam, tmin, tmax, scale=1.0, zero=0.0):
    d0, inten = _reflection_data(cif, lam, tmin, tmax)
    return _positions(d0, inten, lam, scale, zero)


def score_phase(obs_t, cif, lam, tmin, tmax, zero, tol=0.04):
    """粗扫晶胞缩放，按观测峰覆盖 + 理论峰纯度打分。"""
    best = None
    d0_all, int_all = _reflection_data(cif, lam, tmin, tmax)
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
                        primary_scale):
    """识别 Al 集流体的强峰（主要看 Al (111)）。返回 [(lo,hi), …]。"""
    if not AL_CIF.exists():
        return []
    al_t, al_i = theoretical_positions(AL_CIF, lam, tmin, tmax, 1.0, zero)
    prim_t, prim_i = theoretical_positions(
        primary_cif, lam, tmin, tmax, primary_scale, zero)
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
            exclude, tmin, tmax, use_al=False, hot_mask=True, use_lif=True):
    t, y = load_1d(path, geom, tmin, tmax, hot_mask=hot_mask)
    bg = estimate_baseline_sliding(t, y, 0.2)
    w = 1.0 / np.maximum(y, 1.0)
    w_leb = w.copy()
    for lo, hi in exclude:
        w_leb[(t > lo) & (t < hi)] = 0.0
    w_struct = w if use_al else w_leb
    extras = []
    if use_lif and LIF_CIF.exists():
        extras.append({"structure": load_structure(str(LIF_CIF)),
                       "label": "LiF", "size_um": 0.1, "mustrain": 0.0,
                       "b_iso": 0.5})
    if use_al and AL_CIF.exists():
        al = load_structure(str(AL_CIF))
        extras.append({
            "structure": al, "label": "Al",
            "a": 4.061, "b": 4.061, "c": 4.061,
            "size_um": 0.1, "mustrain": 0.0, "b_iso": 0.5,
            "md": ((1, 1, 1), 0.5),      # 轧制铝箔的 (111) 织构
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
        background_knots=0.25, intensity_poly=2, weights=w_struct,
        extra_phases=extras, phase_label=label,
        free=("a", "b", "c", "size_um", "mustrain", "b_iso"),
        initial=init, max_nfev=300)
    # 强度/展示用 Le Bail（带 Al 相时把 Al scale 也解出来）；结构拟合
    # 只负责晶胞/微结构。
    leb_weights = w if use_al else w_leb
    leb_extras = extras if use_al else []
    lebail = fit_pattern(
        primary, t, y, wavelength=lam, b_iso=0.5, background=bg,
        background_knots=0.25, weights=leb_weights, cell_refine=True,
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
    phase_txt = " + ".join((["Al (111) textured"] if use_al else [])
                           + (["LiF"] if use_lif else []))
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
                    help="包含 .cif 的相库目录（默认 data/structures）")
    ap.add_argument("--outdir", default=str(DEFAULT_OOB))
    ap.add_argument("--tmin", type=float, default=1.0)
    ap.add_argument("--tmax", type=float, default=8.0)
    ap.add_argument("--no-hot-mask", action="store_true",
                    help="关掉坏点团掩膜（默认开；标样本来就不过掩膜）")
    ap.add_argument("--no-lif", action="store_true",
                    help="不加 LiF 杂质相（默认加；scale 自由，没有时归零）")
    a = ap.parse_args()
    hot_mask = not a.no_hot_mask
    use_lif = not a.no_lif
    geom = dict(get_config(a.config)["geometry"])
    lam = geom["wavelength_m"] * 1e10
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    lib = Path(a.lib)
    olivines = [lib / p.name for p in OLIVINE_LIB if (lib / p.name).exists()]
    if not olivines:
        ap.error(f"相库里没有橄榄石 CIF：{lib}")
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
        for cif in olivines:
            sc = score_phase(obs_t, cif, lam, a.tmin, a.tmax, zero)
            if sc:
                ranked.append((sc, cif))
        ranked.sort(key=lambda x: -x[0][0])
        top = ranked[0]
        primary = load_structure(str(top[1]))
        scale = top[0][1]
        cell0 = tuple(v * scale for v in primary.lattice.abc)
        print("  相筛选：")
        for (sc, scale_i, cov, pur), cif in ranked[:3]:
            print(f"    {cif.name:34} score {sc:.3f} "
                  f"scale {scale_i:.4f} coverage {cov:.2f} purity {pur:.2f}")
        primary = load_structure(str(top[1]))
        exclude = al_substrate_ranges(
            obs_t, obs_h, lam, a.tmin, a.tmax, zero, top[1], scale)
        use_al = bool(exclude)
        if use_al:
            print("  自动识别 Al 集流体峰，按 (111) 织构第二相建模："
                  + ", ".join(f"{lo:.2f}–{hi:.2f}°" for lo, hi in exclude))
        else:
            print("  未识别到 Al 集流体强峰")
        struct, lebail, out = fit_one(
            p, primary, top[1].name, cell0, geom, lam, inst, outdir,
            exclude, a.tmin, a.tmax, use_al=use_al, hot_mask=hot_mask,
            use_lif=use_lif)
        al_txt = (f" Al={struct.params['Al:scale']:.3g}"
                  if "Al:scale" in struct.params else "")
        al_txt += (f" LiF={struct.params['LiF:scale']:.3g}"
                   if "LiF:scale" in struct.params else "")
        print(f"  结构：Rwp {struct.rwp:.3f}%  a={struct.params['a']:.4f} "
              f"b={struct.params['b']:.4f} c={struct.params['c']:.4f} "
              f"Size={struct.params['size_um']:.3f} µm "
              f"ε={struct.params['mustrain']/4e6*1e3:.2f}e-3 "
              f"B={struct.params['b_iso']:.2f}{al_txt}")
        print(f"  Le Bail：Rwp {lebail.rwp:.3f}%")
        if struct.params["b_iso"] <= 0.01:
            print("  警告：B 撞到 0，结构强度模型不可信；以 Le Bail 峰强为准")
        print(f"  → {out}")
        with open(out.with_suffix(".txt"), "w", encoding="utf-8") as fh:
            fh.write(f"{p.name}\nprimary: {top[1].name} scale={scale:.4f}\n")
            for (sc, scale_i, cov, pur), cif in ranked:
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
                     + (f" LiF_scale={struct.params['LiF:scale']:.6g}"
                        if "LiF:scale" in struct.params else "")
                     + "\n")
            fh.write(f"Le Bail Rwp={lebail.rwp:.3f}%\n")


if __name__ == "__main__":
    main()
