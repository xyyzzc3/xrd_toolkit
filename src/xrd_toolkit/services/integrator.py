# 2D 衍射图像 → 1D 衍射图谱：方位角积分 + LaB₆ 几何校准（基于 pyFAI）。
#
# 背景：每个 (hkl) 晶面族把 X 光散射成以入射束为轴的圆锥（半角 2θ），
# 探测器平面截过所有圆锥得到一组圆环（Debye–Scherrer 环）。方位角
# 积分把每个半径上的像素强度取平均，将二维环压缩为一维曲线
# I(2θ)——标准粉末衍射谱。
import numpy as np
from pyFAI.calibrant import get_calibrant
from pyFAI.detectors import Detector
from pyFAI.goniometer import Geometry, GeometryRefinement, SingleGeometry
from pyFAI.integrator.azimuthal import AzimuthalIntegrator

# pyFAI 内置信标数据库（calibrant），LaB₆ 标准 d 值：
#   d = a / sqrt(h² + k² + l²)，a = 4.1568 Å（NIST 标准值）
LAB6_NAME = "LaB6"

# 束心偏离探测器中心的阈值（像素）：超过则切换自研 numpy 积分。
# 实测（pyFAI 2026.5.0，合成 1 px 宽环验证）：integrate1d/2d 的径向
# 分箱相对探测器中心而非 PONI——束心偏离 24 px 时峰高衰减 5 倍、
# 124 px 时衰减 70 倍；integrate2d 在 5° 环行的存活扇区数随偏离骤降
# （x0=200 时仅 2~3/36，只有 x0=1024 正确）。所有积分方法
# （splitpixel/csr/numpy/cython/lut）、显式 radial_range/
# azimuth_range/Detector 均无法修复，带掩膜同样失效。精修
# （calibrate_lab6）在偏离大时同样失效。
# 本数据 lmfp1_lab6 束心仅偏离 ~21 px 且真实峰宽（非 1 px 合成线），
# 衰减可忽略，保持 pyFAI 路径（输出与已验证的历史结果一致）。
OFF_CENTER_PX = 100.0

# pyFAI 的两种像素坐标差半像素，混用会整体偏 (0.5, 0.5) px（合 0.707 px）：
#   - tth(d1, d2) 的入参 = **图像索引坐标**（与 matplotlib imshow、
#     点击事件的 xdata/ydata 同一套）：索引 i 的像素中心在物理位置
#     (i + 0.5)·pixel（detectors/_common.py 的 calc_cartesian_positions
#     里 d1c = d1 + 0.5，注释 "The half pixel offset is taken into
#     account here !!!"）。
#   - 但 poni1/poni2 与 getFit2D 的输出 = **物理位置 / pixel**，即
#     "索引 + 0.5"。
# 实测（零倾斜、poni=1024.5）：tth(1024.0, 1024.0) 恰为 0°，即直射束
# 落点的索引坐标是 1024.0 = getFit2D(1024.5) − 0.5 ✓。这也解释了长期
# 记为"~0.5 px 精修噪声"的那处差：内置几何 getFit2D (1022.50, 1022.70)
# 减 0.5 得 (1022.00, 1022.20)，与图像实测环心 (1021.65, 1022.23)
# 只差 0.35 px；不减则差 0.85 px——是约定差，不是噪声。
# 用法：把 poni/getFit2D 换算成索引坐标一次，之后全链路都用索引坐标。
PIXEL_CENTER_OFFSET = 0.5


def _is_off_center(image_shape, poni1_m, poni2_m, pixel_size_m) -> bool:
    """束心是否偏离探测器中心超过 OFF_CENTER_PX（触发自研积分）。

    行列：poni1↔行/y、poni2↔列/x（pyFAI 惯例，与 _draw_2d 的束心
    标记同一套）。hypot 对角交换对称，写反不影响本判据的取值，
    但语义要写对，免得以后按 dx/dy 做别的判断时踩坑。
    """
    h, w = image_shape
    dx = poni2_m / pixel_size_m - w / 2.0
    dy = poni1_m / pixel_size_m - h / 2.0
    return float(np.hypot(dx, dy)) > OFF_CENTER_PX


def lab6_theoretical_2theta(wavelength_m: float, n_rings: int = 16) -> np.ndarray:
    """
    计算 LaB6 前 n_rings 个衍射环的理论 2θ（单位：度）。

    布拉格定律：2d·sinθ = λ  →  2θ = 2·asin(λ / 2d)

    参数：
        wavelength_m : float
            X 光波长（米），例如 0.1223e-10（0.1223 Å）
        n_rings : int
            要计算的环数（默认 16）

    返回：
        np.ndarray
            长度 n_rings 的 2θ 数组（度）
    """
    cal = get_calibrant(LAB6_NAME)
    # cal.dspacing 单位为埃（1e-10 m）
    d_angstrom = np.asarray(cal.dspacing[:n_rings])
    wavelength_a = wavelength_m * 1e10
    return np.degrees(2 * np.arcsin(wavelength_a / (2 * d_angstrom)))


def snap_lab6_ring(x_px: float, y_px: float, *, pixel_size_m: float,
                   wavelength_m: float, dist_m: float, poni1_m: float,
                   poni2_m: float, rot1_deg: float = 0.0,
                   rot2_deg: float = 0.0, max_rings: int = 16,
                   tol_deg: float = 0.5):
    """点击点吸附到最近的 LaB₆ 理论环（手动选点校准的判环助手）。

    按当前几何算该点的 2θ（pyFAI Geometry.tth，输入像素坐标、输出
    弧度），与 lab6_theoretical_2theta 的每个理论值比距离；最近者
    在 tol_deg 内返回环序号（0 起），全部超出返回 None（调用方忽略
    该点并提示）。rot1/rot2 默认 0：判环只需要大致几何，倾斜角对
    2θ 的影响远小于环间距。

    参数：
        x_px, y_px   点击点的像素坐标（横向、纵向）
        pixel_size_m / wavelength_m / dist_m / poni1_m / poni2_m
                     当前几何（PONI 为米制，与 calibrate_lab6 的
                     输入约定一致）
        rot1_deg / rot2_deg  倾斜角（度，可选，默认 0）
        max_rings    参与匹配的理论环数（默认 16）
        tol_deg      吸附容差（2θ 度，默认 0.5°）

    返回：
        int 环序号（0 起）或 None
    """
    geo = Geometry(
        dist=dist_m, poni1=poni1_m, poni2=poni2_m,
        rot1=np.radians(rot1_deg), rot2=np.radians(rot2_deg), rot3=0.0,
        detector=Detector(pixel1=pixel_size_m, pixel2=pixel_size_m),
        wavelength=wavelength_m,
    )
    # pyFAI 的 tth 需要数组输入（内部会访问 .size），标量直接传会报错。
    # 形参顺序是 (d1=行, d2=列)，与绘图坐标 (x=列, y=行) 相反——传反会
    # 让偏置摆法（束心远离图像对角线）判错环号；本数据束心几乎在
    # 对角线上，传反被掩盖（对角线镜像不改变到束心的距离）。
    # 入参就是图像索引坐标，与 tth 的约定一致，无需半像素换算
    # （poni/getFit2D 才需要，见 PIXEL_CENTER_OFFSET）。
    tth_deg = float(np.degrees(geo.tth(np.array([y_px]), np.array([x_px]))[0]))
    theo = lab6_theoretical_2theta(wavelength_m, max_rings)
    i = int(np.argmin(np.abs(theo - tth_deg)))
    if abs(theo[i] - tth_deg) > tol_deg:
        return None
    return i


# 环路径二分迭代次数：括号宽度 = 2×名义半径 + 图像对角线（几何离谱时
# 名义半径可达 1e6 px 也要包住真解），36 次后相对精度 ~1e-11。
RING_PATH_ITER = 36


def theoretical_ring_paths(*, pixel_size_m: float, wavelength_m: float,
                           dist_m: float, poni1_px: float, poni2_px: float,
                           rot1_deg: float = 0.0, rot2_deg: float = 0.0,
                           image_shape=None, n_rings: int = 16,
                           n_azim: int = 180) -> dict:
    """当前几何预测的理论环路径（校准面板画青线、判环共用）。

    为什么不用"圆心 + 半径"的正圆：探测器有倾斜时衍射环不是正圆——它
    是衍射圆锥与倾斜平面的交线（椭圆），且环的公共圆心是直射束落点
    B、不是 PONI（两者差 dist·tan(倾角)/pixel，本数据 23 px）。实测
    正圆近似的残余偏差：本数据 0.54~1.0 px，倾角每增大 1° 约恶化
    3 px。本函数直接反解真实交线——沿 B 周围各方位角，对 pyFAI 的
    tth 做二分求每个理论 2θ 等值线的半径：几何怎么说就怎么画，倾斜
    大时自动画出椭圆，与 pyFAI 的旋转约定无关（不用自己复刻公式）。

    参数（几何用 px 键，与面板/结果字典一致）：
        pixel_size_m / wavelength_m / dist_m
                     像素尺寸（米）/ 波长（米）/ 探测器距离（米）
        poni1_px / poni2_px
                     PONI 像素坐标（行、列——pyFAI 惯例：poni1↔Y/行、
                     poni2↔X/列）
        rot1_deg / rot2_deg  倾斜角（度）
        image_shape  (行, 列) 图像尺寸；用于统计"有多少条环落在图像
                     内"，None 时不做统计
        n_rings      环数（默认 16，与 lab6_theoretical_2theta 一致）
        n_azim       方位角采样数（默认 180，即每条折线 180 个点）

    返回：
        dict：
            rings          [(环号, (n_azim, 2) 数组 [x=列, y=行]), ...]；
                           反解不出解的点为 NaN（matplotlib 画折线时
                           自动断开，不会连出假线）
            beam_center_rc 环的公共圆心 = 直射束落点 B（行, 列），取
                           pyFAI getFit2D（含倾斜修正，与几何严格自洽）
            r_min_px / r_max_px  全部有效路径点的半径范围（px）
            n_inside      至少有一个采样点落在图像内的环数（0 = 几何
                           把环全推出图像，调用方应提示用户）

    坐标约定（两个坑，都踩过）：
        - pyFAI 的 tth(d1, d2) 形参顺序是 (行, 列)，与绘图坐标
          (x=列, y=行) 相反；写反会让偏置摆法（束心远离图像对角线）
          判错位置——本数据束心几乎在对角线上，写反会被掩盖。
        - poni/getFit2D 是"物理位置/pixel"（= 索引 + 0.5），而 tth 的
          入参是索引坐标（见 PIXEL_CENTER_OFFSET）。本函数输入输出
          一律用**图像索引坐标**（与 config 的 beam_center、imshow、
          点击事件同一套），只在取 getFit2D 时换算一次；漏换算或换算
          两次都会让画出来的环整体偏 0.707 px。
    """
    levels = np.radians(lab6_theoretical_2theta(wavelength_m, n_rings))
    geo = Geometry(
        dist=dist_m, poni1=poni1_px * pixel_size_m,
        poni2=poni2_px * pixel_size_m,
        rot1=np.radians(rot1_deg), rot2=np.radians(rot2_deg), rot3=0.0,
        detector=Detector(pixel1=pixel_size_m, pixel2=pixel_size_m,
                          max_shape=image_shape),
        wavelength=wavelength_m)
    fit = geo.getFit2D()
    # 换算成图像索引坐标（只此一次，之后全链路都是索引坐标）
    b_row = float(fit["centerY"]) - PIXEL_CENTER_OFFSET
    b_col = float(fit["centerX"]) - PIXEL_CENTER_OFFSET

    phi = np.linspace(0.0, 2.0 * np.pi, n_azim, endpoint=False)
    cos_p, sin_p = np.cos(phi), np.sin(phi)
    # 括号上界：名义半径的 2 倍 + 图像对角线（名义半径按正圆公式估，
    # 只用来定括号，不参与结果）
    diag = float(np.hypot(*image_shape)) if image_shape is not None else 0.0
    nominal = dist_m * np.tan(levels) / pixel_size_m
    hi = 2.0 * nominal[:, None] + diag + 1.0
    lo = np.ones_like(hi)
    # 每条射线从 B 出发、半径递增时 tth 单调递增（环是凸的、B 在环内），
    # 所以对"半径矩阵 (n_rings, n_azim)"整体二分即可
    for _ in range(RING_PATH_ITER):
        mid = 0.5 * (lo + hi)
        tth = geo.tth(b_row + mid * sin_p[None, :], b_col + mid * cos_p[None, :])
        pos = tth > levels[:, None]
        hi = np.where(pos, mid, hi)
        lo = np.where(pos, lo, mid)
    r = 0.5 * (lo + hi)

    # 有效性：把解代回 tth 复核（射线扫不到该环时无解，二分只会停在
    # 括号上界，必须丢掉而不是画一条假的线）
    x = b_col + r * cos_p[None, :]
    y = b_row + r * sin_p[None, :]
    resid = geo.tth(y, x) - levels[:, None]
    bad = ~np.isfinite(resid) | (np.abs(resid) > 1e-6)
    x = np.where(bad, np.nan, x)
    y = np.where(bad, np.nan, y)
    rings = [(k, np.column_stack([x[k], y[k]])) for k in range(len(levels))]

    valid_r = r[~bad]
    n_inside = 0
    if image_shape is not None:
        h, w = image_shape
        for _, xy in rings:
            inside = ((xy[:, 0] >= 0) & (xy[:, 0] <= w - 1)
                      & (xy[:, 1] >= 0) & (xy[:, 1] <= h - 1))
            n_inside += int(bool(inside.any()))
    return {
        "rings": rings,
        "beam_center_rc": (b_row, b_col),
        "r_min_px": float(valid_r.min()) if valid_r.size else float("nan"),
        "r_max_px": float(valid_r.max()) if valid_r.size else float("nan"),
        "n_inside": n_inside,
    }


# 迭代精修收敛参数（calibrate_lab6）：残差目标、改善阈值、最大轮数。
# 初始几何通常已接近真值，1~2 轮即收敛；上限 3 轮防止个别数据集震荡。
REFINE_TARGET_RESIDUAL_DEG = 0.05
REFINE_TOL = 1e-5
REFINE_MAX_ITER = 3


def calibrate_lab6(
    image: np.ndarray,
    pixel_size_m: float = 200e-6,
    wavelength_m: float = 0.1223e-10,
    dist0_m: float = 1.6,
    *,
    center0_px: tuple,
    max_rings: int = 16,
) -> dict:
    """
    用 LaB6 标样自动校准探测器几何（pyFAI GeometryRefinement）。

    原理（迭代精修，最多 3 轮）：
      1) extract_cp：按当前几何预测每个环的 2θ 位置，在预测位置附近
         搜索图像上的真实峰位，得到若干控制点（像素坐标 + 环序号）；
      2) refine2：最小二乘精修距离、环心、倾斜角，使各控制点的实测
         2θ 逼近理论 2θ；
      3) 用精修后的几何重新提取控制点再精修，残差不再改善即停止
         （通常 1~2 轮收敛，残差 < 0.05° 提前停止）。

    参数：
        image : np.ndarray
            2D 衍射强度数组（load_diffraction_image 读出来的）
        pixel_size_m : float
            探测器像素尺寸（米），本仪器 200 µm
        wavelength_m : float
            X 光波长（米），本实验 0.1223 Å
        dist0_m : float
            探测器距离初值（米），本实验 ~1.6 m，精修后得到精确值
        center0_px : tuple
            环心初值（像素，cx, cy），必传。由调用方提供：
            校准脚本里用取点拟合（fit_center_from_rings，FFT 兜底）
            自动定位，或 --center 显式指定；本模块不做自动定位
        max_rings : int
            参与校准的环数上限

    返回：
        dict，包含：
            dist_m       精确探测器距离（米）
            poni1_px     PONI 第一坐标（像素，横向）
            poni2_px     PONI 第二坐标（像素，纵向）
            offset_px    精修环心相对初值（center0_px）的偏移（像素）
            rot1_deg     倾斜角 1（度）
            rot2_deg     倾斜角 2（度）
            rot3_deg     倾斜角 3（度，refine2 不精修，保持 0）
            residual_deg 全部控制点的 2θ 残差 RMS（度）
            control_points 最优轮控制点 [[x, y, 环序号], ...]（像素
                坐标 + 0 起环号；GUI 画绿点验证精修效果用，CLI 不消费）

    备注（pyFAI 接口注意事项）：
        - calibrant 的波长一经设置不可再改；换波长必须重新
          get_calibrant 创建新对象。
        - extract_cp 返回 ControlPoints 对象，getList() 才是 N×3 数组，
          第三列为环序号（从 0 开始），不是 2θ。
        - GeometryRefinement.tth() 的输入为像素坐标，不是米。
        - 束心偏离探测器中心过大（> OFF_CENTER_PX）时精修不可靠
          （pyFAI 已知问题，见常量注释）：偏置摆法数据集应先取居中标样
          图像精修距离/倾斜角（仪器属性），再配已知束心位置使用。
    """
    # pyFAI 内置 LaB₆ 校准数据（d1 = 4.1568 Å ≈ a）
    cal = get_calibrant(LAB6_NAME)
    cal.wavelength = wavelength_m

    det = Detector(pixel1=pixel_size_m, pixel2=pixel_size_m, max_shape=image.shape)

    # 偏置摆法提示：精修在部分环上不可靠（见常量注释），打印提示但
    # 继续执行（居中标样正常流程不会触发）
    off_px = float(np.hypot(center0_px[0] - image.shape[1] / 2.0,
                            center0_px[1] - image.shape[0] / 2.0))
    if off_px > OFF_CENTER_PX:
        print(f"NOTE: beam center is {off_px:.0f} px off the detector center — "
              f"refinement on partial rings is unreliable (pyFAI limitation); "
              f"prefer calibrating distance/tilt on a centered standard image")

    # 迭代精修：每次用当前几何重新提取控制点再 refine2，保留残差
    # 改善的解。收敛判据：残差 < REFINE_TARGET_RESIDUAL_DEG 提前停止；
    # 残差改善不足 REFINE_TOL 或达到最大轮数停止。
    best = None   # (residual_deg, ref, control_points)，取残差最小的一轮
    params = dict(dist=dist0_m,
                  poni1=center0_px[0] * pixel_size_m,
                  poni2=center0_px[1] * pixel_size_m,
                  rot1=0.0, rot2=0.0, rot3=0.0)
    for _ in range(REFINE_MAX_ITER):
        # 1) 按当前参数建几何，提取控制点
        geo = Geometry(
            dist=params["dist"],
            poni1=params["poni1"],
            poni2=params["poni2"],
            rot1=params["rot1"],
            rot2=params["rot2"],
            rot3=params["rot3"],
            detector=det,
            wavelength=wavelength_m,
        )
        single = SingleGeometry("lab6", image, calibrant=cal,
                                detector=det, geometry=geo)
        control_points = np.asarray(single.extract_cp(max_rings=max_rings).getList())

        # 2) 精修：距离 + 环心 + rot1/rot2（rot3 保持 0）
        ref = GeometryRefinement(
            data=control_points,
            calibrant=cal,
            dist=params["dist"],
            poni1=params["poni1"],
            poni2=params["poni2"],
            rot1=params["rot1"],
            rot2=params["rot2"],
            rot3=params["rot3"],
            detector=det,
            wavelength=wavelength_m,
        )
        ref.refine2()

        # 3) 残差检验，决定是否继续迭代
        residual_deg = _control_point_residual(ref, control_points,
                                               wavelength_m, max_rings)
        if best is None or residual_deg < best[0] - REFINE_TOL:
            best = (residual_deg, ref, control_points)
            params = dict(dist=float(ref.dist),
                          poni1=float(ref.poni1),
                          poni2=float(ref.poni2),
                          rot1=float(ref.rot1),
                          rot2=float(ref.rot2),
                          rot3=float(ref.rot3))
        else:
            break
        if residual_deg < REFINE_TARGET_RESIDUAL_DEG:
            break
    residual_deg, ref, control_points = best

    return {
        "dist_m": float(ref.dist),
        "poni1_px": float(ref.poni1 / pixel_size_m),
        "poni2_px": float(ref.poni2 / pixel_size_m),
        "offset_px": (
            float(ref.poni1 / pixel_size_m - center0_px[0]),
            float(ref.poni2 / pixel_size_m - center0_px[1]),
        ),
        "rot1_deg": float(np.degrees(ref.rot1)),
        "rot2_deg": float(np.degrees(ref.rot2)),
        "rot3_deg": float(np.degrees(ref.rot3)),
        "residual_deg": residual_deg,
        "control_points": [
            [float(x), float(y), int(r)] for x, y, r in control_points],
    }


def _control_point_residual(ref, control_points, wavelength_m, max_rings) -> float:
    """每个控制点的实测 2θ 与理论 2θ 偏差的 RMS（度）。

    ref.tth(x, y) 的输入为像素坐标。残差是精修质量的标尺：
    迭代精修以它判断收敛，返回值同时作为 calibrate_lab6 的
    residual_deg。
    """
    tth_theo = lab6_theoretical_2theta(wavelength_m, max_rings)
    residuals = []
    for ring in range(max_rings):
        sub = control_points[control_points[:, 2] == ring]
        if len(sub) == 0:
            continue
        measured = np.degrees(ref.tth(sub[:, 0], sub[:, 1]))
        residuals.append(measured - tth_theo[ring])
    if not residuals:
        return float("inf")   # 无任何控制点：残差无定义（视为最差）
    return float(np.sqrt(np.mean(np.concatenate(residuals) ** 2)))


def refine_lab6_from_points(points_px, ring_indices, *, pixel_size_m: float,
                            wavelength_m: float, dist0_m: float,
                            center0_px: tuple, max_rings: int = 16) -> dict:
    """手动选点校准：用户点击的环上点 → pyFAI 反推探测器几何。

    原理与 calibrate_lab6 完全相同（控制点 → GeometryRefinement.
    refine2 最小二乘精修距离/束心/倾斜角），区别只是控制点来源：
    自动 = extract_cp 按预测位置搜峰，手动 = 用户在图上点环
    （环号由 snap_lab6_ring 判定）。单轮精修即可——用户点固定不动，
    不需要"重取点再精修"的迭代。

    点太少时未知数多于方程（例如 3 点 5 未知数），refine2 可能给
    退化解或直接抛异常——异常如实转成 ValueError（调用方提示用户
    多点几个点），结果可信度以 residual_deg 为准。

    参数：
        points_px      [(x, y), ...] 像素坐标（横向、纵向）
        ring_indices   每个点所属的 LaB₆ 环序号（0 起，长度与
                       points_px 一致）
        pixel_size_m / wavelength_m  探测器像素尺寸（米）/ X 光波长（米）
        dist0_m        探测器距离初值（米，精修起点）
        center0_px     (cx, cy) 环心初值（像素）——通常取配置条目的
                       beam_center（(行, 列) 换序成 (列, 行)）
        max_rings      环号上限（与 snap_lab6_ring 一致，默认 16）

    返回：
        与 calibrate_lab6 同形 dict（dist_m/poni1_px/poni2_px/
        offset_px/rot1_deg/rot2_deg/rot3_deg/residual_deg，见其
        docstring）。

    报错：
        ValueError：点数 < 3、覆盖环 < 2、或 pyFAI refine2 抛异常。
    """
    points = np.asarray(points_px, dtype=np.float64)
    rings = np.asarray(ring_indices, dtype=np.int64)
    if points.ndim != 2 or points.shape[1] != 2:
        raise ValueError("points_px 必须是 [(x, y), ...] 的列表")
    if len(points) != len(rings):
        raise ValueError("points_px 与 ring_indices 长度必须一致")
    if len(points) < 3:
        raise ValueError("至少需要 3 个点")
    if len(np.unique(rings)) < 2:
        raise ValueError("至少需要覆盖 2 个不同的环")
    if np.any((rings < 0) | (rings >= max_rings)):
        raise ValueError(f"环序号越界（应在 0~{max_rings - 1} 内）")

    # 与 calibrate_lab6 相同的 pyFAI 注意事项：calibrant 波长一经
    # 设置不可再改，每次调用新建对象
    cal = get_calibrant(LAB6_NAME)
    cal.wavelength = wavelength_m
    det = Detector(pixel1=pixel_size_m, pixel2=pixel_size_m)
    control_points = np.column_stack([points[:, 0], points[:, 1], rings])
    ref = GeometryRefinement(
        data=control_points, calibrant=cal,
        dist=dist0_m,
        poni1=center0_px[0] * pixel_size_m,
        poni2=center0_px[1] * pixel_size_m,
        rot1=0.0, rot2=0.0, rot3=0.0,
        detector=det, wavelength=wavelength_m,
    )
    try:
        ref.refine2()
    except Exception as err:
        raise ValueError(f"pyFAI 精修失败（点多点几个环上的点会更稳）：{err}") from err
    residual_deg = _control_point_residual(ref, control_points,
                                           wavelength_m, max_rings)
    return {
        "dist_m": float(ref.dist),
        "poni1_px": float(ref.poni1 / pixel_size_m),
        "poni2_px": float(ref.poni2 / pixel_size_m),
        "offset_px": (
            float(ref.poni1 / pixel_size_m - center0_px[0]),
            float(ref.poni2 / pixel_size_m - center0_px[1]),
        ),
        "rot1_deg": float(np.degrees(ref.rot1)),
        "rot2_deg": float(np.degrees(ref.rot2)),
        "rot3_deg": float(np.degrees(ref.rot3)),
        "residual_deg": residual_deg,
    }


def integrate_1d(
    image: np.ndarray,
    pixel_size_m: float,
    wavelength_m: float,
    dist_m: float,
    poni1_m: float,
    poni2_m: float,
    rot1_deg: float,
    rot2_deg: float,
    npt: int = 3000,
    tth_min_deg: float | None = None,
    tth_max_deg: float | None = None,
) -> tuple:
    """
    方位角积分：把 2D 衍射图像变成 1D 图谱 I(2θ)。

    参数：
        image : np.ndarray
            2D 衍射强度数组
        pixel_size_m : float
            像素尺寸（米）
        wavelength_m : float
            X 光波长（米）
        dist_m, poni1_m, poni2_m : float
            探测器几何（米制），一般来自 calibrate_lab6 的结果
        rot1_deg, rot2_deg : float
            倾斜角（度）
        npt : int
            1D 曲线采样点数（默认 3000）
        tth_min_deg, tth_max_deg : float 或 None（可选）
            积分 2θ 区间（度）。None = 该侧不限（默认全探测器范围，
            CLI 脚本不传即走全范围）；传了就只积这个区间，npt 个
            采样点摊在区间内（区间越小分辨率越高）。两者都传时
            必须 tth_min_deg < tth_max_deg，否则 ValueError。

    备注：
        物理/几何参数全部必传、无默认值——由脚本从 config.py 的
        CONFIGS 选中条目取出后显式传入。若在函数签名中复制一份默认值，
        会与 config 形成两份独立数据，更新不同步时静默使用旧值；
        必传参数将此类错误转化为显式的 TypeError。范围参数是可选
        功能（GUI 积分设置传，CLI 不传 = 全范围），不是几何的一部分，
        所以带 None 默认。

    返回：
        (tth_deg, intensity) : tuple
            tth_deg     1D 曲线的 2θ 坐标（度），覆盖积分区间
            intensity   对应强度（numpy 数组）

    备注：
        integrate1d 默认输出单位为 q（nm⁻¹）而非 2θ，必须显式传
        unit="2th_deg"，否则 x 轴为 q 值（0~90）。
        束心偏离探测器中心超过 OFF_CENTER_PX（偏置摆法）时 pyFAI 径向
        分箱错误，自动切换自研 numpy 积分（_integrate_1d_diy，对任意
        束心位置正确；1D 曲线按存在的方位角归一化，与偏置摆法的
        扇形矩阵自洽）。
    """
    if tth_min_deg is not None and tth_max_deg is not None \
            and tth_max_deg <= tth_min_deg:
        raise ValueError(
            f"tth_min_deg ({tth_min_deg}) must be < tth_max_deg "
            f"({tth_max_deg})")
    if _is_off_center(image.shape, poni1_m, poni2_m, pixel_size_m):
        return _integrate_1d_diy(image, pixel_size_m, dist_m,
                                 poni1_m, poni2_m, npt,
                                 tth_min_deg=tth_min_deg,
                                 tth_max_deg=tth_max_deg)
    ai = AzimuthalIntegrator(
        dist=dist_m,
        poni1=poni1_m,
        poni2=poni2_m,
        rot1=np.radians(rot1_deg),
        rot2=np.radians(rot2_deg),
        rot3=0.0,
        pixel1=pixel_size_m,
        pixel2=pixel_size_m,
        wavelength=wavelength_m,
    )
    if tth_min_deg is None and tth_max_deg is None:
        tth_deg, intensity = ai.integrate1d(image, npt, unit="2th_deg")
    else:
        # radial_range 用输出单位（2th_deg → 度）；None = 该侧不限
        tth_deg, intensity = ai.integrate1d(
            image, npt, unit="2th_deg",
            radial_range=(tth_min_deg, tth_max_deg))
    return tth_deg, intensity


def integrate_sectors(
    image: np.ndarray,
    pixel_size_m: float,
    wavelength_m: float,
    dist_m: float,
    poni1_m: float,
    poni2_m: float,
    rot1_deg: float,
    rot2_deg: float,
    n_sectors: int = 36,
    npt: int = 3000,
) -> tuple:
    """
    扇形积分：把 0°~360° 方位角分成 n_sectors 个扇区，各自独立积分。

    用途：检查衍射环的方位均匀性——
      - 理想粉末（晶粒随机取向）：各扇区曲线应几乎一致；
      - 大晶粒 / 择优取向：部分扇区强度明显偏高；
      - 探测器几何失真：各扇区峰位互相错开。

    实现：pyFAI 的 integrate2d 一次调用输出径向（2θ）× 方位角（χ）
    二维矩阵，第 2 维即方位角分箱，无需循环调用 36 次 integrate1d。

    χ 角约定（合成图像验证）：χ = 0° 沿探测器水平方向（图像 +x 轴，
    向右），逆时针为正（图像上方 = +90°，下方 = −90°）。
    integrate2d 默认从 −180° 起分箱：χ[0] = −175°（[−180°, −170°)），
    χ = 0° 落在 ±5° 两个分箱（sector 17/18）。

    参数：
        image : np.ndarray
            2D 衍射强度数组
        像素/波长/几何参数与 integrate_1d 相同（全部必传，
        统一由脚本从 config.CONFIGS 选中条目传入，无默认值）
        n_sectors : int
            扇区数（默认 36，每 10° 一个）

    返回：
        (tth_deg, I2d, chi_centers_deg) : tuple
            tth_deg        1D 曲线的 2θ 坐标（度），长度 npt
            I2d            (npt, n_sectors) 矩阵，第 k 列 = 第 k 个扇区的强度
            chi_centers_deg  每个扇区中心方位角（度）

    备注：束心偏离探测器中心超过 OFF_CENTER_PX（偏置摆法）时 pyFAI
    径向分箱错误，自动切换自研 numpy 扇形积分
    （_integrate_sectors_diy，对任意束心位置正确）；此时 χ 标注由
    _covered_azimuth_labels 给出图像实际覆盖的方位角跨度（部分环
    摆法下不是整圈 −180°~180°，瀑布图 y 轴如实标注）。
    """
    if _is_off_center(image.shape, poni1_m, poni2_m, pixel_size_m):
        return _integrate_sectors_diy(image, pixel_size_m, dist_m,
                                      poni1_m, poni2_m, n_sectors, npt)
    ai = AzimuthalIntegrator(
        dist=dist_m,
        poni1=poni1_m,
        poni2=poni2_m,
        rot1=np.radians(rot1_deg),
        rot2=np.radians(rot2_deg),
        rot3=0.0,
        pixel1=pixel_size_m,
        pixel2=pixel_size_m,
        wavelength=wavelength_m,
    )
    # integrate2d：第 1 维径向（2θ），第 2 维方位角（χ）
    i2d, tth_deg, chi = ai.integrate2d(image, npt, n_sectors, unit="2th_deg")
    i2d = np.asarray(i2d)
    # 不同版本返回的形状可能转置，统一成 (npt, n_sectors)
    if i2d.shape[0] != npt and i2d.shape[1] == npt:
        i2d = i2d.T
    chi_centers_deg = np.asarray(chi, dtype=float)
    # 异常兜底：pyFAI 返回的 χ 数组长度不对或明显不是整圈分箱时，
    # 用图像实际覆盖的方位角范围重建标注，保证文件名/表头的
    # 扇区标注正确（近居中摆法时束心在图像内 → 整圈分箱）
    if len(chi_centers_deg) != n_sectors or \
            chi_centers_deg.max() - chi_centers_deg.min() < 350.0:
        chi_centers_deg = _covered_azimuth_labels(
            (poni1_m / pixel_size_m, poni2_m / pixel_size_m),
            image.shape, n_sectors)
    return tth_deg, i2d, chi_centers_deg


def _polar_bins(image, pixel_size_m, dist_m, poni1_m, poni2_m, npt,
                tth_min_deg=None, tth_max_deg=None):
    """逐像素极坐标分箱：返回 (tth_grid, idx, chi_px)。

    idx[i, j] = 像素 (i, j) 的 2θ 分箱号（0..npt-1），区间外的像素
    记 -1（计数前滤掉：bincount 不收负数）；chi_px[i, j] = 像素的方位角
    （度，约定同 integrate2d 返回值：atan2(dy, dx)，0° 沿 +x 向右、
    逆时针为正，图像下方 = +90°）。
    _integrate_1d_diy 与 _integrate_sectors_diy 共用此分箱，保证
    1D 曲线 = 扇形矩阵的加权平均（自洽）。范围参数与 integrate_1d
    同语义（None = 不限），扇形积分不传 = 全范围。
    """
    h, w = image.shape
    x0 = poni1_m / pixel_size_m
    y0 = poni2_m / pixel_size_m
    rows, cols = np.mgrid[0:h, 0:w]
    r_px = np.hypot(cols - x0, rows - y0)
    tth_px = np.degrees(np.arctan(r_px * pixel_size_m / dist_m))
    t_max = float(np.degrees(np.arctan(
        np.hypot(max(x0, w - x0), max(y0, h - y0)) * pixel_size_m / dist_m)))
    lo = 0.0 if tth_min_deg is None else max(0.0, tth_min_deg)
    hi = t_max if tth_max_deg is None else min(tth_max_deg, t_max)
    tth_grid = np.linspace(lo, hi, npt)
    idx = np.digitize(tth_px, tth_grid) - 1
    idx[(tth_px < lo) | (tth_px > hi)] = -1   # 区间外像素不进任何箱
    chi_px = np.degrees(np.arctan2(rows - y0, cols - x0))
    return tth_grid, idx, chi_px


def _integrate_1d_diy(image, pixel_size_m, dist_m, poni1_m, poni2_m, npt,
                      tth_min_deg=None, tth_max_deg=None):
    """自研 numpy 方位角积分（1D），束心偏离探测器中心过大时使用。

    pyFAI 2026.x 在该条件下径向分箱错误（见 OFF_CENTER_PX 注释），
    此函数用逐像素极坐标直接分箱，对任意束心位置都正确：
      1) 每个像素的 2θ = atan(到束心距离 · pixel / dist)；
      2) digitize 到 npt 个等距 2θ 分箱（区间外的像素不参与）；
      3) 每箱强度 = 箱内像素强度平均（bincount 累加 / 计数），
         空箱填 NaN。
    强度按存在的方位角归一化——曲线上看不到环被截断的失效，
    与偏置摆法的扇形矩阵自洽（曲线 = 各扇区加权平均）。
    """
    tth_grid, idx, _ = _polar_bins(image, pixel_size_m, dist_m,
                                   poni1_m, poni2_m, npt,
                                   tth_min_deg=tth_min_deg,
                                   tth_max_deg=tth_max_deg)
    flat = idx.ravel()
    keep = flat >= 0   # 区间外像素（-1）不进任何箱：bincount 不收负数
    flat = flat[keep]
    counts = np.bincount(flat, minlength=npt)
    sums = np.bincount(flat,
                       weights=image.ravel().astype(np.float64)[keep],
                       minlength=npt)
    intensity = np.full(npt, np.nan)
    alive = counts > 0
    intensity[alive] = sums[alive] / counts[alive]
    return tth_grid, intensity


def _integrate_sectors_diy(image, pixel_size_m, dist_m, poni1_m, poni2_m,
                           n_sectors, npt):
    """自研 numpy 扇形积分（2D），束心偏离探测器中心过大时使用。

    与 _integrate_1d_diy 相同的径向分箱（共享 2θ 网格与箱号），再按
    每个像素的方位角 χ 划分扇区：扇区 k 覆盖
    [−180°+width·k, −180°+width·(k+1))。死角（箱内无像素）填 NaN。
    束心在图像外时部分扇区整列死区，即部分环数据的真实形态。
    返回 (tth_deg, I2d, chi_centers_deg)，I2d 形状 (npt, n_sectors)。
    """
    tth_grid, idx, chi_px = _polar_bins(image, pixel_size_m, dist_m,
                                        poni1_m, poni2_m, npt)
    width = 360.0 / n_sectors
    I2d = np.full((npt, n_sectors), np.nan)
    img64 = image.astype(np.float64)
    for k in range(n_sectors):
        c0 = -180.0 + width * k
        sel = (chi_px >= c0) & (chi_px < c0 + width)
        flat = idx[sel]
        counts = np.bincount(flat, minlength=npt)
        sums = np.bincount(flat, weights=img64[sel], minlength=npt)
        col = np.full(npt, np.nan)
        alive = counts > 0
        col[alive] = sums[alive] / counts[alive]
        I2d[:, k] = col
    chi = np.linspace(-180.0 + width / 2.0, 180.0 - width / 2.0, n_sectors)
    return tth_grid, I2d, chi


def _covered_azimuth_labels(poni_px, image_shape, n_sectors):
    """图像实际覆盖的方位角范围 → n_sectors 个等距扇区中心 χ（度）。

    束心在图像内 → 整圈分箱（中心从 −180°+半宽到 180°−半宽）。
    束心在图像外 → 只覆盖有限方位角跨度：矩形在图像外视角的张角
    < 180°，四个角相对束心的 χ = atan2(corner_y − y0, corner_x − x0)
    （约定同 integrate2d 返回值：0° 沿 +x 向右、逆时针为正）。以
    "指向图像中心的方向"为基准展开到同一圈（±180° 断层可能穿过
    图像方向，直接取 min/max 会取到背向图像的空半面），跨度 =
    展开后最大 − 最小，等距取 n_sectors 个中心值——瀑布图 y 轴
    标注真实覆盖的方位角，不假装整圈。
    """
    h, w = image_shape
    x0, y0 = poni_px
    if 0 < x0 < w and 0 < y0 < h:
        return np.linspace(-180.0 + 180.0 / n_sectors,
                           180.0 - 180.0 / n_sectors, n_sectors)
    phi0 = float(np.degrees(np.arctan2(h / 2.0 - y0, w / 2.0 - x0)))
    corners = np.array([[0.0, 0.0], [w, 0.0], [w, h], [0.0, h]])
    chi_c = np.degrees(np.arctan2(corners[:, 1] - y0, corners[:, 0] - x0))
    d = (chi_c - phi0 + 180.0) % 360.0 - 180.0   # d ∈ (−180°, 180°]
    chi_unw = phi0 + d
    lo, hi = float(chi_unw.min()), float(chi_unw.max())
    return np.linspace(lo, hi, n_sectors)


def calibrate_and_integrate(
    image: np.ndarray,
    pixel_size_m: float = 200e-6,
    wavelength_m: float = 0.1223e-10,
    dist0_m: float = 1.6,
    *,
    center0_px: tuple,
    max_rings: int = 16,
    npt: int = 3000,
) -> tuple:
    """
    先校准几何，再用校准结果做方位角积分。

    参数与 calibrate_lab6 / integrate_1d 相同。

    返回：
        (tth_deg, intensity, geometry) : tuple
            tth_deg, intensity  1D 图谱
            geometry            calibrate_lab6 返回的几何结果 dict
    """
    geometry = calibrate_lab6(
        image,
        pixel_size_m=pixel_size_m,
        wavelength_m=wavelength_m,
        dist0_m=dist0_m,
        center0_px=center0_px,
        max_rings=max_rings,
    )
    tth_deg, intensity = integrate_1d(
        image,
        pixel_size_m=pixel_size_m,
        wavelength_m=wavelength_m,
        dist_m=geometry["dist_m"],
        poni1_m=geometry["poni1_px"] * pixel_size_m,
        poni2_m=geometry["poni2_px"] * pixel_size_m,
        rot1_deg=geometry["rot1_deg"],
        rot2_deg=geometry["rot2_deg"],
        npt=npt,
    )
    return tth_deg, intensity, geometry
