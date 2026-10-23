"""空间群约束下的位点坐标参数化（2026-10-23）。

Rietveld 里原子坐标**不能**按 P1 给每个原子各放三个参数：等效位点
必须一起动，特殊位置（如 4a）只允许在稳定子留下的子空间里动。本模块
把结构按空间群分成若干**轨道（orbit）**，每条轨道给：

  * 代表位点的标签（CIF 里的原子名，如 Fe5）；
  * 允许的位移方向 `basis`（3×d，分数坐标）；
  * 每个等效位点的对称操作 `transforms`（代表位点 → 该位点）；
  * 优化器参数名（x_Fe5 / z_Fe5 …；轴向无法对齐时叫 c0_Fe5）。

把参数值交给 `positions()` 就得到全部原子的分数坐标。LMFP（Pnma，
28 个原子）按这套只有 6 条轨道、11 个坐标参数；Li 的 4a 位点是 0 维，
自动固定。

坐标约束的数学：代表位点 r 的稳定子（满足 R·r+t ≡ r 的对称操作）
要求位移 δ 满足 (R−I)·δ ≡ 0；把这些矩阵堆起来取零空间，就是该
Wyckoff 位置允许的自由方向。等效位点由 R·(r+δ)+t 生成，所以精修
过程中空间群对称性不会被破坏。

注意：依赖 pymatgen 的 `SpacegroupAnalyzer`（simulation 的可选依赖）。
"""
from dataclasses import dataclass

import numpy as np
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

_TOL = 1e-4


def _periodic_close(a, b, tol=_TOL):
    """分数坐标的周期性比较（0 与 1 等价）。"""
    d = (np.asarray(a, float) - np.asarray(b, float) + 0.5) % 1.0 - 0.5
    return float(np.max(np.abs(d))) < tol


def _sanitize(text):
    """参数名只留字母数字下划线（CIF 标签通常本来就干净）。"""
    out = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in text)
    return out or "site"


def _axis_name(vec):
    """一个基向量若沿某个坐标轴，返回 x/y/z；否则 None。"""
    v = np.asarray(vec, float)
    for i, name in enumerate("xyz"):
        if abs(abs(v[i]) - 1.0) < 1e-8 and np.all(np.abs(v[np.arange(3) != i])
                                                   < 1e-8):
            return name
    return None


def _canonical_basis(basis):
    """把零空间基整理成"主分量取正、按 x→y→z 排序"的形式。"""
    cols = []
    for j in range(basis.shape[1]):
        v = np.array(basis[:, j], dtype=float)
        main = int(np.argmax(np.abs(v)))
        if v[main] < 0:
            v = -v
        v[np.abs(v) < 1e-12] = 0.0
        cols.append(v)
    cols.sort(key=lambda v: int(np.argmax(np.abs(v))))
    return np.column_stack(cols) if cols else np.zeros((3, 0))


@dataclass(frozen=True)
class SiteOrbit:
    """一条对称等效位点轨道。"""

    label: str                 # 报告用标签（CIF 原子名）
    param_key: str             # 参数名前缀（sanitize 后）
    rep_index: int             # 代表位点在 structure 里的下标
    indices: tuple             # 所有等效位点的下标
    wyckoff: str               # Wyckoff 符号（如 4c）
    base: np.ndarray           # 代表位点的分数坐标 (3,)
    basis: np.ndarray          # 允许的位移方向 (3, d)
    transforms: tuple          # 每个等效位点对应的对称操作（代表 → 该位点）
    params: tuple              # 参数名，如 ("x_Fe5", "z_Fe5")

    @property
    def n_free(self):
        return int(self.basis.shape[1])

    def displacement(self, values):
        """参数值 → 代表位点的位移（分数坐标）。"""
        coeffs = np.array([float(values.get(n, 0.0)) for n in self.params],
                          dtype=float)
        return self.basis @ coeffs


class SiteModel:
    """按空间群分组的位点模型。"""

    def __init__(self, structure, symprec=1e-3):
        self.structure = structure
        try:
            sga = SpacegroupAnalyzer(structure, symprec=float(symprec))
            sym = sga.get_symmetrized_structure()
            ops = tuple(sga.get_symmetry_operations())
        except Exception as err:                # noqa: BLE001
            raise ValueError(
                f"空间群分析失败（symprec={symprec}）：{err}") from err
        if len(sym) != len(structure):
            raise ValueError("对称化后的位点数与原结构不一致")
        for i, (a, b) in enumerate(zip(sym, structure)):
            if a.species != b.species or not _periodic_close(
                    a.frac_coords, b.frac_coords):
                raise ValueError(
                    "对称化重排了位点顺序（暂不支持）；"
                    "请用 P1 结构或报告这个问题")

        self.space_group = sga.get_space_group_symbol()
        self.orbits = {}
        self.param_names = []
        self._by_param = {}
        used = set()
        for inds, wyckoff in zip(sym.equivalent_indices,
                                 sym.wyckoff_symbols):
            inds = tuple(int(i) for i in inds)
            rep = inds[0]
            base = np.asarray(structure[rep].frac_coords, dtype=float)
            basis = _canonical_basis(self._free_basis(base, ops))
            transforms = []
            for j in inds:
                op = next((o for o in ops
                           if _periodic_close(o.operate(base),
                                              structure[j].frac_coords)),
                          None)
                if op is None:
                    raise ValueError(
                        f"找不到把 {structure[rep].species_string} "
                        f"映射到位点 {j} 的对称操作")
                transforms.append(op)

            label = (getattr(structure[rep], "label", "") or
                     f"{structure[rep].species_string}{rep}")
            key = _sanitize(label)
            if key in used:
                key = f"{key}_{rep}"
            used.add(key)
            params = []
            for j in range(basis.shape[1]):
                axis = _axis_name(basis[:, j])
                name = (f"{axis}_{key}" if axis is not None
                        else f"c{j}_{key}")
                params.append(name)
            orbit = SiteOrbit(
                label=label, param_key=key, rep_index=rep, indices=inds,
                wyckoff=str(wyckoff), base=base, basis=basis,
                transforms=tuple(transforms), params=tuple(params))
            self.orbits[key] = orbit
            self.param_names.extend(params)
            for name in params:
                self._by_param[name] = orbit
        self.param_names = tuple(self.param_names)

    @staticmethod
    def _free_basis(base, ops):
        """稳定子 (R−I) 的零空间 = 允许的位移方向。"""
        mats = []
        for op in ops:
            if _periodic_close(op.operate(base), base):
                mats.append(op.rotation_matrix - np.eye(3))
        if not mats:
            return np.eye(3)
        _u, s, vt = np.linalg.svd(np.vstack(mats))
        rank = int(np.sum(s > 1e-6))
        return vt[rank:].T

    def orbit(self, key):
        """按参数前缀（或原始 CIF 标签）取轨道；不存在时报错。"""
        if key in self.orbits:
            return self.orbits[key]
        for orbit in self.orbits.values():
            if orbit.label == key:
                return orbit
        known = ", ".join(sorted(self.orbits)) or "（无）"
        raise ValueError(f"结构里没有可精修的位点 {key!r}；可选：{known}")

    def positions(self, values):
        """参数值 → 全部位点的分数坐标 (n_sites, 3)。"""
        coords = np.array([site.frac_coords for site in self.structure],
                          dtype=float)
        for orbit in self.orbits.values():
            rep_new = orbit.base + orbit.displacement(values)
            for j, op in zip(orbit.indices, orbit.transforms):
                coords[j] = np.asarray(op.operate(rep_new), dtype=float) % 1.0
        return coords

    def summary(self, values):
        """报告用：{标签: (wyckoff, x, y, z)}，坐标取代表位点。"""
        out = {}
        for orbit in self.orbits.values():
            xyz = (orbit.base + orbit.displacement(values)) % 1.0
            out[orbit.label] = (orbit.wyckoff, *(float(v) for v in xyz))
        return out
