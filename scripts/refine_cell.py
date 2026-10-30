#!/usr/bin/env python3
"""从 1D 峰位精修晶胞（全七个晶系，2026-10-31）。

和全谱引擎分工：全谱精修只支持立方/正交晶胞；这里用观测峰位 + CIF 的
hkl 做峰位级最小二乘（CELREF 一类），六方/单斜/三斜也能精修。

用法：
    # 自动找峰（最多 20 根），精修 CIF 的晶胞
    python scripts/refine_cell.py --image data/lab6-00024.tif \
        --cif data/structures/LaB6_NIST660c.cif --config lmfp1_lab6

    # 手动给峰位（度）
    python scripts/refine_cell.py --image data/LMFP_1_atten0-00029.tif \
        --cif data/structures/LiMn0.6Fe0.4PO4_olivine.cif \
        --peaks 1.633,1.785,2.007,2.324,2.680

输出：终端报告 + `cell_refine_<图名>.txt`（默认系统临时目录，不写仓库
outputs/）。报告里的 wt%/强度不参与——这里只回答晶胞与峰位匹配。
"""
import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.config import get_config  # noqa: E402
from xrd_toolkit.services.background import estimate_baseline_sliding  # noqa: E402
from xrd_toolkit.services.data_loader import load_diffraction_image  # noqa: E402
from xrd_toolkit.services.integrator import integrate_1d  # noqa: E402
from xrd_toolkit.services.peak_finding import find_peaks_1d  # noqa: E402
from xrd_toolkit.simulation import load_structure  # noqa: E402
from xrd_toolkit.simulation.cell_refine import refine_cell  # noqa: E402


def load_1d(path, geom, tmin=1.0, tmax=8.0, npt=3000):
    img = load_diffraction_image(str(path))
    t, y = integrate_1d(img, npt=npt, tth_min_deg=tmin, tth_max_deg=tmax,
                        **geom)
    return t, y


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image", required=True, help="含峰位的 2D 图")
    ap.add_argument("--cif", required=True, help="初始晶胞的 CIF")
    ap.add_argument("--config", default="lmfp1_lab6")
    ap.add_argument("--peaks", default="",
                    help="逗号分隔的观测峰位（度）；省略则自动寻峰")
    ap.add_argument("--npeaks", type=int, default=20,
                    help="自动寻峰最多取多少根（默认 20）")
    ap.add_argument("--tol", type=float, default=0.08,
                    help="峰位匹配容差（度，默认 0.08）")
    ap.add_argument("--max-hkl", type=int, default=12)
    ap.add_argument("--no-zero", action="store_true",
                    help="不精修 2θ 零位（默认精修）")
    ap.add_argument("--npt", type=int, default=3000)
    ap.add_argument("--outdir",
                    default=str(Path(tempfile.gettempdir())
                                / "xrd_toolkit_cell"),
                    help="报告输出目录（默认系统临时目录）")
    a = ap.parse_args()
    image = Path(a.image)
    cif = Path(a.cif)
    if not image.exists():
        ap.error(f"图不存在：{image}")
    if not cif.exists():
        ap.error(f"CIF 不存在：{cif}")
    geom = dict(get_config(a.config)["geometry"])
    lam = geom["wavelength_m"] * 1e10
    t, y = load_1d(image, geom, npt=a.npt)
    if a.peaks.strip():
        obs = [float(v) for v in a.peaks.split(",")]
    else:
        bg = estimate_baseline_sliding(t, y, 0.2)
        peaks = find_peaks_1d(t, y, baseline=bg, max_peaks=a.npeaks)
        obs = [float(p.center) for p in peaks]
    structure = load_structure(str(cif))
    print(f"{image.name}：{len(obs)} 根观测峰；CIF {cif.name}；"
          f"λ={lam:.4f} Å")
    try:
        result = refine_cell(
            structure, obs, wavelength=lam, match_tol_deg=a.tol,
            max_hkl=a.max_hkl, refine_zero=not a.no_zero)
    except ValueError as err:
        ap.error(str(err))
    print(result.report())
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"cell_refine_{image.stem}.txt"
    out.write_text(result.report() + "\n", encoding="utf-8")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
