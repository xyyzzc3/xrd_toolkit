#!/usr/bin/env python3
"""查看二维衍射图像，并绘制穿过中心的线性剖面图.
"""

import argparse
from pathlib import Path

from matplotlib.colors import LogNorm
import matplotlib.pyplot as plt

from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.core.processor import line_profile


def main() -> None:

    # ── 第 1 步：读懂用户敲的命令（argparse）──
    # 用户敲 python scripts/view_diffraction.py --file a.tif --center 1,2
    # 时，argparse 会把它拆成 args.file、args.center 等变量。
    parser = argparse.ArgumentParser(description="View a 2D diffraction image and plot a line profile through the center")
    parser.add_argument("--file", required=True,
                        help="Path to the diffraction image file (.edf/.tif/.cbf, etc.)")
    parser.add_argument("--angle", type=float, default=0.0,
                        help="Angle between the profile line and the horizontal axis (degrees), default 0")
    parser.add_argument("--center",
                        help="Ring center pixel coordinates cx,cy (e.g. 1020,1024); defaults to the image geometric center")
    parser.add_argument("--outdir", default="outputs", help="Directory to save the PNGs, default outputs/")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(exist_ok=True)  # 目录不存在就创建；exist_ok 让已存在时不报错

    # ── 第 2 步：读图像 ──
    # data 是一个 2D numpy 数组：data[行][列] = 该像素的强度。
    # 行号从上往下数，列号从左往右数，每个元素是一个浮点数。
    data = load_diffraction_image(args.file)

    # 文件名去掉路径和扩展名，空格换成下划线（避免生成的文件名里有空格）
    tag = Path(args.file).stem.replace(" ", "_")

    # 圆心：用户用 --center 给了就用用户的；没给就用图像几何中心。
    # 注意顺序：屏幕上习惯说 (x, y) = (列, 行)，而数组下标是 data[行][列]，
    # 所以传给 line_profile 时要反过来存成 (行, 列)。
    center = None
    if args.center:
        # 生成器表达式：(处理(s) for s in 一串东西)
        # = "对这一串里的每个 s 做处理"的简写。
        # split(",") 把 "1020,1024" 按逗号切开 → ["1020", "1024"]，
        # float(s) 把字符串 "1020" 变成数字 1020.0。
        # 元组：(cy, cx) 顺序不能乱。
        cx, cy = (float(s) for s in args.center.split(","))
        center = (cy, cx)

    h, w = data.shape  # 数组形状：h = 行数（高），w = 列数（宽）

    # f-string：字符串前加 f，花括号 {} 里的变量会被替换成它的值。
    # {data.min():.1f} 里的 :.1f 表示"保留 1 位小数"（浮点数格式化）。
    print(f"Image size: {w} x {h}, intensity range: {data.min():.1f} ~ {data.max():.1f}")

    # ── 第 3 步：图 1 —— 衍射图本身 ──
    # subplots 创建"画布 fig + 画板 ax"。
    # 【可调】figsize=(宽, 高)，单位英寸：调大→图更大更清晰（文件也更大），调小→反之
    fig, ax = plt.subplots(figsize=(5, 4.5))

    # 【可调】imshow 的显示参数：
    #   cmap="magma"   色带（颜色主题）：magma=黑→紫→橙黄；
    #                  viridis=蓝绿黄、gray=黑白、jet=彩虹、hot=黑红黄白
    #   vmin=100       颜色条下限：调小（如 10）→更弱的环也看得见，但背景更亮更花
    #   vmax=50000     颜色条上限：调大（如 200000）→最亮处不糊成一团，中心细节更多
    #                  （vmin/vmax 一起调 = 调对比度）
    #   LogNorm        对数色标：删掉 norm=... 就变线性色标；强弱差几万倍时
    #                  线性图几乎全黑，所以衍射图一般用对数
    #   origin         行方向："lower"=第 0 行画在下方（现在用的）；"upper"=上下翻转
    im = ax.imshow(data, cmap="magma", norm=LogNorm(vmin=100, vmax=50000), origin="lower")

    # 标题分两行：文件名很长，一行放不下会超出画布被裁掉
    ax.set_title(f"2D Diffraction Image\n({tag}, log scale)")
    ax.set_xlabel("Detector pixel X (px)")
    ax.set_ylabel("Detector pixel Y (px)")
    # 显式指定刻度：防止自动刻度跑到数据范围外、把文字挤出画布
    ax.set_xticks(range(0, w, 500))
    ax.set_yticks(range(0, h, 500))

    # 用红色十字标记剖面经过的圆心，方便核对位置对不对
    # 【可调】红线的样子：color="r" 颜色（"r"红 "b"蓝 "w"白）；linewidth 线宽越大越粗；
    # alpha 透明度（0=全透明 ~ 1=不透明）；ms=14 十字大小；mew=2 十字描边粗细
    prof_cy, prof_cx = center if center is not None else (h / 2.0, w / 2.0)
    ax.axhline(prof_cy, color="r", linewidth=0.8, alpha=0.7)  # 过圆心的水平红线
    ax.axvline(prof_cx, color="r", linewidth=0.8, alpha=0.7)  # 过圆心的竖直红线
    # ax.plot(prof_cx, prof_cy, "+", color="r", ms=14, mew=2)   # 圆心处的红色十字

    # 【可调】pad=0.02 是颜色条与图的间距，调大离得更远
    # shrink=0.85 让颜色条短一点，上下刻度不贴到画布边缘
    cbar = fig.colorbar(im, ax=ax, pad=0.02, shrink=0.85)   # 颜色条：把颜色和强度数值对应起来
    cbar.set_label("Intensity (counts)")

    fig.tight_layout()   # 自动收紧边距，防止标签被裁掉
    fig.subplots_adjust(bottom=0.20)   # 底部多留白：颜色条最下刻度 10^1 不会被裁
    # 【可调】dpi=150 保存清晰度：改成 300 更清晰但文件更大（印刷用 300）
    fig.savefig(outdir / f"{tag}_image.png", dpi=150)  # 存成 PNG

    # ── 第 4 步：图 2 —— 过圆心的强度剖面 ──
    # line_profile 返回两个数组：
    #   t       —— 采样点到圆心的距离（像素，圆心左侧为负、右侧为正）
    #   profile —— 每个采样点的强度
    t, profile = line_profile(data, center=center, angle_deg=args.angle)

    # 【可调】figsize=(7, 3.5) 剖面图更宽更扁，改数值可调宽高比
    fig2, ax2 = plt.subplots(figsize=(7, 3.5))
    # 【可调】linewidth=1.2 曲线粗细：改大更粗更醒目，改小更细
    ax2.plot(t, profile, linewidth=1.2)  # plot(x, y)：把点连成曲线
    ax2.set_title(f"Intensity Profile Through Center ({args.angle:g}° from horizontal)")
    ax2.set_xlabel("Distance from center (px) (negative = left, positive = right)")
    ax2.set_ylabel("Intensity (counts)")
    ax2.set_xticks([-1000, -500, 0, 500, 1000])  # 显式刻度：自动刻度会超出范围被裁
    # 【可调】网格：grid(False) 关掉网格；alpha=0.3 网格深浅（0=最浅 ~ 1.0=最深）
    ax2.grid(True, alpha=0.3)  # 浅色网格线，方便读数
    fig2.tight_layout()
    fig2.savefig(outdir / f"{tag}_profile.png", dpi=150)  # 【可调】dpi 同图 1：清晰度与文件大小的权衡

    print(f"Figures saved -> {outdir.resolve()}/")
    plt.show()  # 交互运行时弹窗显示；保存的 PNG 不受影响


# ══ 程序入口 ════════════════════════════════════════════════════════
if __name__ == "__main__":
    main()
