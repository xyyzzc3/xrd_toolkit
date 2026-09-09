#!/usr/bin/env python3
"""全角度积分：把 2D 衍射图像变成标准 1D 粉末衍射谱（两列 txt）。

这就是常规 XRD 粉末衍射的标准格式：横坐标 2θ（度），纵坐标强度。
后续所有分析（寻峰、拟合、PDF 计算）都基于这张 1D 谱。

"全角度积分" = 对 0°~360° 所有方位角上的像素积分（每个 2θ 环一整圈都算），
而不是只取某一条剖面线（那是 view_diffraction.py 做的事）。

几何参数默认用任务三对 LaB₆ 标定好的精确值（同一批实验、同一个仪器，
几何通用，不需要对每个文件重新标定）。

用法示例：
    python scripts/integrate_pattern.py --file data/week2_lab6.tif
    python scripts/integrate_pattern.py --file data/week1_LMFP_1.tif
    python scripts/integrate_pattern.py          # 不带 --file：交互菜单选文件（可多选）
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
from xrd_toolkit.config import CALIBRATED  # 任务三标定几何（全项目唯一一份）
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.services.integrator import integrate_1d


def main() -> None:
    parser = argparse.ArgumentParser(description="Full azimuthal integration (2D -> 1D powder pattern)")
    parser.add_argument("--file",
                        help="diffraction image path; if not given, an interactive menu "
                             "lists files in data/ and lets you pick (comma-separated for several)")
    parser.add_argument("--datadir", default="data",
                        help="folder scanned by the interactive menu (only used without --file), default data/")
    parser.add_argument("--outdir", default="outputs", help="output directory")
    parser.add_argument("--npt", type=int, default=5000, help="number of points in the 1D curve")
    parser.add_argument("--dist", type=float, default=None,
                        help="override detector distance in mm (default: calibrated 1595.79)")
    parser.add_argument("--poni", default=None,
                        help="override PONI as cx,cy in pixel (default: calibrated 1045.17,1022.03)")
    parser.add_argument("--wavelength", type=float, default=None,
                        help="override wavelength in Angstrom (default: 0.1223)")
    args = parser.parse_args()

    # 几何参数：默认任务三标定值，可用命令行覆盖
    dist_m = CALIBRATED["dist_m"] if args.dist is None else args.dist * 1e-3
    wavelength_m = CALIBRATED["wavelength_m"] if args.wavelength is None else args.wavelength * 1e-10
    if args.poni is None:
        poni1_m, poni2_m = CALIBRATED["poni1_m"], CALIBRATED["poni2_m"]
    else:
        cx, cy = (float(v) for v in args.poni.split(","))
        poni1_m, poni2_m = cx * CALIBRATED["pixel_size_m"], cy * CALIBRATED["pixel_size_m"]

    # 决定要跑哪些文件：给了 --file 就跑指定的；没给就弹交互菜单（同 view_diffraction），
    # 菜单支持多选（如 1,2）→ 循环里逐个处理，输出各自进 outputs/{数据名}/ 不会互相覆盖
    if args.file:
        file_list = [Path(args.file)]
    else:
        file_list = interactive_pick_files(Path(args.datadir))

    for path in file_list:
        image = load_diffraction_image(str(path))
        print(f"\n图像: {path} ({image.shape[0]}x{image.shape[1]} px)")

        # 全角度方位角积分：azimuth_range=(-180, 180) 表示 0°~360° 一整圈
        tth, intensity = integrate_1d(
            image,
            pixel_size_m=CALIBRATED["pixel_size_m"],
            wavelength_m=wavelength_m,
            dist_m=dist_m,
            poni1_m=poni1_m,
            poni2_m=poni2_m,
            rot1_deg=CALIBRATED["rot1_deg"],
            rot2_deg=CALIBRATED["rot2_deg"],
            npt=args.npt,
        )

        # 标准两列 txt：第一列 2θ（度），第二列强度
        # 输出按样品分文件夹：outputs/{数据名}/，文件名不带数据名前缀
        outdir = Path(args.outdir)
        stem = path.stem
        sample_dir = outdir / stem
        sample_dir.mkdir(parents=True, exist_ok=True)
        txt_path = sample_dir / "integrated_2th.txt"
        png_path = sample_dir / "integrated.png"
        np.savetxt(txt_path, np.c_[tth, intensity], header="2theta(deg)  intensity")

        # 出图（对数纵轴 + 线性各存一张，对数能看清弱峰）
        for log, suffix in ((False, ""), (True, "_log")):
            fig, ax = plt.subplots(figsize=(10, 5))
            ax.plot(tth, intensity, "b-", lw=0.8)
            ax.set_xlabel("2θ (deg)")
            ax.set_ylabel("Intensity (a.u.)")
            ax.set_title(f"{stem}: full azimuthal integration (λ={wavelength_m*1e10:.4f} Å)")
            if log:
                ax.set_yscale("log")
                p = sample_dir / "integrated_log.png"
            else:
                p = png_path
            ax.grid(alpha=0.3)
            fig.tight_layout()
            fig.savefig(p, dpi=150)
            plt.close(fig)

        print(f"2θ 范围: {tth.min():.3f} ~ {tth.max():.3f}°, {len(tth)} 个点")
        print(f"TXT 已保存: {txt_path}")
        print(f"PNG 已保存: {png_path} 和 {sample_dir / 'integrated_log.png'}")


if __name__ == "__main__":
    main()
