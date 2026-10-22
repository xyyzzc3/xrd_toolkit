#!/usr/bin/env python3
"""LMFP 实验数据处理（2026-10-22）：标样定仪器 → 样品全谱拟合。

数据：`data/LMFP_1_atten0-00029.tif` / `-00031.tif`（LMFP 第 1 批，同一几何
`lmfp1_lab6`，λ = 0.1223 Å）。两张几乎重复，分别拟合并对照。

流程：
  1. 用 LaB₆ 标样（`data/lab6-00024.tif`）拟合仪器峰形
     zero/U/V/W/X/Y，之后固定给样品；
  2. 样品 1D 积分 + 自动基线；**排除 2.90–3.08° 的 Al (111) 基片峰**
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
from xrd_toolkit.services.integrator import integrate_1d  # noqa: E402
from xrd_toolkit.simulation import (  # noqa: E402
    b_iso_from_cif, fit_pattern, load_structure, plot_fit)

DEFAULT_CIF = ROOT / "data" / "structures" / "LiMn0.5Fe0.5PO4_olivine.cif"
DEFAULT_IMAGES = [
    ROOT / "data" / "LMFP_1_atten0-00029.tif",
    ROOT / "data" / "LMFP_1_atten0-00031.tif",
]
LAB6_TIF = ROOT / "data" / "lab6-00024.tif"
LAB6_CIF = ROOT / "data" / "structures" / "LaB6_1000055.cif"

# 样品晶胞初值：由低角峰 (200)/(101)/(111)/(020) 粗估（Å）
SAMPLE_CELL = (10.376, 6.040, 4.708)
AL_EXCLUDE = (2.90, 3.08)      # Al (111) 集流体，2θ≈2.99°


def load_1d(path, geom, tmin=1.0, tmax=8.0, npt=3000):
    img = load_diffraction_image(str(path))
    return integrate_1d(img, npt=npt, tth_min_deg=tmin, tth_max_deg=tmax,
                        **geom)


def instrument_profile(geom, lam):
    """用 LaB₆ 标样拟合 zero/U/V/W/X/Y（样品拟合时固定）。"""
    st = load_structure(str(LAB6_CIF))
    b6 = b_iso_from_cif(str(LAB6_CIF))
    t, y = load_1d(LAB6_TIF, geom)
    bg = estimate_baseline_sliding(t, y, 0.2)
    return fit_pattern(
        st, t, y, wavelength=lam, b_iso=b6, background=bg,
        background_knots=0.25, weights="poisson",
        free=("zero", "u", "v", "w", "x", "y"))


def fit_one(path, structure, geom, lam, inst, outdir, exclude, cif_cell):
    t, y = load_1d(path, geom)
    bg = estimate_baseline_sliding(t, y, 0.2)
    w = 1.0 / np.maximum(y, 1.0)
    for lo, hi in exclude:
        w[(t > lo) & (t < hi)] = 0.0

    init = {
        "zero": inst.params["zero"],
        "u": inst.params["u"], "v": inst.params["v"], "w": inst.params["w"],
        "x": inst.params["x"], "y": inst.params["y"],
        "a": cif_cell[0], "b": cif_cell[1], "c": cif_cell[2],
        "size_um": 0.2, "mustrain": 1000.0, "b_iso": 0.5,
    }
    struct = fit_pattern(
        structure, t, y, wavelength=lam, b_iso=0.5, background=bg,
        background_knots=0.25, intensity_poly=2, weights=w,
        free=("a", "b", "c", "size_um", "mustrain", "b_iso"),
        initial=init)
    # 固定结构拟合得到的晶胞，逐峰强度自由（Le Bail）
    lebail = fit_pattern(
        structure, t, y, wavelength=lam, b_iso=0.5, background=bg,
        background_knots=0.25, weights=w, cell_refine=True,
        refine_intensities=True,
        free=("size_um", "mustrain"),
        initial={**init, "a": struct.params["a"], "b": struct.params["b"],
                 "c": struct.params["c"],
                 "size_um": struct.params["size_um"],
                 "mustrain": struct.params["mustrain"]})
    tag = path.stem.rsplit("-", 1)[-1]
    out = outdir / f"sim_xrd_fit_LMFP_{tag}.png"
    plot_fit(
        lebail,
        f"LMFP {path.name} — Le Bail profile "
        f"(structural Rwp {struct.rwp:.2f}%)",
        str(out), zoom=(2.6, 3.2), exclude_ranges=list(exclude))
    return struct, lebail, out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cif", default=str(DEFAULT_CIF))
    ap.add_argument("--images", nargs="*", default=[str(p) for p in DEFAULT_IMAGES])
    ap.add_argument("--outdir", default=str(ROOT / "showcase" / "simulation"))
    ap.add_argument("--exclude", default="2.90,3.08",
                    help="排除区间 lo,hi（度）；默认 Al (111) 集流体")
    ap.add_argument("--tmin", type=float, default=1.0)
    ap.add_argument("--tmax", type=float, default=8.0)
    a = ap.parse_args()
    geom = dict(get_config("lmfp1_lab6")["geometry"])
    lam = geom["wavelength_m"] * 1e10
    cif = Path(a.cif)
    if not cif.exists():
        ap.error(f"CIF 不存在：{cif}")
    structure = load_structure(str(cif))
    lo, hi = (float(x) for x in a.exclude.split(","))
    exclude = [(lo, hi)]
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    inst = instrument_profile(geom, lam)
    print(f"仪器（LaB₆ 标样）：zero={inst.params['zero']:+.5f}°  "
          f"U={inst.params['u']:.3f} V={inst.params['v']:.3f} "
          f"W={inst.params['w']:.3f} X={inst.params['x']:.3f} "
          f"Y={inst.params['y']:.3f}  (Rwp {inst.rwp:.3f}%)")
    print(f"CIF: {cif.name}；Al (111) 排除区 {lo:.2f}–{hi:.2f}°")
    print(f"{'image':>28} {'Rwp结构':>8} {'RwpLeBail':>10} "
          f"{'a':>7} {'b':>7} {'c':>7} {'size/µm':>8} {'ε/1e-3':>7} {'B':>6}")

    for image in a.images:
        p = Path(image)
        if not p.exists():
            print(f"  跳过（不存在）：{p}")
            continue
        struct, lebail, out = fit_one(
            p, structure, geom, lam, inst, outdir, exclude, SAMPLE_CELL)
        print(f"{p.name:>28} {struct.rwp:8.3f} {lebail.rwp:10.3f} "
              f"{struct.params['a']:7.3f} {struct.params['b']:7.3f} "
              f"{struct.params['c']:7.3f} {struct.params['size_um']:8.3f} "
              f"{struct.params['mustrain'] / 4e6 * 1e3:7.3f} "
              f"{struct.params['b_iso']:6.2f}")
        print(f"  结构参数：Size={struct.params['size_um']:.3f} µm，"
              f"应变={struct.params['mustrain'] / 4e6 * 1e3:.3f}e-3，"
              f"ΔB={struct.params['b_iso']:+.2f} Å²；→ {out}")


if __name__ == "__main__":
    main()
