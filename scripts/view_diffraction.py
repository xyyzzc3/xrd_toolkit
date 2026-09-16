#!/usr/bin/env python3
"""查看二维衍射图像，并绘制穿过中心的线性剖面图.

用法示例：
    # 方式一：指定一个文件（原用法，兼容不变）
    python scripts/view_diffraction.py --file data/lab6.tif

    # 方式二：不带 --file 运行 → 列出 data/ 里的文件，按编号选一个或多个
    python scripts/view_diffraction.py

    # 换几何配置条目（默认 lmfp1_lab6）：
    python scripts/view_diffraction.py --file data/xxx.tif --config lmfp2_lab6
"""

import argparse
from pathlib import Path

from matplotlib.colors import LogNorm
import matplotlib.pyplot as plt
import numpy as np

from xrd_toolkit.cli import interactive_pick_files, pick_config  # 交互菜单（选文件 / 选配置，四脚本共用）
from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG, get_config  # 几何配置注册表（--config 指定 / 菜单选择）
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.core.processor import line_profile


def nice_step(size: float, target_ticks: int = 5) -> float:
    """选择规整的刻度间隔：约等于 size/target_ticks，取整到 1/2/2.5/5×10^n.

    例：size=2048 → 目标 410 → 取 500；size=1000 → 目标 200 → 取 200。
    保证任意尺寸的图像坐标轴上有约 target_ticks 个刻度。
    """
    target = size / target_ticks
    power = 10 ** np.floor(np.log10(max(target, 1e-12)))  # 数量级（10 的整数次方）
    for m in (1, 2, 2.5, 5, 10):   # 从小到大尝试规整系数
        if m * power >= target:
            return m * power
    return 10 * power


def process_one_file(path: Path, args, cfg: dict, outdir: Path) -> None:
    """对单个文件完成 读图 → 画衍射图 → 画剖面 → 存 PNG 的完整流程.

    outdir 与 cfg 均由 main() 传入：cfg 为 --config 选中的配置条目
    （含 geometry / beam_center），函数内不直接 import 配置。"""

    # ── 读图像 ──
    # 2D numpy 数组：data[行][列] = 该像素强度（行向下、列向右）
    data = load_diffraction_image(str(path))

    # 文件名去掉路径与扩展名，空格换下划线
    tag = path.stem.replace(" ", "_")

    # 输出按样品分文件夹：outputs/{数据名}/
    sub = outdir / tag
    sub.mkdir(parents=True, exist_ok=True)

    # 圆心：默认使用选中配置条目（--config）的 beam_center（校准值，
    # 同一仪器通用；自动定位仅保留给校准脚本作初值），
    # 指定 --center 时以用户输入覆盖。
    # 注意坐标顺序：屏幕习惯 (x, y) = (列, 行)，数组下标为 data[行][列]；
    # beam_center 存储的正是 (行, 列)。
    center = None
    if args.center:
        # --center 输入 "cx,cy"，转成 (cy, cx) 供数组下标使用
        cx, cy = (float(s) for s in args.center.split(","))
        center = (cy, cx)
    else:
        center = tuple(cfg["beam_center"])   # 选中条目的校准值 (行, 列)

    h, w = data.shape  # h = 行数（高），w = 列数（宽）

    print(f"\n=== {path.name} ===  Image size: {w} x {h}, intensity range: {data.min():.1f} ~ {data.max():.1f}")
    # 回显圆心来源与坐标（校准值 / 手动指定）
    print(f"  Ring center ({'user-specified' if args.center else 'calibrated'}): "
          f"({center[1]:.2f}, {center[0]:.2f}) px")

    # ── 颜色标尺范围 ──
    # 未指定 --vmin/--vmax 时自动确定：
    #   vmin = 1% 分位数（压制个别坏点，背景为最暗值）
    #   vmax = 99.9% 分位数（压制单晶亮斑，避免局部过曝）
    # 每张图按自身数据确定范围，保证动态范围充分利用
    if args.vmin is None or args.vmax is None:
        # q 分位数：q% 像素低于该值的强度
        vmin = max(1.0, float(np.percentile(data, 1)))     # 下限至少 1（对数色标要求正数）
        vmax = max(float(np.percentile(data, 99.9)), vmin * 10.0)  # 上限不低于下限的 10 倍
    else:
        vmin, vmax = args.vmin, args.vmax  # 指定时以用户输入覆盖（手动对比度）
    # 回显实际采用的颜色范围
    print(f"  Color scale: vmin={vmin:.0f}, vmax={vmax:.0f}")

    # ── 图 1：衍射图 ──
    # figsize 单位英寸；宽度固定 5，高度按图像宽高比 (h/w) 自适应，
    # 避免图像被拉伸变形
    base_w = 5.0
    fig, ax = plt.subplots(figsize=(base_w, base_w * h / w))

    # 显示参数：
    #   cmap="magma"   色带（magma=黑→紫→橙黄；可选 viridis/gray/jet/hot）
    #   vmin/vmax      颜色范围，未指定时按上文自动计算
    #   LogNorm        对数色标；衍射图动态范围大，线性色标会整体偏黑
    #   origin         行方向："lower" = 第 0 行在下方
    im = ax.imshow(data, cmap="magma", norm=LogNorm(vmin=vmin, vmax=vmax), origin="lower")

    # 两行标题：文件名较长时避免被裁；第二行为环圆心坐标
    ax.set_title(f"2D Diffraction Image\n({tag}, log scale, ring center = ({center[1]:.1f}, {center[0]:.1f}) px)")
    ax.set_xlabel("Detector pixel X (px)")
    ax.set_ylabel("Detector pixel Y (px)")
    # 显式指定刻度，防止自动刻度超出数据范围；间隔用 nice_step
    # 自适应（约 5 个刻度）
    step_x = max(1, int(round(nice_step(w))))
    step_y = max(1, int(round(nice_step(h))))
    ax.set_xticks(range(0, w, step_x))
    ax.set_yticks(range(0, h, step_y))

    # 圆心标记 = 十字（先黑色粗线打底、再叠白色细线）：黑白描边保证
    # 在深浅背景上均清晰。沿 --angle 方向画白色虚线表示剖面取样方向。
    # arm 随图像尺寸缩放（w/60）；线宽 <1.2 pt 时抗锯齿会把纯白抹灰、
    # 十字在缩略图中几乎不可见，因此白线固定 1.2 pt。
    prof_cy, prof_cx = center
    arm = max(16.0, w / 60.0)
    for color, lw in [("k", 2.0), ("w", 1.2)]:
        ax.plot([prof_cx - arm, prof_cx + arm], [prof_cy, prof_cy],
                color=color, lw=lw)  # 十字横臂
        ax.plot([prof_cx, prof_cx], [prof_cy - arm, prof_cy + arm],
                color=color, lw=lw)  # 十字竖臂
    angle_rad = np.radians(args.angle)
    r_ext = np.hypot(w, h) / 2.0  # 半对角线长，保证虚线穿出图像
    dx, dy = r_ext * np.cos(angle_rad), r_ext * np.sin(angle_rad)
    ax.plot([prof_cx - dx, prof_cx + dx], [prof_cy - dy, prof_cy + dy],
            color="w", ls="--", lw=0.8, alpha=0.8)  # 剖面取样方向（白色虚线）

    # 剖面虚线延伸到图像外（保证贯穿整图）；显式 set_xlim/set_ylim
    # 关闭该轴的自动缩放，显示范围锁定为图像本身，越界部分被裁剪
    ax.set_xlim(-0.5, w - 0.5)
    ax.set_ylim(-0.5, h - 0.5)

    # pad = 颜色条与图间距；shrink 让颜色条略短，避免刻度贴边
    cbar = fig.colorbar(im, ax=ax, pad=0.02, shrink=0.85)
    cbar.set_label("Intensity (counts)")

    fig.tight_layout()   # 自动收紧边距，防止标签被裁
    fig.subplots_adjust(bottom=0.20)   # 底部留白，避免颜色条刻度被裁
    # dpi=150；印刷可调 300（文件更大）
    fig.savefig(sub / "image.png", dpi=150)

    # ── 图 2：过圆心的强度剖面 ──
    # t = 采样点到圆心的距离（圆心左侧为负、右侧为正）；profile = 各采样点强度
    t, profile = line_profile(data, center=center, angle_deg=args.angle)

    fig2, ax2 = plt.subplots(figsize=(7, 3.5))  # 剖面图采用更宽扁的比例
    ax2.plot(t, profile, linewidth=1.2)
    ax2.set_title(f"Intensity Profile Through Center ({args.angle:g}° from horizontal)")
    ax2.set_xlabel("Distance from center (px) (negative = left, positive = right)")
    ax2.set_ylabel("Intensity (counts)")
    # 刻度按剖面长度自适应（约 6 个），自 0 向两侧对称展开
    t_step = nice_step(t.max() - t.min(), target_ticks=6)
    ticks_pos = np.arange(0, t.max() + 1, t_step)                       # 0 和正侧
    ticks_neg = -np.arange(t_step, -t.min() + 1, t_step)[::-1]          # 负侧（从里向外）
    ax2.set_xticks(np.concatenate([ticks_neg, ticks_pos]))
    ax2.grid(True, alpha=0.3)  # 浅色网格线，便于读数
    fig2.tight_layout()
    fig2.savefig(sub / "profile.png", dpi=150)  # dpi 同图 1

    print(f"[OK] {path.name} -> {sub.resolve()}/image.png, profile.png")


def main() -> None:

    # ── 参数解析（argparse）──
    parser = argparse.ArgumentParser(description="View a 2D diffraction image and plot a line profile through the center")
    parser.add_argument("--file",
                        help="Path to a diffraction image file (.edf/.tif/.cbf, etc.). "
                             "If not given, an interactive menu lists files in data/ and lets you pick.")
    parser.add_argument("--datadir", default="data",
                        help="Folder scanned by the interactive menu (only used without --file), default data/")
    parser.add_argument("--config",
                        help=f"Geometry config name from config.py (default: {DEFAULT_CONFIG}); "
                             "without --file an interactive menu lets you pick instead")
    parser.add_argument("--angle", type=float, default=0.0,
                        help="Angle between the profile line and the horizontal axis (degrees), default 0")
    parser.add_argument("--center",
                        help="Ring center pixel coordinates cx,cy (e.g. 1020,1024); "
                             "if not given, the beam center of the selected config is used")
    parser.add_argument("--outdir", default="outputs",
                        help="Base output directory, default outputs/ (each file is saved into outputs/{filename}/)")
    parser.add_argument("--vmin", type=float, default=None,
                        help="Color scale lower limit; if not given, auto = 1st percentile of the image")
    parser.add_argument("--vmax", type=float, default=None,
                        help="Color scale upper limit; if not given, auto = 99.9th percentile of the image")
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

    outdir = Path(args.outdir)
    outdir.mkdir(exist_ok=True)  # 不存在则创建；已存在不报错

    # ── 确定待处理的文件 ──
    if args.file:
        # 指定 --file：仅处理该文件
        file_list = [Path(args.file)]
    else:
        # 未指定 --file：交互菜单按编号选择
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

    # 依次处理选中的每个文件
    for path in file_list:
        process_one_file(path, args, cfg, outdir)

    plt.show()  # 全部绘制完成后统一显示；已保存的 PNG 不受影响


# ══ 程序入口 ════════════════════════════════════════════════════════
if __name__ == "__main__":
    main()
