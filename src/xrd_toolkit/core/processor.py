"""XRD 图像处理：对 2D 强度数组的计算。

文件顺序按调用依赖排列：先定圆心（find_ring_center），再画剖面线
（line_profile），最后是复用剖面线的取点拟合圆心
（fit_center_from_rings）。
"""
import numpy as np


def find_ring_center(image: np.ndarray):
    """自动定位衍射环圆心（= 直射束落点），亚像素精度，无需参数。

    原理：粉末衍射图关于圆心中心对称（Friedel 定律保证：每个衍射
    信号在穿过圆心的对跖点强度相同）。将图像绕几何中心旋转 180°
    后与原图只差一个平移，平移量 = 圆心偏移几何中心的 2 倍；
    用 FFT 互相关求该平移量，除以 2 即得圆心。

    步骤：
        1. 裁剪最亮的 0.1% 像素。强单晶亮斑之间的"假配对"会在相关
           面制造假峰（LMFP 数据实测假峰高于真峰）；裁剪后亮斑变为
           平顶、假峰消失，环的结构主导相关面。
        2. 原图与 180° 旋转图 FFT 互相关。相关峰位置 = 2×偏移，
           峰可出现在任意位置，圆心偏移数百像素也可定位。
        3. 峰附近 5×5 最小二乘二次拟合，将整数峰位细化到亚像素
           （相关峰为连续峰形，真实峰顶位于像素之间）。

    实测精度：lab6 → (1022.2, 1021.7)，LMFP → (1021.3, 1021.9)，
    与标定值 B = (1022.0, 1022.3) 相差 < 1 px（约 0.05% 图像宽度）。

    使用策略：本函数仅作校准脚本的环心初值，其余环节一律使用
    config.py 中的校准值，不重复自动定位。

    参数：
        image —— 2D numpy 数组，image[行][列] = 该像素强度

    返回：
        (cy, cx) —— 圆心（行, 列），与 line_profile 的 center 顺序一致
    """
    h, w = image.shape
    f = np.asarray(image, dtype=np.float64)

    # 1) 裁剪亮斑：超过 99.9% 分位数的像素压平，消除亮斑假配对峰
    cap = np.percentile(f, 99.9)
    f = np.clip(f, 0, cap)

    # 2) 绕几何中心旋转 180°：np.rot90(f, 2) 绕的正是阵列中心
    #    ((w-1)/2, (h-1)/2) = 几何中心 C
    g = np.rot90(f, 2)

    # 3) 互相关：corr[k] = Σ_p f[p]·g[p−k]。
    #    先去均值（减掉背景平台，避免直流分量淹没相关峰），
    #    fftshift 后平移量 0 在数组正中心 (h//2, w//2)。
    corr = np.fft.fftshift(np.fft.ifft2(
        np.fft.fft2(f - f.mean()) * np.conj(np.fft.fft2(g - g.mean()))
    )).real
    cy0, cx0 = h // 2, w // 2
    iy, ix = np.unravel_index(np.argmax(corr), corr.shape)
    sy = float(iy - cy0)              # 纵向平移量（整数部分）
    sx = float(ix - cx0)              # 横向平移量

    # 4) 亚像素细化：把峰附近 5×5 小块的"行和 / 列和"各拟合一条二次
    #    曲线（最小二乘，5 点求 3 系数），顶点位置即小数部分。
    def fit_axis(patch_sums: np.ndarray) -> float:
        idx = np.arange(-2, 3, dtype=np.float64)
        a, b, _ = np.linalg.lstsq(
            np.stack([idx ** 2, idx, np.ones(5)], axis=1),
            patch_sums, rcond=None)[0]
        return -b / (2 * a) if abs(a) > 1e-12 else 0.0

    # 峰贴边时切片会负数回绕，拟合没意义（贴边峰 = 周期延拓伪峰，直接用整数位）
    if 2 <= iy < h - 2 and 2 <= ix < w - 2:
        patch = corr[iy - 2:iy + 3, ix - 2:ix + 3]
        sy += fit_axis(patch.sum(axis=1))  # 行方向（y）
        sx += fit_axis(patch.sum(axis=0))  # 列方向（x）

    # 5) 平移量 k = 2(c - C)  →  圆心 c = C + k/2
    cx = (w - 1) / 2.0 + sx / 2.0
    cy = (h - 1) / 2.0 + sy / 2.0
    return cy, cx


def line_profile(image: np.ndarray, center, angle_deg: float = 0.0):
    """沿过圆心、与水平方向成 angle_deg 的直线采样强度。

    参数：
        image     —— 2D numpy 数组，image[行][列] = 该像素强度
        center    —— 圆心 (行, 列)，必传。调用方传入校准值
                     （view_diffraction 传选中配置的 beam_center）；
                     自动定位仅保留在校准脚本中作初值
        angle_deg —— 直线与水平方向的夹角（度），0 = 水平线

    返回：
        t        —— 每个采样点到圆心的距离（像素）。圆心左侧为负、右侧为正
        intensity—— 每个采样点的强度（经过插值）

    双线性插值，用周围 4 个像素按距离加权平均，得到的曲线更平滑、更接近真实。
    """

    h, w = image.shape
    cy, cx = center             # 注意：center 是 (行, 列) = (y, x)

    # 角度 → 弧度
    theta = np.radians(angle_deg)
    cos_t, sin_t = np.cos(theta), np.sin(theta)

    # 直线方程：x = cx + t·cosθ, y = cy + t·sinθ（t 为沿直线的距离）。
    # 先求 t 的合法范围，保证采样点不越出图像边界。圆心不居中时两侧
    # 采样范围不同，硬截断会让边缘像素被重复采样。
    t_min, t_max = -float(np.hypot(h, w)), float(np.hypot(h, w))
    for c, lo_edge, hi_edge in (
            (cos_t, -cx, w - 1 - cx),   # x 方向约束：0 ≤ x ≤ w-1
            (sin_t, -cy, h - 1 - cy)):  # y 方向约束：0 ≤ y ≤ h-1
        if abs(c) < 1e-12:
            continue  # 该方向没有移动（如水平线时 sinθ = 0）
        if c > 0:
            t_max = min(t_max, hi_edge / c)
            t_min = max(t_min, lo_edge / c)
        else:
            t_max = min(t_max, lo_edge / c)
            t_min = max(t_min, hi_edge / c)

    # 采样点序列：t_min 到 t_max，间隔 1 像素
    t = np.arange(np.ceil(t_min), np.floor(t_max) + 1, dtype=np.float64)

    # 向量化：对数组逐元素同时计算
    xs = cx + t * cos_t
    ys = cy + t * sin_t

    # 双线性插值：每个采样点用周围 4 个像素加权平均
    # x0, y0 = 采样点左上方的像素；fx, fy = 采样点在该像素内的小数位置 (0~1)
    x0 = np.floor(xs).astype(int)
    y0 = np.floor(ys).astype(int)
    fx = xs - x0
    fy = ys - y0

    # 坐标限制在图像范围内，防止越界
    x0c = np.clip(x0, 0, w - 1)
    y0c = np.clip(y0, 0, h - 1)
    x1c = np.clip(x0 + 1, 0, w - 1)
    y1c = np.clip(y0 + 1, 0, h - 1)

    # 周围四个点的强度
    i00 = image[y0c, x0c]  # 左上
    i10 = image[y0c, x1c]  # 右上
    i01 = image[y1c, x0c]  # 左下
    i11 = image[y1c, x1c]  # 右下

    # 按距离加权平均：离哪个像素近，它的权重就大
    intensity = (i00 * (1 - fx) * (1 - fy)
                 + i10 * fx * (1 - fy)
                 + i01 * (1 - fx) * fy
                 + i11 * fx * fy)
    return t, intensity


def _profile_peaks(t, profile, max_rings, rel_thresh=0.25):
    """径向剖面寻峰：平滑 → 局部极大 → 自适应阈值 → 平顶合并 → 亚像素细化。

    平滑（盒宽 7）压噪声；局部背景用 151 px 宽中值滤波估计（远宽
    于峰，不被峰抬高）；只保留强峰：超出局部背景的高度 ≥ 本剖面
    最大超出高度的 rel_thresh（25%）——弱到只比背景高 2~24% 的外环
    凸起对圆心拟合贡献很小且峰位不可靠，宁缺毋滥。

    合并规则按"鞍点"而非固定距离：相邻候选峰之间的谷底若不低到
    较矮峰的 50% 以下，说明是同一宽峰的平顶两端（1 px 环经盒平滑
    后平顶可宽 6~8 px，固定距离合并会漏并），合并取高者；真正的
    相邻环之间谷底回到背景，保留两峰。

    亚像素细化在未平滑剖面上做：平滑平顶上没有峰位信息，取平滑
    峰位 ±3 px 内原始剖面的最高点，再三点抛物线细化（对对称峰
    精确落在中心，如 1 px 环只有 2 个非零采样点的凸起）。

    返回 [(t_peak, height), ...] 按 t 升序，至多 max_rings 个。
    """
    from scipy.ndimage import median_filter   # 惰性导入：仅此函数需要

    p = np.asarray(profile, dtype=np.float64)
    n = len(p)
    if n < 10:
        return []
    half = 3
    s = np.convolve(p, np.ones(2 * half + 1) / (2 * half + 1), mode="same")
    bg = median_filter(s, size=151, mode="nearest")
    exc = s - bg                       # 超出局部背景的高度

    # 全平剖面（如全黑图）：exc 处处为 0，直接返回，避免把每个点都当峰
    top = float(np.max(exc))
    if top <= 0:
        return []
    cand = [i for i in range(1, n - 1) if s[i] >= s[i - 1] and s[i] >= s[i + 1]]
    if not cand:
        return []
    keep = [i for i in cand if exc[i] >= rel_thresh * top]

    # 鞍点合并：谷底 ≥ 0.5×较矮峰 → 同一峰（平顶两端），保留较高者
    merged = []
    for i in keep:
        if merged:
            j = merged[-1]
            valley = float(np.min(exc[j:i + 1]))
            if valley > 0.5 * min(exc[j], exc[i]):
                if exc[i] > exc[j]:
                    merged[-1] = i
                continue
        merged.append(i)

    # 亚像素细化：原始剖面 p 在平滑峰位 ±3 px 内取最高点 + 三点抛物线
    out = []
    for i in merged:
        lo, hi = max(1, i - 3), min(n - 2, i + 3)
        m = lo + int(np.argmax(p[lo:hi + 1]))
        d = 0.0
        denom = p[m - 1] - 2.0 * p[m] + p[m + 1]
        if abs(denom) > 1e-12:
            d = float(np.clip(0.5 * (p[m - 1] - p[m + 1]) / denom, -1.0, 1.0))
        out.append((float(t[m]) + d, float(exc[i])))
    return out[:max_rings]


def fit_center_from_rings(image: np.ndarray, center0=None, n_azim=180,
                          max_rings=16, r_min_px=0.0, rounds=2):
    """多环取点反推圆心：取可见弧段上的点，最小二乘拟合共同圆心。

    原理：所有衍射环是同心圆、共享一个圆心。沿各方位角取径向
    剖面、在每条剖面上寻峰取点，再解"所有点到共同圆心 C 的距离
    等于各自环半径"的最小二乘。只要求有可见环弧段——完整环、
    半环、四分之一环、束心在图像外都适用（find_ring_center 的
    FFT 对跖配对在半环上失效，本方法无此限制）。

    解出的圆心 = 环的几何中心 = 直射束落点 B（探测器有倾斜时
    B ≠ PONI，注意与 pyFAI 几何区分）。

    步骤：
      1) 从初值圆心向 n_azim 个方位角各取一条径向剖面（复用
         line_profile，只用 t ≥ 0 一侧），寻峰取点
         （_profile_peaks）；
      2) 网格初值：束心到同一环各点的距离应相等——在覆盖图像
         （含边距）的粗网格上，按距离聚类成环、以稳健残差估价，
         取最优格点作初值圆心与环标签。可见弧段很短时圆拟合
         有"远处伪解"简并（直接拟合会滑向图像外），网格搜索
         避开此陷阱；
      3) 最小二乘：min Σ (‖p − C‖ − r_环)²，未知数 = 共同圆心
         C + 每环半径 r_k。scipy least_squares + soft_l1 稳健
         损失——个别方位角漏峰/错编号产生的野点被自动降权
         （单晶斑同理）；
      4) EM 修正错标签：拟合后按"最近环半径"重分配每个点的
         环序号再拟合。修正两类错标——近切线射线穿过同一环
         两次（近侧/远侧两点同属一环却拿相邻序号）、部分方位
         角漏峰导致的序号错位；
      5) 用拟合出的圆心重新取剖面再拟合（共 rounds 轮，通常
         2 轮收敛：更好的圆心 → 更锐的剖面 → 更准的峰位）。

    参数：
        image    —— 2D numpy 数组，image[行][列] = 该像素强度
        center0  —— 初值圆心 (行, 列)；None 时用图像几何中心。
                    初值只影响剖面采样位置（须穿得过环），圆心
                    本身由网格搜索+拟合决定，初值远离真值也能
                    收敛（剖面穿不过任何环时仍会失败）
        n_azim   —— 剖面数，均匀覆盖 360°（默认 180，每 2° 一条）
        max_rings—— 每剖面最多取多少个峰（默认 16）
        r_min_px —— 跳过半径小于该值的峰（排除 beamstop 光晕区；
                    默认 0。光晕本身以束心为中心对称，即使取到
                    也不会系统性偏置圆心，只是多加噪声）
        rounds   —— 取点→拟合迭代轮数（默认 2）

    返回：
        dict：cy, cx（圆心，行/列，与 find_ring_center 一致）、
        residual_px（全部点的距离残差 RMS，像素）、n_points、
        ring_radii（每环拟合半径，升序）。取不到足够点时返回
        None（如实报告失败，由调用方兜底）。
    """
    h, w = image.shape
    if center0 is None:
        cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    else:
        cy, cx = float(center0[0]), float(center0[1])

    azims = np.linspace(0.0, 360.0, n_azim, endpoint=False)

    def extract_points(cy, cx):
        """从当前圆心向各方位角取剖面、寻峰，返回取到的点 (x, y)。"""
        pts_x, pts_y = [], []
        for phi in azims:
            t, profile = line_profile(image, (cy, cx), float(phi))
            pos = t >= r_min_px
            t, profile = t[pos], profile[pos]
            if len(t) < 20:
                continue
            peaks = _profile_peaks(t, profile, max_rings)
            cos_p, sin_p = np.cos(np.radians(phi)), np.sin(np.radians(phi))
            for tp, _ in peaks:
                pts_x.append(cx + tp * cos_p)
                pts_y.append(cy + tp * sin_p)
        return (np.asarray(pts_x, dtype=np.float64),
                np.asarray(pts_y, dtype=np.float64))

    def _compact(labels):
        """标签压缩去空号（EM 重分配可能腾空某环）。"""
        uniq = np.unique(labels)
        if len(uniq) == int(labels.max()) + 1:
            return labels
        remap = {old: new for new, old in enumerate(uniq)}
        return np.asarray([remap[l] for l in labels], dtype=np.int64)

    def fit_rings(pts_x, pts_y, labels, cy0, cx0):
        """最小二乘：min Σ soft_l1(‖p−C‖ − r_环)，未知数 = C + 每环半径。

        初值：当前圆心 + 每环点到当前圆心的距离中位数（中位数抗野点）。
        """
        from scipy.optimize import least_squares   # 惰性导入：仅此函数需要

        k_rings = int(labels.max()) + 1
        dist = np.hypot(pts_x - cx0, pts_y - cy0)
        r0 = [float(np.median(dist[labels == k])) for k in range(k_rings)]
        x0 = np.r_[cx0, cy0, r0]

        def res(x):
            d = np.hypot(pts_x - x[0], pts_y - x[1])
            return d - x[2 + labels]

        return least_squares(
            res, x0, loss="soft_l1", f_scale=3.0,
            bounds=([-w, -h] + [1e-6] * k_rings,
                    [2.0 * w, 2.0 * h] + [np.inf] * k_rings))

    def grid_init(pts_x, pts_y, pad=0.5, n_grid=11):
        """网格搜索初值：找"点到候选圆心的距离聚成若干紧簇"的格点。

        束心到同一环上各点的距离应严格相等（= 环半径），到不同环
        的距离相差大。在覆盖图像（含 pad 边距，束心可能在图像外
        一点）的 n_grid×n_grid 粗网格上：按距离 gap 聚类成环，
        以 soft_l1 残差估价，取最优格点。可见弧段短时圆拟合存在
        远处伪解，网格初值把它拉回真值盆地。返回 (cx, cy, labels)
        或 None（每格点都凑不出 ≥2 环 × ≥3 点，如无环图像）。
        """
        gx = np.linspace(-pad * w, (1 + pad) * w, n_grid)
        gy = np.linspace(-pad * h, (1 + pad) * h, n_grid)
        best = None                       # (cost, cx, cy, labels)
        for cxg in gx:
            for cyg in gy:
                d = np.hypot(pts_x - cxg, pts_y - cyg)
                order = np.argsort(d)
                ds = d[order]
                # gap 分裂：相邻距离差 > max(4 px, 3% 距离) 处分环
                gap = np.diff(ds)
                splits = np.where(gap > np.maximum(4.0, 0.03 * ds[1:]))[0]
                labs = np.zeros(len(ds), dtype=np.int64)
                for s in splits:
                    labs[s + 1:] += 1
                k = int(labs.max()) + 1
                counts = np.bincount(labs)
                # 只让 ≥3 点的簇参与估价：1~2 点的野簇（图像边缘截断
                # 产生的离群峰）不拒绝整个格点，也不进代价
                keep = np.where(counts[labs] >= 3)[0]
                if (len(np.unique(labs[keep])) < 2
                        or len(keep) < max(12, 0.25 * len(ds))):
                    continue                # 凑不出 ≥2 环，或大部分点是散的
                med = np.array([np.median(ds[labs == j]) for j in range(k)])
                z = (ds[keep] - med[labs[keep]]) / 3.0
                cost = float(np.sum(2.0 * (np.sqrt(1.0 + z * z) - 1.0)))
                if best is None or cost < best[0]:
                    inv = np.empty_like(order)
                    inv[order] = np.arange(len(order))
                    best = (cost, float(cxg), float(cyg), labs[inv])
        if best is None:
            return None
        return best[1], best[2], best[3]

    pts_x = pts_y = None
    for _ in range(rounds):
        pts_x, pts_y = extract_points(cy, cx)
        n_pts = len(pts_x)
        if n_pts < 12:
            return None                    # 点太少：拟合无意义

        init = grid_init(pts_x, pts_y)
        if init is None:
            return None                    # 聚不出 ≥2 环：拟合无意义
        cx, cy, labels = init

        # EM 迭代：拟合 → 按"最近环半径"重分配标签 → 再拟合（2 轮）。
        # 修正两类错标签：近切线射线穿过同一环两次（近侧/远侧两点
        # 同属一环却拿相邻序号）、部分方位角漏峰导致的序号错位。
        for _ in range(2):
            k_rings = int(labels.max()) + 1
            if k_rings < 2:
                return None                # 环太少：拟合无意义
            fit = fit_rings(pts_x, pts_y, labels, cy, cx)
            cx, cy = float(fit.x[0]), float(fit.x[1])
            d = np.hypot(pts_x - cx, pts_y - cy)
            labels = np.abs(d[:, None] - fit.x[2:][None, :]).argmin(axis=1)
            labels = _compact(labels)

    # 合并半径近同的环：非真值格点上的 gap 聚类会把一个环拆成多个
    # 伪簇（半径几乎相同的若干"环"），按环间距阈值合并后重算残差。
    # 合并后只剩一个环 → 数据里没有多环结构，如实返回 None。
    rs_sort = np.argsort(fit.x[2:])
    rs = fit.x[2:][rs_sort]
    bounds = ([0]
              + list(np.where(np.diff(rs)
                              > np.maximum(4.0, 0.03 * rs[1:]))[0] + 1)
              + [len(rs)])
    if len(bounds) - 1 < 2:
        return None
    radii = [float(np.mean(rs[bounds[i]:bounds[i + 1]]))
             for i in range(len(bounds) - 1)]
    group = np.empty(len(rs), dtype=np.int64)
    for g, (lo, hi) in enumerate(zip(bounds[:-1], bounds[1:])):
        group[lo:hi] = g
    merged_label = group[np.argsort(rs_sort)]   # 原环序号 → 合并组
    d = np.hypot(pts_x - cx, pts_y - cy)
    r_of_label = np.asarray(radii)[merged_label[labels]]
    residual = np.sqrt(np.mean((d - r_of_label) ** 2))
    return {"cy": cy, "cx": cx, "residual_px": float(residual),
            "n_points": int(n_pts), "ring_radii": radii}

