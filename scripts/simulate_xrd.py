#!/usr/bin/env python3
"""XRD 模拟：从晶体结构文件（CIF）计算理论粉末衍射图谱。

默认用手写版实现（布拉格定律 + 结构因子，xrd_toolkit.simulation.core），
数值与 pymatgen 的 XRDCalculator 逐峰一致（--compare 可现场复核）。

用法示例：
    python scripts/simulate_xrd.py data/structures/Si_9008565.cif outputs/sim_xrd_Si.png
    python scripts/simulate_xrd.py ... --annotate             # 峰位标注 (hkl)
    python scripts/simulate_xrd.py ... --wavelength 0.7093    # 换 Mo Kα
    python scripts/simulate_xrd.py ... --compare              # 两版对答案（不画图）
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.simulation import core, plots


def _deviation(t1, i1, t2, i2):
    """手写版相对 pymatgen 版的最大偏差（2θ 与强度，按最近峰配对）。"""
    dmax = imax = 0.0
    for tt, ii in zip(t1, i1):
        j = int(np.argmin(np.abs(t2 - tt)))
        dmax = max(dmax, abs(float(t2[j]) - float(tt)))
        imax = max(imax, abs(float(i2[j]) - float(ii)))
    return dmax, imax


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Simulated powder XRD from a CIF file (Bragg + structure factor).")
    ap.add_argument("cif", help="输入 CIF 路径")
    ap.add_argument("out", help="输出 PNG 路径")
    ap.add_argument("--wavelength", type=float, default=1.5406,
                    help="X 射线波长（Å），默认 Cu Kα1 = 1.5406")
    ap.add_argument("--two-theta", nargs=2, type=float, default=(10.0, 80.0),
                    metavar=("MIN", "MAX"))
    ap.add_argument("--annotate", action="store_true",
                    help="在峰顶标注 (hkl)（六方用四位指数）")
    ap.add_argument("--pymatgen", action="store_true",
                    help="改用 pymatgen XRDCalculator 参考实现")
    ap.add_argument("--compare", action="store_true",
                    help="打印手写版与 pymatgen 版的逐峰偏差（不画图）")
    args = ap.parse_args()

    structure = core.load_structure(args.cif)
    print(f"material  : {structure.composition.reduced_formula}")
    print(f"space grp : {structure.get_space_group_info()[0]}")
    print(f"wavelength: {args.wavelength} A | 2theta: {args.two_theta[0]}-{args.two_theta[1]} deg")

    if args.compare:
        t1, i1, _, _ = core.pattern_from_structure(
            structure, args.wavelength, tuple(args.two_theta))
        t2, i2, _ = core.pattern_from_structure_pymatgen(
            structure, args.wavelength, tuple(args.two_theta))
        dmax, imax = _deviation(t1, i1, t2, i2)
        print(f"manual {len(t1)} peaks | pymatgen {len(t2)} peaks | "
              f"max d2theta = {dmax:.4f} deg | max dI = {imax:.2f}")
        return

    if args.pymatgen:
        two_thetas, intensities, _ = core.pattern_from_structure_pymatgen(
            structure, args.wavelength, tuple(args.two_theta))
        print(f"peaks     : {len(two_thetas)}   (pymatgen XRDCalculator)")
        title = (f"{structure.composition.reduced_formula} — simulated XRD "
                 f"(λ = {args.wavelength:.4f} Å)")
        plots.plot_pattern(two_thetas, intensities, title, args.out)
        print(f"saved: {args.out}")
        return

    two_thetas, intensities, peaks, is_hex = core.pattern_from_structure(
        structure, args.wavelength, tuple(args.two_theta))
    print(f"peaks     : {len(two_thetas)}   (hand-written: Bragg + structure factor)")
    print("-" * 62)
    print(f"{'2theta(deg)':>11} {'I(%)':>6}   hkl x multiplicity")
    for tt, inten, refs in peaks:
        print(f"{tt:11.3f} {inten:6.1f}   {core.format_hkls(refs, is_hex)}")

    title = (f"{structure.composition.reduced_formula} — simulated XRD "
             f"(λ = {args.wavelength:.4f} Å)")
    if args.annotate:
        plots.plot_annotated(two_thetas, intensities, peaks, is_hex, title, args.out)
    else:
        plots.plot_pattern(two_thetas, intensities, title, args.out)
    print(f"saved: {args.out}")


if __name__ == "__main__":
    main()
