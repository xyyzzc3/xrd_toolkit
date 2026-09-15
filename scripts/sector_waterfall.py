#!/usr/bin/env python3
"""扇形积分 + 瀑布图：把 0°~360° 分成 36 个扇区（每 10° 一个）分别积分。

用途：
  - 检查各方位角衍射谱的强度一致性；
  - 检查峰位是否随方位角偏移；
  - 可视化探测器边缘对衍射环的截断几何。

输出（按样品分文件夹 outputs/{stem}/）：
  - sectors/ 下 36 个两列 txt（2θ, intensity），每个扇区一个；
    完整版始终保存，指定 --range 时另存 *_auto.txt 裁剪版
  - waterfall.png（原强度堆叠瀑布：36 条沿 Y 轴错开，每条画到自己
    强度变 0 的位置——右端阶梯展示探测器对环的截断几何）

χ 角约定（pyFAI，实测验证）：χ=0° 沿探测器水平方向（图像 +x 向右），
逆时针为正（图像上方 = +90°）。integrate2d 从 -180° 分箱，sector_00
中心 -175°；文件名和瀑布图 y 轴都标注扇区中心 χ 值。

用法示例：
    python scripts/sector_waterfall.py --file data/xxx.tif
    python scripts/sector_waterfall.py          # 不带 --file：交互菜单选文件（可多选）
    python scripts/sector_waterfall.py --file data/xxx.tif --range full
    python scripts/sector_waterfall.py --file data/xxx.tif --range 1.3,7.3
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
from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG, get_config  # 几何配置注册表（--config 指定 / 菜单选择）
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.services.integrator import integrate_sectors
from xrd_toolkit.services.range_selector import detect_material, select_auto_range


def main() -> None:
    parser = argparse.ArgumentParser(description="Sector integration + waterfall plot")
    parser.add_argument("--file",
                        help="diffraction image path; if not given, an interactive menu "
                             "lists files in data/ and lets you pick (comma-separated for several)")
    parser.add_argument("--datadir", default="data",
                        help="folder scanned by the interactive menu (only used without --file), default data/")
    parser.add_argument("--config",
                        help=f"Geometry config name from config.py (default: {DEFAULT_CONFIG}); "
                             "without --file an interactive menu lets you pick instead")
    parser.add_argument("--n-sectors", type=int, default=36, help="number of azimuthal sectors")
    parser.add_argument("--npt", type=int, default=3000, help="points in each 1D curve")
    parser.add_argument("--range", dest="range_", default="auto",
                        help="2θ range for the plots and the *_auto trimmed txt: "
                             "full, auto (default: per-material standard range), or "
                             "lo,hi in degrees (e.g. 1.3,7.3). The full txt is always "
                             "saved regardless.")
    parser.add_argument("--material", default="auto",
                        help="material for the auto-range standard: "
                             "auto (detect from filename), lab6, or lmfp — the range "
                             "is then fixed by that material's known peak positions")
    parser.add_argument("--outdir", default="outputs", help="output directory")
    args = parser.parse_args()

    # --config 名称先行校验：出错时以 argparse 风格退出（用法 + 错误行、
    # 退出码 2），无需等到选完文件才发现
    if args.config is not None:
        config_name = args.config
        try:
            cfg = get_config(config_name)
        except ValueError as err:
            parser.error(str(err))
    else:
        config_name = cfg = None

    # 未指定 --file 时弹出交互菜单（同 view_diffraction），支持多选
    # （如 1,2）；各文件输出到 outputs/{数据名}/，互不覆盖
    if args.file:
        file_list = [Path(args.file)]
    else:
        file_list = interactive_pick_files(Path(args.datadir))

    # 几何配置解析顺序：
    #   1. --config 指定（已校验）
    #   2. 交互模式（未带 --file）：选完数据文件后再显示配置菜单
    #   3. 其余情况使用 DEFAULT_CONFIG（PyCharm 运行配置依赖此默认值）
    if cfg is None:
        if args.file is None:
            config_name = pick_config(CONFIGS, DEFAULT_CONFIG)
            cfg = CONFIGS[config_name]
        else:
            config_name = DEFAULT_CONFIG
            cfg = get_config()
    print(f"Using geometry config: {config_name}")

    geom = cfg["geometry"]   # 直接展开配置条目的 7 个几何键

    for path in file_list:
        image = load_diffraction_image(str(path))
        print(f"\nImage: {path} ({image.shape[0]}x{image.shape[1]} px)")

        # 扇形积分：36 条 1D 曲线
        tth, I2d, chi = integrate_sectors(
            image,
            n_sectors=args.n_sectors,
            npt=args.npt,
            **geom,
        )
        n = args.n_sectors
        width = 360.0 / n
        print(f"Sectors: {n} (one per {width:.1f}°), {len(tth)} points per curve")

        # ---- 2θ 有效区间（默认 auto）----
        # 完整版 txt 始终保存；区间只影响图与另存的 _auto 裁剪版。
        # auto：下界 = 材料专属标准（lmfp 第一峰 −0.3°；lab6 光环结束点
        # −0.6°，约 1.0°；同一材料所有数据共用同一标准）；上界 = 数据
        # 失效点自动检测（36 条曲线存活比例跌破 80% 的位置，实测 7.44°）。
        # 材料未知时下界退回缓坡检测。细节见 services/range_selector.py。
        sel = parse_range_arg(args.range_)
        mean_curve = np.nanmean(I2d, axis=1)   # 36 扇区平均曲线（区间检测用）
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
                tth, mean_curve, material, geom["wavelength_m"],
                image.shape, geom["pixel_size_m"], geom["dist_m"],
                I2d=I2d.T,    # 36 条扇区曲线 → 上界按存活比例 <80% 自动检测
                poni_px=(geom["poni1_m"] / geom["pixel_size_m"],
                         geom["poni2_m"] / geom["pixel_size_m"]))
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

        # ---- 保存 36 个两列 txt（输出按样品分文件夹：outputs/{数据名}/sectors/）----
        # 完整版始终保存；选定区间时另存 *_auto.txt 裁剪版
        outdir = Path(args.outdir)
        stem = path.stem
        sec_dir = outdir / stem / "sectors"
        sec_dir.mkdir(parents=True, exist_ok=True)
        if lo is not None:
            keep = (tth >= lo) & (tth <= hi)
            tth_trim, I2d_trim = tth[keep], I2d[keep, :]
            tth_plot, I2d_plot = tth_trim, I2d_trim
        else:
            tth_trim, I2d_trim = None, None
            tth_plot, I2d_plot = tth, I2d
        for k in range(n):
            c0 = -180 + width * k
            # np.savetxt 会自动给 header 每行加 "# "，这里不重复写
            header = f"sector {k:02d}: chi = {chi[k]:.2f} deg (range [{c0:.0f}, {c0+width:.0f}))\n" \
                     f"columns: 2theta(deg)  intensity"
            np.savetxt(sec_dir / f"sector_{k:02d}_chi{chi[k]:.0f}deg.txt",
                       np.c_[tth, I2d[:, k]], header=header)
            if tth_trim is not None:
                np.savetxt(sec_dir / f"sector_{k:02d}_chi{chi[k]:.0f}deg_auto.txt",
                           np.c_[tth_trim, I2d_trim[:, k]], header=header)
        print(f"36 two-column txt files saved to: {sec_dir}/"
              + (", plus 36 *_auto.txt trimmed copies" if tth_trim is not None else ""))

        # ---- 每条曲线画到自身首个零强度点 ----
        # 环弧被探测器边缘截断后积分值为 0（实测：截断前一刻仍有数百、
        # 下一格直接为 0），故每条曲线画到自身的第一个 0；未截断的
        # 曲线画到 hi。右端呈阶梯状，直观展示方形探测器对环的截断
        # 几何：指向边缘的扇区先被截断。
        colors = plt.cm.viridis(np.linspace(0, 1, n))
        curves = []              # [(tth_cut, I_cut, k), ...] 每条曲线的作图段
        for k in range(n):
            v = I2d_plot[:, k]
            dead = ~np.isfinite(v) | (v == 0)
            end = int(np.argmax(dead)) if dead.any() else len(v)
            curves.append((tth_plot[:end], v[:end], k))

        # ---- 瀑布图：36 条原强度曲线沿 Y 轴错开堆叠 ----
        # 每条曲线画到自身首个零强度点，使用原强度（不取根号）。
        # 行间距自适应：每行高度 = 该行峰值 × 0.7（偏移系数），
        # 弱扇区行矮、强扇区行高，各行峰形不会被压扁；相邻行允许
        # 峰顶部分探入上一行。每行基线标 χ 值，曲线可对应回各自的
        # 10° 扇区。
        I_pos = np.clip(I2d_plot, 0.0, None)
        heights = np.maximum(I_pos.max(axis=0), 0.05 * np.nanmax(I_pos))
        offsets = np.zeros(n)
        for k in range(1, n):
            offsets[k] = offsets[k - 1] + heights[k - 1] * 0.7

        fig, ax = plt.subplots(figsize=(12, 8))
        for t_cut, v_cut, k in curves:
            ax.plot(t_cut, np.clip(v_cut, 0.0, None) + offsets[k],
                    color=colors[k], lw=0.5)
        ax.set_yticks(offsets)
        ax.set_yticklabels([f"{c:.0f}°" for c in chi], fontsize=6)
        if lo is not None:
            ax.set_xlim(lo, hi)   # 选定区间；full 时用数据自然范围
        ax.set_xlabel("2θ (deg)")
        ax.set_ylabel("Azimuthal sector (χ)")
        ax.set_title(f"{stem}: 36-sector waterfall (raw intensity, stacked)")
        ax.grid(alpha=0.2)
        fig.tight_layout()
        fig.savefig(outdir / stem / "waterfall.png", dpi=150)
        plt.close(fig)

        print(f"Waterfall saved: {outdir / stem / 'waterfall.png'}")

        # ---- 强度一致性与峰位偏移统计 ----
        # 统计使用选定区间内的数据（auto 已排除直射束晕区）
        mean_curve = np.nanmean(I2d_plot, axis=1)                  # 36 扇区平均曲线
        i0 = np.argmax(mean_curve)
        t0 = tth_plot[i0]
        print(f"\nStrongest peak: 2θ = {t0:.4f}° (mean of 36 sectors)")

        win = (tth_plot > t0 - 0.15) & (tth_plot < t0 + 0.15)      # 最强峰 ±0.15° 窗口
        peaks, positions = [], []
        for k in range(n):
            j = np.argmax(I2d_plot[win, k])
            peaks.append(I2d_plot[win, k][j])
            positions.append(tth_plot[win][j])
        peaks = np.array(peaks); positions = np.array(positions)
        print(f"Peak intensity per sector: mean {np.mean(peaks):.0f}, relative std {np.std(peaks)/np.mean(peaks)*100:.1f}%")
        print(f"Peak position per sector: std {np.std(positions):.4f}°, max deviation {np.max(np.abs(positions-np.mean(positions))):.4f}°")
        print(f"Weakest / strongest sector: χ={chi[np.argmin(peaks)]:.0f}° / χ={chi[np.argmax(peaks)]:.0f}°")


if __name__ == "__main__":
    main()
