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

from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.core.processor import line_profile


# 支持的衍射数据扩展名（交互菜单只列出这些格式的文件）。
# 集合 set：花括号 {} 包起来的一堆值，特点是"查找快、不重复"。
SUPPORTED_EXTS = {".tif", ".tiff", ".edf", ".cbf"}


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


def interactive_pick_files(datadir: Path) -> list[Path]:
    """交互式选择要跑的文件：列出 datadir 里的数据文件，让用户按编号选.

    返回选中的文件路径列表。用户可输入：
        2       → 只跑第 2 个
        1,3     → 跑第 1 和第 3 个
        all     → 全部跑
    """
    # iterdir() 逐个列出文件夹里的条目；suffix 是扩展名（含点），.lower() 转小写
    # （防止 .TIF 大写被漏掉）；sorted 按名字排序，让编号顺序稳定
    files = sorted(p for p in datadir.iterdir() if p.suffix.lower() in SUPPORTED_EXTS)

    if not files:
        print(f"在 {datadir}/ 里没有找到数据文件（支持 {', '.join(sorted(SUPPORTED_EXTS))}）")
        raise SystemExit(1)  # 没有可跑的文件，直接退出程序（退出码 1 = 出错）

    print(f"{datadir}/ 里找到 {len(files)} 个数据文件：")
    # enumerate(..., start=1)：给文件编号，从 1 开始
    # （人习惯从 1 数，程序内部习惯从 0 数，这里在"给人看"的环节用 1）
    for i, p in enumerate(files, start=1):
        print(f"  [{i}] {p.name}")

    # while True = 无限循环，直到拿到合法输入才 return 跳出
    while True:
        raw = input("输入要跑的编号（如 1,2 或 all）: ").strip()
        if raw.lower() == "all":
            return files
        try:
            picks = []
            # replace("，", ",")：把中文逗号换成英文逗号，两种写法都能认
            for part in raw.replace("，", ",").split(","):
                part = part.strip()
                if not part:
                    continue  # 跳过空段（比如输入 "1,," 里的空）
                n = int(part)
                if not 1 <= n <= len(files):
                    raise ValueError  # 编号超出范围 → 抛错，跳到 except
                picks.append(files[n - 1])  # 人的编号 1 → 列表下标 0
            if picks:
                return picks
        except ValueError:
            pass  # 非法输入：走不到 return，落到下面的提示，再问一遍
        print(f"输入不合法，请输入 1~{len(files)} 的编号（多个用逗号隔开）或 all")


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
    print(f"\n=== {path.name} ===  Image size: {w} x {h}, intensity range: {data.min():.1f} ~ {data.max():.1f}")

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

    # 标题分两行：文件名很长，一行放不下会超出画布被裁掉
    ax.set_title(f"2D Diffraction Image\n({tag}, log scale)")
    ax.set_xlabel("Detector pixel X (px)")
    ax.set_ylabel("Detector pixel Y (px)")
    # 显式指定刻度：防止自动刻度跑到数据范围外、把文字挤出画布
    # 刻度间隔自动取：图像 2048 → 500；图像 1000 → 200；不管多大都有约 5 个刻度
    step_x = max(1, int(round(nice_step(w))))
    step_y = max(1, int(round(nice_step(h))))
    ax.set_xticks(range(0, w, step_x))
    ax.set_yticks(range(0, h, step_y))

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
                        help="Ring center pixel coordinates cx,cy (e.g. 1020,1024); defaults to the image geometric center")
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
