#!/usr/bin/env python3
"""全角度积分：把 2D 衍射图像变成标准 1D 粉末衍射谱（两列 txt）。

这就是常规 XRD 粉末衍射的标准格式：横坐标 2θ（度），纵坐标强度。
后续所有分析（寻峰、拟合、PDF 计算）都基于这张 1D 谱。

"全角度积分" = 对 0°~360° 所有方位角上的像素积分（每个 2θ 环一整圈都算），
而不是只取某一条剖面线（那是 view_diffraction.py 做的事）。

几何参数默认用选中配置条目（--config）的标定值（默认 lab6_exp1：同一批
实验、同一个仪器，几何通用，不需要对每个文件重新标定）；多批实验时用
--config 切换（交互模式下选完文件会再弹配置菜单）。

用法示例：
    python scripts/integrate_pattern.py --file data/xxx.tif
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

from xrd_toolkit.cli import interactive_pick_files, parse_range_arg, pick_config  # 交互菜单（选文件 / 选配置）+ 区间解析
from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG, get_config  # 几何配置注册表（--config 点名 / 菜单选择）
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.services.integrator import integrate_1d
from xrd_toolkit.services.range_selector import detect_material, select_auto_range


def main() -> None:
    parser = argparse.ArgumentParser(description="Full azimuthal integration (2D -> 1D powder pattern)")
    parser.add_argument("--file",
                        help="diffraction image path; if not given, an interactive menu "
                             "lists files in data/ and lets you pick (comma-separated for several)")
    parser.add_argument("--datadir", default="data",
                        help="folder scanned by the interactive menu (only used without --file), default data/")
    parser.add_argument("--outdir", default="outputs", help="output directory")
    parser.add_argument("--npt", type=int, default=5000, help="number of points in the 1D curve")
    parser.add_argument("--config",
                        help=f"Geometry config name from config.py (default: {DEFAULT_CONFIG}); "
                             "without --file an interactive menu lets you pick instead")
    parser.add_argument("--dist", type=float, default=None,
                        help="override detector distance in mm (default: from the selected config; lab6_exp1: 1595.80)")
    parser.add_argument("--poni", default=None,
                        help="override PONI as cx,cy in pixel (default: from the selected config; lab6_exp1: 1045.2,1022.0)")
    parser.add_argument("--wavelength", type=float, default=None,
                        help="override wavelength in Angstrom (default: from the selected config; lab6_exp1: 0.1223)")
    parser.add_argument("--range", dest="range_", default="auto",
                        help="2θ range for the plots and the *_auto trimmed txt: "
                             "full, auto (default: per-material standard range), or "
                             "lo,hi in degrees (e.g. 1.3,7.3). The full txt is always "
                             "saved regardless.")
    parser.add_argument("--material", default="auto",
                        help="material for the auto-range standard: "
                             "auto (detect from filename), lab6, or lmfp — the range "
                             "is then fixed by that material's known peak positions")
    args = parser.parse_args()

    # --config 名字先校验：写错立即报错退出（argparse 风格：打印用法 +
    # error 行、退出码 2），不用等到选完文件、读了图才发现
    if args.config is not None:
        config_name = args.config
        try:
            cfg = get_config(config_name)
        except ValueError as err:
            parser.error(str(err))
    else:
        config_name = cfg = None

    # 决定要跑哪些文件：给了 --file 就跑指定的；没给就弹交互菜单（同 view_diffraction），
    # 菜单支持多选（如 1,2）→ 循环里逐个处理，输出各自进 outputs/{数据名}/ 不会互相覆盖
    if args.file:
        file_list = [Path(args.file)]
    else:
        file_list = interactive_pick_files(Path(args.datadir))

    # 几何配置三选一（2026-09-16 用户拍板"交互式"）：
    #   1. --config 点名（命令行 / 脚本用，上面已校验过）
    #   2. 交互模式下（没带 --file）选完数据文件，再弹菜单选一次配置
    #   3. 其余情况用默认条目 DEFAULT_CONFIG（PyCharm 运行配置靠它）
    if cfg is None:
        if args.file is None:
            config_name = pick_config(CONFIGS, DEFAULT_CONFIG)
            cfg = CONFIGS[config_name]
        else:
            config_name = DEFAULT_CONFIG
            cfg = get_config()
    print(f"Using geometry config: {config_name}")

    # 几何参数：默认选中条目的标定值，可用命令行覆盖
    geom = cfg["geometry"]
    dist_m = geom["dist_m"] if args.dist is None else args.dist * 1e-3
    wavelength_m = geom["wavelength_m"] if args.wavelength is None else args.wavelength * 1e-10
    if args.poni is None:
        poni1_m, poni2_m = geom["poni1_m"], geom["poni2_m"]
    else:
        cx, cy = (float(v) for v in args.poni.split(","))
        poni1_m, poni2_m = cx * geom["pixel_size_m"], cy * geom["pixel_size_m"]

    for path in file_list:
        image = load_diffraction_image(str(path))
        print(f"\nImage: {path} ({image.shape[0]}x{image.shape[1]} px)")

        # 全角度方位角积分：azimuth_range=(-180, 180) 表示 0°~360° 一整圈
        tth, intensity = integrate_1d(
            image,
            pixel_size_m=geom["pixel_size_m"],
            wavelength_m=wavelength_m,
            dist_m=dist_m,
            poni1_m=poni1_m,
            poni2_m=poni2_m,
            rot1_deg=geom["rot1_deg"],
            rot2_deg=geom["rot2_deg"],
            npt=args.npt,
        )

        # ---- 2θ 有效区间选择（可选，默认 auto，与 sector_waterfall 同一套逻辑）----
        # txt 永远保存完整版（数据母版）；区间只影响图和另存的 _auto 裁剪版。
        # auto（A+A 方案，2026-09-16 拍板）：下界 = 材料专属标准
        # （lmfp 第一峰 −0.3°；lab6 光环结束点 −0.6°，≈1.0°）；
        # 上界 = 数据失效点自动检测（单曲线：几何算"80% 方位角仍在
        # 探测器内"的精确位置，与瀑布图实测 7.44° 互相印证）。
        # 注意传的是本次积分实际用的 wavelength/dist/poni（用户可能用
        # 命令行覆盖过），保证区间计算和积分用的是同一套几何。
        sel = parse_range_arg(args.range_)
        if sel == "full":
            lo = hi = None
            print("Range: full")
        elif sel == "auto":
            material = args.material if args.material != "auto" \
                else detect_material(path.stem)
            if material is None:
                print("Material: not recognized from filename "
                      "(use --material lab6|lmfp); known-peak check skipped")
            lo, hi, info = select_auto_range(
                tth, intensity, material, wavelength_m, image.shape,
                geom["pixel_size_m"], dist_m,
                poni_px=(poni1_m / geom["pixel_size_m"],
                         poni2_m / geom["pixel_size_m"]))
            print(f"Range: auto -> [{lo:.3f}, {hi:.3f}] deg "
                  f"({info['lo_reason']} / {info['hi_reason']})")
            if material is not None:
                print(f"Material: {material} "
                      f"({info['n_known_peaks']} known peaks within range)")
                for line in info["checks"]:
                    print(f"  {line}")
                for line in info.get("warnings", []):
                    print(f"  WARNING: {line}")
        else:
            lo, hi = sel
            print(f"Range: manual -> [{lo:.3f}, {hi:.3f}] deg")

        # 标准两列 txt：第一列 2θ（度），第二列强度。完整版永远保存，
        # 选了区间时另存一份 *_auto.txt（裁剪版），两个都留
        # 输出按样品分文件夹：outputs/{数据名}/，文件名不带数据名前缀
        outdir = Path(args.outdir)
        stem = path.stem
        sample_dir = outdir / stem
        sample_dir.mkdir(parents=True, exist_ok=True)
        txt_path = sample_dir / "integrated_2th.txt"
        png_path = sample_dir / "integrated.png"
        np.savetxt(txt_path, np.c_[tth, intensity], header="2theta(deg)  intensity")
        if lo is not None:
            keep = (tth >= lo) & (tth <= hi)
            tth_plot, intensity_plot = tth[keep], intensity[keep]
            np.savetxt(sample_dir / "integrated_2th_auto.txt",
                       np.c_[tth_plot, intensity_plot], header="2theta(deg)  intensity")
        else:
            tth_plot, intensity_plot = tth, intensity

        # 出图（对数纵轴 + 线性各存一张，对数能看清弱峰）
        for log, suffix in ((False, ""), (True, "_log")):
            fig, ax = plt.subplots(figsize=(10, 5))
            ax.plot(tth_plot, intensity_plot, "b-", lw=0.8)
            ax.set_xlabel("2θ (deg)")
            ax.set_ylabel("Intensity (a.u.)")
            ax.set_title(f"{stem}: full azimuthal integration (λ={wavelength_m*1e10:.4f} Å)")
            if log:
                ax.set_yscale("log")
                p = sample_dir / "integrated_log.png"
            else:
                p = png_path
            if lo is not None:
                ax.set_xlim(lo, hi)
            ax.grid(alpha=0.3)
            fig.tight_layout()
            fig.savefig(p, dpi=150)
            plt.close(fig)

        print(f"2θ range: {tth.min():.3f} ~ {tth.max():.3f}°, {len(tth)} points")
        print(f"TXT saved: {txt_path}")
        print(f"PNG saved: {png_path} and {sample_dir / 'integrated_log.png'}")


if __name__ == "__main__":
    main()
