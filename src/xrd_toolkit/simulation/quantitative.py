"""定量相分数（wt%）：Rietveld scale → 重量分数（2026-10-31）。

多相全谱拟合里，每相的 scale 只说明"这一相在谱里有多强"；要换成
重量分数，还要乘上每晶胞的化学式单元数 Z、化学式量 M 和晶胞体积 V：

    W_p = S_p·(Z_p·M_p·V_p) / Σ_q S_q·(Z_q·M_q·V_q)

S_p 是**作用在 m|F|²LP 原始强度上的**比例因子（本引擎的 scale 乘的是
表内归一化强度，所以换算时要乘 100/norm；Le Bail 相用逐反射强度的
加权平均）。公式只对晶态相成立：非晶/未知相不在拟合模型里，不参与
分母；报告里必须写清这一点，别把 Le Bail 的"相对散射强度分数"直接
改名叫 wt%。

Z/M 从 pymatgen 结构的约简化学式来：结构是整个晶胞，约简化学式是
一个化学式单元（如 LiFePO4），factor 就是每晶胞的化学式单元数。
V 用精修后的 a/b/c（角度不精修，取结构原角度）。
"""
from dataclasses import dataclass
import math

import numpy as np

WT_NOTE = ("wt% 只对拟合模型里的晶态相；非晶/未知相不参与分母。"
           "Le Bail 相的 S 取逐反射强度的加权平均，精度低于结构模式。")


@dataclass
class PhaseQuantity:
    """一个参与定量的相：结构 + 作用在原始强度上的 scale。"""
    label: str
    structure: object
    scale: float
    cell: tuple = None          # 精修后的 (a, b, c)；None = 结构原晶胞
    source: str = "structure"   # structure / le_bail


@dataclass
class PhaseWeight:
    label: str
    weight_percent: float
    scale: float
    z: float
    molar_mass: float
    volume: float
    source: str = "structure"
    warning: str = ""


def cell_volume(cell, angles_deg=(90.0, 90.0, 90.0)):
    """a/b/c（Å）+ α/β/γ（度）→ 晶胞体积（Å³）。"""
    a, b, c = (float(v) for v in cell)
    al, be, ga = (math.radians(float(v)) for v in angles_deg)
    ca, cb, cg = math.cos(al), math.cos(be), math.cos(ga)
    inside = 1.0 - ca * ca - cb * cb - cg * cg + 2.0 * ca * cb * cg
    return a * b * c * math.sqrt(max(inside, 0.0))


def phase_mass_factor(structure, cell=None):
    """返回 (Z, M, V, warning)：每晶胞化学式单元数、化学式量、晶胞体积。

    结构缺 composition 或约简失败时返回全 NaN 和说明；调用方把这相
    排除在分母外（不能拿 0 当权重，否则会悄悄改变其它相的比例）。
    """
    try:
        comp = structure.composition
        reduced, factor = comp.get_reduced_composition_and_factor()
        z = float(factor)
        molar = float(reduced.weight)
        angles = tuple(float(a) for a in structure.lattice.angles)
        abc = (tuple(float(v) for v in structure.lattice.abc)
               if cell is None else tuple(float(v) for v in cell))
        vol = cell_volume(abc, angles)
    except Exception as err:                       # noqa: BLE001
        return (float("nan"),) * 3 + (f"取不到结构信息：{err}",)
    if not (z > 0 and molar > 0 and vol > 0
            and np.isfinite(z + molar + vol)):
        return (float("nan"),) * 3 + ("Z/M/V 非正，无法定量",)
    return z, molar, vol, ""


def phase_weight_percent(phases):
    """按 Rietveld 公式换算各相 wt%；返回 PhaseWeight 列表。

    只有 Z/M/V 与 scale 都有效的相进入分母；无效相在结果里给 NaN
    和 warning（不参与归一化），避免"悄悄把别人的比例抬高"。
    """
    rows = []
    factors = []
    for phase in phases:
        z, molar, vol, warn = phase_mass_factor(
            phase.structure, getattr(phase, "cell", None))
        scale = float(getattr(phase, "scale", float("nan")))
        if scale < 0:
            warn = (warn + "；" if warn else "") + "scale < 0"
        mass = (scale * z * molar * vol
                if math.isfinite(scale) and scale > 0
                and math.isfinite(z * molar * vol) else float("nan"))
        factors.append(mass)
        rows.append([phase, scale, z, molar, vol, warn, mass])
    total = float(np.nansum([m for m in factors if math.isfinite(m)]))
    out = []
    for phase, scale, z, molar, vol, warn, mass in rows:
        if math.isfinite(mass) and total > 0:
            wt = 100.0 * mass / total
        else:
            wt = float("nan")
        out.append(PhaseWeight(
            label=str(phase.label), weight_percent=float(wt),
            scale=float(scale), z=float(z), molar_mass=float(molar),
            volume=float(vol), source=str(getattr(phase, "source",
                                                  "structure")),
            warning=warn))
    return out
