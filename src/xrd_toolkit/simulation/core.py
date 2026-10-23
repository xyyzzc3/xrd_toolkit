"""XRD 模拟计算核心：布拉格定律 + 结构因子（手写实现），以及 pymatgen 参考版。

本模块由 RY04 第六周任务并入（2026-10-10）。两类入口：

- 手写版（默认）：`pattern_from_structure` / `pattern_from_cif`。
  每一步公式自写：面间距（度量矩阵求逆）、布拉格定律、散射因子、
  结构因子、多重性（对称操作轨道）、洛伦兹偏振因子。三个材料
  （Si / LaB₆ / Al₂O₃）与 pymatgen 的 XRDCalculator 逐峰一致
  （max Δ2θ = 0.0000°，见 tests/test_simulation.py）。

- 参考版：`pattern_from_structure_pymatgen` / `pattern_from_cif_pymatgen`，
  包装 pymatgen 的 XRDCalculator，用于对照与快速验证。

pymatgen 属可选依赖：`pip install pymatgen`，或 `pip install 'xrd-toolkit[simulation]'`。
"""
import math
import re
from pathlib import Path

import numpy as np

try:
    from pymatgen.analysis.diffraction.xrd import ATOMIC_SCATTERING_PARAMS, XRDCalculator
    from pymatgen.core import Structure
    from pymatgen.core.periodic_table import Element
    from pymatgen.symmetry.groups import SpaceGroup
except ImportError as exc:  # pragma: no cover - 依赖缺失时的友好提示
    raise ImportError(
        "xrd_toolkit.simulation 需要 pymatgen：pip install pymatgen，"
        "或 pip install 'xrd-toolkit[simulation]'"
    ) from exc


# ---------------- 第 1 步：面间距 ----------------

def d_spacing(hkl, lattice_matrix):
    """面间距 d(hkl)。通用做法：度量矩阵 G = A·Aᵀ 取逆，d = 1/√(hᵀG⁻¹h)。

    立方晶系时它退化成 d = a/√(h²+k²+l²)；六方晶系时等于
    1/d² = (4/3)(h²+hk+k²)/a² + l²/c²。一个公式管所有晶系。
    """
    h = np.asarray(hkl, dtype=float)
    g_inv = np.linalg.inv(lattice_matrix @ lattice_matrix.T)
    return 1.0 / math.sqrt(float(h @ g_inv @ h))


# ---------------- 第 2 步：布拉格定律 ----------------

def two_theta_from_d(d, wavelength=1.5406):
    """布拉格定律 sinθ = λ/(2d) → 2θ（度）。算不出（sinθ≥1）就返回 None。"""
    sin_theta = wavelength / (2.0 * d)
    if sin_theta >= 1.0:
        return None
    return 2.0 * math.degrees(math.asin(sin_theta))


# ---------------- 第 3 步：散射因子与结构因子 ----------------

def scattering_factor(symbol, s2):
    """原子散射因子 f(s)：查系数表，自己套公式。

    f(s) = Z − 41.78214·s²·Σ aᵢ·exp(−bᵢ·s²)，s = sinθ/λ，s2 = s²。
    s→0 时 f→Z（原子序数），是个好用的自检。
    """
    coeff = np.asarray(ATOMIC_SCATTERING_PARAMS[symbol])  # 形状 (n,2)，两列是 aᵢ、bᵢ
    z = Element(symbol).Z
    return z - 41.78214 * s2 * float(np.sum(coeff[:, 0] * np.exp(-coeff[:, 1] * s2)))


def structure_factor(hkl, structure, s, b_iso=None):
    """结构因子 F(hkl)：对晶胞里每个原子求和。

    F = Σ_j f_j(s)·occ_j·exp[ 2πi (h·x_j + k·y_j + l·z_j) ]
    （每个原子散射的波因位置不同有相位差，复数求和得到总振幅）

    b_iso（可选）= 原子的**温度因子** B（Debye-Waller），Å²：热振动让
    每个原子的散射振幅按 f → f·exp(−B·s²) 衰减（B = 8π²⟨u²⟩，室温典型
    0.3~1 Å²；振动越厉害、高角度衰减越狠——这正是一条"真实效应"）。
    传单个 float（全体原子同 B）或 {元素符号: B} 字典（分元素）。默认
    None = 理想刚性晶格、不衰减（第 6 周原口径）。
    """
    h, k, l = hkl
    s2 = s * s
    F = 0j
    for site in structure:
        for sp, occ in site.species.items():
            f = scattering_factor(sp.symbol, s2)
            if b_iso is not None:
                b = (float(b_iso) if not isinstance(b_iso, dict)
                     else float(b_iso.get(sp.symbol, 0.0)))
                f *= math.exp(-b * s2)
            phase = 2.0 * math.pi * (h * site.frac_coords[0]
                                     + k * site.frac_coords[1]
                                     + l * site.frac_coords[2])
            F += f * occ * complex(math.cos(phase), math.sin(phase))
    return F


def structure_factor_partials(hkl, structure, s):
    """分元素的复结构因子（不含温度因子）：{元素: Σ occ·f(s²)·e^{iφ}}。

    与 `structure_factor` 的区别：不乘 Debye-Waller，按元素分组返回。
    精修分元素 B 时，F(hkl) = Σ_e A_e · exp(−B_e·s²)，可以每步重算而
    不用重建整张峰表。
    """
    h, k, l = hkl
    s2 = s * s
    out = {}
    for site in structure:
        for sp, occ in site.species.items():
            f = scattering_factor(sp.symbol, s2)
            phase = 2.0 * math.pi * (h * site.frac_coords[0]
                                     + k * site.frac_coords[1]
                                     + l * site.frac_coords[2])
            z = f * occ * complex(math.cos(phase), math.sin(phase))
            out[sp.symbol] = out.get(sp.symbol, 0j) + z
    return out


def march_dollase_cos2(hkl, axis, lattice_matrix):
    """晶面法向与取向轴夹角的 cos²（March-Dollase 的几何因子）。"""
    g_star = np.linalg.inv(np.asarray(lattice_matrix)
                           @ np.asarray(lattice_matrix).T)
    h = np.asarray(hkl, dtype=float)
    a = np.asarray(axis, dtype=float)
    hg, ag = h @ g_star @ h, a @ g_star @ a
    if hg <= 0 or ag <= 0:
        return 0.0
    cos_a = float(h @ g_star @ a) / math.sqrt(hg * ag)
    cos_a = min(1.0, max(-1.0, cos_a))
    return cos_a * cos_a


def reflection_table_details(structure, wavelength=1.5406,
                             two_theta_range=(10.0, 80.0), nmax=10,
                             polariz=0.5):
    """逐条反射的**动态强度**表：hkl/d/2θ/多重性/LP/s²/分元素复 F。

    给分元素 B 与可精修织构用：强度不再一次性算死，模型可以在每次
    最小二乘评估时按当前的 B_e / R 重算：
        I_r = m_r·LP_r·|Σ_e A_{r,e}·exp(−B_e·s²)|² · P_md(hkl; R)
    返回 (hkl, d, tth, mult, lp, s2, partials)，partials 是
    {元素: 复数数组}（无温度因子）；hkl 是代表反射。
    """
    lo, hi = two_theta_range
    lattice = structure.lattice.matrix
    is_hex = structure.lattice.is_hexagonal()
    rot_mats = distinct_rotations(structure.get_space_group_info()[0])
    hkls, ds, tths, ms, lps, s2s = [], [], [], [], [], []
    partials = {}
    visited = set()
    for h in range(-nmax, nmax + 1):
        for k in range(-nmax, nmax + 1):
            for l in range(-nmax, nmax + 1):
                if (h, k, l) == (0, 0, 0):
                    continue
                d = d_spacing((h, k, l), lattice)
                tt = two_theta_from_d(d, wavelength)
                if tt is None or not (lo <= tt <= hi):
                    continue
                if (h, k, l) in visited:
                    continue
                orbit = multiplicity((h, k, l), rot_mats)
                visited |= orbit
                m = len(orbit)
                theta = math.radians(tt / 2.0)
                s = math.sin(theta) / wavelength
                part = structure_factor_partials((h, k, l), structure, s)
                rep = (_hex_representative(orbit) if is_hex
                       else _positive_representative(orbit))
                hkls.append(rep)
                ds.append(d)
                tths.append(tt)
                ms.append(m)
                lps.append(lp_factor(theta, polariz))
                s2s.append(s * s)
                for elem, val in part.items():
                    partials.setdefault(elem, []).append(val)
    order = np.argsort(tths)
    hkls = np.asarray(hkls, dtype=int)[order]
    ds = np.asarray(ds, dtype=float)[order]
    tths = np.asarray(tths, dtype=float)[order]
    ms = np.asarray(ms, dtype=float)[order]
    lps = np.asarray(lps, dtype=float)[order]
    s2s = np.asarray(s2s, dtype=float)[order]
    partials = {e: np.asarray(v, dtype=complex)[order]
                for e, v in partials.items()}
    return hkls, ds, tths, ms, lps, s2s, partials


def march_dollase(hkl, axis, r, lattice_matrix):
    """择优取向的 March-Dollase 因子 P(hkl)（单参数模型）。

    用户/仪器条件里若存在织构（压片取向、针状/片状晶粒），某些晶面的
    取向概率偏离随机分布，峰强随之异常。标准模型（Dollase 1986）按
    "晶面法向 h 与取向轴 a 的夹角 α"给每个反射一个乘数：

        P = ( R²·cos²α + sin²α/R )^(−3/2)

    R = 1 → P ≡ 1（无取向，逐点复原）；R < 1 → 法向沿轴的晶面**增强**
    （cosα=1 时 P = R⁻³），R > 1 → 沿轴的晶面被压低。α 用倒空间度量算
    （两个晶面法向的夹角），所以只要传 (hkl) 形式的轴（如六方的
    (0,0,1)）就行，晶系自动正确。
    """
    g_star = np.linalg.inv(np.asarray(lattice_matrix) @ np.asarray(lattice_matrix).T)
    h = np.asarray(hkl, dtype=float)
    a = np.asarray(axis, dtype=float)
    hg, ag = h @ g_star @ h, a @ g_star @ a
    cos_a = float(h @ g_star @ a) / math.sqrt(hg * ag)
    cos_a = min(1.0, max(-1.0, cos_a))
    sin2 = 1.0 - cos_a * cos_a
    return (r * r * cos_a * cos_a + sin2 / r) ** -1.5


# ---------------- 第 4 步：多重性与洛伦兹偏振因子 ----------------

def distinct_rotations(sg_symbol):
    """取空间群所有对称操作里"不同的旋转部分"（3×3 整数矩阵，去重）。"""
    rots, seen = [], set()
    for op in SpaceGroup(sg_symbol).symmetry_ops:
        R = np.rint(np.asarray(op.rotation_matrix)).astype(int)
        key = tuple(R.ravel())
        if key not in seen:
            seen.add(key)
            rots.append(R)
    return rots


def multiplicity(hkl, rot_mats):
    """多重性 m：把 (hkl) 用每个对称操作转一圈，数有几种不同结果。

    倒空间里晶面指数的变换是 h' = (R⁻¹)ᵀ·h，这是标准公式。
    """
    h = np.asarray(hkl)
    orbit = set()
    for R in rot_mats:
        hh = np.rint(np.linalg.inv(R).T @ h).astype(int)
        orbit.add(tuple(int(v) for v in hh))
    return orbit


def lp_factor(theta, polariz=0.5):
    """洛伦兹-偏振因子（θ 是布拉格角，弧度）。

    GSAS-II 口径：偏振项 P(2θ) = P + (1−P)·cos²2θ（方位角 0），
    Lorentz 项 = 1/(2 sin²θ cosθ)，两者相乘。这里前面留了一个常数
    4（比例因子会被峰表归一化吸收），使 polariz=0.5 时与第七周的
    (1+cos²2θ)/(sin²θ·cosθ) 逐位相同——旧结果不动，只多了偏振度
    这个自由度（同步辐射常用 P≈0.95；P=0.5 = 非偏振）。
    """
    pola = float(polariz) + (1.0 - float(polariz)) * math.cos(2.0 * theta) ** 2
    return 4.0 * pola / (2.0 * math.sin(theta) ** 2 * math.cos(theta))


def _conventionalize_refs(refs):
    """把"同一常规反射家族"的条目合并，多重性相加。

    六方晶系里 (h k l) 和 (h k -l) 这类指数在点群下是两个不同的轨道
    （本函数把它们并成常规晶体学表里的一条，例如 x6 + x6 → x12）；
    立方等其它晶系不受影响。显示口径的变化不影响强度计算。
    """
    order, groups = [], {}
    for hkl, m in refs:
        key = tuple(sorted(abs(v) for v in hkl))
        if key not in groups:
            groups[key] = [hkl, 0]
            order.append(key)
        groups[key][1] += m
    return [(groups[key][0], groups[key][1]) for key in order]


def format_hkls(refs, is_hex, four_index=False):
    """把 (代表 hkl, 多重性) 列表排成 '(111) x8'、'(300) x6, (221) x24' 式短文本。

    **默认三位卡片式**（用户 2026-10-17："hkl 就显示三位，图上不要写四位"）：
    六方写与 PDF 卡片常见行号一致的三位正指数（如 (104)、(116)）；
    four_index=True 才写四位的对称等价 (h k i l)（i = -h-k，晶体学表口径）。
    非六方晶系本来就只有三位，不受这个开关影响。
    """
    parts = []
    for (h, k, l), m in refs:
        if is_hex and four_index:
            s = f"({h} {k} {-h - k} {l})"
        elif all(abs(v) <= 9 for v in (h, k, l)):
            s = f"({h}{k}{l})"
        else:                      # 两位数的指数留空格防歧义（如 1 0 10）
            s = f"({h} {k} {l})"
        if m:
            s += f" x{m}"
        parts.append(s)
    return ", ".join(parts)


# ---------------- 显示代表 ----------------

def _positive_representative(orbit):
    """"正半边"里最大的一个（其它晶系用；(111) 家族显示成 (111) 而不是 (1-11)）。"""
    halves = [cand for cand in orbit
              if cand[0] > 0 or (cand[0] == 0 and cand[1] > 0)
              or (cand[0] == 0 and cand[1] == 0 and cand[2] > 0)]
    return max(halves) if halves else max(orbit)   # 后者是非中心对称的兜底


def _hex_representative(orbit):
    """六方晶系的显示代表：优先 h ≥ k ≥ 0（并列取 l 大者）。

    与晶体学表常用的正指数写法一致——(110)、(113)、(116)、(214)、(300) 等，
    也是 PDF 卡片上最常见的行号形式。注意：同一族面在不同卡片里可能出现
    等价但数字不同的写法（例如 (102) 与 (012)，2θ 相同），本函数只保证
    自身一致，不逐字复刻某张特定卡片。找不到非负组合时退回正半边规则。
    """
    cands = [m for m in orbit if m[0] >= m[1] >= 0]
    if not cands:
        cands = [m for m in orbit if m[0] >= 0 and m[1] >= 0]
    if cands:
        return max(cands)          # 元组比较：先 h 再 k 再 l
    return _positive_representative(orbit)


# ---------------- 主流程 ----------------

def pattern_from_structure(structure, wavelength=1.5406, two_theta_range=(10.0, 80.0),
                           nmax=10, merge_tol=1e-3, min_intensity=0.01,
                           b_iso=None, md=None, polariz=0.5):
    """手写版主流程。返回 (2θ 数组, 归一化强度数组, 峰表, 是否六方)。

    峰表每项：[2θ, 强度%, [(代表 hkl, 多重性), ...]]
    nmax 要足够大：比如刚玉到 80° 需要 l 到 10。

    b_iso / md（可选，2026-10-17 RY04 第七周加）：温度因子（见
    structure_factor 的 b_iso）与择优取向——md = (轴, R)，见
    march_dollase；强度 = m·|F|²·LP·P_md。两者默认 None = 第 6 周的
    理想晶体口径，老结果逐位不变。

    polariz（可选，2026-10-22 向 GSAS-II 对齐）：光束偏振度 P，
    0.5 = 非偏振（默认，与旧结果逐位相同），同步辐射常用 0.95。
    """
    lo, hi = two_theta_range
    lattice = structure.lattice.matrix
    is_hex = structure.lattice.is_hexagonal()
    rot_mats = distinct_rotations(structure.get_space_group_info()[0])

    candidates, visited = [], set()
    for h in range(-nmax, nmax + 1):
        for k in range(-nmax, nmax + 1):
            for l in range(-nmax, nmax + 1):
                if (h, k, l) == (0, 0, 0):
                    continue
                tt = two_theta_from_d(d_spacing((h, k, l), lattice), wavelength)
                if tt is None or not (lo <= tt <= hi):
                    continue
                if (h, k, l) in visited:          # 这个晶面家族已处理过
                    continue
                orbit = multiplicity((h, k, l), rot_mats)
                visited |= orbit
                m = len(orbit)
                theta = math.radians(tt / 2.0)
                s = math.sin(theta) / wavelength
                F = structure_factor((h, k, l), structure, s, b_iso=b_iso)
                intensity = m * abs(F) ** 2 * lp_factor(theta, polariz)
                if md is not None:
                    axis, r_md = md
                    intensity *= march_dollase((h, k, l), axis, r_md, lattice)

                # 报告用的代表：六方晶系取规范正指数写法（见 _hex_representative），
                # 其它晶系取"正半边"里最大的（(111) 家族显示成 (111) 而不是 (1-11)）
                rep = (_hex_representative(orbit) if is_hex
                       else _positive_representative(orbit))
                candidates.append((tt, intensity, rep, m))

    candidates.sort(key=lambda r: r[0])
    # 合并 2θ 退化重叠的峰（比如 LaB6 的 (300) 和 (221)）
    merged = []
    for tt, inten, rep, m in candidates:
        if merged and abs(tt - merged[-1][0]) < merge_tol:
            merged[-1][1] += inten
            merged[-1][2].append((rep, m))
        else:
            merged.append([tt, inten, [(rep, m)]])

    imax = max(v[1] for v in merged)
    peaks = [[tt, inten / imax * 100.0, _conventionalize_refs(refs)]
             for tt, inten, refs in merged
             if inten / imax * 100.0 >= min_intensity]
    two_thetas = np.array([p[0] for p in peaks])
    intensities = np.array([p[1] for p in peaks])
    return two_thetas, intensities, peaks, is_hex


def reflection_table(structure, wavelength=1.5406, two_theta_range=(10.0, 80.0),
                     nmax=10, b_iso=None, md=None, polariz=0.5):
    """逐条反射的“可精修晶胞”表：代表 hkl、d、2θ、未归一化强度。

    与 `pattern_from_structure` 的区别：**不按 2θ 合并**，并保留 hkl 与 d。
    拟合器要改晶胞时，必须知道每条峰对应的 hkl 才能重算 d/2θ；合并后
    （如 (300)+(221)）就没法各自随晶胞移动了。

    返回 (hkl, d, tth, intensity) 四个数组；hkl 形如 (N,3)，d 单位 Å，
    tth 单位度，intensity = m·|F|²·LP·P_md（相对值，未归一化）。
    """
    lo, hi = two_theta_range
    lattice = structure.lattice.matrix
    is_hex = structure.lattice.is_hexagonal()
    rot_mats = distinct_rotations(structure.get_space_group_info()[0])

    hkls, ds, tths, ints = [], [], [], []
    visited = set()
    for h in range(-nmax, nmax + 1):
        for k in range(-nmax, nmax + 1):
            for l in range(-nmax, nmax + 1):
                if (h, k, l) == (0, 0, 0):
                    continue
                d = d_spacing((h, k, l), lattice)
                tt = two_theta_from_d(d, wavelength)
                if tt is None or not (lo <= tt <= hi):
                    continue
                if (h, k, l) in visited:
                    continue
                orbit = multiplicity((h, k, l), rot_mats)
                visited |= orbit
                m = len(orbit)
                theta = math.radians(tt / 2.0)
                s = math.sin(theta) / wavelength
                F = structure_factor((h, k, l), structure, s, b_iso=b_iso)
                intensity = m * abs(F) ** 2 * lp_factor(theta, polariz)
                if md is not None:
                    axis, r_md = md
                    intensity *= march_dollase((h, k, l), axis, r_md, lattice)
                rep = (_hex_representative(orbit) if is_hex
                       else _positive_representative(orbit))
                hkls.append(rep)
                ds.append(d)
                tths.append(tt)
                ints.append(intensity)
    order = np.argsort(tths)
    return (np.asarray(hkls, dtype=int)[order],
            np.asarray(ds, dtype=float)[order],
            np.asarray(tths, dtype=float)[order],
            np.asarray(ints, dtype=float)[order])


def b_iso_from_cif(cif_path):
    """从 CIF 原子行读**各向同性**温度因子 Uiso → {元素符号: B}（Å²）。

    B = 8π²·Uiso（Debye-Waller 系数，室温典型 0.3~1 Å²）。热振动让每个
    原子的散射振幅按 exp(−B·s²) 衰减（s = sinθ/λ），**高角度峰衰减最狠**——
    这正是 RY04 第七周任务三"热振动"参数的数据来源：直接喂给
    `simulate_pattern(b_iso=…)` / `structure_factor(..., b_iso=…)` 即可。

    每元素取首次出现的 Uiso（如 LaB₆："La … Uiso .00858(29) 1"）。
    两类情况返回**不含该元素**（调用方按理想刚性晶格处理即可）：
      * CIF 没写 Uiso（如 Si_9008565，早期文献条目）；
      * 只写了各向异性位移 U11/U22/…（如 Al₂O₃_1000032）——各向异性没实现，
        不硬折算成假各向同性。
    """
    out = {}
    for raw in Path(cif_path).read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith(("#", "_", "loop_")):
            continue
        parts = s.split()
        if "Uiso" in parts:
            u = float(parts[parts.index("Uiso") + 1].split("(")[0])
            elem = re.match(r"[A-Za-z]+", parts[0]).group(0)
            out.setdefault(elem, 8.0 * math.pi ** 2 * u)
    return out


def reflection_table_sites(structure, wavelength=1.5406,
                           two_theta_range=(10.0, 80.0), nmax=10,
                           polariz=0.5):
    """逐**位点**的动态强度表（原子坐标/占位精修用）。

    与 `reflection_table_details` 相同的几何量，但强度不再按元素分组，
    而是保留每个位点的散射因子，方便每次最小二乘评估时用当前坐标
    重算相位：
        F_r = Σ_j [Σ_sp f_sp(s_r)·occ_sp·e^(−B_sp s_r²)]·e^(2πi h_r·x_j)
    返回 (hkl, d, tth, mult, lp, s2, f_species, species, site_occ)：
        f_species  (n_ref, n_species) 每个元素在每个反射上的 f(s)
        species    元素符号列表（f_species 的列顺序）
        site_occ   (n_sites, n_species) CIF 里的分数占位（混合位点
                   如 Fe:Mn = 0.497:0.503 会落在同一行）
    """
    hkl, d, tth, mult, lp, s2, _partials = reflection_table_details(
        structure, wavelength=wavelength,
        two_theta_range=two_theta_range, nmax=nmax, polariz=polariz)
    species = sorted({sp.symbol for site in structure for sp in site.species})
    col = {e: i for i, e in enumerate(species)}
    site_occ = np.zeros((len(structure), len(species)), dtype=float)
    for j, site in enumerate(structure):
        for sp, occ in site.species.items():
            site_occ[j, col[sp.symbol]] += float(occ)
    f_species = np.empty((len(tth), len(species)), dtype=float)
    for r, s2r in enumerate(np.asarray(s2, dtype=float)):
        for c, elem in enumerate(species):
            f_species[r, c] = scattering_factor(elem, float(s2r))
    return hkl, d, tth, mult, lp, s2, f_species, species, site_occ


def load_structure(cif_path):
    """读 CIF（统一入口的薄包装）。"""
    return Structure.from_file(cif_path)


def pattern_from_cif(cif_path, wavelength=1.5406, two_theta_range=(10.0, 80.0)):
    """方便入口：直接从 CIF 文件算（手写版）。"""
    return pattern_from_structure(
        Structure.from_file(cif_path), wavelength, two_theta_range)


def pattern_from_structure_pymatgen(structure, wavelength=1.5406, two_theta_range=(10.0, 80.0)):
    """参考版：pymatgen XRDCalculator 从 Structure 算谱。

    返回 (2θ 数组, 归一化强度数组, hkl 信息列表)；hkl 信息为
    [{'hkl': (h, k, l), 'multiplicity': m}, ...] 的列表（每峰一条）。
    """
    calc = XRDCalculator(wavelength=wavelength)
    pattern = calc.get_pattern(structure, two_theta_range=two_theta_range)
    return np.asarray(pattern.x), np.asarray(pattern.y), pattern.hkls


def pattern_from_cif_pymatgen(cif_path, wavelength=1.5406, two_theta_range=(10.0, 80.0)):
    """参考版：pymatgen XRDCalculator 直接从 CIF 文件算。"""
    return pattern_from_structure_pymatgen(
        Structure.from_file(cif_path), wavelength, two_theta_range)
