"""标样环的质量指标：环位偏差 / 完整度 / a 自洽（引擎层，纯函数、无 Qt）。

校准界面要的是一张"成绩单"：这一次校准到底好不好？本模块把"好不好"
落成可以直接比较的数字。三个指标都基于**图像上真实测到的环**，而不是
拟合自身的自洽残差——后者没有分辨力（实测：自动与手动两套几何的残差
都报 0.004°，而它们的距离差了 0.4 mm，环位差了几个像素）。

  1. 环位偏差 (px)（主指标）
     把标定几何预测的环路径（与校准图面板上那条青线同一套理论环，
     theoretical_ring_paths）放到图像上，沿每条环的各方位角在预测半径
     附近找真实峰，量"实测半径 − 预测半径"。它直接回答用户能看得见的
     问题：青线有没有压在真实的环上。本数据环宽约 2.5 px，所以 1 px
     量级的偏差就是肉眼可见的错位。

  2. 完整度
     每条环是否整圈都在图像上、且整圈都有信号。校准算法（pyFAI
     extract_cp / refine2）对完整环最可靠：半环、四分之一环的几何解在
     某些方向上没有约束。两个估计器都跑、都记录：
       * coverage（取点计数法，主判据）——每个方位角在预测半径附近是否
         有显著峰（显著 = 峰高 > 3 × 噪声，噪声由窗口两端的离环样本
         估计，见 NOISE_K_BG 的注释），数出被覆盖的方位角比例。直观、
         可解释；单点噪声会直接体现在计数上。
       * coverage_fft（谱相干法，交叉验证）——把**检出样本**的峰高当成
         一条绕圆周的"环存在信号" g(φ)，取 |G₀|² / Σ|G_m|²（FFT 实现，
         等价于 (Σg)²/(n·Σg²)，见 _spectral_coverage）。对 0/1 指示
         函数它恰好是覆盖比例（弧长 Δφ → Δφ/2π），不需要选阈值。
         由于 g 只在检出样本上非零，恒有 coverage_fft ≤ coverage：两者
         相等 = 信号整圈均匀；明显偏小 = 环强但不匀（纹理/大晶粒）或
         刚过阈值的弱环在噪声里浮沉。**但要注意它只回答"信不信得匀"，
         不回答"有没有环"**——若拿全窗噪声当 g（无环），它反而接近 1。
         所以判据用 coverage（不完整以取点为准），FFT 值并列记录，
         两者差得远时置 disagree 标志（该环信号可疑）。

  3. a 自洽
     每条环按实测 2θ 反推 LaB₆ 晶格常数 a（a_k = d_k · a_ref/d_ref_k，
     其中 d_k = λ/(2 sin θ_k)），看各环之间的一致性。
       * 离散度 spread_ppm 有信息：大 = 单一 a + 单一几何解释不了这些环
         （典型原因：环号认错、探测器畸变、几何模型缺项、某个峰被别的
         东西顶偏）。
       * 均值**没有**信息：λ 与探测器距离在标样上完全简并（两者同时
         缩放时所有环的 2θ 完全不变），任何标定都无法只靠标样定出 λ。
         所以本模块只报离散度，不报"波长偏差"——报出来会误导。
     也因此 a 自洽与环位偏差是同一信息的两种单位（前者无量纲、可跨
     批次比，后者是像素、直观看得见），不是两个独立证据。

坐标约定与本模块的邻居完全一致（踩过坑，见 pyfai-pixel-coordinate-
convention）：全线用**图像索引坐标**（imshow/点击事件那一套），PONI 与
getFit2D 只在 theoretical_ring_paths 内部换算一次；tth 的形参序是
(行, 列)。环路径直接复用 theoretical_ring_paths，保证"量的环"与"画的
青线"严格同源。
"""
import numpy as np
from pyFAI.calibrant import get_calibrant
from pyFAI.detectors import Detector
from pyFAI.goniometer import Geometry
from scipy import ndimage

from xrd_toolkit.services.integrator import (
    LAB6_NAME, lab6_theoretical_2theta, theoretical_ring_paths)

# ══ 参数（按本数据标定过，理由见各常量注释）══════════════════════
# 径向采样步长（px）：环宽约 2.5 px，0.5 px 采样给峰形 ~5 个点，抛物线
# 亚像素插值足够（实测偏差重复性 < 0.05 px）。
RADIAL_STEP_PX = 0.5
# 搜索窗半宽 = 该比例 × 邻环间距（px，再夹到下面的上下限）。
# 0.35 保证窗口够装下真峰（几何差一点也找得到）又不会碰到邻环
# （本数据最小环间距 28.5 px → 窗半宽 10 px）。
WINDOW_FRAC = 0.35
WINDOW_MIN_PX = 2.0
WINDOW_MAX_PX = 25.0
# 峰形平滑宽度（px）：约等于环宽，抑制单像素噪声；取奇数格（偶数会让
# 峰位整体偏半格）。
SMOOTH_PX = 2.5
# 显著峰判据：峰高 > NOISE_K_BG × 噪声（噪声由窗口两端"离环样本"估计，
# 见 _peak_stats）。**必须用绝对噪声尺度，不能用"环内参考峰高的比例"**：
# 没有环的窗口里峰高只由噪声起伏决定、分布很窄（实测噪声下 20% 分位到
# 90% 分位只差 0.8σ），任何比例阈值都会把其中 25~40% 的方位角误判成
# "有峰"——实测纯噪声图会报出"环位偏差 11 px"这种假结果。
NOISE_K_BG = 3.0
# 窗口背景取"两端离环样本的中位数"（而不是整窗的低分位）：窗口内背景
# 常有随半径的斜率（空气散射），两端取中位相当于在环半径处取值，
# 不被斜率带偏。
EDGE_FRAC = 0.2          # "离环样本" = 窗口每侧外侧该比例的格数
MIN_EDGE_SAMPLES = 8     # 少于这个数就不估噪声（判据自动全否）
# 完整环判据：弧覆盖 ≥ 该值（弧覆盖 = 测到峰的方位角 / 全部方位角）。
COMPLETE_COV = 0.9
# 参与"环位偏差"汇总量（dev_px 等）的最低覆盖：覆盖太低的环里剩下的
# "峰"多半是噪声，纳入汇总只会污染主指标。
MIN_COVER_FOR_DEV = 0.5
# 两法覆盖差超过该值 → 记 disagree（见模块 docstring 第 2 条）。
FFT_DISAGREE = 0.15
# 反推 a 需要的最少实测方位角数（太少则中位 2θ 不足以代表该环）。
MIN_AZIM_FOR_A = 8


def ring_metrics(image, *, pixel_size_m: float, wavelength_m: float,
                 dist_m: float, poni1_px: float, poni2_px: float,
                 rot1_deg: float = 0.0, rot2_deg: float = 0.0,
                 n_rings: int = 16, n_azim: int = 180) -> dict:
    """量一组几何参数把 LaB₆ 理论环放到图像上的质量（三个指标）。

    参数：
        image        2D 强度数组（标样图，load_diffraction_image 的输出）
        pixel_size_m / wavelength_m / dist_m
                     像素尺寸（米）/ 波长（米）/ 探测器距离（米）
        poni1_px / poni2_px
                     PONI 像素坐标（行、列——pyFAI 惯例：poni1↔行、poni2↔列）
        rot1_deg / rot2_deg  倾斜角（度）
        n_rings      参与计算的理论环数（默认 16）
        n_azim       每条环的方位角采样数（默认 180，即 2° 一个）

    返回 dict（键名即含义，全部为 JSON 友好的标量/列表）：
        dev_px        主指标：全体纳入环的 |实测 − 预测| 半径偏差中位数（px）
        dev_signed_px 有符号偏差中位数（正 = 实测比预测更靠外）
        dev_rms_px / dev_max_px  有符号偏差的 RMS / 最大 |偏差|
        clip_frac     峰顶到搜索窗边界的方位角比例（汇总）。几何差得太多时
                      真峰在窗外，dev_* 会是 NaN——这时它是唯一证据
        n_complete    完整环数（coverage ≥ COMPLETE_COV）
        n_rings_used  参与 dev_* 的环数（coverage ≥ MIN_COVER_FOR_DEV）
        rings         [每条环一个 dict]，键：
                        ring, r_pred_px, r_meas_px   预测/实测半径中位（px）
                        dev_px, dev_signed_px, dev_rms_px  该环的偏差
                        n_valid, n_detected    窗口在图像内 / 测到峰的方位角数
                        fov_frac               窗口在图像内的方位角比例
                        coverage, coverage_fft 两种覆盖估计
                        fft_disagree           两法不一致（覆盖差 > FFT_DISAGREE）
                        complete, prom_med     完整环标志 / 峰高中位
                        clip_frac              峰顶到窗边的比例（>0 说明真峰
                                               可能在窗外，偏差被截断）
                        a_angstrom             该环反推的晶格常数（Å，或 NaN）
        a             {mean_angstrom, std_angstrom, spread_ppm, n_used,
                       worst_ring}   详见模块 docstring 第 3 条
        beam_center_rc  环的公共圆心 = 直射束落点 B（行, 列，索引坐标）
        half_window_px  各环实际用的搜索窗半宽（诊断用）

    几何离谱时（环全在图像外、或真峰落在搜索窗之外）不抛异常：无效环
    的值为 NaN、clip_frac 接近 1，由调用方决定怎么提示。
    """
    image = np.asarray(image, dtype=np.float64)
    if image.ndim != 2:
        raise ValueError(f"image 必须是 2D 数组，收到 {image.ndim} 维")
    h, w = image.shape

    # 预测环路径：与校准图面板的青线同一个函数（量的环 = 画的环）
    paths = theoretical_ring_paths(
        pixel_size_m=pixel_size_m, wavelength_m=wavelength_m, dist_m=dist_m,
        poni1_px=poni1_px, poni2_px=poni2_px, rot1_deg=rot1_deg,
        rot2_deg=rot2_deg, image_shape=image.shape, n_rings=n_rings,
        n_azim=n_azim)
    b_row, b_col = paths["beam_center_rc"]

    # 实测 2θ → a 需要 tth：几何与 theoretical_ring_paths 内部同一套
    # （含半像素约定，见模块 docstring 末段）
    geo = Geometry(
        dist=dist_m, poni1=poni1_px * pixel_size_m,
        poni2=poni2_px * pixel_size_m,
        rot1=np.radians(rot1_deg), rot2=np.radians(rot2_deg), rot3=0.0,
        detector=Detector(pixel1=pixel_size_m, pixel2=pixel_size_m,
                          max_shape=image.shape),
        wavelength=wavelength_m)

    # 从路径反推每个方位角的预测半径与方位角（不复制生成约定：路径怎么
    # 生成的就怎么读回来）
    radii, phis = [], []
    for _, xy in paths["rings"]:
        dx = xy[:, 0] - b_col
        dy = xy[:, 1] - b_row
        radii.append(np.hypot(dx, dy))
        phis.append(np.arctan2(dy, dx))
    median_r = np.array([float(np.nanmedian(r)) if np.isfinite(r).any()
                         else np.nan for r in radii])
    windows = _half_windows_px(median_r)

    # a 参照：dspacing[0] 就是 LaB₆ 的晶格常数 a（4.1568 Å，NIST SRM 660
    # 的 (100) 线，N=1），故 a_k = d_k · dspacing[0]/dspacing[k]（= d_k·√N_k）
    dspacing = np.asarray(get_calibrant(LAB6_NAME).dspacing[:n_rings])

    ring_rows, dev_pool, dev_pool_abs = [], [], []
    clip_pool, n_valid_total = [], 0
    for k in range(n_rings):
        r_pred, phi = radii[k], phis[k]
        if not np.isfinite(r_pred).any():
            ring_rows.append(_empty_row(k))   # 该几何下这条环无解
            continue
        band, offs = _sample_band(image, b_row, b_col, r_pred, phi,
                                  windows[k], w, h)
        st = _peak_stats(band, offs)
        valid = st["valid"] & np.isfinite(r_pred)
        row, det = _ring_row(k, r_pred, phi, st, valid, n_azim, b_row, b_col,
                             geo, wavelength_m, dspacing)
        ring_rows.append(row)
        n_valid_total += int(valid.sum())
        clip_pool.append(int((st["clipped"] & valid).sum()))
        if det is not None and row["coverage"] >= MIN_COVER_FOR_DEV:
            dev_pool.append(st["off_px"][det])
            dev_pool_abs.append(np.abs(st["off_px"][det]))

    all_dev = (np.concatenate(dev_pool) if dev_pool else np.array([]))
    all_abs = (np.concatenate(dev_pool_abs) if dev_pool_abs else np.array([]))
    out = {
        "dev_px": _med(all_abs),
        "dev_signed_px": _med(all_dev),
        "dev_rms_px": (float(np.sqrt(np.mean(all_dev ** 2)))
                       if all_dev.size else float("nan")),
        "dev_max_px": (float(np.max(all_abs)) if all_abs.size
                       else float("nan")),
        # 峰顶到搜索窗边界的比例（全体方位角汇总）：几何差得太多时真峰
        # 在窗外，dev_* 会是 NaN——此时这个数就是"偏差超出搜索窗"的唯一
        # 证据，调用方要据此给用户一句人话（不能只显示"没有数据"）
        "clip_frac": (sum(clip_pool) / n_valid_total if n_valid_total
                      else float("nan")),
        "n_complete": int(sum(1 for row in ring_rows if row["complete"])),
        "n_rings_used": len(dev_pool),
        "rings": ring_rows,
        "a": _a_consistency(ring_rows),
        "beam_center_rc": (b_row, b_col),
        "half_window_px": windows.tolist(),
    }
    return out


def metrics_note(m: dict, tag: str = "") -> str:
    """把指标摘要成一行英文（日志/CLI 用；GUI 的中文措辞在 calib.py）。

    没有可用环信号时返回 "no usable ring signal"，由调用方决定怎么措辞。
    """
    if m is None:
        return "no metrics"
    if not np.isfinite(m["dev_px"]):
        return "no usable ring signal"
    a = m["a"]
    a_txt = (f"a spread {a['spread_ppm']:.0f} ppm"
             if np.isfinite(a["spread_ppm"]) else "a spread n/a")
    tag_txt = f"{tag} " if tag else ""
    return (f"{tag_txt}ring dev {m['dev_px']:.2f} px, "
            f"{m['n_complete']}/{len(m['rings'])} complete rings, {a_txt}")


# ══ 内部：采样与找峰 ═══════════════════════════════════════════
def _half_windows_px(median_r) -> np.ndarray:
    """每条环的搜索窗半宽（px）= 0.35 × 最近邻环间距，夹到 [2, 25]。

    端点环只有一侧邻居就用那一侧。环半径无解（NaN）时给上限（用不到）。
    """
    n = len(median_r)
    out = np.full(n, WINDOW_MAX_PX)
    for k in range(n):
        if not np.isfinite(median_r[k]):
            continue
        gaps = [abs(median_r[k] - median_r[j])
                for j in (k - 1, k + 1)
                if 0 <= j < n and np.isfinite(median_r[j])]
        if gaps:
            out[k] = float(np.clip(WINDOW_FRAC * min(gaps),
                                   WINDOW_MIN_PX, WINDOW_MAX_PX))
    return out


def _sample_band(image, b_row, b_col, r_pred, phi, half_w, w, h):
    """沿预测路径做径向采样：返回 (band, offs)。

    band[i, j] = 方位角 i、相对预测半径偏移 offs[j] 处的图像强度（双线性
    插值）；采样点落在图像外（留 1 px 边距，避免插值取到边界外）时为
    NaN——有效性由调用方按"整窗都在图像内"判定，所以边界附近宁缺毋滥。
    """
    n_step = max(1, int(round(half_w / RADIAL_STEP_PX)))
    offs = np.arange(-n_step, n_step + 1) * RADIAL_STEP_PX
    r_use = np.where(np.isfinite(r_pred), r_pred, 0.0)
    rr = r_use[:, None] + offs[None, :]
    x = b_col + rr * np.cos(phi)[:, None]
    y = b_row + rr * np.sin(phi)[:, None]
    inside = ((x >= 1.0) & (x <= w - 2.0) & (y >= 1.0) & (y <= h - 2.0)
              & np.isfinite(r_pred)[:, None])
    band = ndimage.map_coordinates(
        image, np.stack([y, x]).reshape(2, -1), order=1,
        mode="nearest").reshape(rr.shape)
    return np.where(inside, band, np.nan), offs


def _peak_stats(band, offs) -> dict:
    """逐方位角在径向剖面上找峰，并用窗口两端的离环样本估背景与噪声。

    返回 dict：
        prom     (n_azim,) 峰高 = 平滑后峰值 − 该方位角局部背景（截到 ≥0）
        off_px   (n_azim,) 峰位相对预测半径的偏移（px，抛物线亚像素）
        clipped  (n_azim,) 峰值顶在窗口边界（真峰可能在窗外）
        valid    (n_azim,) 整个搜索窗都在图像内
        sigma_bg float    噪声尺度（全环共用一个；NaN = 样本太少，判据全否）
    """
    n_azim, n_r = band.shape
    valid = np.isfinite(band).all(axis=1)

    # 离环样本 = 窗口每侧外侧 EDGE_FRAC 的格数：环在窗心，两端必然是背景
    k_edge = max(1, int(round(EDGE_FRAC * (n_r - 1))))
    edge_mask = np.zeros(n_r, dtype=bool)
    edge_mask[:k_edge] = True
    edge_mask[-k_edge:] = True
    edge = band[:, edge_mask]                 # (n_azim, 2k)
    finite_e = np.isfinite(edge)
    # 每方位角的局部背景 = 该方位角两端离环样本的中位（不受背景斜率
    # 影响：两端取中位 ≈ 在环半径处取值）
    bg = np.full(n_azim, np.nan)
    rows_ok = finite_e.any(axis=1)
    if rows_ok.any():
        bg[rows_ok] = np.median(np.where(finite_e, edge, np.inf)[rows_ok],
                                axis=1)
    # 噪声尺度 = 离环样本相对自身中位的稳健标准差（全体方位角共用）
    dev = (edge - bg[:, None])[finite_e]
    sigma_bg = (float(1.4826 * np.median(np.abs(dev)))
                if dev.size >= MIN_EDGE_SAMPLES else float("nan"))

    # 出界点只出现在无效行；用该行最小值填，避免 NaN 流进平滑
    finite = np.isfinite(band)
    row_min = np.min(np.where(finite, band, np.inf), axis=1, keepdims=True)
    row_min[~finite.any(axis=1)] = 0.0     # 整窗出界：行会被判无效
    filled = np.where(finite, band, row_min)

    # 平滑宽度取奇数格：偶数会让峰位系统性偏半格
    smooth_n = int(round(SMOOTH_PX / RADIAL_STEP_PX)) | 1
    sm = ndimage.uniform_filter1d(filled, size=smooth_n, axis=1,
                                  mode="nearest")

    i = np.argmax(sm, axis=1)
    clipped = (i == 0) | (i == n_r - 1)
    i_c = np.clip(i, 1, n_r - 2)      # 抛物线需要左右各一点
    rows = np.arange(n_azim)
    y1, y2, y3 = sm[rows, i_c - 1], sm[rows, i_c], sm[rows, i_c + 1]
    denom = y1 - 2.0 * y2 + y3
    with np.errstate(divide="ignore", invalid="ignore"):
        delta = np.where(denom != 0.0, 0.5 * (y1 - y3) / denom, 0.0)
    # 噪声大时抛物线顶点可能算出离谱值：夹到 ±1 格内
    delta = np.clip(np.nan_to_num(delta), -1.0, 1.0)
    off_px = offs[i_c] + delta * RADIAL_STEP_PX
    prom = np.maximum(sm[rows, i_c] - bg, 0.0)
    return {"prom": prom, "off_px": off_px, "clipped": clipped,
            "valid": valid, "sigma_bg": sigma_bg}


def _spectral_coverage(g) -> float:
    """谱相干覆盖估计：|G₀|² / Σ_m |G_m|²（FFT 实现）。

    g(φ) = 各方位角的"环存在信号"（峰高；未检出的方位角置 0）。由
    Parseval，该比值 = (Σg)² / (n · Σg²)：对 0/1 指示函数正是覆盖比例
    （弧长 Δφ → Δφ/2π），g 有起伏时是**保守下界**（恒 ≤ coverage，见
    模块 docstring 第 2 条）。全零信号（没有可用数据）返回 NaN。

    这里用 FFT 算（rfft），等价的直接形式留在单元测试里交叉验证。
    """
    n = int(np.size(g))
    total = float(np.sum(np.asarray(g, dtype=float) ** 2))
    if n == 0 or total <= 0.0:
        return float("nan")
    spec = np.fft.rfft(np.asarray(g, dtype=float))
    return float(abs(spec[0]) ** 2 / (n * total))


def _ring_row(k, r_pred, phi, st, valid, n_azim, b_row, b_col, geo,
              wavelength_m, dspacing) -> tuple:
    """汇总单条环的一行指标（并返回参与汇总的 det 掩码给调用方）。"""
    prom, off = st["prom"], st["off_px"]
    # 阈值用绝对噪声尺度（见 NOISE_K_BG）：纯噪声下 prom 只由噪声起伏
    # 决定，比例阈值会误判一大片（实测 25~40%）
    thr = NOISE_K_BG * st["sigma_bg"]
    # 顶到窗边的样本：真峰在窗外、偏差被截断——不当"测到"，但要如实
    # 记进 clip_frac（几何离谱时这是最响的信号）
    det = valid & np.isfinite(prom) & (prom > thr) & ~st["clipped"]
    n_det = int(det.sum())
    row = {
        "ring": int(k),
        "r_pred_px": (_nanmed(r_pred[valid]) if valid.any() else float("nan")),
        "r_meas_px": (float(np.median(r_pred[det] + off[det])) if n_det
                      else float("nan")),
        "dev_px": (float(np.median(np.abs(off[det]))) if n_det
                   else float("nan")),
        "dev_signed_px": (float(np.median(off[det])) if n_det
                          else float("nan")),
        "dev_rms_px": (float(np.sqrt(np.mean(off[det] ** 2))) if n_det
                       else float("nan")),
        "n_valid": int(valid.sum()),
        "n_detected": n_det,
        "fov_frac": float(valid.mean()),
        "coverage": n_det / float(n_azim),
        "coverage_fft": _spectral_coverage(np.where(det, prom, 0.0)),
        "complete": False,      # 下面按 coverage 覆盖
        "fft_disagree": False,
        "prom_med": (float(np.median(prom[det])) if n_det else float("nan")),
        "clip_frac": float(st["clipped"][valid].mean()) if valid.any()
                     else float("nan"),
        "a_angstrom": float("nan"),
    }
    row["fft_disagree"] = bool(
        np.isfinite(row["coverage_fft"])
        and row["coverage"] - row["coverage_fft"] > FFT_DISAGREE)
    row["complete"] = bool(row["coverage"] >= COMPLETE_COV)

    # 反推晶格常数：该环实测峰点的 2θ（逐方位角取中位，避免个别方位角
    # 被别的散射顶偏）→ d = λ/(2 sin θ) → a = d · a_ref/d_ref。tth 形参
    # 序是 (行, 列)，见模块 docstring 的坐标约定。dspacing 单位是 Å，
    # 所以波长先转 Å（1e10）——写成米会把 a 报成 4.16e-10（踩过）。
    if n_det >= MIN_AZIM_FOR_A:
        rp = r_pred[det] + off[det]
        xp = b_col + rp * np.cos(phi[det])
        yp = b_row + rp * np.sin(phi[det])
        tth_deg = float(np.median(np.degrees(geo.tth(yp, xp))))
        d_meas_a = (wavelength_m * 1e10
                    / (2.0 * np.sin(np.radians(tth_deg) / 2.0)))
        row["a_angstrom"] = float(d_meas_a * dspacing[0] / dspacing[k])
    return row, (det if n_det else None)


def _empty_row(k) -> dict:
    """该几何下这条环无解（路径全 NaN）时的行：全 NaN、不完整。"""
    nan = float("nan")
    return {"ring": int(k), "r_pred_px": nan, "r_meas_px": nan,
            "dev_px": nan, "dev_signed_px": nan, "dev_rms_px": nan,
            "n_valid": 0, "n_detected": 0, "fov_frac": 0.0, "coverage": 0.0,
            "coverage_fft": nan, "complete": False, "fft_disagree": False,
            "prom_med": nan, "clip_frac": nan, "a_angstrom": nan}


def _a_consistency(ring_rows) -> dict:
    """各环反推的晶格常数 a 的一致性（详见模块 docstring 第 3 条）。"""
    vals = [(row["ring"], row["a_angstrom"]) for row in ring_rows
            if np.isfinite(row["a_angstrom"])]
    if len(vals) < 2:
        return {"mean_angstrom": float("nan"), "std_angstrom": float("nan"),
                "spread_ppm": float("nan"), "n_used": len(vals),
                "worst_ring": None}
    rings = [r for r, _ in vals]
    a = np.array([v for _, v in vals])
    mean = float(a.mean())
    worst = int(rings[int(np.argmax(np.abs(a - mean)))])
    return {
        "mean_angstrom": mean,
        "std_angstrom": float(a.std(ddof=1)),
        "spread_ppm": float((a.max() - a.min()) / mean * 1e6),
        "n_used": len(vals),
        "worst_ring": worst,
    }


def _med(arr) -> float:
    return float(np.median(arr)) if np.size(arr) else float("nan")


def _nanmed(arr) -> float:
    arr = np.asarray(arr, dtype=float)
    return float(np.nanmedian(arr)) if np.isfinite(arr).any() else float("nan")
