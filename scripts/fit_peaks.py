#!/usr/bin/env python3
"""单峰峰形拟合 + Williamson–Hall 演示（2026-10-23）。

用法：
    # LaB6 标样：对几根孤立峰分别拟合四种线型
    python scripts/fit_peaks.py --image data/lab6-00024.tif \
        --peaks 1.685,2.384,2.920,3.769 --shape split_pearson7

    # 样品：坏点掩膜 + 用 LaB6 标样扣仪器宽度，做 W-H
    python scripts/fit_peaks.py --image data/LMFP_1_atten0-00029.tif \
        --config lmfp1_lab6 --hot-mask \
        --peaks 1.633,1.785,2.007,2.324,2.680,2.772 \
        --wh --instrument-from data/lab6-00024.tif

线型：gaussian / lorentzian / pseudovoigt / split_pearson7。
输出：终端表格；`--wh` 时另存 W-H 图到 `--outdir`（默认系统临时目录，
不写仓库 outputs/）。

注意：W-H 要求各峰**彼此孤立、窗口里没有邻峰**；重叠严重的谱（如 LMFP
低角）单峰 FWHM 会被背景/邻峰带偏，得到的尺寸/应变只作趋势参考，
定量请用全谱拟合的 Size/Mustrain。W-H 模块本身在合成谱上能精确反解
（见 tests/test_microstructure.py）。
"""
import argparse
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
from xrd_toolkit.simulation import (  # noqa: E402
    core, fit_pattern, fit_single_peak, load_structure, plot_williamson_hall,
    reflection_sig_gam, sig_gam_to_fwhm_eta, williamson_hall)

LAB6_CIF = ROOT / "data" / "structures" / "LaB6_NIST660c.cif"


def load_1d(path, geom, hot_mask=False):
    img = load_diffraction_image(str(path))
    if hot_mask:
        img, _mask, info = mask_hot_pixels(img)
        print(f"坏点掩膜：{info['masked_components']} 团 / "
              f"{info['masked_pixels']} px")
    t, y = integrate_1d(img, npt=3000, tth_min_deg=1.0, tth_max_deg=8.0,
                        **geom)
    bg = estimate_baseline_sliding(t, y, 0.2)
    return t, y, bg


def instrument_profile(path, geom, lam):
    """LaB6 标样：Le Bail 定 zero/U/V/W/X/Y，返回 fit 结果。"""
    st = load_structure(str(LAB6_CIF))
    b6 = core.b_iso_from_cif(str(LAB6_CIF))
    t, y, bg = load_1d(path, geom, hot_mask=False)
    return fit_pattern(st, t, y, wavelength=lam, b_iso=b6, background=bg,
                       background_knots=0.25, weights="poisson",
                       refine_intensities=True,
                       free=("zero", "u", "v", "w", "x", "y"), max_nfev=300)


def detect_peaks(t, y, bg, n=12):
    from scipy.signal import find_peaks
    ys = y - bg
    med = float(np.median(ys))
    sigma = 1.4826 * float(np.median(np.abs(ys - med)))
    prom = max(5.0 * sigma, 0.02 * float(np.max(ys)))
    dist = max(2, int(round(0.008 / max(float(np.median(np.diff(t))), 1e-9))))
    idx, _ = find_peaks(ys, prominence=prom, distance=dist)
    idx = idx[np.argsort(ys[idx])[::-1]][:n]
    return np.sort(t[idx])


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image", default=str(ROOT / "data" / "lab6-00024.tif"))
    ap.add_argument("--config", default="lmfp1_lab6")
    ap.add_argument("--shape", default="pseudovoigt",
                    choices=("gaussian", "lorentzian", "pseudovoigt",
                             "split_pearson7"))
    ap.add_argument("--peaks", default="",
                    help="逗号分隔的峰位（度）；省略则自动找最强的 12 根")
    ap.add_argument("--half-window", type=float, default=0.08)
    ap.add_argument("--hot-mask", action="store_true",
                    help="积分前掩固定坏点团（样品数据建议开）")
    ap.add_argument("--wh", action="store_true", help="做 Williamson–Hall")
    ap.add_argument("--instrument-from", default=None,
                    help="LaB6 标样图，用于扣仪器宽度（W-H 用）")
    ap.add_argument("--outdir",
                    default=str(Path(tempfile.gettempdir())
                                / "xrd_toolkit_peaks"),
                    help="W-H 图输出目录（默认系统临时目录；脚本不写仓库 outputs/）")
    a = ap.parse_args()
    geom = dict(get_config(a.config)["geometry"])
    lam = geom["wavelength_m"] * 1e10
    image = Path(a.image)
    if not image.exists():
        ap.error(f"图不存在：{image}")
    t, y, bg = load_1d(image, geom, hot_mask=a.hot_mask)
    if a.peaks.strip():
        centers = [float(x) for x in a.peaks.split(",")]
    else:
        centers = detect_peaks(t, y, bg)
    print(f"{image.name}：{len(centers)} 根峰，线型 {a.shape}，"
          f"λ={lam:.4f} Å")
    print(f"{'2θ':>8} {'FWHM':>7} {'left':>7} {'right':>7} {'m':>5} "
          f"{'η':>5} {'area':>9} {'Rwp%':>6}")
    results = []
    for c in centers:
        try:
            fit = fit_single_peak(t, y, c, shape=a.shape,
                                  half_window=a.half_window)
        except Exception as err:                # noqa: BLE001
            print(f"{c:8.4f}  拟合失败：{err}")
            continue
        results.append(fit)
        print(f"{fit.center:8.4f} {fit.fwhm:7.4f} {fit.fwhm_left:7.4f} "
              f"{fit.fwhm_right:7.4f} {fit.m:5.2f} {fit.eta:5.2f} "
              f"{fit.area:9.1f} {fit.rwp:6.2f}")
    if not a.wh:
        return
    if len(results) < 3:
        ap.error("W-H 至少需要 3 根成功拟合的峰")
    inst_deg = None
    if a.instrument_from:
        inst = instrument_profile(Path(a.instrument_from), geom, lam)
        print(f"\n仪器（LaB6，Le Bail Rwp {inst.rwp:.2f}%）："
              f"U={inst.params['u']:.2f} V={inst.params['v']:.2f} "
              f"W={inst.params['w']:.3f} X={inst.params['x']:.3f} "
              f"Y={inst.params['y']:.3f}")
        sig, gam = reflection_sig_gam(
            np.array([f.center for f in results]), lam,
            u=inst.params["u"], v=inst.params["v"], w=inst.params["w"],
            x=inst.params["x"], y=inst.params["y"],
            size_um=1e4, mustrain=0.0)
        inst_deg = sig_gam_to_fwhm_eta(sig, gam)[0]
    wh = williamson_hall([f.center for f in results],
                         [f.fwhm for f in results],
                         wavelength=lam, K=0.9,
                         instrument_fwhm_deg=inst_deg)
    print(f"\nW-H：D = {wh.size_nm:.2f} nm，ε = {wh.strain:.3e}"
          + (f"（{wh.warning}）" if wh.warning else ""))
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / "williamson_hall.png"
    plot_williamson_hall(wh, str(out),
                         title=f"{image.stem} — Williamson–Hall")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
