"""相筛选：CIF 相库 + 观测峰位 → 候选相打分（2026-10-30）。

从 `scripts/analyze_pattern.py` 抽出，命令行与 GUI 共用同一套打分：

    观测峰位 → 每个 CIF 的理论 d 值
             → 粗扫晶胞缩放（0.96–1.05）
             → coverage（观测峰被覆盖的比例）
             + 0.2 × purity（理论峰被匹配的比例）

打分只看**峰位**；强度只用来挑出理论强峰（intensity ≥ 0.02），不参与
相似度——计算强度受织构/择优取向影响，定性阶段信不过。

`phase_library` 默认跳过 `Al_metal.cif`：它是集流体第二相，放进主相
候选会让“纯 Al 样品”拿最高分。`phase_candidates` 支持按元素集合剪枝
（大库先把不可能的化学体系排掉）和 `preferred` 同分排序（例如 LMFP
已知 Mn:Fe = 6:4，与 5:5 分数打平时优先 6:4）。
"""
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from xrd_toolkit.presets import PROJECT_PRESET
from xrd_toolkit.simulation import core
from xrd_toolkit.simulation.core import load_structure

AL_CIF_NAME = PROJECT_PRESET["second_phase_cif"]
# 粗扫晶胞缩放：固溶体、温度/成分导致的小幅晶胞变化都落在这个范围
DEFAULT_SCALE_RANGE = (0.96, 1.05)
DEFAULT_N_SCALES = 91


def _element_set(elements):
    """元素集合：接受字符串（空格/逗号分隔）或可迭代对象。"""
    if isinstance(elements, str):
        elements = re.split(r"[,\s]+", elements)
    return {str(e).strip().capitalize() for e in elements if str(e).strip()}


def _skip_set(skip_names):
    """None = 用默认第二相（CLI 老口径）；空元组 = 不排除任何相。"""
    if skip_names is None:
        skip_names = (AL_CIF_NAME,)
    return {str(name) for name in skip_names}


def phase_library(lib, skip_names=None):
    """相库目录里的全部 CIF，按文件名排序；skip_names 里的不参与。

    skip_names=None 时按项目默认跳过 Al_metal.cif（与旧口径一致）；
    GUI 应用预设时显式传入预设里的第二相文件名，空元组 = 不排除。
    """
    lib = Path(lib)
    skip = _skip_set(skip_names)
    return [p for p in sorted(lib.glob("*.cif"))
            if p.is_file() and p.name not in skip]


def phase_candidates(lib, elements=(), preferred=(), skip_names=None):
    """扫描相库并读结构，按元素集合剪枝；返回 [(path, Structure)]。

    elements 为空 = 不筛；给了集合时只保留**元素全部落在集合里**的相。
    读不出来的 CIF 跳过，不让一张坏卡片打断整批分析。preferred 里的
    文件名排在最前（只影响同分时的先后，不参与打分）。skip_names 的
    含义见 phase_library。
    """
    wanted = _element_set(elements)
    rank = {str(name): i for i, name in enumerate(preferred)}
    out = []
    for path in phase_library(lib, skip_names=skip_names):
        try:
            structure = load_structure(str(path))
        except Exception:                          # noqa: BLE001
            continue
        if wanted:
            have = {el.symbol for el in structure.composition.elements}
            if not have.issubset(wanted):
                continue
        out.append((path, structure))
    out.sort(key=lambda item: (rank.get(item[0].name, len(rank)),
                               item[0].name))
    return out


def reflection_data(structure, lam, tmin, tmax):
    """一次算好 hkl 无关的 d0 与相对强度，供晶胞缩放扫描复用。"""
    _hkl, d0, _tth, inten = core.reflection_table(
        structure, wavelength=lam, two_theta_range=(tmin, tmax), nmax=10)
    inten = np.asarray(inten, dtype=float)
    if inten.size and inten.max() > 0:
        inten = inten / inten.max()
    return np.asarray(d0, dtype=float), inten


def scaled_positions(d0, inten, lam, scale, zero):
    """按晶胞缩放把 d0 换成理论 2θ；返回 (2θ, 强度)。"""
    sin_t = np.clip(0.5 * lam / (d0 * scale), 0.0, 1.0)
    return 2.0 * np.degrees(np.arcsin(sin_t)) + zero, inten


def score_phase(obs_t, structure, lam, tmin, tmax, zero, tol=0.04,
                scale_range=DEFAULT_SCALE_RANGE,
                n_scales=DEFAULT_N_SCALES):
    """粗扫晶胞缩放，按观测峰覆盖 + 理论峰纯度打分。

    返回 `(score, scale, coverage, purity)`；没有理论峰时返回 None。
    """
    obs_t = np.asarray(obs_t, dtype=float)
    d0_all, int_all = reflection_data(structure, lam, tmin, tmax)
    strong = int_all >= 0.02
    d0_all, int_all = d0_all[strong], int_all[strong]
    if d0_all.size == 0:
        return None
    best = None
    for scale in np.linspace(scale_range[0], scale_range[1], n_scales):
        tt, _inten = scaled_positions(d0_all, int_all, lam, scale, zero)
        used = set()
        matched_theo = 0
        for x in tt:
            j = int(np.argmin(np.abs(obs_t - x)))
            if abs(float(obs_t[j]) - x) <= tol and j not in used:
                used.add(j)
                matched_theo += 1
        coverage = len(used) / max(len(obs_t), 1)
        purity = matched_theo / max(len(tt), 1)
        score = coverage + 0.2 * purity
        if best is None or score > best[0]:
            best = (score, float(scale), coverage, purity)
    return best


@dataclass
class PhaseMatch:
    """一个候选相的筛选结果（路径、结构、分数与晶胞缩放）。"""
    path: Path
    structure: object
    score: float
    scale: float
    coverage: float
    purity: float


def estimate_zero(obs_t, structure, lam, tmin, tmax, *, scale=1.0,
                  search_deg=0.5, min_matches=3):
    """用观测峰与该相理论峰位估一个全局 2θ 零位（中位平移）。

    典型来源是 2θ 零位没校（所有峰一起偏）——筛相时先对齐再打分/叠图，
    用户就不会看到"结构峰总在实验峰的右边"（2026-11-11 反馈）。
    只认 0.02 以上强度的理论峰；匹配窗口 search_deg 内找不到就跳过该峰。
    匹配少于 min_matches 或中位超出窗口时返回 0.0（不硬掰）。
    """
    obs_t = np.asarray(obs_t, dtype=float)
    d0, inten = reflection_data(structure, lam, tmin, tmax)
    strong = inten >= 0.02
    d0, inten = d0[strong], inten[strong]
    if d0.size == 0 or obs_t.size == 0:
        return 0.0
    t_th, _ = scaled_positions(d0, inten, lam, scale, 0.0)
    shifts = []
    for t in obs_t:
        j = int(np.argmin(np.abs(t_th - t)))
        if abs(t_th[j] - t) <= search_deg:
            shifts.append(float(t - t_th[j]))
    if len(shifts) < min_matches:
        return 0.0
    zero = float(np.median(shifts))
    return zero if abs(zero) <= search_deg else 0.0


def screen_phases(obs_t, lib, lam, tmin, tmax, zero, elements=(),
                  preferred=(), tol=0.04, skip_names=None):
    """对整个相库打分，按分数从高到低返回 PhaseMatch 列表。

    同分时保持 `phase_candidates` 的先后（preferred 优先、其余按文件名）。
    skip_names 传给 phase_candidates；None = 默认跳过 Al_metal.cif。
    """
    matches = []
    for path, structure in phase_candidates(
            lib, elements=elements, preferred=preferred,
            skip_names=skip_names):
        result = score_phase(obs_t, structure, lam, tmin, tmax, zero, tol=tol)
        if result is None:
            continue
        score, scale, coverage, purity = result
        matches.append(PhaseMatch(path, structure, score, scale,
                                  coverage, purity))
    matches.sort(key=lambda m: -m.score)
    return matches
