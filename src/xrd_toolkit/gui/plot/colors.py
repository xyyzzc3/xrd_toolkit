"""对比/热图配色：色系列表、混色、渐变取色。

2026-10-21 从 panel_state.py 原样搬出（只改结构，不动逻辑）。
panel_state 再导出，plot_compare / customize / app 照旧经 panel_state 取用。
"""


# 一种颜色的渐变过程……只留渐变顺序，颜色这种选项"——原先的 8 槽分类
# 色板与"逐条自定义色"一起退休，用户原话："不是每个数据都能改颜色，
# 非常无效"）。每系一个基色（"蓝"沿用原来第一个文件的蓝，老用户的
# 视觉是连续的）；多条曲线从"基色加深一档"过渡到"基色与白混 55%"
# （两头各见下面两个常量；单条曲线仍用基色本身）。
_GRADIENT_FAMILIES = {
    "蓝": "#2a78d6",
    "绿": "#1baf7a",
    "橙": "#eb6834",
    "紫": "#4a3aa7",
    "灰": "#595959",
}
_GRADIENT_LIGHT_MIX = 0.55   # 最浅一条 = 基色与白混到 55%（再浅就看不见）
# 最深一头的加深量（2026-10-14 加，用户："彩虹没问题，[其余色系]是对的，但是
# 不明显"——实测 81 条橙色从底到顶只走了"基色→55% 白"这半段，肉眼一片同色）。
# 现在跨度拉成整整一段：第 1 条 = 基色与黑混 _GRADIENT_DARK_MIX，最后一条
# 不变（55% 白，保住"最浅的白底可辨"），中间线性过渡——8 条时每条明显拉开，
# 大堆叠也比原来有层次。
_GRADIENT_DARK_MIX = 0.30

# 第六个色系「彩虹」（2026-10-14 用户："对比图不堆叠时颜色很难区分，要不要
# 改成瀑布图的配色那种"＋"除了不要循环的颜色，按顺序的数据能获得有规律的
# 按顺序颜色就可以"）：不走"基色混白"，而是**瀑布同一张色图 viridis 全段
# 等距取样**（平铺 8 条实测：单色系八条挤在浅蓝里分不出，viridis 深紫→黄
# 一条一色一眼认得出；81 条也能读出顺序）。它不是被退休的那套分类色
# （红绿蓝随便给），是顺序色——颜色严格跟序号走、不循环。
_RAINBOW_FAMILY = "彩虹"
_RAINBOW_SINGLE_POS = 0.5    # 单曲线（1D/剖面/只有一条对比）取中段青绿

# 第七个色系「经典色」（2026-10-15 用户："对比图添加论文中常用颜色"）：
# Okabe–Ito 8 色——期刊图表规范最常点名的那套（色盲友好），顺序按
# 相邻对比度排过（预览图给用户看过就是这个顺序）。它不是渐变、也
# 不是"彩虹"那种顺序色，是**固定串色**：前 8 条一色一条，互不重复；
# 超过 8 条**每轮整体压暗一档**（×0.75^轮，见 _PAPER_DARK_*）——不与
# 上一轮任何一条同色、顺序仍然读得出（"不循环"精神与彩虹一致）；
# 黑色压不动，改往白提亮（见 _CLASSIC_DARK_BRIGHT_FLOOR）。
_CLASSIC_FAMILY = "经典色"
_CLASSIC_COLORS = ("#0072B2", "#D55E00", "#009E73", "#E69F00",
                 "#56B4E9", "#CC79A7", "#F0E442", "#000000")
_CLASSIC_DARK_PER_ROUND = 0.75
_CLASSIC_DARK_FLOOR = 0.15     # 压暗到底线就不再深（再深全成黑）
_CLASSIC_DARK_BRIGHT_FLOOR = 0.22   # 亮度低于它 = 压不动了（黑色那格）
_CLASSIC_LIGHT_CAP = 0.5            # 压不动的改往白提亮，混白 50% 封顶

# UI 下拉按这个顺序列色系（app.py 参数坞 / customize.py「外观」共用）
_GRADIENT_FAMILY_ORDER = (*_GRADIENT_FAMILIES, _RAINBOW_FAMILY,
                          _CLASSIC_FAMILY)

def _family_label(name: str) -> str:
    """色系下拉的显示名。"经典色"不是渐变（固定串色 + 压暗轮换），
    直接用名字；其余照旧"X色渐变"（含彩虹）。"""
    return name if name == _CLASSIC_FAMILY else f"{name}色渐变"

def _mix_hex(a: str, b: str, t: float) -> str:
    """两个 #rrggbb 按 t 线性混合（0 = 全 a，1 = 全 b）。"""
    ar, ag, ab = (int(a[i:i + 2], 16) for i in (1, 3, 5))
    br, bg, bb = (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(
        f"{int(round(x + (y - x) * t)):02x}"
        for x, y in ((ar, br), (ag, bg), (ab, bb)))

def _gradient_color(family: str, i: int, n: int) -> str:
    """第 i 条（共 n 条）曲线的渐变色。

    单色系（蓝/绿/橙/紫/灰）：从"基色加深一档"线性过渡到"基色与白混 55%"
    （一头一尾见 _GRADIENT_DARK_MIX / _GRADIENT_LIGHT_MIX）——第 1 条最深、
    最后一条最浅。「彩虹」：与瀑布同款——viridis 全段等距（i 从 0 到 n-1
    恰好走 linspace(0, 1, n) 那几档），顺序 = 序号、不循环，平铺压线时比
    单色系多一个色相辨认通道。「经典色」：Okabe–Ito 8 色固定串色（按
    序号一色一条，见 _CLASSIC_COLORS），超过 8 条每轮整体压暗一档
    （_CLASSIC_DARK_PER_ROUND）——不与上一轮同色、顺序仍读得出。

    n <= 1（单曲线面板 / 只有一条对比曲线）给一个稳妥的单曲线色：单色系
    用基色本身（1D/剖面拿它当"这条曲线的颜色"），彩虹取中段，经典色取
    色 1。i 越界夹回范围（防御）；未知色系兜底蓝。
    """
    if family == _RAINBOW_FAMILY:
        # 延迟导入：panel_state 是最底层共享层，模块级不拉 matplotlib
        from matplotlib import colormaps
        from matplotlib.colors import to_hex
        pos = _RAINBOW_SINGLE_POS if n <= 1 else min(max(i / (n - 1), 0.0), 1.0)
        return to_hex(colormaps["viridis"](pos))
    if family == _CLASSIC_FAMILY:
        if n <= 1:
            return _CLASSIC_COLORS[0]
        i = min(max(i, 0), n - 1)   # 越界夹回（防御，同彩虹）
        color = _CLASSIC_COLORS[i % len(_CLASSIC_COLORS)]
        rnd = i // len(_CLASSIC_COLORS)   # 第几轮（0 = 原色）
        if rnd:
            factor = max(_CLASSIC_DARK_FLOOR, _CLASSIC_DARK_PER_ROUND ** rnd)
            bright = max(int(color[j:j + 2], 16)
                         for j in (1, 3, 5)) / 255.0
            if bright * factor < _CLASSIC_DARK_BRIGHT_FLOOR:
                # 黑色那格压不动：改往白提亮（混白 50% 封顶），免得
                # 每轮长得一模一样（"不与上一轮同色"要真成立）
                color = _mix_hex(color, "#ffffff",
                                 min(1.0 - factor, _CLASSIC_LIGHT_CAP))
            else:
                color = _mix_hex(color, "#000000", 1.0 - factor)
        return color
    base = _GRADIENT_FAMILIES.get(family) or _GRADIENT_FAMILIES["蓝"]
    if n <= 1:
        return base
    t = min(max(i / (n - 1), 0.0), 1.0)
    return _mix_hex(_mix_hex(base, "#000000", _GRADIENT_DARK_MIX),
                    _mix_hex(base, "#ffffff", _GRADIENT_LIGHT_MIX), t)

