#!/usr/bin/env python3
"""LMFP 实验数据处理（2026-10-22）：标样定仪器 → 样品全谱拟合。

数据：`data/LMFP_1_atten0-00029.tif` / `-00031.tif`（LMFP 第 1 批，同一几何
`lmfp1_lab6`，λ = 0.1223 Å）。两张几乎重复，分别拟合并对照。

流程：
  1. 用 LaB₆ 标样（`data/lab6-00024.tif`）拟合仪器峰形
     zero/U/V/W/X/Y，之后固定给样品；
  2. 样品 1D 积分（先掩固定坏点团）+ 自动基线；**排除 2.90–3.08° 的 Al (111) 基片峰**
     （LMFP 电极的铝集流体，d≈2.34 Å；不排除会把强度模型带偏）；
  3. 结构拟合：晶胞 a/b/c + Size/应变 + 全局 B + 0.25° 背景修正，
     用来定晶胞；
  4. Le Bail 拟合：固定上一步的晶胞，逐峰强度自由，给出峰形/背景的
     质量上限（结构强度模型受织构/占位影响时看这个）；
  5. 存图 `sim_xrd_fit_LMFP_<帧号>.png`（Al 区画灰带）。

CIF 默认 `LiMn0.5Fe0.5PO4_olivine.cif`（由 LiFePO4 结构把 Fe 位换成
Fe0.5/Mn0.5 得到，作为起始模型；样品实际 Mn/Fe 比未知）。
"""
import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.config import get_config  # noqa: E402
from xrd_toolkit.services.background import estimate_baseline_sliding  # noqa: E402
from xrd_toolkit.services.data_loader import load_diffraction_image  # noqa: E402
from xrd_toolkit.services.hot_pixels import mask_hot_pixels  # noqa: E402
from xrd_toolkit.services.integrator import integrate_1d  # noqa: E402
from xrd_toolkit.simulation import (  # noqa: E402
    b_iso_from_cif, fit_pattern, load_structure, plot_fit)

DEFAULT_CIF = ROOT / "data" / "structures" / "LiMn0.5Fe0.5PO4_olivine.cif"
DEFAULT_IMAGES = [
    ROOT / "data" / "LMFP_1_atten0-00029.tif",
    ROOT / "data" / "LMFP_1_atten0-00031.tif",
]
LAB6_TIF = ROOT / "data" / "lab6-00024.tif"
# 实验拟合用 NIST SRM 660c 室温晶胞（a=4.156826 Å）：几何就是按同一套
# 参考标定的；COD 的 4.157597 Å 偏大，会让模型峰位系统性偏低。
LAB6_CIF = ROOT / "data" / "structures" / "LaB6_NIST660c.cif"

# 样品晶胞初值：由低角峰 (200)/(101)/(111)/(020) 粗估（Å）
SAMPLE_CELL = (10.376, 6.040, 4.708)
AL_CIF = ROOT / "data" / "structures" / "Al_metal.cif"


def load_1d(path, geom, tmin=1.0, tmax=8.0, npt=3000, hot_mask=False):
    img = load_diffraction_image(str(path))
    if hot_mask:
        img, _mask, info = mask_hot_pixels(img)
        print(f"  坏点掩膜：{info['masked_components']} 团 / "
              f"{info['masked_pixels']} px（候选 {info['candidates']}）")
    return integrate_1d(img, npt=npt, tth_min_deg=tmin, tth_max_deg=tmax,
                        **geom)


def instrument_profile(geom, lam):
    """用 LaB₆ 标样拟合 zero/U/V/W/X/Y（样品拟合时固定）。

    峰强用 **Le Bail** 逐峰自由：标样的结构强度模型（B 因子/占位）本来就
    有误差，若一起精修，会把强度残差挤进峰形参数里。逐峰强度放开后，
    U/V/W/X/Y 只由峰形决定（2026-10-23 实测 Rwp 6.1 → 4.5）。
    """
    st = load_structure(str(LAB6_CIF))
    b6 = b_iso_from_cif(str(LAB6_CIF))
    t, y = load_1d(LAB6_TIF, geom)
    bg = estimate_baseline_sliding(t, y, 0.2)
    return fit_pattern(
        st, t, y, wavelength=lam, b_iso=b6, background=bg,
        background_knots=0.25, weights="poisson", refine_intensities=True,
        free=("zero", "u", "v", "w", "x", "y"), max_nfev=200)


def fit_one(path, structure, geom, lam, inst, outdir, exclude, cif_cell,
            use_al=False, phase_label=None, hot_mask=True):
    t, y = load_1d(path, geom, hot_mask=hot_mask)
    bg = estimate_baseline_sliding(t, y, 0.2)
    w = 1.0 / np.maximum(y, 1.0)
    w_leb = w.copy()
    for lo, hi in exclude:
        w_leb[(t > lo) & (t < hi)] = 0.0
    w_struct = w.copy()
    if not use_al:
        w_struct = w_leb

    extras = []
    if use_al:
        al = load_structure(str(AL_CIF))
        extras.append({
            "structure": al, "label": "Al",
            "a": 4.061, "b": 4.061, "c": 4.061,
            "size_um": 0.1, "mustrain": 0.0, "b_iso": 0.5,
            # 铝箔是轧制的，(111) 强取向；R 由 (200)/(220)/(311)
            # 弱反射定出（0.72；早期用 0.5 会把这三根压得过低，
            # 误加了 LiF 相——LiF 与 Al 同为岩盐、晶胞几乎相同，
            # 2θ 上完全退化）。
            "md": ((1, 1, 1), 0.72),
        })
    init = {
        "zero": inst.params["zero"],
        "u": inst.params["u"], "v": inst.params["v"], "w": inst.params["w"],
        "x": inst.params["x"], "y": inst.params["y"],
        "a": cif_cell[0], "b": cif_cell[1], "c": cif_cell[2],
        "size_um": 0.2, "mustrain": 1000.0, "b_iso": 0.5,
    }
    struct = fit_pattern(
        structure, t, y, wavelength=lam, b_iso=0.5, background=bg,
        background_knots=0.25, intensity_poly=2, weights=w_struct,
        extra_phases=extras, phase_label=phase_label,
        free=("a", "b", "c", "size_um", "mustrain", "b_iso"),
        initial=init, max_nfev=300)
    # 展示/强度口径：带 Al 相时用 Le Bail（逐峰强度 + Al scale）——
    # 弱反射的相对强度受织构/晶粒统计影响，结构模型压不住；结构拟合
    # 只负责晶胞/微结构。不带 Al 时仍用排除 Al 区的 Le Bail。
    leb_weights = w if use_al else w_leb
    leb_extras = extras
    lebail = fit_pattern(
        structure, t, y, wavelength=lam, b_iso=0.5, background=bg,
        background_knots=0.25, weights=leb_weights, cell_refine=True,
        refine_intensities=True, phase_label=phase_label,
        extra_phases=leb_extras,
        free=("size_um", "mustrain"),
        initial={**init, "a": struct.params["a"],
                 "b": struct.params["b"], "c": struct.params["c"],
                 "size_um": struct.params["size_um"],
                 "mustrain": struct.params["mustrain"]}, max_nfev=200)
    tag = path.stem.rsplit("-", 1)[-1]
    out = outdir / f"sim_xrd_fit_LMFP_{tag}.png"
    plot_src = lebail
    phases = ["Al (111) textured"] if use_al else []
    phase_txt = f" ({' + '.join(phases)})" if phases else ""
    plot_fit(
        plot_src,
        f"{path.name} — Le Bail fit{phase_txt}"
        f", 2θ = 1–8°, λ = {lam:.4f} Å",
        str(out), zoom=(2.6, 3.2),
        exclude_ranges=[] if use_al else list(exclude))
    return struct, lebail, out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cif", default=str(DEFAULT_CIF))
    ap.add_argument("--images", nargs="*", default=[str(p) for p in DEFAULT_IMAGES])
    ap.add_argument("--outdir", default=str(ROOT / "outputs" / "simulation"),
                    help="输出目录（默认 outputs/simulation，不进仓库）")
    ap.add_argument("--exclude", default="2.90,3.08",
                    help="排除区间 lo,hi（度）；Le Bail 始终排除 Al 区")
    ap.add_argument("--exclude-al", action="store_true",
                    help="不用 Al 第二相，直接把 Al 区从结构拟合里排除")
    ap.add_argument("--tmin", type=float, default=1.0)
    ap.add_argument("--tmax", type=float, default=8.0)
    ap.add_argument("--no-hot-mask", action="store_true",
                    help="关掉坏点团掩膜（默认开；标样本来就不过掩膜）")
    a = ap.parse_args()
    geom = dict(get_config("lmfp1_lab6")["geometry"])
    lam = geom["wavelength_m"] * 1e10
    cif = Path(a.cif)
    if not cif.exists():
        ap.error(f"CIF 不存在：{cif}")
    structure = load_structure(str(cif))
    exclude = []
    if a.exclude.strip():
        lo, hi = (float(x) for x in a.exclude.split(","))
        exclude = [(lo, hi)]
    use_al = not a.exclude_al
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    inst = instrument_profile(geom, lam)
    print(f"仪器（LaB₆ 标样）：zero={inst.params['zero']:+.5f}°  "
          f"U={inst.params['u']:.3f} V={inst.params['v']:.3f} "
          f"W={inst.params['w']:.3f} X={inst.params['x']:.3f} "
          f"Y={inst.params['y']:.3f}  (Rwp {inst.rwp:.3f}%)")
    print(f"CIF: {cif.name}；"
          + (f"Al 排除区 {exclude[0][0]:.2f}–{exclude[0][1]:.2f}°"
             if not use_al and exclude else "Al (111) 织构第二相"))
    print(f"{'image':>28} {'Rwp结构':>8} {'RwpLeBail':>10} "
          f"{'a':>7} {'b':>7} {'c':>7} {'size/µm':>8} {'ε/1e-3':>7} "
          f"{'B':>6} {'Al(LeB)':>8}")

    for image in a.images:
        p = Path(image)
        if not p.exists():
            print(f"  跳过（不存在）：{p}")
            continue
        struct, lebail, out = fit_one(
            p, structure, geom, lam, inst, outdir, exclude, SAMPLE_CELL,
            use_al=use_al, phase_label=cif.stem.split("_")[0],
            hot_mask=not a.no_hot_mask)
        al_scale = (lebail.params.get("Al:scale") if use_al
                    and lebail is not None else struct.params.get("Al:scale"))
        leb_txt = (f"{lebail.rwp:10.3f}" if lebail is not None
                   else f"{chr(8212):>10}")
        print(f"{p.name:>28} {struct.rwp:8.3f} {leb_txt} "
              f"{struct.params['a']:7.3f} {struct.params['b']:7.3f} "
              f"{struct.params['c']:7.3f} {struct.params['size_um']:8.3f} "
              f"{struct.params['mustrain'] / 4e6 * 1e3:7.3f} "
              f"{struct.params['b_iso']:6.2f} "
              f"{(f'{al_scale:8.3g}' if al_scale is not None else f'{chr(8212):>8}')}")
        print(f"  结构参数：Size={struct.params['size_um']:.3f} µm，"
              f"应变={struct.params['mustrain'] / 4e6 * 1e3:.3f}e-3，"
              f"ΔB={struct.params['b_iso']:+.2f} Å²"
              + (f"，Al scale（Le Bail）={al_scale:.3g}"
                 if al_scale is not None else "")
              + f"；→ {out}")


if __name__ == "__main__":
    main()
