"""相筛选：CIF 相库 + 观测峰位 → 候选相打分（2026-10-30）。

从 `scripts/analyze_pattern.py` 抽出，命令行与 GUI 共用同一套打分：

    观测峰位 → 每个 CIF 的理论 d 值
             → 粗扫晶胞缩放（0.96–1.05）
             → coverage（观测峰被覆盖的比例）
             + 0.2 × purity（理论峰被匹配的比例）

打分只看**峰位**；强度只用来挑出理论强峰（intensity ≥ 0.02），不参与
相似度——计算强度受织构/择优取向影响，定性阶段信不过。观测峰也对称地
只留最强的 20 根（`observed_peaks`）：裁剪区间会改变全谱最大残差，寻峰
阈值若跟着走，同一张图在原始/处理两条路径上会差出几十根弱峰。

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
from xrd_toolkit.services.background import estimate_baseline_sliding
from xrd_toolkit.services.peak_finding import (
    finite_runs, find_peaks_1d, noise_sigma)
from xrd_toolkit.simulation import core
from xrd_toolkit.simulation.core import load_structure

AL_CIF_NAME = PROJECT_PRESET["second_phase_cif"]
# 粗扫晶胞缩放：固溶体、温度/成分导致的小幅晶胞变化都落在这个范围
DEFAULT_SCALE_RANGE = (0.96, 1.05)
DEFAULT_N_SCALES = 91
# 筛相只认最强的这么多根观测峰（与 1D 页 [寻峰] 默认"最多根数"一致，
# 也是 Hanawalt 定性匹配只看最强线的老规矩）。理由见 observed_peaks。
SCREEN_MAX_PEAKS = 20
# "被元素表排除的相近相"判据：与元素表的**对称差**不超过这么多元素。
# 用户漏写/多写一两个元素是常态（LMFP 漏 Mn 时，富锂相多 1 个、贫锂
# heterosite 多 Mn 又少 Li = 差 2 个）；差得多的相属于别的化学体系，
# 报出来只会淹没提示。
NEAR_MISS_MAX_DIFF = 3


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


def phase_candidates_report(lib, elements=(), preferred=(), skip_names=None,
                            max_near_miss=10, on_progress=None):
    """扫描相库，返回 `(kept, near_miss)` 两组相。

    elements 为空 = 不筛。给了集合时按"样品元素白名单"剪枝：只保留
    元素**全部落在表内**的相（kept）。同时把与元素表**只差几个元素**
    的相收进 near_miss —— 这类相正是用户漏写元素时被误删的真相（例：
    元素表 `Li Fe P O` 漏了 Mn：富锂相 `含 Mn、少 0`，贫锂 heterosite
    `含 Mn、少 Li`，两者都只差 1–2 个元素，会以"多 Mn"的身份进入
    near_miss 而不是静默消失；差 6 个元素的 LaB6 之类不会混进来）。
    near_miss 的每项是 `(path, Structure, extra_symbols)`，按对称差
    从小到大、再按文件名排序，最多 `max_near_miss` 个（大库只评分最
    接近的那几张）。

    读不出来的 CIF 跳过，不让一张坏卡片打断整批分析。preferred 里的
    文件名排在最前（只影响同分时的先后，不参与打分）。skip_names 的
    含义见 phase_library。

    `on_progress(done, total, label)` 可选：每读完一张 CIF 报一次
    （2026-11-17：物相工作台的"读取 CIF k/n"进度条；后台线程里调用，
    回调方自己保证线程安全）。
    """
    wanted = _element_set(elements)
    rank = {str(name): i for i, name in enumerate(preferred)}
    kept, near_scored = [], []
    files = phase_library(lib, skip_names=skip_names)
    for index, path in enumerate(files, start=1):
        try:
            try:
                structure = load_structure(str(path))
            except Exception:                      # noqa: BLE001
                continue
            if wanted:
                have = {el.symbol for el in structure.composition.elements}
                if not have.issubset(wanted):
                    extra = have - wanted
                    missing = wanted - have
                    diff = len(extra) + len(missing)
                    if diff <= NEAR_MISS_MAX_DIFF:
                        near_scored.append(
                            (diff, len(extra), path, structure,
                             tuple(sorted(extra))))
                    continue
            kept.append((path, structure))
        finally:
            if callable(on_progress):
                on_progress(index, len(files), "读取 CIF")
    kept.sort(key=lambda item: (rank.get(item[0].name, len(rank)),
                                item[0].name))
    near_scored.sort(key=lambda item: (item[0], item[1], item[2].name))
    near = [(path, structure, extra)
            for _diff, _n, path, structure, extra in near_scored]
    if max_near_miss is not None:
        near = near[:int(max_near_miss)]
    return kept, near


def phase_candidates(lib, elements=(), preferred=(), skip_names=None):
    """扫描相库并读结构，按元素集合剪枝；返回 [(path, Structure)]。

    只要保留组；"被元素表漏写误删"的相近相见 `phase_candidates_report`。
    """
    kept, _near = phase_candidates_report(
        lib, elements=elements, preferred=preferred,
        skip_names=skip_names, max_near_miss=0)
    return kept


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
    """一个候选相的筛选结果（路径、结构、分数与晶胞缩放）。

    `extra` 只对"被元素表排除的相近相"非空：它比元素表多出来的元素
    （例：元素表漏了 Mn 时，LiMn0.6Fe0.4PO4 的 extra=('Mn',)）。
    """
    path: Path
    structure: object
    score: float
    scale: float
    coverage: float
    purity: float
    extra: tuple = ()


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


def observed_peaks(tth, intensity, *, prominence=5.0, min_distance=0.008,
                   max_peaks=SCREEN_MAX_PEAKS):
    """筛相用的观测峰位（2θ 升序）：按 NaN 断点分段寻峰，只留最强几根。

    输入可以是原始曲线，也可以是处理产物（扣背景/平滑/裁剪后）。
    裁剪区间是 NaN（`process.cut_ranges` 只标空不删点），这里按段寻峰，
    与 1D 页 / 报告同一口径（`peak_finding.finite_runs`）。

    显著度用**明确的 kσ 阈值**（σ 由一阶差分的 MAD 稳健估计），不用
    `find_peaks_1d` 默认的 `max(kσ, 2%×全谱最大残差)`：那个全局下限会
    被一根异常强峰绑住。实测（2026-11-16，LMFP 帧）：2.8–3.1° 的探测器
    坏点峰最强残差 2417 counts，把阈值抬到 48；处理页把这段挖掉后最强
    残差只剩 427，阈值塌到 12，同一张图从 20 根峰变成 55 根——coverage
    的分母是观测峰数，排序跟着翻（heterosite → FePO4）。筛相只看峰位，
    强度只在"挑出强峰"时用；只保留最强的 `max_peaks` 根（默认 20）后，
    挖不挖坏点得到同一套候选。

    参数：
        tth, intensity : 曲线；NaN 表示裁剪掉、没有数据的区间
        prominence     : 噪声倍数（与界面"显著度 σ"同口径）
        min_distance   : 两峰最小间距（度）
        max_peaks      : 最多留几根（按峰高挑，再按 2θ 排序）

    返回：`np.ndarray`，峰位（度）；没有有效峰时为空数组。
    """
    tth = np.asarray(tth, dtype=float)
    y = np.asarray(intensity, dtype=float)
    found = []
    for seg_t, seg_y in finite_runs(tth, y):
        background = estimate_baseline_sliding(seg_t, seg_y, 0.2)
        sigma = noise_sigma(seg_y - background)
        threshold = float(prominence) * sigma
        found.extend(find_peaks_1d(
            seg_t, seg_y, baseline=background,
            min_prominence=threshold if threshold > 0 else None,
            min_distance_deg=float(min_distance)))
    if max_peaks is not None and len(found) > int(max_peaks):
        found = sorted(found, key=lambda p: -p.height)[:int(max_peaks)]
    return np.array(sorted(p.center for p in found), dtype=float)


def _score_candidates(obs_t, items, lam, tmin, tmax, zero, tol,
                      on_progress=None, label="打分"):
    """给 [(path, Structure, extra), ...] 打分，返回按分数降序的 PhaseMatch。

    `on_progress(done, total, label)` 可选：每打完一张报一次（同
    `phase_candidates_report`，后台线程里调用）。
    """
    matches = []
    total = len(items)
    for index, (path, structure, extra) in enumerate(items, start=1):
        try:
            result = score_phase(
                obs_t, structure, lam, tmin, tmax, zero, tol=tol)
            if result is None:
                continue
            score, scale, coverage, purity = result
            matches.append(PhaseMatch(path, structure, score, scale,
                                      coverage, purity, extra))
        finally:
            if callable(on_progress):
                on_progress(index, total, label)
    matches.sort(key=lambda m: -m.score)
    return matches


def screen_phases_report(obs_t, lib, lam, tmin, tmax, zero, elements=(),
                         preferred=(), tol=0.04, skip_names=None,
                         max_near_miss=10, on_progress=None):
    """对整个相库打分，返回 `(matches, near_misses)`。

    near_misses 是"被样品元素表排除、但只多出几个元素"的相近相
    （`PhaseMatch.extra` 记录多出来的元素）。它们是用户漏写元素时最
    可能被误删的真相，所以照样打分——GUI 用它提示"元素表可能漏了 X"。
    正常相表元素为空时 near_misses 也一定是空的。

    `on_progress(done, total, label)` 可选：三个阶段各报一次进度——
    "读取 CIF" → "打分候选" → "打分相近相"（后两段的范围各自从 0 起）。
    """
    kept, near = phase_candidates_report(
        lib, elements=elements, preferred=preferred,
        skip_names=skip_names, max_near_miss=max_near_miss,
        on_progress=on_progress)
    matches = _score_candidates(
        obs_t, [(path, st, ()) for path, st in kept],
        lam, tmin, tmax, zero, tol, on_progress=on_progress,
        label="打分候选")
    near_matches = _score_candidates(
        obs_t, near, lam, tmin, tmax, zero, tol,
        on_progress=on_progress, label="打分相近相")
    return matches, near_matches


def screen_phases(obs_t, lib, lam, tmin, tmax, zero, elements=(),
                  preferred=(), tol=0.04, skip_names=None,
                  on_progress=None):
    """对整个相库打分，按分数从高到低返回 PhaseMatch 列表。

    同分时保持 `phase_candidates` 的先后（preferred 优先、其余按文件名）。
    skip_names 传给 phase_candidates；None = 默认跳过 Al_metal.cif。
    只返回保留组；需要"被元素表排除的相近相"用 `screen_phases_report`。
    `on_progress` 同 `screen_phases_report`（CLI 不传就是没有进度）。
    """
    matches, _near = screen_phases_report(
        obs_t, lib, lam, tmin, tmax, zero, elements=elements,
        preferred=preferred, tol=tol, skip_names=skip_names,
        max_near_miss=0, on_progress=on_progress)
    return matches
