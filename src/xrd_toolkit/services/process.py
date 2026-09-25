"""处理链：背景扣除 → 平滑 → 裁剪（纯数组函数，无 Qt）。

三项处理共用一条**链**（界面「处理」页的三节，每一项都可以关掉）：

    背景扣除 → 平滑 → 裁剪

顺序固定，理由在两端边界上：

* **锚点在裁剪前取样**——先裁掉 2–3° 的话，落在里面的锚点会取到"空"，
  整条基线跟着报废；
* **平滑看不到空洞**——先把完整曲线平滑好、最后再挖掉那一段，切口边缘
  干净；反过来就是"窗口一半是空值"的边缘处理，每张图的边缘都会悄悄
  不一样（看不太出来，但结果不再可比）。

裁剪**不删点**、只把区间内的强度标成"空"（NaN）：网格保持不变，各文件的
2θ 轴仍然对齐（对比 / 热图 / CSV 总表都依赖这一点）。图上 matplotlib 遇到
空值会自动断线，纵轴自动范围本来就会跳过无效值（`_auto_y_range` 第一步
就筛掉非有限值）——"剪掉一段让其余看得清"因此不需要额外画图代码。

屏幕、产物与导出共用这一份定义：界面画图走 `apply_chain`，产物键由
`chain_parts` 算（见 services/stage_cache.proc_settings_hash），导出写文件
时按同一份设置跳掉空值行并注明链（gui/plot_export）。
"""
import numpy as np

from xrd_toolkit.services.background import (
    _window_to_points, compute_baseline, subtract_background)


def smooth_boxcar(tth, intensity, window_deg: float) -> np.ndarray:
    """滑动平均（最简单的平滑）。返回新数组，不修改入参。

    窗口宽度按 **2θ（度）** 给，内部换算成点数（与背景扣除的窗口共用
    `background._window_to_points`）——所以改"输出点数"不会改变"平滑了
    多宽"，这在换点数重算之后仍然可比。

    两个细节：偶数窗口补成奇数（对称窗口不引入半点位移）；边缘按"实际
    参与的点数"归一化，不做补零——补零会把曲线两端压出一个假凹陷，而
    那正好是衍射谱低角端最需要看清的地方。
    """
    y = np.asarray(intensity, dtype=float)
    deg = float(window_deg or 0.0)
    n = _window_to_points(tth, deg) if deg > 0 else 1
    if n <= 1 or y.size < 3:
        return y.copy()
    if n % 2 == 0:
        n += 1
    kernel = np.ones(n, dtype=float)
    num = np.convolve(y, kernel, mode="same")
    den = np.convolve(np.ones_like(y), kernel, mode="same")
    return num / den


def cut_ranges(tth, intensity, ranges) -> np.ndarray:
    """把落在指定 2θ 区间里的点标成"空"（NaN）。返回新数组。

    ranges = [(起, 止), ...] 度；起 ≥ 止 的区间忽略。空表 = 原样返回。
    只标空、不删点：2θ 网格与各文件的长度都不变（对比 / 热图 / CSV 的
    对齐逻辑不用改）。
    """
    y = np.asarray(intensity, dtype=float).copy()
    t = np.asarray(tth, dtype=float)
    for lo, hi in (ranges or []):
        lo, hi = float(lo), float(hi)
        if not hi > lo:
            continue
        y[(t >= lo) & (t <= hi)] = np.nan
    return y


def apply_chain(tth, intensity, params: dict, *, blank_curve=None):
    """跑整条链：返回 (处理后的强度, 基线或 None)。不修改入参。

    params = gui/panel_state._proc_params 的产物（背景那几项与
    services/background.compute_baseline 同口径，另加）：

        smooth_deg   : 平滑窗口宽度（度）；0 = 不平滑
        cut_ranges   : [(起, 止), ...] 度；空 = 不裁剪
        clip         : 扣背景后负值是否截断为 0

    基线按**平滑前**的曲线算、也只画不平滑的那条（`_draw_bg_overlay` 的
    辅助线），所以返回的 base 与画图用的强度不是同一条——这是有意的：
    辅助线的作用是让你看清"扣了多少"，把基线也平滑只会掩盖它。
    """
    t = np.asarray(tth, dtype=float)
    y = np.asarray(intensity, dtype=float)
    base = compute_baseline(t, y, params, blank_curve=blank_curve)
    if base is not None:
        y = subtract_background(y, base,
                                clip_negative=bool(params.get("clip")))
    y = smooth_boxcar(t, y, params.get("smooth_deg") or 0.0)
    return cut_ranges(t, y, params.get("cut_ranges")), base


def chain_parts(settings: dict) -> dict:
    """链里"背景之外"的部分（规范化后），用来算产物键与元数据。

    只在**开着**的时候出现：平滑 0 度、没有裁剪区间 = 空字典 → 产物键与
    "只有背景扣除"的老产物逐位一致（老缓存继续命中，见 stage_cache.
    proc_settings_hash 与其测试）。
    """
    out = {}
    deg = float(settings.get("smooth_deg") or 0.0)
    if deg > 0:
        out["smooth_deg"] = round(deg, 6)
    cuts = sorted([round(float(lo), 6), round(float(hi), 6)]
                  for lo, hi in (settings.get("cut_ranges") or [])
                  if float(hi) > float(lo))
    if cuts:
        out["cut_ranges"] = cuts
    return out


def _cut_text(ranges) -> str:
    """"2–3°" / "2–3°、7–8°"。"""
    return "、".join(f"{float(lo):g}–{float(hi):g}°" for lo, hi in ranges)


def chain_label(settings: dict) -> str:
    """给人看的链描述（产物组名的括号里那一段）："锚点 5 个、窗口 2°、平滑 0.15°、删 2–3°"。

    组名 = f"处理后 {时间}（{chain_label(...)}）"。只写**真的作用在数据上**
    的东西——三项现在都进产物，所以三项都写；以后若加"只看图"的开关，
    它也绝不能出现在这里（组名必须描述数据本身）。
    """
    mode = settings.get("mode")
    if mode == "anchor":
        parts = [f"锚点 {len(settings.get('anchors') or [])} 个"]
    elif mode == "blank":
        parts = ["空扫相减"]
    elif mode == "off":
        parts = []
    else:
        parts = ["自动基线"]
    if mode in ("anchor", "auto"):
        parts.append(f"窗口 {float(settings.get('window_deg') or 0):g}°")
    if settings.get("clip") and mode != "off":
        parts.append("负值截断")
    deg = float(settings.get("smooth_deg") or 0.0)
    if deg > 0:
        parts.append(f"平滑 {deg:g}°")
    cuts = [(lo, hi) for lo, hi in (settings.get("cut_ranges") or [])
            if float(hi) > float(lo)]
    if cuts:
        parts.append(f"删 {_cut_text(cuts)}")
    return "、".join(parts) if parts else "未做处理"


def chain_desc(settings: dict) -> str:
    """机器可读的链描述（写进产物元数据/日志）：

        bg=anchor(n=5)/win=2° → smooth=0.15° → cut=2–3°、7–8°
    """
    steps = []
    mode = settings.get("mode") or "off"
    if mode != "off":
        head = f"bg={mode}"
        if mode == "anchor":
            head += f"(n={len(settings.get('anchors') or [])})"
        if mode in ("anchor", "auto"):
            head += f"/win={float(settings.get('window_deg') or 0):g}"
        if settings.get("clip"):
            head += "/clip"
        steps.append(head)
    deg = float(settings.get("smooth_deg") or 0.0)
    if deg > 0:
        steps.append(f"smooth=boxcar/{deg:g}°")
    cuts = [(lo, hi) for lo, hi in (settings.get("cut_ranges") or [])
            if float(hi) > float(lo)]
    if cuts:
        steps.append(f"cut={_cut_text(cuts)}")
    return " → ".join(steps) if steps else "none"
