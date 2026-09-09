#!/usr/bin/env python3
"""扇形积分 + 瀑布图：把 0°~360° 分成 36 个扇区（每 10° 一个）分别积分。

观察目标（任务问题）：
  - 不同角度的衍射图谱是否有差异？
  - 强度是否一致？
  - 峰位是否偏移？

输出（按样品分文件夹 outputs/{stem}/）：
  - sectors/ 下 36 个两列 txt（2θ, intensity），每个扇区一个
  - waterfall.png / waterfall_normalized.png（原始强度堆叠 + 各自归一化堆叠）

χ 角约定（pyFAI，实测验证）：χ=0° 沿探测器水平方向（图像 +x 向右），
逆时针为正（图像上方 = +90°）。integrate2d 从 -180° 分箱，sector_00
中心 -175°；文件名和瀑布图 y 轴都标注扇区中心 χ 值。

用法示例：
    python scripts/sector_waterfall.py --file data/week2_lab6.tif
    python scripts/sector_waterfall.py --file data/week1_LMFP_1.tif
    python scripts/sector_waterfall.py          # 不带 --file：交互菜单选文件（可多选）
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
from xrd_toolkit.services.integrator import integrate_sectors


def main() -> None:
    parser = argparse.ArgumentParser(description="Sector integration + waterfall plot")
    parser.add_argument("--file",
                        help="diffraction image path; if not given, an interactive menu "
                             "lists files in data/ and lets you pick (comma-separated for several)")
    parser.add_argument("--datadir", default="data",
                        help="folder scanned by the interactive menu (only used without --file), default data/")
    parser.add_argument("--n-sectors", type=int, default=36, help="number of azimuthal sectors")
    parser.add_argument("--npt", type=int, default=3000, help="points in each 1D curve")
    parser.add_argument("--outdir", default="outputs", help="output directory")
    args = parser.parse_args()

    # 决定要跑哪些文件：给了 --file 就跑指定的；没给就弹交互菜单（同 view_diffraction），
    # 菜单支持多选（如 1,2）→ 循环里逐个处理，输出各自进 outputs/{数据名}/ 不会互相覆盖
    if args.file:
        file_list = [Path(args.file)]
    else:
        file_list = interactive_pick_files(Path(args.datadir))

    for path in file_list:
        image = load_diffraction_image(str(path))
        print(f"\n图像: {path} ({image.shape[0]}x{image.shape[1]} px)")

        # 扇形积分：36 条 1D 曲线
        tth, I2d, chi = integrate_sectors(
            image,
            n_sectors=args.n_sectors,
            npt=args.npt,
            **CALIBRATED,
        )
        n = args.n_sectors
        width = 360.0 / n
        print(f"扇区数: {n}（每 {width:.1f}° 一个）, 每条曲线 {len(tth)} 点")

        # ---- 保存 36 个两列 txt（输出按样品分文件夹：outputs/{数据名}/sectors/）----
        outdir = Path(args.outdir)
        stem = path.stem
        sec_dir = outdir / stem / "sectors"
        sec_dir.mkdir(parents=True, exist_ok=True)
        for k in range(n):
            c0 = -180 + width * k
            header = f"# sector {k:02d}: chi = {chi[k]:.2f} deg (range [{c0:.0f}, {c0+width:.0f}))\n" \
                     f"# columns: 2theta(deg)  intensity"
            np.savetxt(sec_dir / f"sector_{k:02d}_chi{chi[k]:.0f}deg.txt",
                       np.c_[tth, I2d[:, k]], header=header)
        print(f"36 个两列 txt 已保存到: {sec_dir}/")

        # ---- 瀑布图（原始强度：沿 Y 轴错开堆叠）----
        offset = 1.1 * np.nanmax(I2d)          # 每条曲线占一层，层高略大于最大强度
        colors = plt.cm.viridis(np.linspace(0, 1, n))

        fig, ax = plt.subplots(figsize=(12, 8))
        for k in range(n):
            ax.plot(tth, I2d[:, k] + k * offset, color=colors[k], lw=0.5)
        ax.set_yticks(np.arange(n) * offset)
        ax.set_yticklabels([f"{c:.0f}°" for c in chi], fontsize=6)
        ax.set_xlabel("2θ (deg)")
        ax.set_ylabel("Azimuthal sector (χ)")
        ax.set_title(f"{stem}: 36-sector waterfall (raw intensity)")
        ax.grid(alpha=0.2)
        fig.tight_layout()
        fig.savefig(outdir / stem / "waterfall.png", dpi=150)
        plt.close(fig)

        # ---- 瀑布图（各自归一化：只看峰形与峰位，忽略强度差异）----
        fig, ax = plt.subplots(figsize=(12, 8))
        for k in range(n):
            norm = I2d[:, k] / np.nanmax(I2d[:, k])
            ax.plot(tth, norm + k * 1.15, color=colors[k], lw=0.5)
        ax.set_yticks(np.arange(n) * 1.15)
        ax.set_yticklabels([f"{c:.0f}°" for c in chi], fontsize=6)
        ax.set_xlabel("2θ (deg)")
        ax.set_ylabel("Azimuthal sector (χ)")
        ax.set_title(f"{stem}: 36-sector waterfall (normalized)")
        ax.grid(alpha=0.2)
        fig.tight_layout()
        fig.savefig(outdir / stem / "waterfall_normalized.png", dpi=150)
        plt.close(fig)
        print(f"瀑布图已保存: {outdir / stem / 'waterfall.png'}")
        print(f"归一化瀑布图: {outdir / stem / 'waterfall_normalized.png'}")

        # ---- 回答观察问题：强度一致性 + 峰位偏移 ----
        mean_curve = np.nanmean(I2d, axis=1)                       # 36 扇区平均曲线
        mask = tth > 0.4                                           # 排除直射束区
        i0 = np.argmax(mean_curve[mask])
        t0 = tth[mask][i0]
        print(f"\n最强峰位置: 2θ = {t0:.4f}°（36 扇区平均曲线）")

        win = (tth > t0 - 0.15) & (tth < t0 + 0.15)                # 最强峰 ±0.15° 窗口
        peaks, positions = [], []
        for k in range(n):
            j = np.argmax(I2d[win, k])
            peaks.append(I2d[win, k][j])
            positions.append(tth[win][j])
        peaks = np.array(peaks); positions = np.array(positions)
        print(f"该峰各扇区强度: 平均 {np.mean(peaks):.0f}, 相对标准差 {np.std(peaks)/np.mean(peaks)*100:.1f}%")
        print(f"该峰各扇区峰位: 标准差 {np.std(positions):.4f}°, 最大偏移 {np.max(np.abs(positions-np.mean(positions))):.4f}°")
        print(f"强度最弱/最强扇区: χ={chi[np.argmin(peaks)]:.0f}° / χ={chi[np.argmax(peaks)]:.0f}°")


if __name__ == "__main__":
    main()
