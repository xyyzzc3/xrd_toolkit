#!/usr/bin/env python3
"""LaB6 几何校准 + 方位角积分：2D 衍射图像 → 1D 图谱（任务三流程）。

用法示例：
    python scripts/calibrate_integrate.py --file data/week2_lab6.tif
    python scripts/calibrate_integrate.py --file data/xxx.tif --wavelength 0.1223 --dist0 1600
    python scripts/calibrate_integrate.py          # 不带 --file：交互菜单选文件（可多选）

不带 --center 时自动定位环心作初值（find_ring_center，亚像素精度 <1 px），
换任何新数据都不用先手动量圆心。
"""
import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# 让脚本可以直接从仓库根目录运行（无需先 pip install）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.cli import interactive_pick_files  # 交互选文件菜单（四脚本共用）
from xrd_toolkit.core.processor import find_ring_center  # 自动定位环心（校准初值）
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.services.integrator import calibrate_and_integrate, lab6_theoretical_2theta


def main() -> None:
    parser = argparse.ArgumentParser(description="LaB6 calibration + azimuthal integration")
    parser.add_argument("--file",
                        help="diffraction image path; if not given, an interactive menu "
                             "lists files in data/ and lets you pick (comma-separated for several)")
    parser.add_argument("--datadir", default="data",
                        help="folder scanned by the interactive menu (only used without --file), default data/")
    parser.add_argument("--wavelength", type=float, default=0.1223, help="X-ray wavelength in Angstrom")
    parser.add_argument("--pixel", type=float, default=200.0, help="pixel size in micrometer")
    parser.add_argument("--dist0", type=float, default=1600.0, help="initial detector distance in mm")
    parser.add_argument("--center", default=None,
                        help="initial ring center cx,cy in pixel; if not given, "
                             "auto-localized with find_ring_center (<1 px accuracy)")
    parser.add_argument("--max-rings", type=int, default=16, help="number of rings used for calibration")
    parser.add_argument("--outdir", default="outputs", help="output directory")
    args = parser.parse_args()

    wavelength_m = args.wavelength * 1e-10
    pixel_m = args.pixel * 1e-6
    dist0_m = args.dist0 * 1e-3

    # 决定要跑哪些文件：给了 --file 就跑指定的；没给就弹交互菜单（同 view_diffraction），
    # 菜单支持多选（如 1,2）→ 循环里逐个处理，输出各自进 outputs/{数据名}/ 不会互相覆盖
    if args.file:
        file_list = [Path(args.file)]
    else:
        file_list = interactive_pick_files(Path(args.datadir))

    for path in file_list:
        image = load_diffraction_image(str(path))
        print(f"\n图像: {path} ({image.shape[0]}x{image.shape[1]} px)")
        print(f"参数: λ={args.wavelength} Å, pixel={args.pixel} µm, dist0={args.dist0} mm")

        # 环心初值：给了 --center 就用输入的；没给就自动定位（和 view_diffraction
        # 同款 find_ring_center）。注意它返回 (行, 列)，而校准要 (cx, cy)，交换顺序。
        #
        # 纠错点记录（实测）：初值圆心会轻微影响精修落点——环接近正圆时
        # rot1/rot2 与 PONI 存在近似简并，自动定位 (1022.2, 1021.7) 与手动
        # (1024, 1024) 会收敛到两组残差相当的解（PONI 差 ~12/38 px），但
        # 距离始终稳健（1595.79 mm）。官方标定值（config.CALIBRATED）沿用
        # --center 1024,1024 三次平均的结果，自动定位仅作便捷初值。
        if args.center:
            cx, cy = (float(v) for v in args.center.split(","))
            print(f"  环心初值（手动指定）: ({cx}, {cy}) px")
        else:
            cy, cx = find_ring_center(image)
            print(f"  环心初值（自动定位）: ({cx:.2f}, {cy:.2f}) px")

        # 一条龙：校准 → 积分
        tth, intensity, geometry = calibrate_and_integrate(
            image,
            pixel_size_m=pixel_m,
            wavelength_m=wavelength_m,
            dist0_m=dist0_m,
            center0_px=(cx, cy),
            max_rings=args.max_rings,
        )

        print("\n===== Calibration result =====")
        print(f"Detector distance : {geometry['dist_m']*1000:.2f} mm  (initial {args.dist0} mm)")
        print(f"PONI (center)     : ({geometry['poni1_px']:.2f}, {geometry['poni2_px']:.2f}) px")
        print(f"Center offset     : ({geometry['offset_px'][0]:+.2f}, {geometry['offset_px'][1]:+.2f}) px")
        print(f"Tilt              : rot1={geometry['rot1_deg']:.4f} deg, rot2={geometry['rot2_deg']:.4f} deg")
        print(f"Residual (RMS)    : {geometry['residual_deg']:.4f} deg")

        # 保存 1D 数据 + 出图（红虚线 = LaB6 理论峰位）
        # 输出按样品分文件夹：outputs/{数据名}/；用 calibrated_ 前缀与
        # integrate_pattern.py 的 integrated_ 输出区分（避免互相覆盖）
        outdir = Path(args.outdir)
        stem = path.stem
        sample_dir = outdir / stem
        sample_dir.mkdir(parents=True, exist_ok=True)
        dat_path = sample_dir / "calibrated_2th.txt"
        png_path = sample_dir / "calibrated.png"
        np.savetxt(dat_path, np.c_[tth, intensity], header="2theta(deg)  intensity")

        theory = lab6_theoretical_2theta(wavelength_m, args.max_rings)
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(tth, intensity, "b-", lw=0.8)
        for i, t0 in enumerate(theory):
            ax.axvline(t0, color="r", ls="--", lw=0.7, alpha=0.7)
        ax.set_xlabel("2θ (deg)")
        ax.set_ylabel("Intensity (a.u.)")
        ax.set_title(f"{stem}: λ={args.wavelength} Å, D={geometry['dist_m']*1000:.1f} mm")
        ax.set_xlim(0, theory[-1] * 1.2)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(png_path, dpi=150)
        plt.close(fig)  # 处理多个文件时及时关图，防止内存里堆一堆画布

        print(f"\n1D data saved : {dat_path}")
        print(f"1D plot saved : {png_path}")


if __name__ == "__main__":
    main()
