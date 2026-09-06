#!/usr/bin/env python3
"""二维衍射图查看器：显示图像（颜色条 + 坐标轴标签）并绘制过圆心的线剖面。

用法（在项目根目录运行）：
    python scripts/view_diffraction.py --file data/LMFP_1_atten0-00029 copy.tif
    python scripts/view_diffraction.py --file data/你的文件.tif --center 1020,1024
    python scripts/view_diffraction.py --file data/你的文件.tif --angle 45

名词速查：
    像素 (px)      —— 探测器上的一个小格子，图上的最小单元
    强度 (counts)  —— 一个像素收到的 X 射线光子计数，越大越亮
    圆心/光斑中心  —— 所有衍射环共同的中心（直射光束打在探测器上的位置）
    线剖面          —— 沿一条直线把强度读出来，变成"位置 → 强度"的曲线
"""

# ══ 第 1 步：导入需要的工具包 ══════════════════════════════════════
# import 的意思是"把别人（或自己）写好的代码拿过来用"。
# 每行 import 都只拿我们真正用到的东西，没有多余导入。

import argparse                 # Python 自带的"命令行参数解析器"：读懂用户敲的命令
from pathlib import Path        # 处理文件路径的现代工具（比字符串拼路径好用）

import numpy as np              # 数值计算核心库；np.xxx 都是它的功能
from matplotlib.colors import LogNorm   # 画图时把强度转成"对数色标"
import matplotlib.pyplot as plt         # 画图库，plt.xxx 都是它的功能

from xrd_toolkit.config import settings                   # 自己的：全局配置
from xrd_toolkit.services.data_loader import load_diffraction_image  # 自己的：读图像
from xrd_toolkit.core.processor import line_profile       # 自己的：线剖面

# ══ 全局绘图设置 ════════════════════════════════════════════════════
# matplotlib 默认字体不含中文，不设置的话图上的中文标题/标签会变成方框。
plt.rcParams["font.sans-serif"] = ["PingFang SC", "Hiragino Sans GB",
                                   "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False  # 让坐标轴上的负号 "-" 正常显示


def main() -> None:
    """程序主流程：读命令 → 读图像 → 画两张图并保存。"""

    # ── 第 2 步：读懂用户敲的命令（argparse）──
    # 用户敲 python scripts/view_diffraction.py --file a.tif --center 1,2
    # 时，argparse 会把它拆成 args.file、args.center 等变量。
    parser = argparse.ArgumentParser(description="查看二维衍射图并绘制过圆心的线剖面")
    parser.add_argument("--file", required=True,
                        help="衍射图像文件路径（.edf/.tif/.cbf 等）")
    parser.add_argument("--angle", type=float, default=0.0,
                        help="剖面直线与水平方向夹角（度），默认 0")
    parser.add_argument("--center",
                        help="环圆心像素坐标 cx,cy（如 1020,1024）；不传默认图像几何中心")
    parser.add_argument("--outdir", default="outputs", help="PNG 保存目录，默认 outputs/")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(exist_ok=True)  # 目录不存在就创建；exist_ok 让已存在时不报错

    # ── 第 3 步：读图像 ──
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
        # 【知识点】生成器表达式：(处理(s) for s in 一串东西)
        # = "对这一串里的每个 s 做处理"的简写。
        # split(",") 把 "1020,1024" 按逗号切开 → ["1020", "1024"]，
        # float(s) 把字符串 "1020" 变成数字 1020.0。
        # 【知识点】元组：(cy, cx) 是"打包的一组值"，顺序不能乱。
        cx, cy = (float(s) for s in args.center.split(","))
        center = (cy, cx)

    h, w = data.shape  # 数组形状：h = 行数（高），w = 列数（宽）

    # 【知识点】f-string：字符串前加 f，花括号 {} 里的变量会被替换成它的值。
    # {data.min():.1f} 里的 :.1f 表示"保留 1 位小数"（浮点数格式化）。
    print(f"图像尺寸: {w} × {h}，强度范围: {data.min():.1f} ~ {data.max():.1f}")

    # ── 第 4 步：图 1 —— 衍射图本身 ──
    # subplots 创建"一张画布 fig + 一块画板 ax"，之后画东西都往 ax 上画。
    fig, ax = plt.subplots(figsize=(6, 5.5))

    # imshow：把二维数组画成彩色图。三个关键参数：
    #   cmap="viridis" : 用"蓝→绿→黄"的连续色带表示强度（色觉障碍者也分得清）
    #   norm=LogNorm() : 对数色标——真实衍射图弱环和强环差几万倍，
    #                    线性色标下弱环会全黑看不见
    #   origin="lower" : 数组第 0 行画在下方（数学坐标习惯，探测器也是这么摆放的）
    im = ax.imshow(data, cmap="magma", norm=LogNorm(vmin=100, vmax=50000), origin="lower")

    ax.set_title(f"2D Diffraction Image ({tag}, log scale)")
    ax.set_xlabel("Detector pixel X (px)")
    ax.set_ylabel("Detector pixel Y (px)")

    # 用红色十字标记剖面经过的圆心，方便核对位置对不对
    prof_cy, prof_cx = center if center is not None else (h / 2.0, w / 2.0)
    ax.axhline(prof_cy, color="r", linewidth=0.8, alpha=0.7)  # 过圆心的水平红线
    ax.axvline(prof_cx, color="r", linewidth=0.8, alpha=0.7)  # 过圆心的竖直红线
    ax.plot(prof_cx, prof_cy, "+", color="r", ms=14, mew=2)   # 圆心处的红色十字

    cbar = fig.colorbar(im, ax=ax, pad=0.02)   # 颜色条：把颜色和强度数值对应起来
    cbar.set_label("Intensity (counts)")

    fig.tight_layout()   # 自动收紧边距，防止标签被裁掉
    fig.savefig(outdir / f"{tag}_image.png", dpi=150)  # 存成 PNG，dpi 控制清晰度

    # ── 第 5 步：图 2 —— 过圆心的强度剖面 ──
    # line_profile 返回两个数组：
    #   t       —— 采样点到圆心的距离（像素，圆心左侧为负、右侧为正）
    #   profile —— 每个采样点的强度
    t, profile = line_profile(data, center=center, angle_deg=args.angle)

    fig2, ax2 = plt.subplots(figsize=(7, 3.5))
    ax2.plot(t, profile, linewidth=1.2)  # plot(x, y)：把点连成曲线
    ax2.set_title(f"Intensity Profile Through Center ({args.angle:g}° from horizontal)")
    ax2.set_xlabel("Distance from center (px) (negative = left, positive = right)")
    ax2.set_ylabel("Intensity (counts)")
    ax2.grid(True, alpha=0.3)  # 浅色网格线，方便读数
    fig2.tight_layout()
    fig2.savefig(outdir / f"{tag}_profile.png", dpi=150)

    print(f"图片已保存 → {outdir.resolve()}/")
    plt.show()  # 交互运行时弹窗显示；保存的 PNG 不受影响


# ══ 程序入口 ════════════════════════════════════════════════════════
# 这个 if 的意思是："只有直接运行这个文件时才执行 main()；
# 如果它被别人 import，就什么都不做"。这是 Python 的标准写法。
if __name__ == "__main__":
    main()
