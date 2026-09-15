# 自动选取有效 2θ 区间：去掉直射束晕区和环被探测器切掉的无效区。
#
# 设计（2026-09-16，用户拍板 A+A 方案）：
#   下界（材料已知）= 材料专属标准（LO_STANDARDS），同一材料的
#     所有数据共用同一个标准（lmfp 与 lab6 各用各的）：
#       lmfp：第一个已知峰 − 0.3°（峰前有样品信号形成的缓坡爬升，
#         0.3° 把缓坡 + 一小段平台基线完整包进来）；
#       lab6：光环结束点 − 0.6°（标样的环又窄又尖，峰前没有缓坡，
#         唯一的"缓坡"是直射束晕的尾巴——从 ~1.0° 平台结束缓降到
#         ~1.6° 谷底，尾巴长约 0.6°；从检测到的光环结束点往回退
#         0.6° 正好回到平台末尾 ≈1.0°，把整条尾巴包进区间）。
#   下界（材料未知）→ 退回数据驱动：缓坡爬升起点 − 0.3°。
#   上界 = 数据失效点（自动检测，与材料无关）：环被方形探测器切掉
#     是纯几何效应，从某个 2θ 起大多数数据开始失效——
#       瀑布图（有 36 条扇区曲线）：统计每个 2θ"还活着"的扇区
#         比例，跌破 80% 的位置（两种材料实测同为 7.438°）；
#       单曲线脚本（integrate_1d 按存在的方位角归一化，曲线上
#         看不到失效）：用几何直接算"80% 方位角仍在探测器内"的
#         精确位置（geometric_failure_point，与实测 7.438° 一致
#         ——失效本来就是几何效应，两种算法互为印证）。
#     没有扇区信息也没有束心坐标才退回完整环极限（7.163°）。
#   质检：缓坡检测位置与第一已知峰对照、失效点与完整环极限对照
#     打印；偏差异常打 WARNING，但区间始终按标准/检测结果来
#     （标准不随单份数据漂移）。
#
# 为什么材料表存晶格参数而不是 2θ：文献里的 2θ 几乎都在 Cu Kα
# （λ=1.5406 Å）下测得，换一台仪器（本项目 λ=0.1223 Å 同步辐射）数字全变；
# d 值是材料的固有属性（不随波长变），由晶格参数算 d 再换算 2θ 才通用。
import numpy as np

# ---- 材料已知信息表 ----
# lab6：pyFAI 自带 NIST 标样的 d 值（get_calibrant("LaB6")），不用自己存数字。
# lmfp：橄榄石结构（空间群 Pnma）晶格参数，取自文献 LiFe0.5Mn0.5PO4 精修值
#   a=10.3818, b=6.0480, c=4.7186 Å（2026-09-16 检索）。
#   枚举 (hkl) 峰表时按 Pnma 消光条件剔除禁戒反射（(100) 不存在，
#   第一个真实峰是 (200) 1.350°，见 material_peaks 里的注释）。
#   实测核对：低角度主要峰全部吻合（Δ < 0.02°），说明这套参数可信。
MATERIALS = {
    "lab6": {"kind": "calibrant"},
    "lmfp": {"kind": "olivine", "a": 10.3818, "b": 6.0480, "c": 4.7186},
}

# 文件名 → 材料别名（自动识别：文件名小写包含这些关键字即认为是该材料）
STEM_ALIASES = [("lab6", "lab6"), ("lmfp", "lmfp")]


def detect_material(stem: str):
    """从文件名自动识别材料（小写匹配关键字），认不出返回 None。"""
    s = stem.lower()
    for key, name in STEM_ALIASES:
        if key in s:
            return name
    return None


def material_peaks(material: str, wavelength_m: float, max_hkl: int = 4):
    """材料在该波长下的所有峰位（度，升序），每项为 (2θ, 标签)。

    lab6：用 pyFAI 自带标样的 d 值（NIST 标准，最可靠）；
    lmfp：由晶格参数枚举 (hkl)（h,k,l ≤ max_hkl）算
      d = 1/√(h²/a² + k²/b² + l²/c²)，
      再由布拉格定律 2θ = 2·asin(λ/2d) 换算到给定波长。
    """
    if material == "lab6":
        # 局部 import：只有真的用到 lab6 才加载 pyFAI 的标样数据库
        from pyFAI.calibrant import get_calibrant
        cal = get_calibrant("LaB6")
        d = np.asarray(cal.dspacing)
        tth = np.degrees(2 * np.arcsin(wavelength_m * 1e10 / (2 * d)))
        return [(float(t), f"ring{i + 1}") for i, t in enumerate(tth)]

    info = MATERIALS[material]
    a, b, c = info["a"], info["b"], info["c"]
    wl_a = wavelength_m * 1e10          # 波长（埃），与晶格参数同单位
    peaks = {}
    for h in range(max_hkl + 1):
        for k in range(max_hkl + 1):
            for l in range(max_hkl + 1):
                if h == k == l == 0:
                    continue
                # Pnma 系统消光（对称性禁戒反射，强度恒为 0，不能进峰表）：
                #   0kl 需 k+l 偶（n 滑移；0k0 需 k 偶、00l 需 l 偶是其特例）
                #   hk0 需 h 偶（a 滑移；h00 需 h 偶是其特例）
                # 不剔除的话 (100) 这类假峰会混进"已知峰"表，把标准下界
                # 拉进直射束晕里（2026-09-16 实测抓到：0.675° 假峰）。
                if h == 0 and (k + l) % 2:
                    continue
                if l == 0 and h % 2:
                    continue
                d = 1.0 / np.sqrt(h * h / (a * a) + k * k / (b * b) + l * l / (c * c))
                sinth = wl_a / (2 * d)
                if sinth >= 1.0:
                    continue            # 布拉格定律无解（2θ > 180°），跳过
                t = float(np.degrees(2 * np.arcsin(sinth)))
                peaks.setdefault(round(t, 4), (h, k, l))   # 按 2θ 四舍五入去重
    return sorted((t, "".join(map(str, hkl))) for t, hkl in peaks.items())


def complete_ring_limit(image_shape, pixel_size_m: float, dist_m: float,
                        poni_px=None) -> float:
    """完整环极限 2θ：环半径达到"束心到图像边缘最近距离"时的角度，
    之后每个环都被探测器边缘切掉、不再完整。

    poni_px=(x, y)：束心像素坐标。不给时假设束心在图像正中心
    （近似，偏大：本数据 7.313°）；给了算精确值（本数据 7.163°，
    束心 (1045.2, 1022.0) 距右边缘最近 1002.8 px）。
    """
    h, w = image_shape
    if poni_px is None:
        r_complete_px = min(h, w) / 2.0
    else:
        x, y = poni_px
        r_complete_px = min(x, w - x, y, h - y)
    return float(np.degrees(np.arctan(r_complete_px * pixel_size_m / dist_m)))


def detect_halo_end(tth, curve, plateau_lo=0.5, plateau_hi=1.0,
                    scan_hi=3.0, margin_deg=0.05) -> float:
    """自动找"缓坡结束、第一个真实峰开始"的位置（区间下界）。

    思路（类比"走出平原"）：直射束晕是一段平坦的缓坡，第一个真实衍射峰
    是平台之后的第一次明显"爬坡"。做法：
      1) 平滑曲线（盒宽 ~0.06°）去毛刺；
      2) 平台高度 = [plateau_lo, plateau_hi] 里曲线的中位数
         （本仪器两个样品的晕区平台都在 0.5~1.0°）；
      3) 从 plateau_hi 往后扫，找第一个超过 平台×1.25 的点 = 爬坡起点；
      4) 退回 margin_deg 留一点基线。
    找不到（比如样品第一个峰特别弱）→ 退回 0.5°（经验兜底值）。
    """
    dt = float(np.median(np.diff(tth)))
    half = max(1, int(0.03 / dt))
    s = np.convolve(curve, np.ones(2 * half + 1) / (2 * half + 1), mode="same")
    m0 = (tth >= plateau_lo) & (tth <= plateau_hi)
    plateau = float(np.median(s[m0]))
    thr = plateau * 1.25
    scan = (tth > plateau_hi) & (tth <= scan_hi)
    idx = np.flatnonzero(scan & (s > thr))
    if len(idx) == 0:
        return 0.5
    return max(0.0, float(tth[idx[0]] - margin_deg))


# 标准区间外扩边距（度）：下界 = 第一个已知峰 − 边距。
# 0.3°（用户 2026-09-16 拍板）：峰不是一条线而是有宽度的包，
# 峰前还有一段缓坡爬升（实测缓坡在第一峰前 ~0.05~0.1° 处结束，
# 缓坡本身长 ~0.2°），0.3° 正好把缓坡完整包进来、前面再留一点
# 平台基线。想调整只改这一个常数。
PEAK_MARGIN_DEG = 0.3

# ---- 各材料的下界标准（2026-09-16 与用户一起定的，同一材料所有数据共用）----
# ("first_peak", 边距) → 下界 = 第一个已知峰 − 边距；
# ("halo_end",  边距) → 下界 = 检测到的光环结束点 − 边距。
# lab6 为什么用光环结束点：LaB6 标样的环极窄极尖，峰前没有任何样品
# 缓坡；峰前唯一的缓坡是直射束晕的尾巴（平台 ~1.0° 结束，缓降到
# ~1.6° 谷底，尾巴长约 0.6°）。从检测到的光环结束点（尾巴最低处）
# 往回退 0.6° 正好回到平台末尾 ≈1.0°，把整条尾巴包进区间
# （用户 2026-09-16 看瀑布图预览拍板：横坐标从 ~1.0° 开始）。
LO_STANDARDS = {
    "lmfp": ("first_peak", PEAK_MARGIN_DEG),
    "lab6": ("halo_end", 0.6),
}


def detect_failure_end(tth, data, hi_geom, live_threshold=0.8):
    """自动找"数据开始大面积失效"的上界（扇区数据，2026-09-16 A 方案）。

    环被方形探测器切掉是纯几何效应，与材料无关——从某个 2θ 起，
    指向图像边缘的扇区先死、指向角落的还能看见残缺的环，存活比例
    随 2θ 单调下降（实测：7.3° 全部活着，7.44° 死 20%，8.42° 死一半）。

    data = (n_sectors, n) 扇区矩阵：每个扇区的噪声线 = 该扇区 p90 的
    5%；强度超过噪声线算"活着"（扇区在死角没有积分值时是 NaN，
    NaN 比较为 False，按"死了"算）。从完整环极限前 0.3° 起扫，
    第一个跌破 live_threshold(80%) 的位置即失效点（两种材料实测
    同为 7.438°，与 geometric_failure_point 的几何值一致——失效
    本来就是几何效应，两法互为印证）。
    找不到失效点（数据异常）→ 退回完整环极限 hi_geom。
    """
    alpha = 0.05 * np.nanpercentile(data, 90, axis=1)
    frac = (data > alpha[:, None]).mean(axis=0)
    seg = tth >= hi_geom - 0.3
    idx = np.flatnonzero(seg & (frac < live_threshold))
    if len(idx) == 0:
        return hi_geom
    return float(tth[idx[0]])


def geometric_failure_point(poni_px, image_shape, pixel_size_m, dist_m,
                            live_threshold=0.8):
    """几何精确的"数据失效点"：存活方位角比例跌破 80% 的 2θ。

    环被探测器切掉是纯几何：给定束心与探测器尺寸，半径 r 的环上
    有多少比例的点仍落在探测器内可以精确算出。从完整环极限的
    半径起逐个像素外推，找到存活比例 <80% 的半径 → 2θ。
    （与 detect_failure_end 的数据驱动实测结果 7.438°
    完全一致。）单曲线脚本（integrate_pattern/calibrate_integrate）
    没有扇区信息，用这个几何值；瀑布图用实测值并把两者对照打印。
    """
    h, w = image_shape
    x0, y0 = poni_px
    r_complete = min(x0, w - x0, y0, h - y0)
    r_corner = np.hypot(max(x0, w - x0), max(y0, h - y0))
    theta = np.linspace(0.0, 2.0 * np.pi, 3600, endpoint=False)
    for r in np.arange(np.ceil(r_complete), r_corner + 1.0):
        x = x0 + r * np.cos(theta)
        y = y0 + r * np.sin(theta)
        live = float(((x >= 0) & (x <= w) & (y >= 0) & (y <= h)).mean())
        if live < live_threshold:
            return float(np.degrees(np.arctan(r * pixel_size_m / dist_m)))
    return float(np.degrees(np.arctan(r_complete * pixel_size_m / dist_m)))


def select_auto_range(tth, curve, material, wavelength_m, image_shape,
                      pixel_size_m, dist_m, I2d=None, poni_px=None):
    """自动选区间的完整流程，返回 (lo, hi, info)。

    设计（2026-09-16，用户拍板 A+A 方案）：
      下界：材料已知 → 材料专属标准 LO_STANDARDS（lmfp：第一个
        已知峰 − 0.3°；lab6：光环结束点 − 0.6°，≈1.0°）。同一
        材料的所有数据共用同一个标准（lmfp 与 lab6 区间不同）。
        材料未知 → 缓坡爬升起点 − PEAK_MARGIN_DEG（数据驱动兜底）。
      上界：数据失效点自动检测——I2d 给扇区矩阵时用实测存活比例
        <80%（数据驱动，实测 7.438°）；单曲线脚本用几何精确值
        geometric_failure_point（与实测一致）；都没有才退回完整环
        极限。扇区实测值与几何预期对照打印，偏差大报警。
      质检：缓坡检测位置与第一已知峰对照、失效点与完整环极限
        对照打印；异常打 WARNING，但区间始终按标准/检测结果。
    """
    hi_geom = complete_ring_limit(image_shape, pixel_size_m, dist_m, poni_px)
    detected = detect_halo_end(tth, curve)
    if I2d is not None:
        # 实测：扇区存活比例 <80%（数据驱动）
        hi = detect_failure_end(tth, I2d, hi_geom)
        hi_reason = (f"measured data-failure point {hi:.3f}° "
                     f"(<80% of sectors alive)")
    elif poni_px is not None:
        # 单曲线脚本：无扇区信息，用几何精确值（与实测 7.438° 一致）
        hi = geometric_failure_point(poni_px, image_shape, pixel_size_m, dist_m)
        hi_reason = (f"geometric data-failure point {hi:.3f}° "
                     f"(<80% azimuth coverage)")
    else:
        hi = hi_geom
        hi_reason = f"complete-ring limit {hi_geom:.3f}° (fallback)"
    info = {
        "lo_reason": "", "hi_reason": hi_reason,
        "material": material,
        "checks": [],
        "n_known_peaks": 0,
        "warnings": [],
    }
    if I2d is not None and poni_px is not None:
        # 质检：实测失效点 vs 几何预期（应一致，失效本来就是纯几何效应）
        hi_exact = geometric_failure_point(poni_px, image_shape, pixel_size_m, dist_m)
        info["checks"].append(
            f"measured failure point {hi:.3f} deg vs geometric expectation "
            f"{hi_exact:.3f} deg (delta = {abs(hi - hi_exact):.3f} deg)")
        if abs(hi - hi_exact) > 0.2:
            info["warnings"].append(
                f"measured data-failure point {hi:.3f} deg deviates from the "
                f"geometric expectation {hi_exact:.3f} deg; keeping the measured "
                f"value (check mask/beam center)")
    if material is not None:
        known = material_peaks(material, wavelength_m)
        first = known[0][0]
        anchor, margin = LO_STANDARDS.get(material, ("first_peak", PEAK_MARGIN_DEG))
        if anchor == "halo_end":
            # lab6 型：峰前没有样品缓坡，缓坡 = 光环尾巴，从光环结束点往回退
            lo = max(0.0, detected - margin)
            info["lo_reason"] = (f"material standard ({material}): halo end "
                                 f"{detected:.3f}° − {margin}°")
        else:
            lo = first - margin
            info["lo_reason"] = (f"material standard ({material}): first known "
                                 f"peak {first:.3f}° − {margin}°")
        if lo >= hi:
            # 异常组合（标准下界已在失效点之外）：退回缓坡检测兜底
            info["warnings"].append(
                f"standard lo {lo:.3f}° is beyond the data-failure point "
                f"{hi:.3f}°; falling back to detected halo slope start")
            lo = max(0.0, detected - PEAK_MARGIN_DEG)
        else:
            # 质检：本数据实测的缓坡爬升起点 vs 第一已知峰
            # （缓坡应在第一峰前 ~0.05~0.3° 处结束）
            info["checks"].append(
                f"halo end detected {detected:.3f} deg vs first known peak "
                f"{first:.3f} deg (expected ~0.1 deg before)")
            if detected > first + 0.05 or detected < first - 0.3:
                info["warnings"].append(
                    f"detected halo end {detected:.3f} deg is inconsistent "
                    f"with the {material} first peak {first:.3f} deg; keeping "
                    f"the standard (check this dataset or the geometry)")
        # 失效点 vs 几何完整环极限对照（失效点应比极限稍晚，残缺环还有残余信号）
        info["checks"].append(
            f"data failure point {hi:.3f} deg vs complete-ring limit "
            f"{hi_geom:.3f} deg (delta = {hi - hi_geom:.3f} deg)")
        _verify_against_material(tth, curve, lo, hi, material, wavelength_m, info)
    else:
        lo = max(0.0, detected - PEAK_MARGIN_DEG)
        info["lo_reason"] = "halo slope start (auto-detected; material unknown)"
    return lo, hi, info


def _verify_against_material(tth, curve, lo, hi, material, wavelength_m, info):
    """用材料已知峰位核对：找实测第一峰/最强峰，与理论最近峰对比。

    这是"查询已知峰位出现规律"的落地：材料认出来后，理论峰位像一份
    参考答案，用来核对实测峰位——对上了说明区间选得对、数据也正常。
    """
    known = np.array([t for t, _ in material_peaks(material, wavelength_m) if t < hi])
    info["n_known_peaks"] = len(known)
    if len(known) == 0:
        return

    dt = float(np.median(np.diff(tth)))
    half = max(1, int(0.03 / dt))
    s = np.convolve(curve, np.ones(2 * half + 1) / (2 * half + 1), mode="same")

    # 实测峰：区间内"高于平台 1.2 倍"的局部极大（简单寻峰，够核对用）
    m0 = (tth >= 0.5) & (tth <= 1.0)
    plateau = float(np.median(s[m0]))
    peaks = []                       # 存 (2θ, 平滑强度)，方便找最强峰
    for i in range(2, len(s) - 2):
        if not (lo <= tth[i] <= hi):
            continue
        if s[i] >= s[i - 1] and s[i] > s[i + 1] and s[i] > plateau * 1.2:
            peaks.append((tth[i], s[i]))
    if not peaks:
        return

    # 第一峰 = 角度最小的峰；最强峰 = 强度最高的峰。各找最近的理论峰对比
    for label, t0 in (("first", peaks[0][0]),
                      ("strongest", max(peaks, key=lambda p: p[1])[0])):
        j = int(np.argmin(np.abs(known - t0)))
        info["checks"].append(
            f"measured {label} peak {t0:.3f} deg vs known {known[j]:.3f} deg "
            f"(delta = {abs(t0 - known[j]):.3f} deg)")
