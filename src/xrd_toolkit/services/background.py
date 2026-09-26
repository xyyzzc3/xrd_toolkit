# 1D 衍射曲线的背景扣除：空扫相减 / 自动基线 / 手动锚点。
#
# 物理依据：探测器只会计数，不区分"晶格衍射来的"和"空气散射来的"光子，
# 所以 I_总(2θ) = I_样品(2θ) + I_背景(2θ)。背景不含结构信息，减掉它，
# 剩下的峰高/峰面积才对应晶体学量（峰背比、峰面积、结晶度、Rietveld
# 各相比例都对背景定义敏感）。
#
# 背景来源：空气散射（低角最强、平滑衰减）、样品自身非晶漫散射（宽
# 鼓包）、荧光（各向同性、不随角度变 = 常数项）、探测器暗电流（常数、
# 与曝光成正比）、直射束尾/beamstop 光晕（极低角极陡）、光路材料散射。
# 本项目实测数据（LMFP 1–2° 中位数 1429 vs 9–10° 的 275，抬升 5.2 倍；
# lab6 3.8 倍）说明主导项是"平滑但会弯"的空气散射 + 非晶漫散射——
# 减常数无效，必须扣一条会弯的线。
#
# 三种模式 = 对"背景长什么样"的三个不同假设：
#   A 空扫相减（blank）  ：实测——把"没有样品的那个世界"拍一遍逐点减掉，
#                        一次扣净全部加性项。前提：几何/曝光一致。
#   C 自动基线（auto）   ：算法猜——假设背景比峰宽且平滑（滑动分位）。
#   B 手动锚点（anchor） ：人判断——分析者指出"这几处是纯背景"。
# 一句话：A 是测出来的，C 是算出来的，B 是看出来的。
#
# 空扫相减做在曲线层而不是图像层：积分是线性运算（把落在某个 2θ 环带
# 里的像素求和/取平均），几何与 binning 相同时
#   "两图相减再积分" ≡ "两图各自积分再相减"
# （pyFAI 的归一化/偏振因子对两张图是同一个乘性因子，自研 numpy 积分
# 路径同为 bincount 求和再除计数，都保持线性）。因此三种模式共用同一条
# 曲线层路径，空扫只需积分一次，且全部支持实时预览。
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.interpolate import CubicSpline, PchipInterpolator

# --- 自动基线的默认参数 -------------------------------------------------
# 自动基线默认窗口宽度（度）：多宽的一段算"背景"而不是"峰"。
# 0.3° 的来历（2026-09-26 晚 1.0° → 0.5°，2026-09-27 用户："自动的可以
# 默认 0.3 比如" → 0.3°）：窗口取峰宽的 3~10 倍（LMFP/lab6 峰半高宽
# 0.1~0.3°）时窗内多数点是背景；1.0° 会把**宽非晶鼓包**（1.6~2.6° 那个包，
# 正是样品自身的漫散射 = 背景）留在扣后曲线里（残留中位 34、最大 527）。
# 0.5° 残留中位 −1、最大 322，0.3° 残留 9/471、基线在峰下只抬高 149——
# 两者接近；取更小的窗口是为了配合"自动 + 锚点校正"（见
# _correct_with_anchors）：形状由自动给、尺度由锚点定，窗口小一点、
# 让自动少留一点鼓包更划算。
AUTO_WINDOW_DEG = 0.3
# 阶段 1 用的低分位：给一个**不会被峰抬高**的局部下限（可以偏低）。
AUTO_FLOOR_PERCENTILE = 20.0
# 阶段 2 迭代次数与噪声带宽度 k（掩掉"高于局部下限 + kσ"的峰点）。
AUTO_ITER = 2
AUTO_NOISE_K = 2.0


def _dtth_median(tth) -> float:
    """2θ 网格的典型步长（度）——把"窗口宽度（度）"换算成点数的桥梁。"""
    tth = np.asarray(tth, dtype=float)
    if tth.size < 2:
        return 1.0
    return float(np.median(np.diff(tth)))


def _window_to_points(tth, window_deg) -> int:
    """窗口宽度（度）→ 半窗点数 P（至少 1，最多 n//2 - 1）。"""
    n = len(np.asarray(tth))
    p = int(round(max(float(window_deg), 0.0) / max(_dtth_median(tth), 1e-12)))
    return max(1, min(p, n // 2 - 1)) if n >= 3 else 1


def interp_onto_grid(tth_src, i_src, tth_dst) -> np.ndarray:
    """
    把一条曲线重插到目标 2θ 网格上（空扫与样品网格不一致时用）。

    参数：
        tth_src, i_src : np.ndarray
            源曲线（要求 tth_src 单调递增）
        tth_dst : np.ndarray
            目标 2θ 网格

    返回：
        np.ndarray
            在 tth_dst 上的插值结果。**源曲线没覆盖到的区间返回 0.0**
            而不是把端点值平铺出去——平铺会凭空造出一个恒定背景，
            把没有实测依据的区间也扣掉；返回 0 等于"那一段不扣"，
            调用方应据此提示用户（空扫未覆盖的区间）。
    """
    tth_src = np.asarray(tth_src, dtype=float)
    i_src = np.asarray(i_src, dtype=float)
    tth_dst = np.asarray(tth_dst, dtype=float)
    out = np.interp(tth_dst, tth_src, i_src)
    outside = (tth_dst < tth_src[0]) | (tth_dst > tth_src[-1])
    out[outside] = 0.0
    return out


def estimate_baseline_snip(tth, intensity, window_deg) -> np.ndarray:
    """
    SNIP 自动基线（Statistics-sensitive Non-linear Iterative Peak-clipping）。

    **本项目未在 GUI 启用**：在真实 LMFP/lab6 数据上实测会**过度扣除
    ——基线整体塌向 0**。2θ=1° 处它给出基线 26，而该处实测曲线 1141、
    相邻 0.5° 箱的 20% 分位 1144：没有任何合理的"背景"定义等于 26。
    窗口 0.3°/0.5°/1°/2° 的相对误差分别为 43%/75%/96%/98%，且几乎全部
    是过度扣除（越宽的窗口塌得越狠）。原因有二：
      ① LMFP 背景在 2.2° 附近有一个宽鼓包（比邻近高约 1.9 倍）——SNIP
         把它当"峰"削掉，而它其实是背景的一部分；
      ② min 迭代从 P 递减到 1 共 P 次，对陡升型背景会级联塌陷。
    保留此实现供对照与后续研究（算法本身、其数学性质都有单测覆盖），
    自动基线请用 estimate_baseline_sliding。

    经典 XRD/光谱基线算法：先把强度做 log-log-sqrt 压缩（把强峰"压扁"，
    迭代削峰时背景才不会被峰拽高），再从半窗 P 递减到 1 做"取自己与
    两端均值的较小者"的迭代腐蚀，最后逆变换回来。

    形状保真性（实测，见 tests/test_background.py 的标定测试）：
      - **常数背景是精确不动点**（两端均值 = 中点，偏差 ~1e-13）；
      - 削的是"局部向上凸出"的部分。在**压缩后的 v 空间**里看：v 空间
        上凸的不动、下凹的被削。对数压缩会把 y 空间的线性斜坡变成 v
        空间的凹陷 → **线性斜坡会被明显削低**（实测中段 1700 被削到
        760）；而本项目真实数据的"指数衰减 + 窄峰"形状在 v 空间近乎
        线性 → 低角段平均误差仅 0.89 counts（真背景约 2000）。
    所以 SNIP 好不好用取决于背景形状在压缩空间里凹不凹，这正是自动法
    需要手动锚点模式兜底的原因。

    参数：
        tth : np.ndarray
            2θ 网格（度，单调递增）——只用来把 window_deg 换算成点数
        intensity : np.ndarray
            曲线强度。SNIP 的对数变换要求非负，积分数值上偶发的小负值
            先夹到 0（只影响基线估计；减法结果仍可能为负，由
            subtract_background 决定是否截断）
        window_deg : float
            窗口宽度（度）：多宽的一段算"背景"而不是"峰"

    返回：
        np.ndarray
            与 intensity 等长的基线，逐点 ≤ intensity 且 ≥ 0

    实现：迭代部分全切片向量化（5000 点 × P≈100 只需毫秒级），
    参数一变就能立刻重画——实时预览靠的就是这个。
    """
    if window_deg is None or float(window_deg) <= 0:
        raise ValueError(f"window_deg ({window_deg}) must be > 0")
    y = np.clip(_gap_filled(intensity), 0.0, None)   # NaN 先插值补齐
    n = y.size
    if n < 3:
        return np.zeros(n)

    # 前向：log(log(sqrt(y+1)+1)+1)，值域被压到 ~0.5 起，强峰不再主导
    v = np.log(np.log(np.sqrt(y + 1.0) + 1.0) + 1.0)
    out = v.copy()
    p = _window_to_points(tth, window_deg)
    for half in range(p, 0, -1):
        if 2 * half >= n:
            continue
        mid = slice(half, n - half)
        left = out[0:n - 2 * half]
        right = out[2 * half:n]
        out[mid] = np.minimum(out[mid], 0.5 * (left + right))
    # 逆变换：y = (exp(exp(v)-1) - 1)² - 1
    base = (np.exp(np.exp(out) - 1.0) - 1.0) ** 2 - 1.0
    return np.clip(base, 0.0, None)


def _gap_filled(y) -> np.ndarray:
    """把曲线里的非有限值（NaN）按有效点线性插值补上，供基线估计用。

    为什么要补：NaN 在本项目是**预期输入**——自研积分 `_integrate_1d_diy`
    对没有像素落入的 2θ 箱显式填 NaN（偏置摆法/束心偏心的默认路径），
    瀑布的坏扇区整行是 NaN。不补的话 NaN 会顺着窗口扩散：一个 NaN 点
    污染 ±半个窗口的基线（实测 200 点网格、1° 窗口 → 29/200 点为 NaN；
    真实 3000 点 + 1° 窗口 → ±167 点），扣完就出现一条无数据的带。

    补完返回的基线处处有限；减法时 NaN 位置仍是 NaN − 有限值 = NaN，
    "这里没有数据"如实保留，不会凭空造出强度。全 NaN 输入返回全 0
    （= 不扣）。
    """
    y = np.asarray(y, dtype=float)
    good = np.isfinite(y)
    if good.all():
        return y
    if not good.any():
        return np.zeros_like(y)
    idx = np.arange(y.size)
    return np.interp(idx, idx[good], y[good])


def _window_geometry(tth, window_deg, n):
    """窗口几何：半窗点数 half 与抽稀后的锚点下标 idx。

    锚点抽稀到半个窗口的 1/4 一个——再密也分辨不出更多细节（分辨率本来
    就被窗口宽度限制），只是白算。
    """
    half = max(1, int(round(float(window_deg) / max(_dtth_median(tth), 1e-12)
                            / 2.0)))
    width = min(2 * half + 1, n)
    half = width // 2
    step = max(1, half // 2)
    return half, np.arange(0, n, step)


def estimate_baseline_sliding(tth, intensity, window_deg, *,
                              percentile: float = AUTO_FLOOR_PERCENTILE,
                              n_iter: int = AUTO_ITER,
                              noise_k: float = AUTO_NOISE_K) -> np.ndarray:
    """
    滑动窗自动基线，两阶段（GUI 默认自动法）：

    阶段 1：每个位置取窗口内的低分位（AUTO_FLOOR_PERCENTILE）——一个
    **不会被峰抬高**的局部下限，允许偏低。

    阶段 2：把"高于局部下限 + kσ"的点判为峰点掩掉，剩下的点在窗口内做
    **最小二乘直线拟合、取窗口中心处的值**——直线才是背景水平（阶段 1
    的分位是"下限"不是"水平"；用窗口内直线而不是均值，是因为陡降段上
    掩峰会掩掉窗口高的一侧、均值因此落到真值以下，见下）。迭代两次让
    下限收敛到水平。

    为什么不能只用一阶段（实测标定，见 tests）：
      - 只用中位数：峰在窗内占比高时中位数被峰抬高。σ=0.3° 的宽峰在
        3° 窗口下中位数仍达真背景的 1.8 倍；窗口越小越糟（0.3° 窗口
        达 9.9 倍——基线整个骑在峰上）。
      - 只用低分位：贴到噪声下沿，把噪声本身也当背景扣掉。
    两阶段各取所长：分位不被峰抬高、局部线性给水平、掩膜去掉峰。

    为什么是"滑动"而不是"分箱"：分箱只在每箱中心给一个点，箱数一多就
    丢分辨率、一少就跟不上低角陡升；滑动窗口给的是整条曲线上的局部水平
    估计，分辨率只受窗口宽度限制，与采样点数无关。

    已知偏差（方向是故意选的）：窗口内的背景是**弯的**（低角陡降段正是
    如此），拿一段直线去代表它，估计会略高于窗口中心处的真背景 → 偏向
    **扣除不足**，残留一条可见的斜坡（实测中段 +15~+72，窗口越大越明显）。
    反过来，过度扣除会静默压低峰高、看起来还"更干净"，是更危险的错误
    方向。所以宁可偏高一点，让用户看得见、用锚点去修。

    参数：
        tth : np.ndarray
            2θ 网格（度，单调递增）
        intensity : np.ndarray
            曲线强度
        window_deg : float
            窗口宽度（度）：多宽的一段算"背景"而不是"峰"。应取最宽峰的
            3~10 倍；取小了峰会被当成背景留在基线里
        percentile : float
            阶段 1 的局部下限分位（默认 20）
        n_iter : int
            阶段 2 的迭代次数（默认 2，收敛足够）
        noise_k : float
            掩峰阈值取"局部下限 + kσ"（默认 2）

    返回：
        np.ndarray
            与 intensity 等长的基线，处处 ≥ 0
    """
    if window_deg is None or float(window_deg) <= 0:
        raise ValueError(f"window_deg ({window_deg}) must be > 0")
    tth = np.asarray(tth, dtype=float)
    y = _gap_filled(intensity)   # NaN 按有效点插值补齐（见 _gap_filled）
    n = y.size
    if n < 3:
        return np.zeros(n)

    half, idx = _window_geometry(tth, window_deg, n)
    # 两端用端点值填充（mode="edge"），窗口才能在边界处也凑满
    padded = np.pad(y, half, mode="edge")
    win = sliding_window_view(padded, 2 * half + 1)[idx]
    base = np.interp(tth, tth[idx], np.percentile(win, percentile, axis=1))

    for _ in range(max(0, int(n_iter))):
        resid = y - base
        sigma = 1.4826 * float(np.median(np.abs(resid - np.median(resid))))
        if not np.isfinite(sigma) or sigma <= 0:
            break
        # 掩膜是**逐点**判据（每个点跟**它自己位置**上的下限比，而不是跟
        # 窗口中心比——下降背景的左半边整体高于中心值，用中心值当参考会
        # 把整片背景误判成峰掩掉），所以能一次算好，再用前缀和给每个窗口
        # 做最小二乘。
        keep = y <= base + noise_k * sigma
        # 窗口内**直线拟合**、取窗口中心处的值（不是掩峰后取均值）。
        # 为什么：陡降段上掩峰会掩掉窗口高的一侧（那里的点都超过各自
        # 偏低的下限），均值因此落到真值以下——合成真值实测低角端偏
        # −139（1° 窗）/ −163（2°）/ −533（3°），而局部线性没有这个
        # 偏差（同口径回到 +3.7 / +1.0 / −9.3），平均绝对偏差也全线变好
        # （42→35、63→46、94→67）。这正是"局部常数 vs 局部线性"的经典
        # 差别，陡背景（低角空气散射）上最明显。
        # 向量化 = 五个前缀和数组各取一次差分 → 窗口内 Σ1、Σt、Σt²、Σy、
        # Σty → 解二元正规方程；10 万点 21 ms（逐窗 polyfit 的循环写法
        # 结果逐位相同，但点数一大就慢一个量级）
        k = keep.astype(float)
        c0 = np.concatenate([[0.0], np.cumsum(k)])
        ct = np.concatenate([[0.0], np.cumsum(tth * k)])
        ct2 = np.concatenate([[0.0], np.cumsum(tth * tth * k)])
        cy = np.concatenate([[0.0], np.cumsum(y * k)])
        cty = np.concatenate([[0.0], np.cumsum(tth * y * k)])
        lo = np.clip(idx - half, 0, n)
        hi = np.clip(idx + half + 1, 0, n)
        cnt = c0[hi] - c0[lo]
        st, st2 = ct[hi] - ct[lo], ct2[hi] - ct2[lo]
        sy, sty = cy[hi] - cy[lo], cty[hi] - cty[lo]
        enough = cnt >= 3
        safe = np.where(enough, cnt, 1.0)
        t_mean = st / safe
        den = st2 - st * t_mean            # Σt² − (Σt)²/n
        slope = np.where(enough & (den > 0),
                         (sty - sy * t_mean) / np.where(den > 0, den, 1.0),
                         0.0)
        level = np.where(enough,
                         (sy - slope * st) / safe + slope * tth[idx],
                         np.where(cnt > 0, sy / safe, base[idx]))
        base = np.interp(tth, tth[idx], level)
    return np.clip(base, 0.0, None)


def fit_anchor_baseline(tth, anchors, *, method: str = "linear") -> np.ndarray:
    """
    手动锚点基线：把用户点选的"纯背景"锚点连成一条底线。

    锚点两端做线性外推（用首两/末两锚点）而不是常数平铺——低角背景是
    陡升的，用最近锚点的值水平延伸出去会严重少扣。外推结果再夹到 ≥ 0
    （背景是强度量，不能为负）。

    参数：
        tth : np.ndarray
            2θ 网格（度，单调递增）
        anchors : iterable of (float, float)
            锚点 (2θ, 强度)，顺序不限；同一 2θ 上的重复锚点取**后一个**
            （merge 成一个点，不会留下垂直台阶，也不会让样条抛异常）
        method : str
            "linear"（折线，实验室惯例、最透明）/"pchip"（保单调的三次
            插值，界面默认）/"spline"（自然三次样条）。三种都严格过锚点；
            差别在锚点之间：折线是直线段（弯背景上会明显偏高，实测中段
            偏 +29）、自然样条更平滑但会过冲（可能压到真值以下 = 扣过头）、
            **pchip 既平滑又不过冲**——合成真值上平均绝对偏差 6.1
            （折线 28.8、样条 16.4），只有 3 个锚点时 50 vs 118 / 99。
            pchip/spline 至少需要 3 个锚点，不足时自动退回 linear——
            纯函数不做日志/弹窗，静默降级

    返回：
        np.ndarray
            与 tth 等长的基线，处处 ≥ 0；无锚点则返回全 0（= 不扣）
    """
    tth = np.asarray(tth, dtype=float)
    a = np.asarray(sorted((tuple(p) for p in anchors), key=lambda p: p[0]),
                   dtype=float) if len(anchors) else np.zeros((0, 2))
    if a.shape[0] == 0:
        return np.zeros(tth.size)
    # 同一个 2θ 上的重复锚点取**后一个**（用户重点同一个位置，意图就是
    # 改这个点）。不去重的话样条会直接抛 "x must be strictly increasing"，
    # 折线则在该处留一个近乎垂直的台阶——两个都不是用户想要的
    if a.shape[0] > 1:
        keep = np.ones(a.shape[0], dtype=bool)
        keep[:-1] = np.diff(a[:, 0]) != 0
        a = a[keep]
    if a.shape[0] == 1:
        return np.full(tth.size, max(float(a[0, 1]), 0.0))

    xs, ys = a[:, 0], a[:, 1]
    base = np.interp(tth, xs, ys)          # 区间内（两端为 np.interp 的平铺值）
    # 两端线性外推覆盖 np.interp 的平铺值
    lo_slope = (ys[1] - ys[0]) / (xs[1] - xs[0]) if xs[1] != xs[0] else 0.0
    hi_slope = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2]) if xs[-1] != xs[-2] else 0.0
    left = tth < xs[0]
    right = tth > xs[-1]
    base[left] = ys[0] + lo_slope * (tth[left] - xs[0])
    base[right] = ys[-1] + hi_slope * (tth[right] - xs[-1])
    # 曲线拟合只在两端锚点之间生效（外推一律走上面的线性尾巴，
    # 口径与 linear 一致）
    if method == "pchip" and a.shape[0] >= 3 and xs[-1] > xs[0]:
        inner = (~left) & (~right)
        if inner.any():
            # 保单调 PCHIP：过点、光滑、**不**过冲——样条会在锚点之间
            # 冲到真值以下（扣过头），折线则是直线段跟不上背景的弯
            base[inner] = PchipInterpolator(xs, ys)(tth[inner])
    elif method == "spline" and a.shape[0] >= 3 and xs[-1] > xs[0]:
        inner = (~left) & (~right)
        if inner.any():
            spl = CubicSpline(xs, ys, bc_type="natural", extrapolate=False)
            vals = spl(tth[inner])
            base[inner] = np.where(np.isfinite(vals), vals, base[inner])
    return np.clip(base, 0.0, None)


def _correct_with_anchors(tth, intensity, base, anchors) -> np.ndarray:
    """把自动基线**校正**到用户点的锚点上（自动 + 手动矫正）。

    在锚点处量"实测 − 自动基线"这点差，用保单调插值（pchip，不过冲）把
    它摊到整条曲线再加回去：锚点处基线严格落在实测值上，锚点之间保持自动
    那份形状。两端线性外推（与 fit_anchor_baseline 同口径）；只有一个锚点
    时退化成常数平移。结果夹到 ≥0（背景是强度量）。
    """
    if not len(anchors):
        return base
    t = np.asarray(tth, dtype=float)
    xs = np.asarray([float(a[0]) for a in anchors], dtype=float)
    ys = np.asarray([float(a[1]) for a in anchors], dtype=float)
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]
    keep = np.concatenate([[True], np.diff(xs) > 0])   # 同一 2θ 取后一个
    xs, ys = xs[keep], ys[keep]
    if xs.size == 0:
        return base
    resid = ys - np.interp(xs, t, base)
    if xs.size == 1:
        corr = np.full(t.size, float(resid[0]))
    else:
        p = PchipInterpolator(xs, resid, extrapolate=False)
        corr = np.asarray(p(t), dtype=float)
        left, right = t < xs[0], t > xs[-1]
        if left.any():
            slope = (resid[1] - resid[0]) / (xs[1] - xs[0])
            corr[left] = resid[0] + slope * (t[left] - xs[0])
        if right.any():
            slope = (resid[-1] - resid[-2]) / (xs[-1] - xs[-2])
            corr[right] = resid[-1] + slope * (t[right] - xs[-1])
        bad = ~np.isfinite(corr)
        corr[bad] = 0.0
    return np.clip(np.asarray(base, dtype=float) + corr, 0.0, None)


def subtract_background(intensity, baseline, *,
                        clip_negative: bool = False) -> np.ndarray:
    """
    曲线减基线。

    默认**保留负值**：背景是从两侧对称估出来的，扣完噪声会摆动到 0 以下，
    这是正常的（噪声地板露出）；强行切 0 会把噪声平均抬高约 1σ，定量上
    不可忽略（EXAFS 等领域的标准做法就是保留负值）。想切由调用方显式
    打开，代价自负。
    """
    out = np.asarray(intensity, dtype=float) - np.asarray(baseline, dtype=float)
    return np.clip(out, 0.0, None) if clip_negative else out


def compute_baseline(tth, intensity, params, *, blank_curve=None):
    """
    按参数算基线（绘制层与导出层共用这一份口径，避免两处实现漂移）。

    参数：
        tth, intensity : np.ndarray
            样品的 1D 曲线（**原始**曲线，不是扣过的）
        params : dict
            mode        : "off" / "blank" / "auto" / "anchor"
                          （"snip" 也可用，但未在 GUI 启用，见其 docstring）
            window_deg  : float，自动基线的窗口宽度（度）
            blank_scale : float，空扫归一化系数（曝光/束流不一致时 >1）
            anchors     : [(2θ, 强度), ...]，手动锚点
            anchor_method : "linear" / "spline"
        blank_curve : (tth, intensity) 或 None
            空扫曲线（已积分、已缓存）；mode="blank" 且为空时返回 None

    返回：
        np.ndarray 或 None
            基线数组；mode="off"（或缺空扫数据）时返回 None = 不扣背景
    """
    mode = (params or {}).get("mode", "off")
    if mode in (None, "off"):
        return None
    tth = np.asarray(tth, dtype=float)
    if mode == "blank":
        if blank_curve is None:
            return None
        b_tth, b_i = blank_curve
        scale = float((params or {}).get("blank_scale", 1.0) or 1.0)
        return scale * interp_onto_grid(b_tth, b_i, tth)
    if mode == "auto":
        base = estimate_baseline_sliding(
            tth, intensity,
            (params or {}).get("window_deg", AUTO_WINDOW_DEG))
        # 自动 + 锚点校正（用户 2026-09-27："背景扣除采取自动加手动矫正"）：
        # 有锚点就把基线整体挪到用户点的"纯背景"上，形状仍是自动那份
        return _correct_with_anchors(tth, intensity, base,
                                     (params or {}).get("anchors") or [])
    if mode == "snip":                      # 未在 GUI 启用，保留供对照
        return estimate_baseline_snip(
            tth, intensity,
            (params or {}).get("window_deg", AUTO_WINDOW_DEG))
    if mode == "anchor":
        anchors = (params or {}).get("anchors") or []
        if not anchors:
            return None   # 一个锚点都没点 = 还没有"背景"可言，不扣也不画
        return fit_anchor_baseline(
            tth, anchors,
            method=(params or {}).get("anchor_method", "linear"))
    raise ValueError(f"unknown background mode: {mode!r}")
