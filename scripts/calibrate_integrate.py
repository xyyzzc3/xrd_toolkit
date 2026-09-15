#!/usr/bin/env python3
"""LaB6 几何校准 + 方位角积分：2D 衍射图像 → 1D 图谱。

用法示例：
    python scripts/calibrate_integrate.py --file data/xxx.tif
    python scripts/calibrate_integrate.py --file data/xxx.tif --wavelength 0.1223 --dist0 1600
    python scripts/calibrate_integrate.py          # 不带 --file：交互菜单选文件（可多选）

不带 --center 时自动定位环心作初值（find_ring_center，亚像素精度 <1 px），
换任何新数据都不用先手动量圆心。

标定完成后会打印一段可直接粘贴进 config.py CONFIGS 的条目模板
（脚本不直接写配置文件，配置登记需人工复核）。
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

from xrd_toolkit.cli import interactive_pick_files, parse_range_arg  # 交互选文件菜单（四脚本共用）
from xrd_toolkit.core.processor import find_ring_center  # 自动定位环心（校准初值）
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.services.integrator import calibrate_and_integrate, lab6_theoretical_2theta
from xrd_toolkit.services.range_selector import detect_material, select_auto_range


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
    parser.add_argument("--range", dest="range_", default="auto",
                        help="2θ range for the plot and the *_auto trimmed txt: "
                             "full, auto (default: per-material standard range), or "
                             "lo,hi in degrees (e.g. 1.3,7.3). The full txt is always "
                             "saved regardless.")
    parser.add_argument("--material", default="auto",
                        help="material for the auto-range standard: "
                             "auto (detect from filename), lab6, or lmfp — the range "
                             "is then fixed by that material's known peak positions")
    parser.add_argument("--outdir", default="outputs", help="output directory")
    args = parser.parse_args()

    wavelength_m = args.wavelength * 1e-10
    pixel_m = args.pixel * 1e-6
    dist0_m = args.dist0 * 1e-3

    # 未指定 --file 时弹出交互菜单（同 view_diffraction），支持多选
    # （如 1,2）；各文件输出到 outputs/{数据名}/，互不覆盖
    if args.file:
        file_list = [Path(args.file)]
    else:
        file_list = interactive_pick_files(Path(args.datadir))

    for path in file_list:
        image = load_diffraction_image(str(path))
        print(f"\nImage: {path} ({image.shape[0]}x{image.shape[1]} px)")
        print(f"Parameters: λ={args.wavelength} Å, pixel={args.pixel} µm, dist0={args.dist0} mm")

        # 环心初值：指定 --center 时使用输入值，否则自动定位
        # （find_ring_center）。注意自动定位返回 (行, 列)，校准需要
        # (cx, cy)，交换顺序。
        #
        # 说明：初值圆心会轻微影响精修落点——环接近正圆时 rot1/rot2
        # 与 PONI 近似简并，自动定位 (1022.2, 1021.7) 与手动 (1024, 1024)
        # 收敛到两组残差相当的解（PONI 差约 12/36 px），距离则始终稳健
        # （1595.80 mm）。config.py 中的参考标定值统一采用手动初值解。
        if args.center:
            cx, cy = (float(v) for v in args.center.split(","))
            print(f"  Ring center initial (manual): ({cx}, {cy}) px")
        else:
            cy, cx = find_ring_center(image)
            print(f"  Ring center initial (auto): ({cx:.2f}, {cy:.2f}) px")

        # 校准 → 积分
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

        # ══ CONFIGS 条目模板 ════════════════════════════════════════════
        # 精修结果需人工登记进 src/xrd_toolkit/config.py 的 CONFIGS 后，
        # 才能被三个消费脚本通过 --config 使用。脚本不直接写配置文件
        # （避免错误数据进入仓库），仅打印可直接复制的模板：
        #   - key = 材料简写 + 批次编号 + 标样简写（如 lmfp2_lab6）；
        #   - label 只写批次级信息，不含数据集运行号等实验细节（隐私）；
        #   - beam_center 用初值圆心 (行, 列)（自动定位或 --center 输入）
        #     ——它是直射束落点 B，不是 PONI；view_diffraction 用 B 画十字，
        #     探测器有倾斜时 B 与 PONI 差约 23 px；
        #   - rot3_deg / offset_px / residual_deg 为诊断量，不写入注册表。
        print("\n===== CONFIGS entry for config.py (copy-paste ready) =====")
        print(f"# refined residual: {geometry['residual_deg']:.4f} deg (diagnostic, not stored)")
        print('    "lmfp2_lab6": {   # rename key to "<material><n>_<standard>" (e.g. lmfp2_lab6)')
        print('        "label": "（改成实际批次备注）",')
        print('        "geometry": dict(')
        print(f"            pixel_size_m={args.pixel:g}e-6,")
        print(f"            wavelength_m={args.wavelength:g}e-10,")
        print(f"            dist_m={geometry['dist_m']:.5f},")
        print(f"            poni1_m={geometry['poni1_px']:.3f} * {args.pixel:g}e-6,")
        print(f"            poni2_m={geometry['poni2_px']:.3f} * {args.pixel:g}e-6,")
        print(f"            rot1_deg={geometry['rot1_deg']:.4f},")
        print(f"            rot2_deg={geometry['rot2_deg']:.4f},")
        print('        ),')
        print(f'        "beam_center": ({cy:.2f}, {cx:.2f}),   # (row, col) px = direct beam spot')
        print('    },')

        # ---- 2θ 有效区间（默认 auto，同 sector_waterfall/integrate_pattern）----
        # 完整版 txt 始终保存；区间只影响图与另存的 _auto 裁剪版。
        # auto：下界 = 材料专属标准（lmfp 第一峰 −0.3°；lab6 光环结束点
        # −0.6°，约 1.0°）；上界 = 数据失效点自动检测。几何用本次精修
        # 的距离/束心（而非 CONFIGS 参考值），保证区间与积分同一套几何。
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
                pixel_m, geometry["dist_m"],
                poni_px=(geometry["poni1_px"], geometry["poni2_px"]))
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

        # 保存 1D 数据并出图（红虚线 = LaB₆ 理论峰位）。完整版始终保存，
        # 选定区间时另存 *_auto.txt 裁剪版。输出按样品分文件夹
        # （outputs/{数据名}/），calibrated_ 前缀与 integrate_pattern 的
        # integrated_ 输出区分
        outdir = Path(args.outdir)
        stem = path.stem
        sample_dir = outdir / stem
        sample_dir.mkdir(parents=True, exist_ok=True)
        dat_path = sample_dir / "calibrated_2th.txt"
        png_path = sample_dir / "calibrated.png"
        np.savetxt(dat_path, np.c_[tth, intensity], fmt="%.6g", header="2theta(deg)  intensity")
        if lo is not None:
            keep = (tth >= lo) & (tth <= hi)
            tth_plot, intensity_plot = tth[keep], intensity[keep]
            np.savetxt(sample_dir / "calibrated_2th_auto.txt",
                       np.c_[tth_plot, intensity_plot], fmt="%.6g", header="2theta(deg)  intensity")
        else:
            tth_plot, intensity_plot = tth, intensity

        theory = lab6_theoretical_2theta(wavelength_m, args.max_rings)
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(tth_plot, intensity_plot, "b-", lw=0.8)
        for i, t0 in enumerate(theory):
            ax.axvline(t0, color="r", ls="--", lw=0.7, alpha=0.7)
        ax.set_xlabel("2θ (deg)")
        ax.set_ylabel("Intensity (a.u.)")
        ax.set_title(f"{stem}: λ={args.wavelength} Å, D={geometry['dist_m']*1000:.1f} mm")
        if lo is not None:
            ax.set_xlim(lo, hi)               # 选定区间
        else:
            ax.set_xlim(0, theory[-1] * 1.2)  # full：保留原视图（看全所有理论环）
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(png_path, dpi=150)
        plt.close(fig)  # 及时关闭画布，避免多文件处理时内存堆积

        print(f"\n1D data saved : {dat_path}")
        print(f"1D plot saved : {png_path}")


if __name__ == "__main__":
    main()
