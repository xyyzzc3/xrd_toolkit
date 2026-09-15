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
from xrd_toolkit.core.processor import find_ring_center  # 自动定位环心（校准初值）

# pyFAI 内置信标数据库（calibrant），LaB₆ 标准 d 值：
#   d = a / sqrt(h² + k² + l²)，a = 4.1568 Å（NIST 标准值）
LAB6_NAME = "LaB6"


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


def calibrate_lab6(
    image: np.ndarray,
    pixel_size_m: float = 200e-6,
    wavelength_m: float = 0.1223e-10,
    dist0_m: float = 1.6,
    center0_px: tuple = None,
    max_rings: int = 16,
) -> dict:
    """
    用 LaB6 标样自动校准探测器几何（pyFAI GeometryRefinement）。

    原理（两步）：
      1) extract_cp：按初始几何预测每个环的 2θ 位置，在预测位置附近
         搜索图像上的真实峰位，得到若干控制点（像素坐标 + 环序号）；
      2) refine2：最小二乘精修距离、环心、倾斜角，使各控制点的实测
         2θ 逼近理论 2θ。

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
            环心初值（像素，cx, cy）。不传时自动定位
            （find_ring_center，亚像素精度 <1 px）
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

    备注（pyFAI 接口注意事项）：
        - calibrant 的波长一经设置不可再改；换波长必须重新
          get_calibrant 创建新对象。
        - extract_cp 返回 ControlPoints 对象，getList() 才是 N×3 数组，
          第三列为环序号（从 0 开始），不是 2θ。
        - GeometryRefinement.tth() 的输入为像素坐标，不是米。
    """
    # pyFAI 内置 LaB₆ 校准数据（d1 = 4.1568 Å ≈ a）
    cal = get_calibrant(LAB6_NAME)
    cal.wavelength = wavelength_m

    det = Detector(pixel1=pixel_size_m, pixel2=pixel_size_m, max_shape=image.shape)

    # 环心初值：未指定时自动定位。find_ring_center 返回 (行, 列)，
    # 校准需要 (cx, cy)，交换顺序。
    if center0_px is None:
        cy, cx = find_ring_center(image)
        center0_px = (cx, cy)

    # 1) 按初值建几何，提取控制点
    init_geo = Geometry(
        dist=dist0_m,
        poni1=center0_px[0] * pixel_size_m,
        poni2=center0_px[1] * pixel_size_m,
        detector=det,
        wavelength=wavelength_m,
    )
    single = SingleGeometry("lab6", image, calibrant=cal, detector=det, geometry=init_geo)
    control_points = np.asarray(single.extract_cp(max_rings=max_rings).getList())

    # 2) 精修：距离 + 环心 + rot1/rot2（rot3 保持 0）
    ref = GeometryRefinement(
        data=control_points,
        calibrant=cal,
        dist=dist0_m,
        poni1=center0_px[0] * pixel_size_m,
        poni2=center0_px[1] * pixel_size_m,
        rot1=0.0,
        rot2=0.0,
        rot3=0.0,
        detector=det,
        wavelength=wavelength_m,
    )
    ref.refine2()

    # 3) 残差检验：每个控制点的实测 2θ 与理论 2θ 的偏差
    tth_theo = lab6_theoretical_2theta(wavelength_m, max_rings)
    residuals = []
    for ring in range(max_rings):
        sub = control_points[control_points[:, 2] == ring]
        if len(sub) == 0:
            continue
        measured = np.degrees(ref.tth(sub[:, 0], sub[:, 1]))  # 输入为像素坐标
        residuals.append(measured - tth_theo[ring])
    residual_deg = float(np.sqrt(np.mean(np.concatenate(residuals) ** 2)))

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

    备注：
        物理/几何参数全部必传、无默认值——由脚本从 config.py 的
        CONFIGS 选中条目取出后显式传入。若在函数签名中复制一份默认值，
        会与 config 形成两份独立数据，更新不同步时静默使用旧值；
        必传参数将此类错误转化为显式的 TypeError。

    返回：
        (tth_deg, intensity) : tuple
            tth_deg     1D 曲线的 2θ 坐标（度）
            intensity   对应强度（numpy 数组）

    备注：
        integrate1d 默认输出单位为 q（nm⁻¹）而非 2θ，必须显式传
        unit="2th_deg"，否则 x 轴为 q 值（0~90）。
    """
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
    tth_deg, intensity = ai.integrate1d(image, npt, unit="2th_deg")
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
    """
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
    # 束心在图像内时 pyFAI 返回覆盖 -180°~180° 的整圈分箱；束心在图像外
    # 时只返回图像实际覆盖的方位角范围（长度仍为 n_sectors，数值不对应
    # 全局扇区），此时退回均匀分箱，保证文件名/表头的扇区标注正确
    if len(chi_centers_deg) != n_sectors or \
            chi_centers_deg.max() - chi_centers_deg.min() < 350.0:
        chi_centers_deg = np.linspace(-180 + 180 / n_sectors,
                                      180 - 180 / n_sectors, n_sectors)
    return tth_deg, i2d, chi_centers_deg


def calibrate_and_integrate(
    image: np.ndarray,
    pixel_size_m: float = 200e-6,
    wavelength_m: float = 0.1223e-10,
    dist0_m: float = 1.6,
    center0_px: tuple = None,
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
