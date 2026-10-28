"""对比图辅助：短标签、图例、颜色刻度、标题、来源混合。

2026-10-21 从 plot/compare.py 原样搬出（只改结构，不动逻辑）。
compare.py 再导出，plot_compare 与 heatmap 照旧经它取用。
"""
import os
import re
from pathlib import Path

import numpy as np
from matplotlib.patches import Rectangle
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.panel_state import _gradient_color, _log, _panel_param
from xrd_toolkit.gui.plot.curves import _ready_curve


# 对比面板图例最多列几条：再多就不画了（挡住图的是那 81 行名字）。
# 用户 2026-09-26："对比的图例还是影响看图，太多了"。
LEGEND_MAX_CURVES = 12

# 剥公共后缀的门槛（".tif" 4 个字符）：够长才剥，且剥完不能空
MIN_SUFFIX_CHARS = 4

# 色标的 gid 标记：测试与探针按它认领这些图元（不靠位置猜）
COLOR_SCALE_TAG = "compare_color_scale"

_COLOR_SCALE_STEPS = 32   # 渐变条画多少段（段间轻微重叠防白缝）

def _strip_common(names) -> list:
    """剥掉一组名字共同的前缀（在最后一个非数字字符处切断）与共同后缀。

    81 个 "LMFP_1_atten0-00029.tif" 里真正有信息的是 "00029"：共同前缀在
    纵轴上占掉半张图、还糊成一条黑带（用户 2026-09-26 的热图截图）。前缀
    在**最后一个非数字字符之后**切断，号段位数不丢；后缀（如 ".tif"）整段
    剥掉——**同样不许吃数字**（下面补的那道护栏；用户 2026-10-15 晚实测：
    "00029.tif" 与 "00189.tif" 的公共尾巴是 "9.tif"，剥完变成 "0002"/
    "0018"，色标两端、堆叠纵轴名与图例都受影响）。名字不足两个、或剥完
    会空则原样返回。
    """
    if len(names) < 2:
        return list(names)
    prefix = os.path.commonprefix(list(names))
    cut = max((i + 1 for i, ch in enumerate(prefix) if not ch.isdigit()),
              default=0)
    prefix = prefix[:cut] if cut else ""
    # 剥前缀的两个条件：切在分隔符处（"LMFP_1_atten0-" 这样的批次头），
    # 且剥完每个名字都以**数字**开头——我们要的就是那个号段。于是
    # "fake_a/fake_b"（剥完是 a/b）不会被剥，反而更好认。
    out = list(names)
    if prefix and prefix[-1] in "-_. " and all(
            n[len(prefix):len(prefix) + 1].isdigit() for n in names):
        out = [n[len(prefix):] for n in names]
    suffix = os.path.commonprefix([n[::-1] for n in out])[::-1]
    # 后缀护栏（同前缀"切在分隔符处"的道理）：公共后缀若以数字打头，那
    # 数字是各名字号段的尾巴（"…029.tif"/"…189.tif" 共享结尾 "9"），
    # 剥掉就啃掉一位号段——截到第一个非数字为止，只留 ".tif" 这样的
    # 真后缀。号段仍完整（"00029"/"00189"），重复尾号（29 与 29）也留得住。
    i = 0
    while i < len(suffix) and suffix[i].isdigit():
        i += 1
    suffix = suffix[i:]
    if len(suffix) >= MIN_SUFFIX_CHARS and all(len(n) > len(suffix)
                                               for n in out):
        out = [n[:-len(suffix)] for n in out]
    return out

def _short_labels(names, ax) -> list:
    """纵轴标签 = 短名 + 按可用高度抽稀（同瀑布图 χ 刻度那一招）。

    热图一次可能摆 81 个样品：名字糊成黑带之外，整个图还会被标签挤到右边。
    抽稀按面板高度估（英寸 × dpi），每个标签留 ~11 px；放得下就全标。
    完整名字仍在面板标题、悬停读数与导出里。
    """
    short = _strip_common(list(names))
    n = len(short)
    if not n:
        return []
    h_px = float(ax.figure.get_size_inches()[1]) * float(ax.figure.dpi)
    every = max(1, int(np.ceil(n * 11.0 / max(h_px * 0.75, 1.0))))
    return [s if k % every == 0 else "" for k, s in enumerate(short)]

def _draw_compare_legend(window, dock, ax, labels) -> None:
    """对比面板图例：先看「显示数据名」（默认关），再看条数放不放得下。

    2026-10-08：名字由「对比」页的 [显示数据名] 勾选控制——默认一个不画
    （名字多了糊成黑带，见 panel_state._DISPLAY_DEFAULTS 的说明）；勾上
    后超过 LEGEND_MAX_CURVES 仍不画（它挡图，日志说清）。画得下时用
    **短名**（剥共同前后缀）+ 多列排（每列约 8 条）；条数变了才记一次
    日志（重画不刷屏）。认曲线不靠图例也行：悬停读数本来就报曲线名，
    圆点颜色与曲线一致。
    """
    if not _panel_param(window, dock, "显示数据名", False):
        dock._legend_off_n = None   # 关着不算"提示过"：重新勾上要能再提示
        return
    n = len(labels)
    if n > LEGEND_MAX_CURVES:
        if getattr(dock, "_legend_off_n", None) != n:
            dock._legend_off_n = n
            _log(window, f"对比图例：{n} 条曲线太多、挡住图了，不画"
                         f"（{LEGEND_MAX_CURVES} 条以内才画）。点热图某一行"
                         "可隐藏/显示几条；曲线名看状态栏的悬停读数"
                         "（圆点颜色与曲线一致）")
        return
    dock._legend_off_n = None
    shown = [lb for lb in labels if lb]
    if not shown:
        return
    ax.legend(labels=_strip_common(shown), fontsize=8,
              ncols=max(1, int(np.ceil(len(shown) / 8.0))))

def _draw_compare_color_scale(ax, family, names, caption, corner) -> None:
    """对比图的颜色刻度条：给"颜色跟数据对应"这条规则一个读数。

    用户 2026-10-15："之前说的颜色变化代表从哪个数据到哪个数据，
    怎么没落地"——颜色跟数据走是 2026-10-13/14 就定的**规则**，但图上
    一直没有东西把"颜色 ↔ 哪条数据"标出来；平铺超过 12 条又不画图例
    （2026-10-08 定：81 条全名糊成黑带），83 条叠在一起时肉眼只剩悬停
    读数一条路。**画不画 = 「显示色标」开关**（当天晚些用户："把这个
    改为显示图例的选项，默认不显示"），判定在 _redraw_compare。

    **顺序 = 文件名尾号**（用户当天晚些："识别的不对，应该是文件名最后
    几位数的顺序"；颜色分配见 _color_rank_by_number）——`names` 由调用
    方**按色号排好**传进来（最小值在左端、最大值在右端）。`caption` =
    那行说明文字（默认"颜色按文件名尾号顺序"；在「外观…」里改，空串 =
    不画这行字）。`corner`（用户 2026-10-16："实现色标仅保留横向以及
    可以选择放在图的哪个角落"——同日的横竖二选在发版前退休，固定横版）：
    卡片放图的哪个角，四选 "右上"（默认，用户 2026-10-16 当天改）/ "右下" /
    "左下" / "左上"；未知值兜底默认角（老快照/手改文件不会把图弄坏）。

    画法：一个白底小框（框住曲线，与图例同理），里面一条**按当前色系
    按真实条数取样**的渐变条（彩虹=viridis 全段、单色系深→浅、经典色
    含每轮压暗），两端各一个**短名**。全部图元挂 `transform=ax.transAxes`
    ——不落数据坐标、不碰自动量程（mpl 对与 transData 无分支关系的 patch
    直接跳过 _update_patch_limits），`ax.clear()` 一并清干净。平铺/堆叠
    两种模式都画（堆叠下名字默认关，色标正好补上读数）。
    """
    names = [n for n in names if n]
    n = len(names)
    if n < 2:
        return
    if corner not in ("右下", "左下", "右上", "左上"):
        corner = "右上"          # 未知值（老快照/手改文件）兜底默认角
    short = _short_labels([names[0], names[-1]], ax)
    first, last = short[0], short[-1]
    trans = ax.transAxes
    # 全部位置用轴分数（0..1），数值都在对比图的数据范围之内，
    # 即便有意外也扩不到自动量程。尺寸按**开局面板**（5:3、约 400 px 宽）
    # 估过：横卡片 0.40 ≈ 158 px（横排那行字约 80 px，两侧留空）。
    # 四角定位：左右边距 0.02、下边距 0.28（避开 x 轴刻度与轴名）、
    # 上边距 0.02——卡片内部布局不随角落变，永远横版。
    box_x = 0.58 if corner in ("右下", "右上") else 0.02
    box_y = 0.28 if corner in ("右下", "左下") else 0.79
    box = (box_x, box_y, 0.40, 0.19)
    strip = (box_x + 0.02, box_y + 0.125, 0.36, 0.045)
    ax.add_patch(Rectangle((box[0], box[1]), box[2], box[3],
                           transform=trans, facecolor="white",
                           edgecolor="0.8", linewidth=0.5, zorder=4.8,
                           gid=COLOR_SCALE_TAG))
    for k in range(_COLOR_SCALE_STEPS):
        t = int(round(k * (n - 1) / (_COLOR_SCALE_STEPS - 1)))
        color = _gradient_color(family, t, n)
        seg_w = strip[2] / _COLOR_SCALE_STEPS
        ax.add_patch(Rectangle((strip[0] + k * seg_w, strip[1]),
                               seg_w + 0.001, strip[3],
                               transform=trans, facecolor=color,
                               edgecolor="none", zorder=5.0,
                               gid=COLOR_SCALE_TAG))
    ax.add_patch(Rectangle((strip[0], strip[1]), strip[2], strip[3],
                           transform=trans, facecolor="none",
                           edgecolor="0.6", linewidth=0.5, zorder=5.1,
                           gid=COLOR_SCALE_TAG))
    # 左端 = 色号最小（深）、右端 = 最大；说明文字横排在两端短名下面
    ax.text(strip[0], strip[1] - 0.012, first, transform=trans,
            ha="left", va="top", fontsize=6.5,
            zorder=5.2, gid=COLOR_SCALE_TAG)
    ax.text(strip[0] + strip[2], strip[1] - 0.012, last, transform=trans,
            ha="right", va="top", fontsize=6.5,
            zorder=5.2, gid=COLOR_SCALE_TAG)
    if caption:
        ax.text(box[0] + box[2] / 2, box[1] + 0.03, caption,
                transform=trans,
                ha="center", va="bottom", fontsize=6.5, color="0.25",
                zorder=5.2, gid=COLOR_SCALE_TAG)

def _trailing_key(source) -> tuple:
    """排序键：文件名里**最后一串数字**（"LMFP_1_atten0-00029.tif" → 29）。

    取不到数字的名字排在最后（键第二段 1 > 0），同一串数字按文件栏
    先后稳定排（调用方带下标兜底）。产物条目的路径指向源文件，跟它的
    原始条目拿到同一个号——同一条数据的两个视图颜色一致。
    """
    nums = re.findall(r"\d+", Path(str(source.path)).stem)
    return (0, int(nums[-1])) if nums else (1, 0)

def _color_rank_by_number(files) -> dict:
    """按文件名尾号给色号：{compare_files 下标 → 颜色序号}。

    用户 2026-10-15 晚："识别的不对，应该是文件名最后几位数的顺序"——
    颜色跟"哪条数据"的对应按文件名尾号排（编号最小的拿最深的色），
    与文件栏怎么排无关（排只影响行/图例的位置）。热图隐藏不重排色号
    的老规矩照旧：色号按**全部** files 算，显不显示不改变谁是什么色。
    """
    order = sorted(range(len(files)),
                   key=lambda k: (_trailing_key(files[k]), k))
    return {k: r for r, k in enumerate(order)}

def _sources_mix(files) -> str:
    """"原始 2 ｜ 1D 产物 2 ｜ 处理后 2"——这批勾选由什么构成。

    用户 2026-09-27/28 连着两次被"勾选集比想的大"咬到（162 条、243 条）：
    混着几类勾选时，图上画了多少条、都是什么，必须写在标题与日志里。
    """
    kinds = {}
    for s in files:
        kinds[s.kind] = kinds.get(s.kind, 0) + 1
    return " ｜ ".join(f"{text} {kinds[kind]}" for kind, text in
                      ((gui_sources.RAW, "原始数据"), (gui_sources.ONED, "1D 产物"),
                       (gui_sources.BG, "处理产物")) if kinds.get(kind))

def _compare_title(files) -> str:
    """对比面板标题：两个文件 = A vs B；更多 = A 等 N 个文件。

    **混着几类时才把构成写进标题**（"（原始 1 ｜ 处理后 1）"）——单一类的
    那批标题保持干净，也免得另存名被撑长。
    """
    displays = [s.display for s in files]
    if len(files) == 2:
        base = f"对比_{displays[0]}_与_{displays[1]}"
    else:
        base = f"对比_{displays[0]} 等 {len(files)} 个文件"
    kinds = len({s.kind for s in files})
    return f"{base}（{_sources_mix(files)}）" if kinds > 1 else base

def _preview_names(sources) -> str:
    """日志里点名的一小段名单："A" / "A 等 N 个"（同 _compare_title 惯例）。"""
    first = sources[0].display
    return first if len(sources) == 1 else f"{first} 等 {len(sources)} 个"

def _raw_not_ready(window, files) -> list:
    """勾选里"还没有 1D 曲线可用"的原始条目（栅栏判据，用户 2026-10-16）。

    判据与取数完全同一把尺（plot_views._ready_curve）：能取到就放行、
    取不到就拦。产物条目不在其列——它们本来就自带曲线（读不到是另一
    回事，走各自的"产物不存在"跳过）。
    """
    return [s for s in files
            if gui_sources.is_rawlike(s.kind)
            and _ready_curve(window, s.path, s.display) is None]

