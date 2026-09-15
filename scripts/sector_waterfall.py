#!/usr/bin/env python3
"""扇形积分 + 瀑布图：把 0°~360° 分成 36 个扇区（每 10° 一个）分别积分。

观察目标：
  - 不同角度的衍射图谱是否有差异？
  - 强度是否一致？
  - 峰位是否偏移？

输出（按样品分文件夹 outputs/{stem}/）：
  - sectors/ 下 36 个两列 txt（2θ, intensity），每个扇区一个；
    完整版永远保存，选了 --range 时另存 *_auto.txt 裁剪版
  - waterfall.png（原强度堆叠瀑布：36 条沿 Y 轴错开，每条画到自己
    强度变 0 的位置——右端阶梯展示"环被探测器切掉"的几何）

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
from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG, get_config  # 几何配置注册表（--config 点名 / 菜单选择）
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

    geom = cfg["geometry"]   # 扇形积分直接 **geom 展开 7 个几何键

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

        # ---- 2θ 有效区间选择（可选，默认 auto）----
        # txt 永远保存完整版（数据母版）；区间只影响图和另存的 _auto 裁剪版。
        # auto（A+A 方案，2026-09-16 拍板）：下界 = 材料专属标准
        # （lmfp 第一峰 −0.3°；lab6 光环结束点 −0.6°，≈1.0°；缓坡
        # 完整包进来，同一材料所有数据同一个标准，w1 与 w2 各用各的）；
        # 上界 = 数据失效点自动检测（36 条曲线存活比例跌破 80% 的
        # 位置，实测 7.44°）。材料未知 → 下界退回缓坡检测。
        # 细节见 services/range_selector.py。
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
        # 完整版永远保存；选了区间时另存一份 *_auto.txt（裁剪版），两个都留
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
            header = f"# sector {k:02d}: chi = {chi[k]:.2f} deg (range [{c0:.0f}, {c0+width:.0f}))\n" \
                     f"# columns: 2theta(deg)  intensity"
            np.savetxt(sec_dir / f"sector_{k:02d}_chi{chi[k]:.0f}deg.txt",
                       np.c_[tth, I2d[:, k]], header=header)
            if tth_trim is not None:
                np.savetxt(sec_dir / f"sector_{k:02d}_chi{chi[k]:.0f}deg_auto.txt",
                           np.c_[tth_trim, I2d_trim[:, k]], header=header)
        print(f"36 two-column txt files saved to: {sec_dir}/"
              + (", plus 36 *_auto.txt trimmed copies" if tth_trim is not None else ""))

        # ---- 每条曲线截止到自己的"零强度"点（2026-09-16 用户要求）----
        # 死掉的扇区 = 环弧被探测器边缘切光，积分值是严格的 0（实测：
        # 死亡前一刻还有几百、下一格直接 0）。每条曲线画到自己的第一个
        # 0；没死的继续画到 hi（"现在这个位置"）。右端呈阶梯状，直观
        # 展示"环被方形探测器切掉"的几何：指向边缘的扇区先死。
        colors = plt.cm.viridis(np.linspace(0, 1, n))
        curves = []              # [(tth_cut, I_cut, k), ...] 每条曲线自己的作图段
        for k in range(n):
            v = I2d_plot[:, k]
            dead = ~np.isfinite(v) | (v == 0)
            end = int(np.argmax(dead)) if dead.any() else len(v)
            curves.append((tth_plot[:end], v[:end], k))

        # ---- 瀑布图 1：36 条曲线沿 Y 轴错开的堆叠瀑布（原强度）----
        # 设计（2026-09-16 用户要求：瀑布图 = 36 条曲线沿 Y 轴错开，
        # 每条画到自己强度变 0 的位置，用原强度不开根号）：
        #   行间距自适应（用户要求：可以重叠、行间距可以不一样，只要
        #   每 10° 扇区都分得开）：每行高度 = 该行原强度峰值 * X，
        #   行内刚好放下自己的峰——弱扇区行矮、强扇区行高，再弱的
        #   扇区也有自己的行高，峰不会被压扁；相邻行允许峰顶轻轻
        #   探进上一行（X < 1）。每行基线画网格线 + 标 χ 值，36 条
        #   一眼就能对回各自的 10° 扇区。
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

        # ---- 回答观察问题：强度一致性 + 峰位偏移 ----
        # 统计用选定区间内的数据（auto 时已去掉直射束晕区，无需再 mask 0.4°）
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
