#!/usr/bin/env python3
"""查看二维衍射图像，并绘制穿过中心的线性剖面图.

用法示例：
    # 方式一：指定一个文件（原用法，兼容不变）
    python scripts/view_diffraction.py --file data/lab6.tif

    # 方式二：不带 --file 运行 → 列出 data/ 里的文件，按编号选一个或多个
    python scripts/view_diffraction.py
"""

import argparse
from pathlib import Path

from matplotlib.colors import LogNorm
import matplotlib.pyplot as plt
import numpy as np

from xrd_toolkit.cli import interactive_pick_files  # 交互选文件菜单（四脚本共用）
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.core.processor import find_ring_center, line_profile


def nice_step(size: float, target_ticks: int = 5) -> float:
    """选一个"好看"的刻度间隔：约等于 size/target_ticks，但取整到 1/2/2.5/5×10^n.

    例：size=2048 → 目标 410 → 取 500；size=1000 → 目标 200 → 取 200。
    这样不管图像多大，坐标轴上都刚好有约 5 个刻度，不会太密也不会太稀。
    """
    target = size / target_ticks
    power = 10 ** np.floor(np.log10(max(target, 1e-12)))  # 数量级（10 的整数次方）
    for m in (1, 2, 2.5, 5, 10):   # 从小的"好看系数"开始试
        if m * power >= target:
            return m * power
    return 10 * power


def process_one_file(path: Path, args, outdir: Path) -> None:
    """对单个文件完成 读图 → 画图 1 → 画图 2 → 存 PNG 的完整流程.

    outdir 从 main() 传进来：函数内部看不到外面定义的变量，
    要用就得通过参数"递"进来（Python 的作用域规则）。"""

    # ── 第 2 步：读图像 ──
    # data 是一个 2D numpy 数组：data[行][列] = 该像素的强度。
    # 行号从上往下数，列号从左往右数，每个元素是一个浮点数。
    data = load_diffraction_image(str(path))

    # 文件名去掉路径和扩展名，空格换成下划线（避免生成的文件名里有空格）
    tag = path.stem.replace(" ", "_")

    # 输出按样品分文件夹：outputs/{数据名}/，文件名不带数据名前缀
    sub = outdir / tag
    sub.mkdir(parents=True, exist_ok=True)

    # 圆心：用户用 --center 给了就用用户的；没给就自动定位——
    # find_ring_center 利用"衍射图关于圆心中心对称"（Friedel 定律），
    # 把图绕几何中心转 180° 后与原图做 FFT 互相关，峰位的一半就是圆心偏移。
    # 所以以后任何新数据都不用再量圆心，看图时自动找。
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
    else:
        center = find_ring_center(data)   # 自动定位（返回 (行, 列)）

    h, w = data.shape  # 数组形状：h = 行数（高），w = 列数（宽）

    # f-string：字符串前加 f，花括号 {} 里的变量会被替换成它的值。
    # {data.min():.1f} 里的 :.1f 表示"保留 1 位小数"（浮点数格式化）。
    print(f"\n=== {path.name} ===  Image size: {w} x {h}, intensity range: {data.min():.1f} ~ {data.max():.1f}")
    # 打印圆心：自动定位时打印 (x, y)，方便和标定值对照；用户手动指定时也回显一遍
    print(f"  Ring center ({'user-specified' if args.center else 'auto-detected'}): "
          f"({center[1]:.2f}, {center[0]:.2f}) px")

    # ── 颜色标尺范围：自动还是手动？──
    # 不传 --vmin/--vmax 时自动取：
    #   vmin = 1% 分位数（压掉个别坏点，让最暗部分刚好是背景）
    #   vmax = 99.9% 分位数（压掉单晶亮斑，防止糊成一块亮斑）
    # 每张图用自己的数据定范围 → 动态范围永远"摊满"，不会大片全黑或饱和
    if args.vmin is None or args.vmax is None:
        # np.percentile(data, q)：找出"q% 的像素都低于它"的那个强度值
        vmin = max(1.0, float(np.percentile(data, 1)))     # 下限至少 1（对数色标要求正数）
        vmax = max(float(np.percentile(data, 99.9)), vmin * 10.0)  # 保底：上限至少是下限的 10 倍
    else:
        vmin, vmax = args.vmin, args.vmax  # 用户给了固定值就用用户的（手动调对比度）
    # 打印实际用的颜色标尺范围：自动模式时每张图的值不同，方便核对对比度是否合理
    print(f"  Color scale: vmin={vmin:.0f}, vmax={vmax:.0f}")

    # ── 第 3 步：图 1 —— 衍射图本身 ──
    # subplots 创建"画布 fig + 画板 ax"。
    # 【可调】figsize=(宽, 高)，单位英寸：调大→图更大更清晰（文件也更大），调小→反之。
    # 自动适应：宽度固定 5 英寸，高度按图像的宽高比 (h/w) 算 → 正方形数据画出来就是
    # 正方形，不会被拉伸变形；矩形数据（如 2463×2527）也会按真实比例显示。
    base_w = 5.0
    fig, ax = plt.subplots(figsize=(base_w, base_w * h / w))

    # 【可调】imshow 的显示参数：
    #   cmap="magma"   色带（颜色主题）：magma=黑→紫→橙黄；
    #                  viridis=蓝绿黄、gray=黑白、jet=彩虹、hot=黑红黄白
    #   vmin/vmax      颜色条范围：不传 --vmin/--vmax 时自动取（见上面的计算），
    #                  想手动调对比度就传固定值
    #   LogNorm        对数色标：删掉 norm=... 就变线性色标；强弱差几万倍时
    #                  线性图几乎全黑，所以衍射图一般用对数
    #   origin         行方向："lower"=第 0 行画在下方（现在用的）；"upper"=上下翻转
    im = ax.imshow(data, cmap="magma", norm=LogNorm(vmin=vmin, vmax=vmax), origin="lower")

    # 标题分两行：文件名很长，一行放不下会超出画布被裁掉；
    # 第二行写明环圆心坐标（自动定位或用户指定），不用再靠肉眼找圆心
    ax.set_title(f"2D Diffraction Image\n({tag}, log scale, ring center = ({center[1]:.1f}, {center[0]:.1f}) px)")
    ax.set_xlabel("Detector pixel X (px)")
    ax.set_ylabel("Detector pixel Y (px)")
    # 显式指定刻度：防止自动刻度跑到数据范围外、把文字挤出画布
    # 刻度间隔自动取：图像 2048 → 500；图像 1000 → 200；不管多大都有约 5 个刻度
    step_x = max(1, int(round(nice_step(w))))
    step_y = max(1, int(round(nice_step(h))))
    ax.set_xticks(range(0, w, step_x))
    ax.set_yticks(range(0, h, step_y))

    # 圆心标记 = 小十字（地图标注画法）：先画黑色粗十字打底，再叠白色
    # 细十字——黑白双描边保证岩浆色标（黑紫→橙黄）深浅背景上都清晰，
    # 又比瞄准镜环更简洁不占地方。
    # 沿 --angle 方向再画一条白色虚线表示剖面线的取样方向。
    # 【可调】arm=臂半长随图像大小缩放（w/60：2048 图 → 34 px）；
    # 黑底 lw=2.0 / 白面 lw=1.2 是描边粗细。
    # 踩坑：白面 <1.2 pt 时线细到每个像素都只被部分覆盖，抗锯齿把
    # 纯白全抹成灰色，十字在缩略图里基本看不见——"细"有物理下限。
    prof_cy, prof_cx = center
    arm = max(16.0, w / 60.0)
    for color, lw in [("k", 2.0), ("w", 1.2)]:
        ax.plot([prof_cx - arm, prof_cx + arm], [prof_cy, prof_cy],
                color=color, lw=lw)  # 十字横臂
        ax.plot([prof_cx, prof_cx], [prof_cy - arm, prof_cy + arm],
                color=color, lw=lw)  # 十字竖臂
    angle_rad = np.radians(args.angle)
    r_ext = np.hypot(w, h) / 2.0  # 半对角线长，保证虚线一定穿出图像
    dx, dy = r_ext * np.cos(angle_rad), r_ext * np.sin(angle_rad)
    ax.plot([prof_cx - dx, prof_cx + dx], [prof_cy - dy, prof_cy + dy],
            color="w", ls="--", lw=0.8, alpha=0.8)  # 剖面线取样方向（白色虚线）

    # 踩坑记录：虚线故意画到图像外（保证贯穿整图），但 matplotlib 的自动缩放
    # 会把坐标轴撑大——图像缩成中间一小块、两侧留大白边。
    # set_xlim/set_ylim 显式调用后，该轴的自动缩放被关闭，显示范围锁回图像本身，
    # 越界的虚线部分被裁剪掉，不影响图面。
    ax.set_xlim(-0.5, w - 0.5)
    ax.set_ylim(-0.5, h - 0.5)

    # 【可调】pad=0.02 是颜色条与图的间距，调大离得更远
    # shrink=0.85 让颜色条短一点，上下刻度不贴到画布边缘
    cbar = fig.colorbar(im, ax=ax, pad=0.02, shrink=0.85)   # 颜色条：把颜色和强度数值对应起来
    cbar.set_label("Intensity (counts)")

    fig.tight_layout()   # 自动收紧边距，防止标签被裁掉
    fig.subplots_adjust(bottom=0.20)   # 底部多留白：颜色条最下刻度 10^1 不会被裁
    # 【可调】dpi=150 保存清晰度：改成 300 更清晰但文件更大（印刷用 300）
    fig.savefig(sub / "image.png", dpi=150)  # 存成 PNG

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
    # 刻度自动适应剖面长度：t 从 t.min() 到 t.max()（2048 图约 ±2896px），
    # 取约 6 个刻度，从 0 向两侧对称展开。旧写法写死 ±1000，图更大时刻度就盖不住全范围。
    t_step = nice_step(t.max() - t.min(), target_ticks=6)
    ticks_pos = np.arange(0, t.max() + 1, t_step)                       # 0 和正侧
    ticks_neg = -np.arange(t_step, -t.min() + 1, t_step)[::-1]          # 负侧（从里向外）
    ax2.set_xticks(np.concatenate([ticks_neg, ticks_pos]))
    # 【可调】网格：grid(False) 关掉网格；alpha=0.3 网格深浅（0=最浅 ~ 1.0=最深）
    ax2.grid(True, alpha=0.3)  # 浅色网格线，方便读数
    fig2.tight_layout()
    fig2.savefig(sub / "profile.png", dpi=150)  # 【可调】dpi 同图 1：清晰度与文件大小的权衡

    print(f"[OK] {path.name} -> {sub.resolve()}/image.png, profile.png")


def main() -> None:

    # ── 第 1 步：读懂用户敲的命令（argparse）──
    # 用户敲 python scripts/view_diffraction.py --file a.tif --center 1,2
    # 时，argparse 会把它拆成 args.file、args.center 等变量。
    parser = argparse.ArgumentParser(description="View a 2D diffraction image and plot a line profile through the center")
    parser.add_argument("--file",
                        help="Path to a diffraction image file (.edf/.tif/.cbf, etc.). "
                             "If not given, an interactive menu lists files in data/ and lets you pick.")
    parser.add_argument("--datadir", default="data",
                        help="Folder scanned by the interactive menu (only used without --file), default data/")
    parser.add_argument("--angle", type=float, default=0.0,
                        help="Angle between the profile line and the horizontal axis (degrees), default 0")
    parser.add_argument("--center",
                        help="Ring center pixel coordinates cx,cy (e.g. 1020,1024); "
                             "if not given, the center is auto-detected from the image "
                             "(180-degree rotation cross-correlation)")
    parser.add_argument("--outdir", default="outputs",
                        help="Base output directory, default outputs/ (each file is saved into outputs/{filename}/)")
    parser.add_argument("--vmin", type=float, default=None,
                        help="Color scale lower limit; if not given, auto = 1st percentile of the image")
    parser.add_argument("--vmax", type=float, default=None,
                        help="Color scale upper limit; if not given, auto = 99.9th percentile of the image")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(exist_ok=True)  # 目录不存在就创建；exist_ok 让已存在时不报错

    # ── 决定要跑哪些文件 ──
    if args.file:
        # 指定了 --file：只跑这一个（原来的用法，行为不变）
        file_list = [Path(args.file)]
    else:
        # 没指定 --file：进入交互菜单，让用户按编号选
        file_list = interactive_pick_files(Path(args.datadir))

    # for 循环：把选中的每个文件依次交给 process_one_file 处理
    for path in file_list:
        process_one_file(path, args, outdir)

    plt.show()  # 所有图画完后统一弹窗显示；保存的 PNG 不受影响


# ══ 程序入口 ════════════════════════════════════════════════════════
if __name__ == "__main__":
    main()
